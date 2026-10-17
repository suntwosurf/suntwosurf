"""The student's memory of its own decisions (spec.MEMORY).

Petra works in rhythms (an attack every few minutes, one phase at a time),
so what it decided lately says a lot about what it decides next. The
student keeps the same memory in either game: per action, in how many of
the last MEMORY_STEPS steps it took it, and how many steps ago it last took
it (up to MEMORY_CAP). Both are scaled to 0..1.

One class serves training (``of``: the teacher's decisions of a recording)
and play (``vector``, decide, then ``observe``), so the two can't drift
apart.
"""

from __future__ import annotations

from collections import deque

import numpy as np

from .spec import ACTIONS, MEMORY, MEMORY_CAP, MEMORY_STEPS


class Memory:
    def __init__(self):
        self.recent: deque[np.ndarray] = deque(maxlen=MEMORY_STEPS)
        self.since = np.full(len(ACTIONS), float(MEMORY_CAP))

    def vector(self) -> np.ndarray:
        """What the student remembers before deciding (MEMORY order)."""
        recent = np.sum(self.recent, axis=0) / MEMORY_STEPS if self.recent else np.zeros(len(ACTIONS))
        return np.concatenate([recent, self.since / MEMORY_CAP])

    def observe(self, decision) -> None:
        """Remember one step's decisions: {action: count} or counts in ACTIONS order."""
        if isinstance(decision, dict):
            taken = np.array([decision.get(a, 0) > 0 for a in ACTIONS])
        else:
            taken = np.asarray(decision) > 0
        self.recent.append(taken.astype(np.float64))
        self.since = np.where(taken, 1.0, np.minimum(self.since + 1, MEMORY_CAP))


def of(traj) -> np.ndarray:
    """The memory at every step of a recorded trajectory, shape (T, len(MEMORY)):
    what the teacher had decided before each state."""
    m = Memory()
    out = np.empty((len(traj.actions), len(MEMORY)))
    for k, decision in enumerate(traj.actions):
        out[k] = m.vector()
        m.observe(decision)
    return out


def stack_memory(trajs) -> np.ndarray:
    if not trajs:
        return np.zeros((0, len(MEMORY)))
    return np.concatenate([of(t) for t in trajs])
