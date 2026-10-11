# KangEdit

A Python desktop video editor that automatically picks the best moments from your footage, then lets you fine-tune and export.

## Features

| Feature | How it works |
|---|---|
| **Auto Edit (best moments)** | Each clip is analyzed at 4 fps for sharpness, motion and shake, exposure, colorfulness, faces, audio loudness and scene cuts. Candidate windows are scored and picked greedily. |
| **Reuse rules** | One piece of footage can be used several times, but its segments never overlap, always keep a minimum source gap (setting: *Gap between reuses*), and near-identical shots are rejected. Clips that break the rule after manual edits get a red outline on the timeline. |
| **Short / Long format** | *Short-form*: 9:16, about 30 s, 1.2–3 s cuts, best moment first as the hook. *Long-form*: 16:9, about 3 min, 4–10 s shots in chronological order. *Custom*: set everything yourself. You can fine-tune any preset. |
| **Slide / trim** | Drag a clip to reorder it. Drag its edges to trim. **Alt/Shift+drag** (or *Slip* mode) slides which moment the clip shows. Split (S), duplicate, delete, undo/redo, and *Replace with another best moment* (R). |
| **Export ratios & resolutions** | 16:9, 9:16, 1:1, 4:5, 4:3, 2.39:1 at 720p, 1080p, 1440p or 4K. Fill (crop with per-clip pan/zoom), fit, or blurred-background fit. H.264, H.265 or ProRes 422 HQ. |
| **Proxies** | Optional 360–1080p short-GOP H.264 copies for smooth playback and faster analysis. Export always uses the originals unless you pick *Draft export*. |
| **Auto captions** | Offline speech recognition with faster-whisper, using word-level timestamps. Captions follow your trims and reorders automatically, and manual text fixes are kept. You can burn them in, save them as .srt, or both. |
| **Log → Rec.709** | Sony S-Log3/S-Log2, Panasonic V-Log, Canon C-Log2/3, ARRI LogC3/4, Fujifilm F-Log/F-Log2, Nikon N-Log, DJI D-Log, Apple Log, Blackmagic Film Gen5, plus HLG (phones). Uses the published curves and gamut matrices, with filmic or standard tone mapping. |
| **Exposure & color** | Exposure, contrast, highlights, shadows, temperature, tint, saturation and vibrance. **Auto** sets exposure, white balance and contrast for each clip. The preview and the export use the same color math (baked into a 3D LUT), so what you see is what you get. |

## Requirements

- Windows 10/11 (other platforms should work but are untested)
- Python 3.10+
- FFmpeg on PATH (`winget install Gyan.FFmpeg`)

## Run

Double-click `run.bat`. The first run creates `.venv` and installs the dependencies.

Or run it manually:

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
```

## Workflow

1. **Import** footage (button, or drag files/folders onto the Media panel). Analysis runs in the background.
2. If the footage is log: right-click it → *Log profile → Rec.709* and choose your camera profile.
3. Optional: **Generate proxies** for smoother editing, especially with 4K or 10-bit HEVC.
4. **✨ Auto Edit** → choose *Short-form* or *Long-form* → *Create edit*.
5. Adjust on the timeline: drag to reorder, drag edges to trim, Alt+drag to slip.
6. **Auto color all clips**, then tweak the clip you have selected in the Inspector.
7. **Generate captions** (the first run downloads the Whisper model; pick the model and language under *Captions → Style…*).
8. **⬆ Export**: choose the ratio, resolution, codec and caption mode.

Press **F1** in the app for keyboard shortcuts.

## Project layout

```
main.py               entry point
kangedit/
  analysis.py         per-footage signal extraction (OpenCV + ffmpeg)
  autoedit.py         scoring, best-moment selection, Short/Long presets
  color.py            log curves, gamut matrices, grading, LUTs, auto-correct
  captions.py         Whisper transcription, caption building, SRT/ASS
  export.py           ffmpeg filtergraph rendering (+ preview audio mix)
  proxy.py            proxy generation
  reframe.py          aspect-ratio crop/fit geometry (shared by preview and export)
  decoder.py          PyAV frame-accurate preview decoding
  models.py           project model, undo, save/load (.kgproj)
  tasks.py            background task queue
  ui/                 PySide6 interface (viewer, timeline, media bin, inspector, dialogs)
```

Caches (proxies, analysis, temp files) live in `%LOCALAPPDATA%\KangEdit\cache`, keyed by file, so they are reused across projects.
