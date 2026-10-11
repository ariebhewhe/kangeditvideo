"""Color science: camera log -> Rec.709 conversion, basic grading and auto-correction.

Every color operation is a pure per-pixel function of RGB, so the whole grade is baked
into a single 3D LUT. The viewer applies a 96^3 LUT to 8-bit frames and the exporter
writes a 33^3 .cube file for ffmpeg's lut3d filter -- both come from `apply_grade`,
which keeps preview and export identical.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Callable, Optional

import numpy as np

# ----------------------------------------------------------------------------- gamuts

_D65 = (0.3127, 0.3290)

_PRIMARIES = {
    "rec709": ((0.640, 0.330), (0.300, 0.600), (0.150, 0.060)),
    "rec2020": ((0.708, 0.292), (0.170, 0.797), (0.131, 0.046)),
    "sgamut3cine": ((0.766, 0.275), (0.225, 0.800), (0.089, -0.087)),
    "sgamut3": ((0.730, 0.280), (0.140, 0.855), (0.100, -0.050)),
    "vgamut": ((0.730, 0.280), (0.165, 0.840), (0.100, -0.030)),
    "cinemagamut": ((0.740, 0.270), (0.170, 1.140), (0.080, -0.100)),
    "awg3": ((0.6840, 0.3130), (0.2210, 0.8480), (0.0861, -0.1020)),
    "awg4": ((0.7347, 0.2653), (0.1424, 0.8576), (0.0991, -0.0308)),
    "dgamut": ((0.710, 0.310), (0.210, 0.880), (0.090, -0.080)),
    "bmdwg5": ((0.7177, 0.3171), (0.2280, 0.8616), (0.1006, -0.0820)),
}


def _rgb_to_xyz(prim, white=_D65) -> np.ndarray:
    def xyz(xy):
        x, y = xy
        return np.array([x / y, 1.0, (1 - x - y) / y])

    m = np.stack([xyz(p) for p in prim], axis=1)
    s = np.linalg.solve(m, xyz(white))
    return m * s


def gamut_to_709(name: str) -> np.ndarray:
    if name == "rec709":
        return np.eye(3)
    src = _rgb_to_xyz(_PRIMARIES[name])
    dst = _rgb_to_xyz(_PRIMARIES["rec709"])
    return np.linalg.solve(dst, src)


# ----------------------------------------------------------------------------- transfer curves
# Each decoder maps a normalized signal to scene-linear (0.18 = mid grey).
# `domain` tells which signal the published formula expects:
#   "cv"  -> 10-bit code value / 1023 (legal-range encoded file)
#   "ire" -> legal range expanded to 0..1 (what ffmpeg/PyAV RGB conversion produces)


def _slog3(x):
    return np.where(
        x >= 171.2102946929 / 1023.0,
        (10.0 ** ((x * 1023.0 - 420.0) / 261.5)) * (0.18 + 0.01) - 0.01,
        (x * 1023.0 - 95.0) * 0.01125000 / (171.2102946929 - 95.0),
    )


def _slog2(x):  # x in IRE domain
    lin = np.where(
        x >= 0.030001222851889303,
        10.0 ** ((x - 0.616596 - 0.03) / 0.432699) - 0.037584,
        (x - 0.030001222851889303) / 5.0,
    )
    return lin * 0.9 * 219.0 / 155.0


def _vlog(x):
    return np.where(x < 0.181, (x - 0.125) / 5.6, 10.0 ** ((x - 0.598206) / 0.241514) - 0.00873)


def _clog3(x):  # IRE domain
    lin = np.where(
        x < 0.097465473,
        -(10.0 ** ((0.12783901 - x) / 0.36726845) - 1.0) / 14.98325,
        np.where(
            x <= 0.15277891,
            (x - 0.12512219) / 1.9754798,
            (10.0 ** ((x - 0.12240537) / 0.36726845) - 1.0) / 14.98325,
        ),
    )
    return lin * 0.9


def _clog2(x):  # IRE domain
    lin = np.where(
        x < 0.092864125,
        -(10.0 ** ((0.092864125 - x) / 0.24136077) - 1.0) / 87.099375,
        (10.0 ** ((x - 0.092864125) / 0.24136077) - 1.0) / 87.099375,
    )
    return lin * 0.9


def _logc3(x):
    cut, a, b, c, d, e, f = 0.010591, 5.555556, 0.052272, 0.247190, 0.385537, 5.367655, 0.092809
    return np.where(x > e * cut + f, (10.0 ** ((x - d) / c) - b) / a, (x - f) / e)


def _logc4(x):
    a = (2.0 ** 18 - 16.0) / 117.45
    b = (1023.0 - 95.0) / 1023.0
    c = 95.0 / 1023.0
    s = (7.0 * np.log(2.0) * 2.0 ** (7.0 - 14.0 * c / b)) / (a * b)
    t = (2.0 ** (14.0 * (-c / b) + 6.0) - 64.0) / a
    return np.where(x >= 0.0, (2.0 ** (14.0 * (x - c) / b + 6.0) - 64.0) / a, x * s + t)


def _flog(x):
    a, b, c, d, e, f = 0.555556, 0.009468, 0.344676, 0.790453, 8.735631, 0.092864
    return np.where(x >= 0.100537775223865, (10.0 ** ((x - d) / c)) / a - b / a, (x - f) / e)


def _flog2(x):
    a, b, c, d, e, f = 5.555556, 0.064829, 0.245281, 0.384316, 8.799461, 0.092864
    return np.where(x >= 0.100686685370811, (10.0 ** ((x - d) / c)) / a - b / a, (x - f) / e)


def _nlog(x):
    cv = x * 1023.0
    return np.where(cv < 452.0, (np.maximum(cv, 0.0) / 650.0) ** 3 - 0.0075, np.exp((cv - 619.0) / 150.0))


def _dlog(x):
    return np.where(x <= 0.14, (x - 0.0929) / 6.025, (10.0 ** (3.89616 * x - 2.27752) - 0.0108) / 0.9892)


def _applelog(x):  # IRE domain
    r0, rt, c, b, g, d = -0.05641088, 0.01, 47.28711236, 0.00964052, 0.08550479, 0.69336945
    pt = c * (rt - r0) ** 2
    return np.where(x >= pt, 2.0 ** ((x - d) / g) - b, np.where(x >= 0, np.sqrt(np.maximum(x, 0) / c) + r0, r0))


def _bmdfilm5(x):  # IRE domain
    A, B, C, D, E = 0.08692876065491224, 0.005494072432257808, 0.5300133392291939, 8.283605932402494, 0.09246575342465753
    log_cut = D * 0.005 + E
    return np.where(x < log_cut, (x - E) / D, np.exp((x - C) / A) - B)


def _hlg(x):  # IRE domain; scaled so HLG mid grey lands near 0.18
    a, b, c = 0.17883277, 0.28466892, 0.55991073
    lin = np.where(x <= 0.5, (np.maximum(x, 0) ** 2) / 3.0, (np.exp((x - c) / a) + b) / 12.0)
    return lin * 3.75


@dataclass(frozen=True)
class LogProfile:
    label: str
    decode: Callable[[np.ndarray], np.ndarray]
    domain: str
    gamut: str


LOG_PROFILES: dict[str, LogProfile] = {
    "slog3_sg3c": LogProfile("Sony S-Log3 / S-Gamut3.Cine", _slog3, "cv", "sgamut3cine"),
    "slog3_sg3": LogProfile("Sony S-Log3 / S-Gamut3", _slog3, "cv", "sgamut3"),
    "slog2": LogProfile("Sony S-Log2 / S-Gamut", _slog2, "ire", "sgamut3"),
    "vlog": LogProfile("Panasonic V-Log / V-Gamut", _vlog, "cv", "vgamut"),
    "clog3": LogProfile("Canon C-Log3 / Cinema Gamut", _clog3, "ire", "cinemagamut"),
    "clog2": LogProfile("Canon C-Log2 / Cinema Gamut", _clog2, "ire", "cinemagamut"),
    "logc3": LogProfile("ARRI LogC3 (EI 800) / AWG3", _logc3, "cv", "awg3"),
    "logc4": LogProfile("ARRI LogC4 / AWG4", _logc4, "cv", "awg4"),
    "flog": LogProfile("Fujifilm F-Log / F-Gamut", _flog, "cv", "rec2020"),
    "flog2": LogProfile("Fujifilm F-Log2 / F-Gamut", _flog2, "cv", "rec2020"),
    "nlog": LogProfile("Nikon N-Log / Rec.2020", _nlog, "cv", "rec2020"),
    "dlog": LogProfile("DJI D-Log / D-Gamut", _dlog, "cv", "dgamut"),
    "applelog": LogProfile("Apple Log / Rec.2020", _applelog, "ire", "rec2020"),
    "bmdfilm5": LogProfile("Blackmagic Film Gen5 / BMD WG", _bmdfilm5, "ire", "bmdwg5"),
    "hlg": LogProfile("HLG HDR (phones) / Rec.2020", _hlg, "ire", "rec2020"),
}

LOG_CHOICES = [("none", "None (Rec.709 / standard)")] + [(k, p.label) for k, p in LOG_PROFILES.items()]

TONEMAPS = [("filmic", "Filmic (soft highlight roll-off)"), ("standard", "Standard Rec.709 (hard clip)")]


# ----------------------------------------------------------------------------- grade


@dataclass
class Grade:
    exposure: float = 0.0      # stops, -4..+4
    contrast: float = 0.0      # -100..100
    highlights: float = 0.0    # -100..100
    shadows: float = 0.0       # -100..100
    temperature: float = 0.0   # -100 (cool) .. 100 (warm)
    tint: float = 0.0          # -100 (green) .. 100 (magenta)
    saturation: float = 100.0  # 0..200 %
    vibrance: float = 0.0      # -100..100

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: Optional[dict]) -> "Grade":
        g = Grade()
        for k, v in (d or {}).items():
            if hasattr(g, k):
                setattr(g, k, float(v))
        return g

    def is_identity(self) -> bool:
        return self == Grade()

    def key(self) -> tuple:
        return tuple(round(v, 4) for v in asdict(self).values())


_LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def wb_gains(temperature: float, tint: float) -> np.ndarray:
    t = temperature / 100.0
    n = tint / 100.0
    g = 2.0 ** np.array([0.5 * t, -0.4 * n, -0.5 * t])
    return (g / float(np.dot(_LUMA, g))).astype(np.float32)


def _aces_fit(x):
    x = np.maximum(x, 0.0)
    return np.clip((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0.0, 1.0)


def _to_linear(rgb: np.ndarray, log_profile: str) -> np.ndarray:
    """Signal (IRE domain, 0..1) -> scene/display linear Rec.709."""
    if log_profile in LOG_PROFILES:
        p = LOG_PROFILES[log_profile]
        sig = (rgb * 876.0 + 64.0) / 1023.0 if p.domain == "cv" else rgb
        lin = p.decode(sig.astype(np.float64)).astype(np.float32)
        m = gamut_to_709(p.gamut).astype(np.float32)
        return lin @ m.T
    return np.power(np.clip(rgb, 0.0, 1.0), 2.4)


def _to_display(lin: np.ndarray, log_profile: str, tonemap: str) -> np.ndarray:
    if log_profile in LOG_PROFILES and tonemap == "filmic":
        disp_lin = _aces_fit(lin * 0.6)
    else:
        disp_lin = np.clip(lin, 0.0, 1.0)
    return np.power(disp_lin, 1.0 / 2.4)


def apply_grade(rgb: np.ndarray, grade: Grade, log_profile: str = "none", tonemap: str = "filmic") -> np.ndarray:
    """rgb: (..., 3) float in 0..1 (gamma-encoded signal). Returns graded display RGB 0..1."""
    rgb = np.asarray(rgb, dtype=np.float32)
    shape = rgb.shape
    x = rgb.reshape(-1, 3)

    lin = _to_linear(x, log_profile)
    lin = lin * np.float32(2.0 ** grade.exposure)
    if grade.temperature or grade.tint:
        lin = lin * wb_gains(grade.temperature, grade.tint)
    d = _to_display(lin, log_profile, tonemap).astype(np.float32)

    if grade.shadows:
        a = grade.shadows / 100.0 * 1.5
        d = d + a * d * (1.0 - d) ** 2
    if grade.highlights:
        a = grade.highlights / 100.0 * 1.5
        d = d + a * d * d * (1.0 - d)
    if grade.contrast:
        d = np.clip(d, 0.0, 1.0)
        k = 2.0 ** (grade.contrast / 100.0)
        p = 0.45
        d = np.where(d < p, p * (d / p) ** k, 1.0 - (1.0 - p) * ((1.0 - d) / (1.0 - p)) ** k)
    if grade.saturation != 100.0 or grade.vibrance:
        luma = (d @ _LUMA)[:, None]
        sat = grade.saturation / 100.0
        if grade.vibrance:
            cur = np.max(d, axis=1, keepdims=True) - np.min(d, axis=1, keepdims=True)
            sat = sat * (1.0 + grade.vibrance / 100.0 * (1.0 - np.clip(cur * 1.5, 0.0, 1.0)))
        d = luma + (d - luma) * sat

    return np.clip(d, 0.0, 1.0).reshape(shape)


def needs_color(grade: Grade, log_profile: str) -> bool:
    return log_profile in LOG_PROFILES or not grade.is_identity()


# ----------------------------------------------------------------------------- LUTs


class PreviewLut:
    """96^3 uint8 LUT for fast per-frame grading of 8-bit RGB images."""

    N = 96
    _idx = (np.round(np.arange(256) * (N - 1) / 255.0)).astype(np.int32)
    _ir = _idx * N * N
    _ig = _idx * N
    _grid: Optional[np.ndarray] = None

    @classmethod
    def grid(cls) -> np.ndarray:
        if cls._grid is None:
            v = np.linspace(0.0, 1.0, cls.N, dtype=np.float32)
            r, g, b = np.meshgrid(v, v, v, indexing="ij")
            cls._grid = np.stack([r, g, b], axis=-1).reshape(-1, 3)
        return cls._grid

    def __init__(self, grade: Grade, log_profile: str, tonemap: str):
        out = apply_grade(self.grid(), grade, log_profile, tonemap)
        self.table = (out * 255.0 + 0.5).astype(np.uint8)

    def apply(self, img: np.ndarray) -> np.ndarray:
        idx = self._ir[img[..., 0]] + self._ig[img[..., 1]] + self._idx[img[..., 2]]
        return self.table[idx]


class LutCache:
    def __init__(self, max_items: int = 24):
        self._items: dict[tuple, PreviewLut] = {}
        self._max = max_items

    def peek(self, grade: Grade, log_profile: str, tonemap: str) -> Optional[PreviewLut]:
        return self._items.get((grade.key(), log_profile, tonemap))

    def get(self, grade: Grade, log_profile: str, tonemap: str) -> Optional[PreviewLut]:
        if not needs_color(grade, log_profile):
            return None
        key = (grade.key(), log_profile, tonemap)
        lut = self._items.get(key)
        if lut is None:
            if len(self._items) >= self._max:
                self._items.pop(next(iter(self._items)))
            lut = PreviewLut(grade, log_profile, tonemap)
            self._items[key] = lut
        return lut


def write_cube(path: str, grade: Grade, log_profile: str, tonemap: str, size: int = 33) -> None:
    v = np.linspace(0.0, 1.0, size, dtype=np.float32)
    # .cube ordering: red changes fastest.
    b, g, r = np.meshgrid(v, v, v, indexing="ij")
    pts = np.stack([r, g, b], axis=-1).reshape(-1, 3)
    out = apply_grade(pts, grade, log_profile, tonemap)
    with open(path, "w", encoding="ascii") as fh:
        fh.write("TITLE \"KangEdit grade\"\n")
        fh.write(f"LUT_3D_SIZE {size}\n")
        fh.write("DOMAIN_MIN 0.0 0.0 0.0\nDOMAIN_MAX 1.0 1.0 1.0\n")
        fh.write("\n".join(f"{p[0]:.6f} {p[1]:.6f} {p[2]:.6f}" for p in out))
        fh.write("\n")


# ----------------------------------------------------------------------------- auto correction


def auto_correct(frames: list[np.ndarray], log_profile: str, tonemap: str = "filmic",
                 base: Optional[Grade] = None) -> Grade:
    """Estimate exposure, white balance and contrast from sample RGB uint8 frames."""
    g = Grade() if base is None else Grade.from_dict(base.to_dict())
    if not frames:
        return g
    px = np.concatenate([f.reshape(-1, 3)[:: max(1, f.size // 3 // 40000)] for f in frames]).astype(np.float32) / 255.0
    lin = _to_linear(px, log_profile)
    lin = np.maximum(lin, 1e-5)
    luma = lin @ _LUMA

    # White balance: grey-world over mid-tones (ignore clipped and very dark pixels).
    lo, hi = np.percentile(luma, [10, 97])
    mask = (luma > lo) & (luma < hi) & (np.max(px, axis=1) < 0.98)
    if mask.sum() > 100:
        m = lin[mask].mean(axis=0)
        mr, mg, mb = (float(c) for c in m)
        t = np.log2(mb / mr)                       # 0.5T - (-0.5T) = log2(mb/mr)
        n = -np.log2(np.sqrt(mr * mb) / mg) / 0.4
        strength = 0.8
        g.temperature = float(np.clip(t * 100.0 * strength, -60, 60))
        g.tint = float(np.clip(n * 100.0 * strength, -40, 40))

    # Exposure: bring the log-average luminance to mid grey.
    lin_wb = lin * wb_gains(g.temperature, g.tint)
    luma = lin_wb @ _LUMA
    key = float(np.exp(np.mean(np.log(np.maximum(luma, 1e-4)))))
    target = 0.16 if log_profile in LOG_PROFILES else 0.14
    ev = np.log2(target / max(key, 1e-4))
    # Protect highlights: limit the push if many pixels would clip.
    p99 = float(np.percentile(luma, 99.5))
    clip_level = 2.5 if (log_profile in LOG_PROFILES and tonemap == "filmic") else 1.0
    if p99 > 0 and ev > 0:
        ev = min(ev, np.log2(clip_level / p99) + 0.5)
    g.exposure = float(np.clip(ev * 0.85, -3.0, 3.0))

    # Contrast: add punch to flat images, soften harsh ones.
    tmp = Grade(exposure=g.exposure, temperature=g.temperature, tint=g.tint)
    disp = apply_grade(px[::4], tmp, log_profile, tonemap) @ _LUMA
    p5, p95 = np.percentile(disp, [5, 95])
    spread = float(p95 - p5)
    g.contrast = float(np.clip((0.62 - spread) * 120.0, -25.0, 35.0))
    g.vibrance = 15.0 if log_profile in LOG_PROFILES else 5.0
    return g
