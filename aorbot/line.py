"""Reference line of the stage: a resampled polyline with arc length,
heading and curvature, plus projection of the car onto it (progress + offset)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np


def wrap_angle(a):
    return (np.asarray(a) + np.pi) % (2.0 * np.pi) - np.pi


def moving_average(values: np.ndarray, window: int) -> np.ndarray:
    """Centered moving average along axis 0, padding with the edge values."""
    if window <= 1:
        return np.asarray(values, float).copy()
    values = np.asarray(values, float)
    half = window // 2
    pad = [(half, half)] + [(0, 0)] * (values.ndim - 1)
    padded = np.pad(values, pad, mode="edge")
    kernel = np.ones(2 * half + 1) / (2 * half + 1)
    if values.ndim == 1:
        return np.convolve(padded, kernel, mode="valid")
    return np.stack([np.convolve(padded[:, j], kernel, mode="valid") for j in range(values.shape[1])], axis=1)


def resample_polyline(xy: np.ndarray, spacing: float) -> np.ndarray:
    xy = np.asarray(xy, float)
    seg = np.hypot(*np.diff(xy, axis=0).T)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    n = max(2, int(round(s[-1] / spacing)) + 1)
    s_new = np.linspace(0.0, s[-1], n)
    return np.column_stack([np.interp(s_new, s, xy[:, 0]), np.interp(s_new, s, xy[:, 1])])


def drop_duplicates(xy: np.ndarray, min_step: float) -> np.ndarray:
    """Remove points closer than ``min_step`` to the previously kept point."""
    xy = np.asarray(xy, float)
    keep = [0]
    last = xy[0]
    for i in range(1, len(xy)):
        if np.hypot(*(xy[i] - last)) >= min_step:
            keep.append(i)
            last = xy[i]
    return xy[keep]


@dataclass
class Projection:
    s: float  # progress along the line, m
    offset: float  # signed distance, + = left of the line, m
    index: int  # segment index


class RacingLine:
    """Uniformly spaced polyline from the stage start to the finish."""

    def __init__(self, xy, spacing: float | None = 2.0, curvature_smooth: float = 10.0):
        xy = drop_duplicates(np.asarray(xy, float), 1e-6)
        if len(xy) < 3:
            raise ValueError("a racing line needs at least 3 distinct points")
        if spacing:
            xy = resample_polyline(xy, spacing)
        self.xy = xy
        self.meta: dict = {}
        seg = np.diff(xy, axis=0)
        self.seg_len = np.hypot(seg[:, 0], seg[:, 1])
        self.seg_dir = seg / self.seg_len[:, None]
        self.s = np.concatenate([[0.0], np.cumsum(self.seg_len)])
        self.length = float(self.s[-1])

        # Heading per point from central differences, unwrapped so it can be
        # interpolated; curvature is d(heading)/ds, smoothed over ~curvature_smooth m.
        d = np.gradient(xy, axis=0)
        self.heading = np.unwrap(np.arctan2(d[:, 1], d[:, 0]))
        kappa = np.gradient(self.heading, self.s)
        step = self.length / (len(xy) - 1)
        self.kappa = moving_average(kappa, max(1, int(round(curvature_smooth / step))))

    # ------------------------------------------------------------------ build
    @classmethod
    def from_trace(cls, xy, spacing: float = 2.0, smooth: float = 8.0) -> "RacingLine":
        """Build a clean line from recorded car positions (drops standstill
        samples, smooths jitter over ``smooth`` metres, resamples)."""
        xy = drop_duplicates(np.asarray(xy, float), 0.25)
        fine = resample_polyline(xy, 0.5)
        fine = moving_average(fine, max(1, int(round(smooth / 0.5))))
        return cls(fine, spacing=spacing)

    # ------------------------------------------------------------- queries
    def point_at(self, s) -> np.ndarray:
        s = np.clip(s, 0.0, self.length)
        return np.stack([np.interp(s, self.s, self.xy[:, 0]), np.interp(s, self.s, self.xy[:, 1])], axis=-1)

    def heading_at(self, s):
        return np.interp(np.clip(s, 0.0, self.length), self.s, self.heading)

    def curvature_at(self, s):
        return np.interp(np.clip(s, 0.0, self.length), self.s, self.kappa)

    def normal_at(self, s) -> np.ndarray:
        """Unit vector pointing to the left of the direction of travel."""
        h = self.heading_at(s)
        return np.stack([-np.sin(h), np.cos(h)], axis=-1)

    def project(self, x: float, y: float, s_hint: float | None = None, window: float = 60.0) -> Projection:
        """Closest point on the line. With ``s_hint`` only segments within
        ``window`` metres of it are searched, so a stage that doubles back
        close to itself does not make progress jump."""
        if s_hint is None:
            lo, hi = 0, len(self.seg_len)
        else:
            lo = int(np.searchsorted(self.s, s_hint - window, side="right")) - 1
            hi = int(np.searchsorted(self.s, s_hint + window, side="left")) + 1
            lo, hi = max(lo, 0), min(hi, len(self.seg_len))
        a = self.xy[lo:hi]
        d = self.seg_dir[lo:hi]
        px, py = x - a[:, 0], y - a[:, 1]
        t = np.clip(px * d[:, 0] + py * d[:, 1], 0.0, self.seg_len[lo:hi])
        qx, qy = px - t * d[:, 0], py - t * d[:, 1]
        dist2 = qx * qx + qy * qy
        k = int(np.argmin(dist2))
        i = lo + k
        side = d[k, 0] * py[k] - d[k, 1] * px[k]
        offset = float(np.copysign(np.sqrt(dist2[k]), side if side != 0 else 1.0))
        return Projection(s=float(self.s[i] + t[k]), offset=offset, index=i)

    # ----------------------------------------------------------- persist
    def save(self, path: str | Path) -> None:
        with open(path, "wb") as fh:  # file handle: np.savez would append ".npz"
            np.savez(fh, xy=self.xy, meta=json.dumps(self.meta))

    @classmethod
    def load(cls, path: str | Path) -> "RacingLine":
        with np.load(path, allow_pickle=False) as data:
            line = cls(data["xy"], spacing=None)
            if "meta" in data:
                line.meta = json.loads(str(data["meta"]))
        return line
