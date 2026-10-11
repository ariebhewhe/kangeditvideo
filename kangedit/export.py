"""Rendering the timeline with ffmpeg (final export and preview audio)."""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
from dataclasses import dataclass
from typing import Optional

from . import reframe
from .captions import build_captions, caption_layout, write_ass, write_srt
from .color import needs_color, write_cube
from .config import cache_dir, frame_size
from .edits import ClipPlan, plan
from .fonts import copy_family
from .titles import write_titles_ass
from .ffmpeg import FFmpegError, ProgressCb, ffmpeg_matrix, hw_encoder, hw_encoder_args, hw_encoder_label, run_ffmpeg
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
    # Encode on the graphics card when it has a working H.264/H.265 encoder. (GPU *decoding* isn't
    # used here: copying full-size frames back for the CPU filters measured slower than CPU decoding.)
    use_gpu: bool = True


def _clip_inputs(project: Project, use_proxies: bool, i: int, cp: ClipPlan, fps: float) -> tuple[list[str], int, bool, float]:
    """Input args for clip i covering its video and audio ranges (handles and split edits included).
    Returns (args, n_inputs, audio_is_silence, input start time in the source)."""
    clip = project.clips[i]
    f = project.footage[clip.footage_id]
    src = f.proxy_path if (use_proxies and f.has_proxy()) else f.path
    ss = max(0.0, min(cp.v_in, cp.a_in))
    end = max(cp.v_in + cp.v_frames / fps, cp.a_in + cp.a_dur)
    args = ["-ss", f"{ss:.4f}", "-t", f"{end - ss + 0.25:.4f}", "-i", src]
    if f.has_audio:
        return args, 1, False, ss
    args += ["-f", "lavfi", "-t", f"{cp.a_dur + 0.25:.4f}", "-i", "anullsrc=r=48000:cl=stereo"]
    return args, 2, True, ss


def _audio_chain(inp: str, out: str, dur: float, volume: float, skip: float = 0.0) -> str:
    fade = min(0.02, dur / 4)
    trim = f"atrim=start={skip:.4f},asetpts=PTS-STARTPTS," if skip > 0.0005 else ""
    return (f"[{inp}]{trim}aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"volume={volume:.3f},afade=t=in:d={fade:.3f},afade=t=out:st={max(0.0, dur - fade):.4f}:d={fade:.3f},"
            f"apad,atrim=duration={dur:.4f},asetpts=PTS-STARTPTS[{out}]")


def _mix_sounds(project: Project, inputs: list[str], graph: list[str], k: int) -> str:
    """Mix the SFX layer over [acat] (inputs from index k on). Returns the label of the final audio."""
    dur = project.duration
    sounds = [x for x in project.sounds if x.duration > 0.01 and x.start < dur and os.path.exists(x.path)]
    if not sounds:
        return "acat"
    labels = []
    for j, x in enumerate(sounds):
        inputs += ["-ss", f"{x.src_in:.4f}", "-t", f"{x.duration:.4f}", "-i", x.path]
        fade = min(0.02, x.duration / 4)
        delay = int(round(max(0.0, x.start) * 1000))
        graph.append(f"[{k + j}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                     f"volume={x.volume:.3f},afade=t=in:d=0.005,"
                     f"afade=t=out:st={max(0.0, x.duration - fade):.4f}:d={fade:.3f},"
                     f"adelay=delays={delay}:all=1[sfx{j}]")
        labels.append(f"[sfx{j}]")
    # normalize=0: sounds add to the edit's audio at their own level instead of ducking it.
    graph.append(f"[acat]{''.join(labels)}amix=inputs={len(labels) + 1}:normalize=0:duration=first:"
                 f"dropout_transition=0[amix]")
    return "amix"


def _fade_audio(project: Project, graph: list[str], aout: str) -> str:
    """Fade the final mix in / out at the timeline's ends (project settings). Returns the output label."""
    dur = project.duration
    fi, fo = project.settings.fades(dur, sound=True)
    if fi <= 0 and fo <= 0:
        return aout
    fades = []
    if fi > 0:
        fades.append(f"afade=t=in:st=0:d={fi:.4f}")
    if fo > 0:
        fades.append(f"afade=t=out:st={max(0.0, dur - fo):.4f}:d={fo:.4f}")
    graph.append(f"[{aout}]{','.join(fades)}[afaded]")
    return "afaded"


def _join(graph: list[str], plans: list[ClipPlan], fps: float, video: bool) -> None:
    """Join the per-clip streams [v{i}]/[a{i}] into [vcat]/[acat]: transitions overlap neighbours
    (xfade / acrossfade), cuts concatenate. Video and audio are joined separately because split
    edits put their cut points at different times."""
    groups: list[tuple[str, str]] = []
    cur_v = cur_a = ""
    cur_len = 0
    for i, cp in enumerate(plans):
        if cp.xfade and cur_a:
            d_f = cp.pre_f + plans[i - 1].post_f
            d = d_f / fps
            if video:
                graph.append(f"[{cur_v}][v{i}]xfade=transition={cp.xfade}:duration={d:.4f}:"
                             f"offset={(cur_len - d_f) / fps:.4f}[xv{i}]")
                cur_v = f"xv{i}"
            graph.append(f"[{cur_a}][a{i}]acrossfade=d={d:.4f}:c1=tri:c2=tri[xa{i}]")
            cur_a = f"xa{i}"
            cur_len += cp.v_frames - d_f
        else:
            if cur_a:
                groups.append((cur_v, cur_a))
            cur_v, cur_a, cur_len = f"v{i}", f"a{i}", cp.v_frames
    groups.append((cur_v, cur_a))
    if video:
        graph.append("".join(f"[{v}]" for v, _ in groups) + f"concat=n={len(groups)}:v=1:a=0[vcat]")
    graph.append("".join(f"[{a}]" for _, a in groups) + f"concat=n={len(groups)}:v=0:a=1[acat]")


def gpu_encoder(s: ExportSettings) -> Optional[str]:
    """Hardware encoder to use for this export, or None for the CPU encoders."""
    return hw_encoder(s.codec) if s.use_gpu and s.codec in ("h264", "h265") else None


def build_export(project: Project, s: ExportSettings, workdir: str, encoder: Optional[str] = None) -> list[str]:
    tw, th = frame_size(s.aspect, s.resolution)
    inputs: list[str] = []
    graph: list[str] = []
    k = 0
    final_fmt = "yuv422p10le" if s.codec == "prores" else "yuv420p"
    plans = plan(project, s.fps)
    for i, (clip, cp) in enumerate(zip(project.clips, plans)):
        f = project.footage[clip.footage_id]
        args, n_in, silent, ss = _clip_inputs(project, s.use_proxies, i, cp, s.fps)
        inputs += args
        vi, ai = f"{k}:v:0", (f"{k + 1}:a:0" if silent else f"{k}:a:0")
        k += n_in

        skip = max(0.0, cp.v_in - ss)
        trim = f"trim=start={skip:.4f},setpts=PTS-STARTPTS," if skip > 0.0005 else ""
        chain = [f"[{vi}]setpts=PTS-STARTPTS,{trim}fps={s.fps:.4f}[v{i}a]"]
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
                 f"tpad=stop_mode=clone:stop_duration=1,trim=end_frame={cp.v_frames},setpts=PTS-STARTPTS[v{i}]")
        chain.append(post)
        graph += chain
        graph.append(_audio_chain(ai, f"a{i}", cp.a_dur, clip.volume, 0.0 if silent else max(0.0, cp.a_in - ss)))

    _join(graph, plans, s.fps, video=True)
    aout = _fade_audio(project, graph, _mix_sounds(project, inputs, graph, k))
    vout = "vcat"
    fonts_dir = os.path.join(workdir, "fonts")
    texts = [x for x in project.texts if x.text.strip() and x.duration > 0 and x.start < project.duration]
    if texts:
        write_titles_ass(os.path.join(workdir, "titles.ass"), texts, tw, th, s.fps)
        found = [copy_family(f, fonts_dir) for f in {x.font for x in texts}]
        graph.append(f"[{vout}]subtitles=titles.ass{':fontsdir=fonts' if any(found) else ''}[vtitles]")
        vout = "vtitles"
    caps = build_captions(project, aspect=s.aspect) if s.captions != "none" else []
    cap_layout = caption_layout(project.settings.captions, tw, th, project.settings.video_format)
    if s.captions in ("burn", "both") and caps:
        write_ass(os.path.join(workdir, "captions.ass"), caps, project.settings.captions, tw, th, cap_layout)
        # Hand libass the font's own files: it may not find fonts installed for this user only by name.
        fontsdir = ":fontsdir=fonts" if copy_family(project.settings.captions.font, fonts_dir) else ""
        graph.append(f"[{vout}]subtitles=captions.ass{fontsdir}[vsub]")
        vout = "vsub"
    fi, fo = project.settings.fades(project.duration)
    if fi > 0 or fo > 0:   # after the titles and captions, so they fade with the picture
        fades = []
        if fi > 0:
            fades.append(f"fade=t=in:st=0:d={fi:.4f}")
        if fo > 0:
            fades.append(f"fade=t=out:st={max(0.0, project.duration - fo):.4f}:d={fo:.4f}")
        graph.append(f"[{vout}]{','.join(fades)}[vfaded]")
        vout = "vfaded"
    if s.captions in ("srt", "both") and caps:
        write_srt(os.path.splitext(s.output)[0] + ".srt", caps, project.settings.captions, cap_layout)

    graph_path = os.path.join(workdir, "graph.txt")
    with open(graph_path, "w", encoding="utf-8") as fh:
        fh.write(";\n".join(graph))

    # Slice-threaded filters (scale, lut3d) keep the filter graph from being the bottleneck.
    out = ["-filter_complex_threads", str(max(2, min(8, os.cpu_count() or 2))),
           "-/filter_complex", "graph.txt", "-map", f"[{vout}]", "-map", f"[{aout}]"]
    if encoder:
        q = QUALITIES[s.quality] + (2 if s.codec == "h265" else 0)
        out += ["-c:v", encoder] + hw_encoder_args(encoder, q) + ["-pix_fmt", "nv12"]
        if s.codec == "h265":
            out += ["-tag:v", "hvc1"]
    elif s.codec == "h265":
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
    encoder = gpu_encoder(s)
    name = os.path.basename(s.output)
    workdir = tempfile.mkdtemp(prefix="export_", dir=str(cache_dir("tmp")))
    try:
        if encoder:
            try:
                run_ffmpeg(build_export(project, s, workdir, encoder), project.duration, progress, cancel,
                           cwd=workdir, label=f"Exporting {name} (GPU: {hw_encoder_label(encoder)})")
                return s.output
            except FFmpegError:
                pass   # driver hiccup, out of video memory, unsupported source...: redo it on the CPU
        run_ffmpeg(build_export(project, s, workdir), project.duration, progress, cancel, cwd=workdir,
                   label=f"Exporting {name}" + (" (CPU)" if s.use_gpu else ""))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return s.output


def render_audio(project: Project, use_proxies: bool, out_wav: str,
                 cancel: Optional[threading.Event] = None) -> Optional[str]:
    """Mix the timeline audio into a WAV for synced preview playback."""
    if not project.clips:
        return None
    inputs, graph, k = [], [], 0
    fps = project.settings.fps
    plans = plan(project, fps)
    for i, (clip, cp) in enumerate(zip(project.clips, plans)):
        args, n_in, silent, ss = _clip_inputs(project, use_proxies, i, cp, fps)
        inputs += args
        ai = f"{k + 1}:a:0" if silent else f"{k}:a:0"
        k += n_in
        graph.append(_audio_chain(ai, f"a{i}", cp.a_dur, clip.volume, 0.0 if silent else max(0.0, cp.a_in - ss)))
    _join(graph, plans, fps, video=False)
    aout = _fade_audio(project, graph, _mix_sounds(project, inputs, graph, k))
    workdir = tempfile.mkdtemp(prefix="audio_", dir=str(cache_dir("tmp")))
    try:
        with open(os.path.join(workdir, "graph.txt"), "w", encoding="utf-8") as fh:
            fh.write(";\n".join(graph))
        tmp = out_wav + ".part.wav"
        run_ffmpeg(inputs + ["-/filter_complex", "graph.txt", "-map", f"[{aout}]", "-c:a", "pcm_s16le", tmp],
                   project.duration, None, cancel, cwd=workdir)
        os.replace(tmp, out_wav)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return out_wav
