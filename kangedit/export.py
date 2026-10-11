"""Rendering the timeline with ffmpeg (final export and preview audio)."""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
from dataclasses import dataclass
from typing import Optional

from . import reframe
from .captions import build_captions, write_ass, write_srt
from .color import needs_color, write_cube
from .config import cache_dir, frame_size
from .ffmpeg import ProgressCb, ffmpeg_matrix, run_ffmpeg
from .models import Project

CODECS = {
    "h264": ("H.264 (MP4) - most compatible", ".mp4"),
    "h265": ("H.265 / HEVC (MP4) - smaller files", ".mp4"),
    "prores": ("Apple ProRes 422 HQ (MOV) - for further editing", ".mov"),
}
QUALITIES = {"High": 18, "Medium": 21, "Small file": 26}
CAPTION_MODES = {
    "none": "No captions",
    "burn": "Burn into video",
    "srt": "Separate .srt file",
    "both": "Burn in + .srt file",
}


@dataclass
class ExportSettings:
    output: str
    aspect: str
    resolution: str
    fps: float
    fit_mode: str = "fill"
    codec: str = "h264"
    quality: str = "High"
    captions: str = "none"
    use_proxies: bool = False   # draft export from proxies


def _clip_inputs(project: Project, use_proxies: bool, i: int, k: int) -> tuple[list[str], int, bool]:
    """Input args for clip i starting at input index k. Returns (args, n_inputs, audio_is_silence)."""
    clip = project.clips[i]
    f = project.footage[clip.footage_id]
    src = f.proxy_path if (use_proxies and f.has_proxy()) else f.path
    args = ["-ss", f"{clip.src_in:.4f}", "-t", f"{clip.duration + 0.25:.4f}", "-i", src]
    if f.has_audio:
        return args, 1, False
    args += ["-f", "lavfi", "-t", f"{clip.duration + 0.25:.4f}", "-i", "anullsrc=r=48000:cl=stereo"]
    return args, 2, True


def _audio_chain(inp: str, out: str, dur: float, volume: float) -> str:
    fade = min(0.02, dur / 4)
    return (f"[{inp}]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"volume={volume:.3f},afade=t=in:d={fade:.3f},afade=t=out:st={max(0.0, dur - fade):.4f}:d={fade:.3f},"
            f"apad,atrim=duration={dur:.4f},asetpts=PTS-STARTPTS[{out}]")


def build_export(project: Project, s: ExportSettings, workdir: str) -> list[str]:
    tw, th = frame_size(s.aspect, s.resolution)
    inputs: list[str] = []
    graph: list[str] = []
    labels: list[str] = []
    k = 0
    final_fmt = "yuv422p10le" if s.codec == "prores" else "yuv420p"
    for i, clip in enumerate(project.clips):
        f = project.footage[clip.footage_id]
        args, n_in, silent = _clip_inputs(project, s.use_proxies, i, k)
        inputs += args
        vi, ai = f"{k}:v:0", (f"{k + 1}:a:0" if silent else f"{k}:a:0")
        k += n_in
        dur = clip.duration
        nfr = max(1, int(round(dur * s.fps)))
        tdur = nfr / s.fps

        chain = [f"[{vi}]setpts=PTS-STARTPTS,fps={s.fps:.4f}[v{i}a]"]
        chain.append(reframe.ffmpeg_filter(f.width, f.height, tw, th, s.fit_mode, clip.zoom,
                                           clip.offset_x, clip.offset_y, f"v{i}a", f"v{i}b", f"c{i}"))
        post = f"[v{i}b]"
        if needs_color(clip.grade, f.log_profile):
            lut = f"lut_{i}.cube"
            write_cube(os.path.join(workdir, lut), clip.grade, f.log_profile, f.tonemap)
            matrix = "bt709" if (s.use_proxies and f.has_proxy()) else ffmpeg_matrix(f.color_space, min(f.width, f.height))
            post += (f"scale=in_color_matrix={matrix}:in_range=auto,format=rgb48le,"
                     f"lut3d=file={lut}:interp=tetrahedral,")
        post += (f"scale=out_color_matrix=bt709:out_range=tv,format={final_fmt},"
                 f"tpad=stop_mode=clone:stop_duration=1,trim=end_frame={nfr},setpts=PTS-STARTPTS[v{i}]")
        chain.append(post)
        graph += chain
        graph.append(_audio_chain(ai, f"a{i}", tdur, clip.volume))
        labels.append(f"[v{i}][a{i}]")

    graph.append("".join(labels) + f"concat=n={len(project.clips)}:v=1:a=1[vcat][acat]")
    vout = "vcat"
    caps = build_captions(project) if s.captions != "none" else []
    if s.captions in ("burn", "both") and caps:
        write_ass(os.path.join(workdir, "captions.ass"), caps, project.settings.captions, tw, th)
        graph.append("[vcat]subtitles=captions.ass[vsub]")
        vout = "vsub"
    if s.captions in ("srt", "both") and caps:
        write_srt(os.path.splitext(s.output)[0] + ".srt", caps, project.settings.captions)

    graph_path = os.path.join(workdir, "graph.txt")
    with open(graph_path, "w", encoding="utf-8") as fh:
        fh.write(";\n".join(graph))

    out = ["-/filter_complex", "graph.txt", "-map", f"[{vout}]", "-map", "[acat]"]
    if s.codec == "h265":
        out += ["-c:v", "libx265", "-preset", "medium", "-crf", str(QUALITIES[s.quality] + 2),
                "-tag:v", "hvc1", "-pix_fmt", "yuv420p"]
    elif s.codec == "prores":
        out += ["-c:v", "prores_ks", "-profile:v", "3", "-vendor", "apl0", "-pix_fmt", "yuv422p10le"]
    else:
        out += ["-c:v", "libx264", "-preset", "medium", "-crf", str(QUALITIES[s.quality]),
                "-profile:v", "high", "-pix_fmt", "yuv420p"]
    out += ["-r", f"{s.fps:.4f}", "-colorspace", "bt709", "-color_primaries", "bt709",
            "-color_trc", "bt709", "-color_range", "tv"]
    if s.codec == "prores":
        out += ["-c:a", "pcm_s16le"]
    else:
        out += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
    out += [os.path.abspath(s.output)]
    return inputs + out


def export(project: Project, s: ExportSettings, progress: Optional[ProgressCb] = None,
           cancel: Optional[threading.Event] = None) -> str:
    if not project.clips:
        raise RuntimeError("The timeline is empty")
    workdir = tempfile.mkdtemp(prefix="export_", dir=str(cache_dir("tmp")))
    try:
        args = build_export(project, s, workdir)
        run_ffmpeg(args, project.duration, progress, cancel, cwd=workdir,
                   label=f"Exporting {os.path.basename(s.output)}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return s.output


def render_audio(project: Project, use_proxies: bool, out_wav: str,
                 cancel: Optional[threading.Event] = None) -> Optional[str]:
    """Mix the timeline audio into a WAV for synced preview playback."""
    if not project.clips:
        return None
    inputs, graph, labels, k = [], [], [], 0
    for i, clip in enumerate(project.clips):
        args, n_in, silent = _clip_inputs(project, use_proxies, i, k)
        inputs += args
        ai = f"{k + 1}:a:0" if silent else f"{k}:a:0"
        k += n_in
        graph.append(_audio_chain(ai, f"a{i}", clip.duration, clip.volume))
        labels.append(f"[a{i}]")
    graph.append("".join(labels) + f"concat=n={len(labels)}:v=0:a=1[aout]")
    workdir = tempfile.mkdtemp(prefix="audio_", dir=str(cache_dir("tmp")))
    try:
        with open(os.path.join(workdir, "graph.txt"), "w", encoding="utf-8") as fh:
            fh.write(";\n".join(graph))
        tmp = out_wav + ".part.wav"
        run_ffmpeg(inputs + ["-/filter_complex", "graph.txt", "-map", "[aout]", "-c:a", "pcm_s16le", tmp],
                   project.duration, None, cancel, cwd=workdir)
        os.replace(tmp, out_wav)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return out_wav
