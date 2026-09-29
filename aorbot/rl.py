"""Optional deep-RL path: the same stage as a Gymnasium environment
(``pip install gymnasium stable-baselines3``).

The default is *residual* RL: the network adds corrections on top of the
line-following driver instead of learning to drive from nothing, so even an
untrained policy gets down the stage. It works on the simulator
(``SimIO``) and on the real game (``GameIO``) alike - but the real game runs
in real time, so the millions of steps PPO likes are weeks of driving there.
Train in the simulator first; the speed-profile learner is the practical
tool for the game itself.
"""

from __future__ import annotations

import math

import numpy as np

try:
    import gymnasium as gym
    from gymnasium import spaces
except ImportError as exc:  # pragma: no cover
    raise ImportError("the RL environment needs `pip install gymnasium` (and stable-baselines3 to train)") from exc

from .config import BotConfig
from .controller import LineFollower
from .episode import StageTracker, Status, VehicleIO
from .line import RacingLine
from .speed import speed_profile
from .types import Action, CarState

PREVIEW = (10.0, 25.0, 45.0, 70.0, 100.0)  # m ahead where the curvature is observed


class RallyEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(self, io: VehicleIO, line: RacingLine, cfg: BotConfig, v_target: np.ndarray | None = None,
                 residual: bool = True, finish_bonus: float = 10.0, crash_penalty: float = 30.0,
                 time_penalty: float = 0.0):
        super().__init__()
        if cfg.speed.grip <= 0:
            raise ValueError("resolve [speed] grip first (pipeline.resolve_grip)")
        self.io, self.line, self.cfg = io, line, cfg
        self.residual = residual
        self.finish_bonus, self.crash_penalty = finish_bonus, crash_penalty
        # Keep time_penalty well below the progress reward per step, or ending
        # the episode by crashing becomes the "best" strategy. The discount
        # factor alone already makes reaching the finish sooner pay.
        self.time_penalty = time_penalty
        self.tracker = StageTracker(line, cfg.rules)
        v = speed_profile(line, cfg.speed) if v_target is None else v_target
        self.base = LineFollower(line, cfg.controller, v, grip=cfg.speed.grip)
        n_obs = 7 + len(PREVIEW)
        self.observation_space = spaces.Box(-10.0, 10.0, shape=(n_obs,), dtype=np.float32)
        # [steer, pedal]; pedal > 0 throttle, < 0 brake
        self.action_space = spaces.Box(-1.0, 1.0, shape=(2,), dtype=np.float32)

    # ----------------------------------------------------------------------
    def _observe(self, state: CarState, s: float, offset: float) -> np.ndarray:
        base, info = self.base.act(state, s)
        self._base_action = base
        heading_err = math.remainder(state.heading - float(self.line.heading_at(s)), 2 * math.pi)
        curv = [float(self.line.curvature_at(s + d)) * 20.0 for d in PREVIEW]
        obs = [
            state.speed / 30.0,
            offset / self.cfg.rules.max_offset,
            heading_err,
            state.yaw_rate,
            (info["v_target"] - state.speed) / 30.0,
            base.steer,
            base.throttle - base.brake,
            *curv,
        ]
        return np.clip(np.asarray(obs, np.float32), -10.0, 10.0)

    def _to_action(self, a: np.ndarray) -> Action:
        steer, pedal = float(a[0]), float(a[1])
        if self.residual:
            b = self._base_action
            steer = b.steer + 0.5 * steer
            pedal = (b.throttle - b.brake) + pedal
        return Action(steer=steer, throttle=max(pedal, 0.0), brake=max(-pedal, 0.0)).clipped()

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        state = self.io.reset()
        self.tracker.reset(state)
        s, offset, _ = self.tracker.update(state)
        self._s = s
        return self._observe(state, s, offset), {"s": s}

    def step(self, action):
        state = self.io.step(self._to_action(np.asarray(action, np.float32)))
        s, offset, status = self.tracker.update(state)
        reward = 0.1 * (s - self._s) - 0.02 * (offset / self.cfg.rules.max_offset) ** 2 - self.time_penalty
        self._s = s
        terminated = status not in (Status.RUNNING, Status.TIMEOUT)
        truncated = status is Status.TIMEOUT
        if status is Status.FINISHED:
            reward += self.finish_bonus
        elif terminated:
            reward -= self.crash_penalty
        info = {"s": s, "status": status.value, "time": state.t - self.tracker.t0}
        if terminated or truncated:
            self.io.release()
        return self._observe(state, s, offset), float(reward), terminated, truncated, info


def train_ppo(env: RallyEnv, steps: int, out: str, log=print):  # pragma: no cover - long running
    try:
        from stable_baselines3 import PPO
    except ImportError as exc:
        raise ImportError("training needs `pip install stable-baselines3`") from exc
    # small initial exploration noise: start close to the line follower
    model = PPO("MlpPolicy", env, n_steps=2048, batch_size=256, gamma=0.995, learning_rate=3e-4,
                policy_kwargs={"log_std_init": -1.5}, verbose=0)
    done = 0
    while done < steps:
        chunk = min(20_000, steps - done)
        model.learn(chunk, reset_num_timesteps=False)
        done += chunk
        log(f"  {done:8d} steps")
    model.save(out)
    return model


def evaluate(env: RallyEnv, model=None) -> dict:
    """One episode with the model (deterministic), or with zero residual."""
    obs, _ = env.reset()
    total = 0.0
    while True:
        action = model.predict(obs, deterministic=True)[0] if model is not None else np.zeros(2, np.float32)
        obs, reward, terminated, truncated, info = env.step(action)
        total += reward
        if terminated or truncated:
            return {"status": info["status"], "time": info["time"], "s": info["s"], "reward": total}
