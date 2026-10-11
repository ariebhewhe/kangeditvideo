"""Background music: NCS (NoCopyrightSounds) suggestions by mood, and audio download from a YouTube link.

A YouTube link (or a search for a suggested track) is fetched with yt-dlp, and FFmpeg extracts its sound
into the sound library as an .mp3. NCS tracks are free to use in videos as long as the video's
description credits them (see credit()); anything else from YouTube needs its own permission.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
import urllib.parse
from dataclasses import dataclass
from typing import Callable, Optional, Union

from .config import sfx_dir
from .ffmpeg import ffmpeg_bin

NCS_CHANNEL = "https://www.youtube.com/@NoCopyrightSounds"
_YT_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")


@dataclass(frozen=True)
class Track:
    artist: str
    title: str

    @property
    def name(self) -> str:
        return f"{self.artist} - {self.title}"

    @property
    def query(self) -> str:
        """What a YouTube search for the track's NCS release looks like."""
        return f"{self.name} NCS Release"


def _t(*pairs: tuple[str, str]) -> list[Track]:
    return [Track(a, t) for a, t in pairs]


# Suggested NCS tracks for the kind of video being made.
SUGGESTIONS: dict[str, list[Track]] = {
    "Vlog": _t(("Cartoon", "On & On (feat. Daniel Levi)"), ("Syn Cole", "Feel Good"), ("Elektronomia", "Sky High"),
               ("Diamond Eyes", "Everything"), ("Jo Cohen & Sex Whales", "We Are"), ("Janji", "Together"),
               ("Syn Cole", "Gizmo"), ("Itro", "Panda"), ("Tobu", "Candyland"), ("Tobu", "Hope"),
               ("Itro & Tobu", "Cloud 9"), ("Raven & Kreyn", "So Happy"), ("Cartoon", "C U Again (feat. Mikk Mäe)"),
               ("Unknown Brain", "Why Do I? (feat. Bri Tolani)")),
    "Cinematic": _t(("Kovan & Electro-Light", "Skyline"), ("Lost Sky", "Dreams"), ("Electro-Light", "Symbolism"),
                    ("Ship Wrek & Zookeepers", "Ark"), ("Egzod & Maestro Chives", "Royalty (ft. Neoni)"),
                    ("Rival x Cadmium", "Seasons (feat. Harley Bird)"), ("Distrion & Alex Skrindo", "Entropy"),
                    ("Rob Gasser", "Hollow"), ("Alan Walker", "Spectre"), ("Alan Walker", "Force"),
                    ("Phantom Sage", "Our Lives Past (feat. Emily Sandberg)"), ("Laszlo", "Supernova"),
                    ("Ahrix", "Nova")),
    "Aftermovie / party": _t(("Elektronomia", "Energy"), ("Different Heaven & EH!DE", "My Heart"),
                             ("Janji", "Heroes Tonight (feat. Johnning)"), ("Jim Yosef", "Firefly"),
                             ("DEAF KEV", "Invincible"), ("Culture Code", "Make Me Move (feat. Karra)"),
                             ("Spektrem", "Shine"), ("Unknown Brain", "Superhero (feat. Chris Linton)"),
                             ("Robin Hustin x TobiMorrow", "Light It Up (feat. Jex)"), ("Tobu", "Infectious"),
                             ("Jensation", "Joystick"), ("DEAF KEV", "Safe & Sound"), ("Lensko", "Let's Go!"),
                             ("Raven & Kreyn", "Muffin"), ("Elektronomia", "Vision")),
    "Travel": _t(("Lost Sky", "Fearless pt.II (feat. Chris Linton)"), ("Elektronomia", "Limitless"),
                 ("Cartoon", "Why We Lose (feat. Coleman Trapp)"), ("Lost Sky", "Where We Started (feat. Jex)"),
                 ("Diviners", "Savannah (feat. Philly K)"), ("Jim Yosef", "Eclipse"), ("Elektronomia", "Collide"),
                 ("Alan Walker", "Fade"), ("Tobu & Itro", "Sunburst"), ("Lost Sky", "Vision"),
                 ("Elektronomia", "Summersong 2018"), ("Tobu", "Roots"), ("Lensko", "Circles")),
    "Chill": _t(("Ascence", "About You"), ("Cartoon", "Howling (feat. Asena)"), ("Clarx & Harddope", "Castle"),
                ("Jim Yosef & Anna Yvette", "Linked"), ("Jim Yosef", "Arrow"), ("Krys Talk", "Fly Away"),
                ("Arc North", "Meant To Be (feat. Krista Marina)"), ("Lost Sky", "Fearless"), ("Kisma", "Fingertips"),
                ("Itro & Kontinuum", "Alive")),
    "Gaming / action": _t(("Disfigure", "Blank"), ("Jim Yosef", "Link"), ("Alex Skrindo", "Jumbo"),
                          ("Different Heaven", "Nekozilla"), ("Lensko", "Cetus"),
                          ("Warriyo", "Mortals (feat. Laura Brehm)"), ("Desmeon", "Hellcat"),
                          ("Max Brhon", "Cyberpunk"), ("Ship Wrek", "Pain"), ("Heuse & Zeus X Crona", "Pill"),
                          ("Distrion & Electro-Light", "Rubik"), ("Jim Yosef", "Speed"), ("Jim Yosef", "Lights"),
                          ("Distrion & Alex Skrindo", "Lightning"), ("Clarx", "Zig Zag")),
}


def available() -> bool:
    """yt-dlp is installed (it comes with requirements.txt)."""
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        return False
    return True


def is_youtube_link(text: str) -> bool:
    m = re.match(r"^\s*(?:https?://)?(?:[\w-]+\.)*([\w-]+\.[a-z]+)(?:[/?#]|$)", text, re.I)
    return bool(m) and any(m.group(1).lower() == h or m.group(1).lower().endswith("." + h) for h in _YT_HOSTS)


def search_url(track: Track) -> str:
    """The track searched for on the NCS YouTube channel."""
    words = re.sub(r"[^\w.' ]+|\s+(?:x|ft\.|feat\.)\s+|\s*\(.*", " ", f"{track.artist} {track.title}")
    return f"{NCS_CHANNEL}/search?" + urllib.parse.urlencode({"query": " ".join(words.split())})


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def matches(track: Track, video_title: str) -> bool:
    """A video title is this track (not another track or a remix of it)."""
    t = f" {_norm(clean_title(video_title))} "
    song = _norm(re.sub(r"\s*[(\[].*", "", track.title))                         # without "(feat. ...)"
    lead = _norm(re.split(r"\s+(?:&|,|x|vs\.?|ft\.|feat\.)\s+", track.artist)[0])  # first artist
    remix = "remix" in _norm(track.title)
    return f" {song} " in t and f" {lead} " in t and ("remix" in t.split()) == remix


def clean_title(title: str) -> str:
    """A YouTube title without its NCS decoration: "A - B | House | NCS - Copyright Free Music" or
    "A - B [NCS Release]" -> "A - B"."""
    title = title.split(" | ")[0]
    title = re.sub(r"\s*[\[(][^\])]*(?:NCS|Official|Copyright)[^\])]*[\])]", "", title, flags=re.I)
    return title.strip() or "music"


def credit(title: str, url: str = "") -> str:
    """The credit NCS asks for in the description of a video using one of its tracks."""
    lines = [f"Music: {title}", "Music provided by NoCopyrightSounds"]
    if url:
        lines.append(f"Watch: {url}")
    lines.append(f"Free Download / Stream: {NCS_CHANNEL}")
    return "\n".join(lines)


def _safe_name(name: str) -> str:
    name = re.sub(r"[^\w\-. ()&,']+", "_", name, flags=re.UNICODE).strip(" ._") or "music"
    return name[:80]


@dataclass
class Downloaded:
    path: str
    title: str
    url: str          # the video's page


def _options() -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        # The system's certificates, not certifi's: antivirus HTTPS scanning (Avast, Kaspersky...) signs with
        # a root that is only in the system store.
        "compat_opts": {"no-certifi"},
    }
    # YouTube only serves the media to clients that solve its JavaScript challenge: use any runtime installed.
    runtimes = {rt: {"path": path} for rt in ("deno", "node", "bun") if (path := shutil.which(rt))}
    if runtimes:
        opts["js_runtimes"] = runtimes
    return opts


def find_on_ncs(track: Track) -> Optional[str]:
    """The link of the track's release on the NCS channel (None if the channel doesn't have it)."""
    import yt_dlp

    with yt_dlp.YoutubeDL({**_options(), "extract_flat": True, "playlistend": 10}) as ydl:
        info = ydl.extract_info(search_url(track), download=False)
    for e in (info or {}).get("entries") or []:
        if e and len(e.get("id") or "") == 11 and matches(track, e.get("title") or ""):   # videos, not playlists
            return f"https://www.youtube.com/watch?v={e['id']}"
    return None


def _target(source: Union[str, Track]) -> tuple[str, str]:
    """What yt-dlp is given for a link or a suggested track, and the name to show for it."""
    if isinstance(source, Track):
        return find_on_ncs(source) or f"ytsearch1:{source.query}", source.name
    target = source.strip()
    if not target:
        raise ValueError("no link given")
    if not is_youtube_link(target):
        raise ValueError("only YouTube links are supported")
    return target, target


def _first_video(info: Optional[dict], label: str) -> dict:
    if info and info.get("_type") == "playlist":
        info = next((e for e in info.get("entries") or [] if e), None)
    if not info:
        raise ValueError(f"nothing found on YouTube for “{label}”")
    return info


@dataclass
class Stream:
    url: str          # the audio stream itself, playable for a few hours
    title: str
    page: str         # the video's page


def stream(source: Union[str, Track]) -> Stream:
    """The audio stream of a YouTube link or a suggested track, to listen to it without downloading it."""
    import yt_dlp

    target, label = _target(source)
    with yt_dlp.YoutubeDL({**_options(), "format": "bestaudio[ext=m4a]/bestaudio/best",
                           "noplaylist": True}) as ydl:
        info = _first_video(ydl.extract_info(target, download=False), label)
    url = info.get("url") or next((f.get("url") for f in info.get("requested_formats") or [] if f.get("url")), "")
    if not url:
        raise ValueError(f"no audio stream found for “{label}”")
    return Stream(url, clean_title(info.get("title") or label), info.get("webpage_url") or target)


def download(source: Union[str, Track], progress: Optional[Callable[[float, str], None]] = None,
             cancel: Optional[threading.Event] = None) -> Downloaded:
    """Download a YouTube link, or a suggested track (from the NCS channel, else the best YouTube search
    result), and extract its sound into the library as an .mp3. A track already in the library is reused."""
    import yt_dlp

    target, label = _target(source)

    def hook(d: dict):
        if cancel is not None and cancel.is_set():
            raise yt_dlp.utils.DownloadCancelled("cancelled")
        if progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            progress(min(0.95, d.get("downloaded_bytes", 0) / total) if total else 0.0, "Downloading…")
        elif progress and d.get("status") == "finished":
            progress(0.96, "Extracting the sound…")

    ffmpeg = ffmpeg_bin()
    if not ffmpeg:
        raise OSError("FFmpeg is needed to extract the sound")
    tmp = tempfile.mkdtemp(prefix="music-", dir=sfx_dir())
    opts = {
        **_options(),
        "format": "bestaudio/best",
        "noplaylist": True,
        "outtmpl": os.path.join(tmp, "%(id)s.%(ext)s"),
        "progress_hooks": [hook],
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
        "ffmpeg_location": os.path.dirname(os.path.abspath(ffmpeg)),
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = _first_video(ydl.extract_info(target, download=False), label)
            title = clean_title(info.get("title") or label)
            url = info.get("webpage_url") or target
            dst = os.path.join(sfx_dir(), _safe_name(title) + ".mp3")
            if os.path.exists(dst) and os.path.getsize(dst) > 0:
                return Downloaded(dst, title, url)
            ydl.process_ie_result(info, download=True)
        made = [f for f in os.listdir(tmp) if f.lower().endswith(".mp3")]
        if not made:
            raise OSError("the sound could not be extracted")
        os.replace(os.path.join(tmp, made[0]), dst)
        if progress:
            progress(1.0, "Done")
        return Downloaded(dst, title, url)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
