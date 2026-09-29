"""Did the bot learn? A verdict from the attempt history of a learner state,
for the simulator or the live game alike."""

from __future__ import annotations


def learning_verdict(history: list[dict], stable_window: int = 5, min_gain: float = 0.01) -> dict:
    """Three checks, all needed for PASS:

    1. goal       - at least one attempt finished the stage;
    2. learning   - it failed before its first finish (learned to finish), or
                    its best time beats its first finished time by ``min_gain``;
    3. stable     - the last ``stable_window`` attempts all finished.
    """
    attempts = [h for h in history if not h.get("ignored")]
    finished = [h for h in attempts if h["status"] == "finished"]
    first = next((i for i, h in enumerate(attempts) if h["status"] == "finished"), None)
    summary: dict = {
        "attempts": len(attempts),
        "ignored": len(history) - len(attempts),
        "finishes": len(finished),
        "failures_before_first_finish": first if first is not None else len(attempts),
    }
    if first is not None:
        first_time = attempts[first]["time"]
        best_time = min(h["time"] for h in finished)
        summary.update(
            first_finish_attempt=attempts[first]["iteration"],
            first_finish_time=round(first_time, 3),
            best_time=round(best_time, 3),
            gain_percent=round(100 * (first_time - best_time) / first_time, 2),
        )
    tail = attempts[-stable_window:]
    checks = {
        "goal: finished the stage": first is not None,
        "learning: learned to finish, or got faster": first is not None
        and (first > 0 or summary["best_time"] < summary["first_finish_time"] * (1 - min_gain)),
        f"stable: last {stable_window} attempts all finished": len(tail) == stable_window
        and all(h["status"] == "finished" for h in tail),
    }
    return {"passed": all(checks.values()), "checks": checks, "summary": summary}
