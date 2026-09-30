"""Play OpenRA games with a macro policy through an OpenRA-RL server
(transfer/openra/setup.sh; start it with BOT_TYPE=<opponent>)."""

from __future__ import annotations

import asyncio
import time

from .openra import STEP_TICKS, TICKS_PER_SECOND, OpenRAEncoder, OpenRAExecutor


async def play_game(policy, url: str = "http://localhost:8000", max_minutes: float = 30,
                    opponent: str | None = None, seed: int | None = None, map_name: str | None = None,
                    log=None) -> dict:
    """One game from reset to the end (or ``max_minutes`` of game time).
    Returns the result ("win", "lose", "draw", or "time_limit"), and the
    features and decisions of every step."""
    from openra_env.mcp_ws_client import OpenRAMCPClient

    started = time.time()
    steps = []
    async with OpenRAMCPClient(base_url=url, message_timeout_s=300.0) as env:
        async def call(tool, **args):
            return await env.call_tool(tool, **args)

        options = {k: v for k, v in {"bot_type": opponent, "seed": seed, "map_name": map_name}.items() if v is not None}
        await env.reset(**options)
        await call("advance", ticks=5)
        st = await call("get_game_state")
        for u in st.get("units_summary", []):
            if u.get("type") == "mcv":
                await call("deploy_unit", unit_id=u["id"])
        encoder, executor = OpenRAEncoder(), OpenRAExecutor(call, await call("get_map_analysis"))
        result = "time_limit"
        while True:
            st = await call("get_game_state")
            if st.get("done"):
                result = st.get("result") or "draw"
                break
            if st.get("tick", 0) >= max_minutes * 60 * TICKS_PER_SECOND:
                break
            f = encoder.step(st)
            counts = policy(f, st)
            done = await executor.act(st, counts)
            steps.append({"t": st.get("tick", 0) / TICKS_PER_SECOND, "f": f, "a": counts, "done": done,
                          "failed": executor.failures[-len(counts):] if executor.failures else []})
            executor.failures.clear()
            if log and len(steps) % 30 == 0:
                log(f"  {f[0]:5.1f} game-min: army {f[12]:.0f}, stock {f[1]:.0f}, killed {st['military']['kills_cost']}"
                    f", lost {st['military']['deaths_cost']}")
            try:
                for _ in range(STEP_TICKS // 50):
                    r = await call("advance", ticks=50)
                    if isinstance(r, dict) and r.get("done"):
                        break
            except RuntimeError as e:  # the game or the server failed: not a result
                result, error = "error", str(e)
                break
    out = {"result": result, "game_min": steps[-1]["t"] / 60 if steps else 0.0,
           "wall_s": time.time() - started, "steps": steps}
    if result == "error":
        out["error"] = error
    return out


def play(policy, games: int = 1, **kw) -> list[dict]:
    return [asyncio.run(play_game(policy, **kw)) for _ in range(games)]
