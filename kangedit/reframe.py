"""Reframing geometry shared by the viewer and the exporter."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Placement:
    scaled_w: int      # size of the scaled source
    scaled_h: int
    crop_x: int        # crop window inside the scaled source
    crop_y: int
    crop_w: int
    crop_h: int
    pad_x: int         # where the crop lands on the output canvas
    pad_y: int


def _even(v: float) -> int:
    return max(2, int(round(v / 2.0)) * 2)


def place(sw: int, sh: int, tw: int, th: int, mode: str, zoom: float = 1.0,
          ox: float = 0.0, oy: float = 0.0) -> Placement:
    """Fit a sw x sh source into a tw x th frame. mode: fill (crop) | fit/blur (letterbox)."""
    if mode == "fill":
        s = max(tw / sw, th / sh)
    else:
        s = min(tw / sw, th / sh)
    s *= max(1.0, zoom)
    w, h = _even(sw * s), _even(sh * s)
    cw, ch = min(w, tw), min(h, th)
    cx = int(round((w - cw) / 2.0 * (1.0 + max(-1.0, min(1.0, ox)))))
    cy = int(round((h - ch) / 2.0 * (1.0 + max(-1.0, min(1.0, oy)))))
    cx = max(0, min(w - cw, cx))
    cy = max(0, min(h - ch, cy))
    return Placement(w, h, cx, cy, cw, ch, (tw - cw) // 2, (th - ch) // 2)


def render(img: np.ndarray, tw: int, th: int, mode: str, zoom: float = 1.0,
           ox: float = 0.0, oy: float = 0.0) -> np.ndarray:
    """Reframe an RGB image onto a tw x th canvas (viewer path)."""
    sh, sw = img.shape[:2]
    p = place(sw, sh, tw, th, mode, zoom, ox, oy)
    scaled = cv2.resize(img, (p.scaled_w, p.scaled_h), interpolation=cv2.INTER_AREA
                        if p.scaled_w < sw else cv2.INTER_LINEAR)
    crop = scaled[p.crop_y:p.crop_y + p.crop_h, p.crop_x:p.crop_x + p.crop_w]
    if p.crop_w == tw and p.crop_h == th:
        return np.ascontiguousarray(crop)
    if mode == "blur":
        bp = place(sw, sh, tw, th, "fill")
        bg = cv2.resize(img, (max(2, bp.scaled_w // 8), max(2, bp.scaled_h // 8)), interpolation=cv2.INTER_AREA)
        bg = cv2.GaussianBlur(bg, (0, 0), 3)
        bg = cv2.resize(bg, (bp.scaled_w, bp.scaled_h), interpolation=cv2.INTER_LINEAR)
        canvas = np.ascontiguousarray(bg[bp.crop_y:bp.crop_y + th, bp.crop_x:bp.crop_x + tw])
        canvas = (canvas * 0.7).astype(np.uint8)
    else:
        canvas = np.zeros((th, tw, 3), dtype=np.uint8)
    canvas[p.pad_y:p.pad_y + p.crop_h, p.pad_x:p.pad_x + p.crop_w] = crop
    return canvas


def ffmpeg_filter(sw: int, sh: int, tw: int, th: int, mode: str, zoom: float, ox: float, oy: float,
                  inp: str, out: str, tag: str) -> str:
    """Filtergraph snippet reframing label `inp` into label `out` (export path)."""
    p = place(sw, sh, tw, th, mode, zoom, ox, oy)
    fg = (f"scale={p.scaled_w}:{p.scaled_h}:flags=lanczos,"
          f"crop={p.crop_w}:{p.crop_h}:{p.crop_x}:{p.crop_y}")
    if p.crop_w == tw and p.crop_h == th:
        return f"[{inp}]{fg},setsar=1[{out}]"
    if mode == "blur":
        bp = place(sw, sh, tw, th, "fill")
        return (
            f"[{inp}]split=2[{tag}bgi][{tag}fgi];"
            f"[{tag}bgi]scale={_even(bp.scaled_w / 8)}:{_even(bp.scaled_h / 8)}:flags=area,"
            f"gblur=sigma=3,scale={bp.scaled_w}:{bp.scaled_h}:flags=bilinear,"
            f"crop={tw}:{th}:{bp.crop_x}:{bp.crop_y},colorlevels=romax=0.7:gomax=0.7:bomax=0.7[{tag}bg];"
            f"[{tag}fgi]{fg}[{tag}fg];"
            f"[{tag}bg][{tag}fg]overlay={p.pad_x}:{p.pad_y},setsar=1[{out}]"
        )
    return f"[{inp}]{fg},pad={tw}:{th}:{p.pad_x}:{p.pad_y}:black,setsar=1[{out}]"
