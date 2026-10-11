# KangEdit

KangEdit is a desktop video editor for Windows, macOS and Linux. It analyzes your footage, picks the best moments and builds a short-form or long-form edit for you. You then fine-tune the edit, color, captions and sound effects, and export.

Everything runs on your own computer. Analysis, speech recognition and rendering work offline. The only network use is the one-time FFmpeg and Whisper model downloads and the optional sound-effect search.

---

## Contents

- [Features](#features)
- [Install and run](#install-and-run)
- [Quick start](#quick-start)
- [Keyboard and mouse](#keyboard-and-mouse)
- [Sound effects](#sound-effects)
- [Official camera LUTs](#official-camera-luts)
- [Where files are stored](#where-files-are-stored)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)
- [Third-party software](#third-party-software)

---

## Features

### Automatic editing

| | |
|---|---|
| **Best moments** | Each clip is analyzed at 4 fps for sharpness, motion and shake, exposure, colorfulness, faces, audio loudness and scene cuts. Candidate windows are scored and picked greedily. Analysis runs on several CPU lanes plus the GPU video decoder (NVIDIA, AMD or Intel). |
| **Short / Long format** | *Short-form*: 9:16, about 30 s, 1.2–3 s cuts, strongest moment first as the hook. *Long-form*: 16:9, 4–10 s shots in story order. Its default length follows the footage, at about a quarter of it, between 1 and 20 minutes. *Custom*: set everything yourself. |
| **Story order** | Clips are ordered by recording time from the file metadata, then by file name (C0002 before C0013). |
| **Opening & closing** | Looks for the greeting that opens the video and the sign-off that closes it, in English and Indonesian: "hello…", "welcome…", "assalamualaikum…", "thanks for watching", "sampai jumpa"… It searches intro/outro-named files and the first and last recordings, and uses the whole spoken passage. |
| **Transitions & J/L cuts** | Optional transitions between scenes or at every cut: dissolve, dip to black or white, wipe, slide, zoom, or a mix. For long-form, J-cuts let the next scene's sound lead in, and L-cuts let a voice finish over the next picture. |
| **Reuse rules** | Footage can be used several times, but its segments never overlap, always keep a minimum gap, and near-identical shots are rejected. Clips that break the rule after manual edits get a red outline. |
| **Regenerate shorter / longer** | **− Shorter** / **+ Longer** on the toolbar rebuild the edit about 25% shorter or longer with the same settings. *Tools → Regenerate auto edit at length…* asks for an exact length. |

### Editing

| | |
|---|---|
| **Smooth preview** | Decoding, grading and reframing run in the background. Playback reads ahead and prepares the next clips while one plays, so cuts in long-GOP 4K H.265 play without stutter. Scrubbing never freezes the window. |
| **Timeline** | Drag to reorder, drag edges to trim, **Alt/Shift+drag** to slip. **Ctrl+click** / **Shift+click** / **Ctrl+A** select several clips. Delete, duplicate, auto color, transitions and *Replace with another best moment* work on the whole selection. |
| **Transitions & split edits** | Per clip, in the Inspector or the right-click menu. Transitions are centered on the cut and use the footage around each clip, so the timeline length and captions don't move. |
| **Sound-effect layer** | A layer of sound effects under the video track, placed freely in time. Drag to move, drag edges to trim, set the volume by right-clicking. Search and add sounds from MyInstants in the **Sounds** tab, or drop any audio file on the timeline. |

### Color

| | |
|---|---|
| **Log → Rec.709** | Sony S-Log3/S-Log2, Panasonic V-Log, Canon C-Log2/3, ARRI LogC3/4, Fujifilm F-Log/F-Log2, Nikon N-Log, DJI D-Log, Apple Log, Blackmagic Film Gen5 and HLG, using the published curves and gamut matrices. |
| **Camera auto-detect** | The camera is identified from the file's metadata, and its log profile is chosen from the metadata or the picture. **Detect all** re-runs it for every file in the background. |
| **Official camera LUTs** | Use the makers' own conversion LUTs (DJI D-Log M, Sony, Panasonic, Fujifilm, Nikon, ARRI…) instead of the built-in curves. See [Official camera LUTs](#official-camera-luts). |
| **LUTs (.cube)** | Import 1D/3D `.cube` LUTs (or the `.zip` they come in) as a log conversion or as a creative look with a strength slider. |
| **Exposure & color** | Exposure, contrast, highlights, shadows, temperature, tint, saturation, vibrance. **Auto** sets exposure, white balance and contrast. *Auto color all clips* runs in the background with progress and Cancel. Preview and export share the same color math. |

### Captions

| | |
|---|---|
| **Automatic captions** | Offline speech recognition with faster-whisper. The default `large-v3` model is the most accurate; `large-v3-turbo` is almost as accurate and much faster. Captions follow every trim and reorder, and manual fixes are kept. |
| **Word animations** | Karaoke highlight, highlight box, pop, reveal, or one word at a time, timed to the speech. They look the same in the viewer and in the burned-in export. |
| **Automatic placement** | Size and position follow the frame ratio and format. 9:16: lower middle, clear of the app buttons. 1:1 / 4:5: lower third. 16:9: subtitles at the bottom. |
| **Output** | Burn in, save as `.srt`, or both. |

### Export

| | |
|---|---|
| **Ratios & resolutions** | 16:9, 9:16, 1:1, 4:5, 4:3, 2.39:1 at 720p, 1080p, 1440p or 4K. Fill (crop with per-clip pan/zoom), fit, or blurred-background fit. |
| **Codecs** | H.264, H.265 or ProRes 422 HQ. |
| **GPU encoding** | H.264/H.265 use the graphics card's encoder when it has one (NVIDIA NVENC, AMD AMF, Intel Quick Sync, Apple VideoToolbox), and fall back to the CPU automatically. H.265 exports were about 3.7× faster on an AMD RX 480. |
| **Proxies** | Optional 360–1080p short-GOP H.264 copies for lighter editing. Exports use the originals unless you choose *Draft export*. |

---

## Install and run

You need **Python 3.10 or newer**. The launcher sets up everything else on the first start:

1. a private Python environment in `.venv`;
2. the Python packages from `requirements.txt` (PySide6, NumPy, OpenCV, PyAV, faster-whisper);
3. **FFmpeg**, downloaded into the `ffmpeg/` folder if it isn't already on your `PATH`.

The first start takes a few minutes and needs about 1.5 GB of disk space. Later starts are instant. When `requirements.txt` changes, the packages are updated automatically.

### Windows 10 / 11

1. Install Python from [python.org](https://www.python.org/downloads/). Tick **"Add python.exe to PATH"** in the installer.
2. Double-click **`run.bat`**.

### macOS 12 or newer (Apple Silicon or Intel)

1. Install Python: `brew install python@3.12`, or the installer from [python.org](https://www.python.org/downloads/).
2. In Terminal, in the KangEdit folder:
   ```sh
   chmod +x run.sh run.command   # once
   ./run.sh
   ```
   After that you can double-click **`run.command`** in Finder.

### Linux (x86-64 or ARM64)

1. Install Python with venv support and the libraries Qt needs:
   ```sh
   # Debian / Ubuntu
   sudo apt install python3 python3-venv libxcb-cursor0 libxkbcommon-x11-0 libegl1 libgl1 libpulse0
   # Fedora
   sudo dnf install python3 xcb-util-cursor libxkbcommon-x11 mesa-libEGL pulseaudio-libs
   # Arch
   sudo pacman -S python xcb-util-cursor libxkbcommon-x11 libglvnd libpulse
   ```
2. Start it:
   ```sh
   chmod +x run.sh   # once
   ./run.sh
   ```

### Manual setup

```sh
python install.py             # create/update .venv, install packages, download FFmpeg if needed
python install.py --no-ffmpeg # same, but use the FFmpeg on your PATH
python install.py --run       # set up if needed, then start KangEdit
```

To start KangEdit directly afterwards, run `.venv/bin/python main.py` (macOS/Linux) or `.venv\Scripts\python main.py` (Windows). You can pass a project file or video files: `main.py project.kgproj` or `main.py clip1.mp4 clip2.mov`.

**FFmpeg.** KangEdit uses the FFmpeg in its own `ffmpeg/` folder first, then the one on your `PATH`. If neither exists when the app starts, it offers to download it. To install FFmpeg yourself instead: `winget install Gyan.FFmpeg` (Windows), `brew install ffmpeg` (macOS), or your package manager (Linux).

**GPU speech recognition** is used automatically on NVIDIA cards with CUDA. Everything else runs on the CPU.

---

## Quick start

1. **Import** footage: use the button, or drag files and folders onto the Media panel. Analysis runs in the background.
2. Check the log profile. It's usually detected automatically (see the *Camera* and *Log* columns); **Detect all** in the Inspector re-runs it. For DJI D-Log M and other makers' own conversions, set up [Official camera LUTs](#official-camera-luts).
3. Optional: **Generate proxies** for lighter editing of 4K or 10-bit H.265.
4. **✨ Auto Edit**: choose *Short-form* or *Long-form*, check the transitions, J/L cuts and opening/closing options, then click *Create edit*. Use **− Shorter** / **+ Longer** to try other lengths.
5. Refine the timeline: reorder, trim, slip, transitions (right-click a clip), and sound effects (**Sounds** tab).
6. **Auto color all clips**, then fine-tune the selected clip in the Inspector.
7. **Generate captions**. The first run downloads the Whisper model. Choose the model, language, animation and placement under *Captions → Style…*.
8. **⬆ Export**: choose the ratio, resolution, codec, GPU encoding and caption mode.

---

## Keyboard and mouse

| Key | Action |
|---|---|
| Space | Play / pause |
| ← / → | Previous / next frame (Shift: 10 frames) |
| ↑ / ↓ | Previous / next clip |
| Home / End | Start / end |
| S | Split at the playhead |
| Del | Delete the selected clips (or the selected sound) |
| Ctrl+D | Duplicate clip |
| Ctrl+A | Select all clips |
| R | Replace the clip with another best moment |
| Ctrl+Shift+− / Ctrl+Shift+= | Regenerate the auto edit shorter / longer |
| Ctrl+Z / Ctrl+Shift+Z | Undo / redo |
| Shift+Z | Fit the timeline |
| Ctrl+Shift+A | Auto Edit… |
| F1 | Shortcut list |

| Mouse on the timeline | Action |
|---|---|
| Drag a clip | Reorder |
| Drag a clip's edge | Trim |
| Alt/Shift+drag (or *Slip* mode) | Slide the moment inside the clip |
| Ctrl+click / Shift+click | Add to the selection / select a range |
| Right-click a clip | Transition, J/L cut, color, duplicate, delete… |
| Drag / drag edges of a sound | Move / trim the sound |
| Right-click a sound | Volume, move to the playhead, delete |
| Ctrl+wheel | Zoom |

On macOS, use ⌘ where this table says Ctrl.

---

## Sound effects

The **Sounds** tab searches [MyInstants](https://www.myinstants.com), a large collection of short meme and effect sounds.

- **▶ Preview** plays a sound.
- **＋ Add at playhead** downloads it into your sound library and puts it on the sound-effect layer.
- The **Library** list holds every sound you added or imported. Drag one onto the timeline, or add it at the playhead.
- **Import…** adds your own audio files (MP3, WAV, OGG, M4A, FLAC…). You can also drop audio files straight onto the timeline.

Sound effects are mixed into the preview and the export at their own volume (right-click → *Volume*).

> MyInstants sounds are uploaded by its users and may be copyrighted. Make sure you're allowed to use a sound before you publish a video with it.

---

## Official camera LUTs

Camera makers publish their own log → Rec.709 conversion LUTs. **Tools → Official camera LUTs…** lists them with a **Download page…** button for each maker:

| Maker | Log | Where |
|---|---|---|
| DJI | D-Log M, D-Log | [dji.com/lut](https://www.dji.com/lut) |
| Sony | S-Log3 / S-Gamut3(.Cine), S-Log2 | [Sony LUT library](https://pro.sony/bp_BR/technology/professional-video-lut-look-up-table) |
| Panasonic | V-Log, V-Log L | [LUMIX downloads](https://av.jpn.support.panasonic.com/support/global/cs/dsc/download/index3.html) |
| Fujifilm | F-Log, F-Log2 | [fujifilm-x.com LUTs](https://www.fujifilm-x.com/en-gb/support/download/lut/) |
| Nikon | N-Log | [Nikon Download Center](https://downloadcenter.nikonimglib.com/en/download/sw/258.html) |
| ARRI | LogC3, LogC4 | [arri.com/log-c](https://www.arri.com/log-c) |
| Canon | C-Log2, C-Log3 | Your camera's page on Canon's support site → Downloads → "Canon lookup table" |
| Blackmagic | Film Gen5 | Installed with DaVinci Resolve (**Import from DaVinci Resolve**) |

1. Download the LUT from the maker's page (KangEdit can't download these itself, because the pages require accepting the maker's terms).
2. Click **Import LUT files (.cube / .zip)…** and choose the file as downloaded. Recognized official files are marked ★ and selected automatically.
3. Keep **Use these LUTs automatically when a camera's log profile is detected** ticked, and optionally switch existing footage now.

DJI cameras can record D-Log or D-Log M. Most current models (Mini 4/5 Pro, Air 3/3S, Avata 2, Osmo Pocket 3, Osmo Action 4–6, Mavic 3) use D-Log M, so pick the D-Log M LUT in the DJI row for those.

---

## Where files are stored

| What | Where |
|---|---|
| Cache: proxies, analysis, temporary files | `cache/` in the KangEdit folder. If that folder isn't writable, it goes in the user data folder below. |
| Downloaded FFmpeg | `ffmpeg/` in the KangEdit folder |
| LUT library and sound library | User data folder: `%LOCALAPPDATA%\KangEdit` (Windows), `~/Library/Application Support/KangEdit` (macOS), `~/.local/share/KangEdit` (Linux) |
| Whisper models | The Hugging Face cache (`~/.cache/huggingface`) |
| Settings | The system settings store (registry on Windows, `~/Library/Preferences` on macOS, `~/.config` on Linux) |

Caches are keyed by file, so they're reused across projects. **Tools → Cache folder & clearing…** shows how much space each kind uses, clears them, and changes the cache folder. When you change the folder, KangEdit offers to **copy** the existing proxies and analysis into the new one; the old folder is left untouched. Clearing the cache never affects the LUT or sound libraries.

Earlier versions kept the cache in the user data folder. On the first start after updating, KangEdit offers once to copy it into the new location.

---

## Troubleshooting

| Problem | What to do |
|---|---|
| "Python 3.10 or newer is required" | Install a current Python. On Windows, tick *Add python.exe to PATH*. |
| Linux: Qt "could not load the xcb platform plugin" | Install the system libraries listed under [Linux](#linux-x86-64-or-arm64). |
| Linux: `ensurepip is not available` | `sudo apt install python3-venv` (Debian/Ubuntu). |
| macOS: "run.sh: Permission denied" | Run `chmod +x run.sh run.command` once, or start it with `sh run.sh`. |
| FFmpeg download failed (offline, proxy, firewall) | Install FFmpeg yourself and put it on `PATH`. KangEdit uses it automatically. |
| Playback starts after a short pause | The first frame of long-GOP footage has to be decoded from the previous keyframe. Proxies make the pause much shorter. |
| Choppy playback on a slow computer | Turn on proxies (toolbar → *Use proxies*), then **Generate proxies**. |
| Export uses the CPU | Your GPU has no H.264/H.265 encoder, its driver is too old, or ProRes is selected. Untick *Encode on the graphics card* to force the CPU. |
| Captions are slow | Choose `large-v3-turbo` (or `small`) under *Captions → Style…*. |
| Sound search fails | MyInstants is unreachable from your network. Import audio files instead. |

---

## Project layout

```
main.py                 entry point:  python main.py [project.kgproj | video files…]
install.py              cross-platform setup (venv, packages, FFmpeg)
run.bat / run.sh / run.command   launchers for Windows / macOS + Linux / macOS Finder
requirements.txt
kangedit/
  app.py                startup (offers the FFmpeg download when it's missing)
  config.py             paths (app folder, cache, user data), constants, open_path()
  ffmpeg.py             FFmpeg discovery, probing, progress runner, GPU encoder/decoder detection
  ffmpeg_setup.py       FFmpeg download and unpacking (standard library only)
  analysis.py           per-footage signal extraction (OpenCV + FFmpeg, parallel CPU/GPU decode)
  autoedit.py           scoring, best-moment selection, presets, transition / J-L placement
  bookends.py           story order and opening / closing detection
  edits.py              transitions and split edits shared by preview, export and preview audio
  captions.py           Whisper transcription, caption building, layouts, animations, SRT/ASS
  color.py              log curves, gamut matrices, grading, preview LUTs, auto color
  camera.py             camera identification and log-profile choice
  lut.py                .cube LUTs, the LUT library and the official camera LUT catalog
  sfx.py                sound library, MyInstants search and download
  export.py             FFmpeg filtergraph rendering (+ preview audio mix)
  preview.py            background preview engine: read-ahead, pre-roll, transitions
  decoder.py            PyAV frame-accurate decoding
  reframe.py            aspect-ratio crop/fit geometry (shared by preview and export)
  proxy.py              proxy generation
  models.py             project model, undo, save/load (.kgproj)
  tasks.py              background task queue
  ui/                   PySide6 interface: viewer, timeline, media bin, inspector, captions,
                        sounds, dialogs
```

---

## Third-party software

KangEdit is written in Python and uses [PySide6 / Qt](https://www.qt.io/qt-for-python), [NumPy](https://numpy.org), [OpenCV](https://opencv.org), [PyAV](https://pyav.org) and [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (OpenAI Whisper models via CTranslate2).

FFmpeg isn't included in this repository. When needed, setup downloads static GPL builds from [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds) (Windows, Linux) or [Martin Riedl's build server](https://ffmpeg.martin-riedl.de/) (macOS). Those builds are covered by FFmpeg's own license.
