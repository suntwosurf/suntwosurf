import numpy as np
import pytest

gym = pytest.importorskip("gymnasium")

from gymnasium.utils.env_checker import check_env  # noqa: E402

from aorbot.pipeline import resolve_grip, sim_config, simulate_demo  # noqa: E402
from aorbot.record import build_line  # noqa: E402
from aorbot.rl import RallyEnv, evaluate  # noqa: E402
from aorbot.sim.io import SimIO  # noqa: E402
from aorbot.sim.stage import make_stage  # noqa: E402


@pytest.fixture(scope="module")
def env():
    stage = make_stage(5, length=400)
    cfg = sim_config(stage, "dry")
    line, _ = build_line(simulate_demo(stage, "dry", cfg))
    return RallyEnv(SimIO(stage), line, resolve_grip(cfg, line, log=lambda m: None))


def test_env_passes_gymnasium_checks(env):
    check_env(env, skip_render_check=True)


def test_zero_residual_is_the_line_follower_and_finishes(env):
    result = evaluate(env)
    assert result["status"] == "finished" and result["reward"] > 0


def test_random_residual_steps(env):
    obs, _ = env.reset(seed=0)
    for _ in range(50):
        obs, reward, terminated, truncated, info = env.step(env.action_space.sample())
        assert env.observation_space.contains(obs)
        if terminated or truncated:
            break


def test_ppo_smoke(env):
    sb3 = pytest.importorskip("stable_baselines3")
    model = sb3.PPO("MlpPolicy", env, n_steps=128, batch_size=64, verbose=0)
    model.learn(256)
    action, _ = model.predict(np.zeros(env.observation_space.shape, np.float32), deterministic=True)
    assert action.shape == (2,)
