# Changelog

All notable changes to KangEdit are listed here. Versions follow [semantic versioning](https://semver.org).
The Release workflow uses a version's section as the opening of its GitHub release notes.

## [Unreleased]

## [0.1.0] - 2026-10-12

First public release, with packaged builds for Windows x64, macOS (Apple Silicon) and Linux x64.

### Automatic editing
- Best-moment analysis (sharpness, motion and shake, exposure, colorfulness, faces, loudness, scene cuts) on several CPU lanes plus the GPU video decoder.
- Short-form (9:16), long-form (16:9) and custom presets, story order from recording time, and opening / closing detection in English and Indonesian.
- Transitions, J/L cuts, reuse rules, hooks, and *Regenerate shorter / longer*.

### Editing
- Pipelined preview that keeps up with 4K H.265, timeline with trim, slip, multi-select, undo / redo, autosave with rolling backups and recovery.
- Animated titles and text overlays with templates.
- Platform overlays (Instagram Reels, TikTok) and full-screen viewer.

### Color
- Log → Rec.709 for Sony, Panasonic, Canon, ARRI, Fujifilm, Nikon, DJI, Apple and Blackmagic, with camera auto-detection.
- Official camera LUTs, `.cube` LUT import, exposure and color controls, auto color; color on / off in the viewer.

### Captions
- Offline captions with faster-whisper, word animations, automatic placement, burn-in and `.srt` output.

### Sound
- Sound-effect layer with MyInstants search, volume, fades and crossfades.
- Background music: NCS suggestions by mood, YouTube audio download, preview and fit to timeline.

### Export
- 16:9, 9:16, 1:1, 4:5, 4:3, 2.39:1 up to 4K in H.264, H.265 or ProRes 422 HQ, with GPU encoding and CPU fallback.
- Optional proxies for lighter editing.

### App
- App and tray icons, crash and error logging, *Help → About* with version and build.

[Unreleased]: https://github.com/kloworizer/kangedit/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/kloworizer/kangedit/releases/tag/v0.1.0
