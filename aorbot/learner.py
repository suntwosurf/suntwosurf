"""Learning across attempts (iterative learning control).

Because the stage, car and weather never change, every attempt replays the
same problem. The learner keeps one speed scale per stretch ("bin") of the
stage and adjusts it after each attempt:

* the bins leading up to a crash / leaving the stage get slower, and the
  scale they failed at is remembered as that bin's ``limit``,
* bins with a near miss (large offset from the line) get a little slower and
  their limit is set just above the current scale,
* bins driven cleanly get a little faster, but never above
  ``limit_margin`` x their limit, so they settle just under the speed that
  went wrong instead of crashing again and again.

The scales multiply the physics-based corner speeds before the braking pass,
so slowing down a corner also moves its braking point earlier. The fastest
finished attempt is kept as ``best`` and is what ``aorbot drive`` uses.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from .config import LearnConfig, SpeedConfig
from .episode import EpisodeResult, Status
from .line import RacingLine
from .speed import speed_profile
from .types import Conditions


class SpeedProfileLearner:
    FORMAT = 1

    def __init__(
        self,
        line: RacingLine,
        speed_cfg: SpeedConfig,
        learn_cfg: LearnConfig,
        max_offset: float,
        conditions: Conditions,
    ):
        self.line = line
        self.speed_cfg = speed_cfg
        self.cfg = learn_cfg
        self.max_offset = max_offset
        self.conditions = conditions
        self.n_bins = max(1, math.ceil(line.length / learn_cfg.bin_length))
        self.scale = np.full(self.n_bins, learn_cfg.init_scale)
        self.limit = np.full(self.n_bins, np.inf)
        self.lateral = np.zeros(self.n_bins)
        self.iteration = 0
        self.best: dict | None = None
        self.history: list[dict] = []

    # ------------------------------------------------------------ targets
    def bins_of(self, s) -> np.ndarray:
        return np.clip(np.floor(np.asarray(s) / self.cfg.bin_length), 0, self.n_bins - 1).astype(int)

    def _per_point(self, per_bin: np.ndarray) -> np.ndarray:
        centers = np.minimum((np.arange(self.n_bins) + 0.5) * self.cfg.bin_length, self.line.length)
        return np.interp(self.line.s, centers, per_bin)

    def targets(self, best: bool = False) -> tuple[np.ndarray, np.ndarray | None]:
        """Target speed and lateral aim shift per line point, for the current
        parameters or (``best=True``) those of the fastest finished attempt."""
        if best and self.best is not None:
            scale, lateral = np.asarray(self.best["scale"]), np.asarray(self.best["lateral"])
        else:
            scale, lateral = self.scale, self.lateral
        v = speed_profile(self.line, self.speed_cfg, self._per_point(scale))
        lat = self._per_point(lateral) if np.any(lateral) else None
        return v, lat

    # ------------------------------------------------------------- update
    def update(self, result: EpisodeResult) -> dict:
        c, n = self.cfg, self.n_bins
        used_scale, used_lateral = self.scale.copy(), self.lateral.copy()
        record = {
            "iteration": self.iteration,
            "status": result.status.value,
            "time": round(result.time, 3),
            "progress": round(result.progress, 1),
            "end_s": round(result.end_s, 1),
            "mean_scale": round(float(used_scale.mean()), 4),
        }
        self.iteration += 1

        # A "stuck"/timeout right at the start usually means the reset or the
        # telemetry went wrong, not the driving: don't learn from it.
        if result.status in (Status.STUCK, Status.TIMEOUT) and result.progress < 2 * c.bin_length:
            record["ignored"] = True
            self.history.append(record)
            return record

        grow = np.zeros(n, bool)
        warn = np.zeros(n, bool)
        fail = np.zeros(n, bool)
        s = result.trace.get("s", np.zeros(0))
        visited = np.zeros(n, bool)
        mean_offset = np.zeros(n)
        if len(s):
            b = self.bins_of(s)
            offset = result.trace["offset"]
            peak = np.full(n, -np.inf)
            np.maximum.at(peak, b, np.abs(offset))
            count = np.bincount(b, minlength=n)
            visited = count > 0
            mean_offset = np.bincount(b, weights=offset, minlength=n) / np.maximum(count, 1)
            for i in np.flatnonzero(visited & (peak > c.warn_frac * self.max_offset)):
                warn[max(0, i - c.warn_back) : i + 1] = True
            grow = visited & (peak < c.calm_frac * self.max_offset)

        if not result.finished:
            end_bin = int(self.bins_of(result.end_s))
            fail[int(self.bins_of(result.end_s - c.fail_lookback)) : end_bin + 1] = True
            grow[end_bin:] = False

        self.limit[fail] = np.minimum(self.limit[fail], self.scale[fail])
        self.limit[warn] = np.minimum(self.limit[warn], self.scale[warn] * c.warn_limit)
        new = self.scale.copy()
        grow &= ~(warn | fail)
        new[grow] = np.minimum(new[grow] * c.grow, self.limit[grow] * c.limit_margin)
        new[warn] *= c.warn_decay
        new[fail] *= c.fail_decay
        self.scale = np.clip(new, c.min_scale, c.max_scale)

        if c.lateral_gain > 0 and visited.any():
            # Consistently ending up left of the line -> aim a bit further right.
            self.lateral[visited] -= c.lateral_gain * mean_offset[visited]
            self.lateral = np.clip(self.lateral, -c.max_lateral_shift, c.max_lateral_shift)

        if result.finished and (self.best is None or result.time < self.best["time"]):
            self.best = {
                "time": result.time,
                "iteration": record["iteration"],
                "scale": used_scale.tolist(),
                "lateral": used_lateral.tolist(),
            }
            record["new_best"] = True
        record.update(slower_bins=int((fail | warn).sum()), faster_bins=int(grow.sum()))
        self.history.append(record)
        return record

    # ------------------------------------------------------------ persist
    def to_dict(self) -> dict:
        return {
            "format": self.FORMAT,
            "conditions": {"stage": self.conditions.stage, "car": self.conditions.car, "weather": self.conditions.weather},
            "line_length": self.line.length,
            "bin_length": self.cfg.bin_length,
            "scale": self.scale.tolist(),
            "limit": [None if np.isinf(v) else v for v in self.limit.tolist()],
            "lateral": self.lateral.tolist(),
            "iteration": self.iteration,
            "best": self.best,
            "history": self.history,
        }

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=1))

    def load_state(self, data: dict) -> None:
        cond = Conditions(**data["conditions"])
        if cond != self.conditions:
            raise ValueError(
                f"learner state is for [{cond.label()}] but the config is [{self.conditions.label()}]; "
                "a learned profile only fits the exact stage + car + weather it was learned on"
            )
        if abs(data["line_length"] - self.line.length) > 1.0 or data["bin_length"] != self.cfg.bin_length:
            raise ValueError("learner state was made for a different reference line or bin_length")
        self.scale = np.asarray(data["scale"], float)
        self.limit = np.array([np.inf if v is None else v for v in data["limit"]], float)
        self.lateral = np.asarray(data["lateral"], float)
        self.iteration = int(data["iteration"])
        self.best = data["best"]
        self.history = list(data["history"])

    def load(self, path: str | Path) -> None:
        self.load_state(json.loads(Path(path).read_text()))
