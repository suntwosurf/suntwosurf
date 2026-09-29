from aorbot.report import learning_verdict


def h(i, status, time):
    return {"iteration": i, "status": status, "time": time, "progress": 100.0}


def test_learned_to_finish_passes():
    hist = [h(0, "off_stage", 20), h(1, "stuck", 30)] + [h(i, "finished", 90 - i) for i in range(2, 8)]
    v = learning_verdict(hist)
    assert v["passed"]
    assert v["summary"]["failures_before_first_finish"] == 2
    assert v["summary"]["first_finish_attempt"] == 2


def test_finishing_without_improvement_is_not_proof_of_learning():
    v = learning_verdict([h(i, "finished", 90.0) for i in range(6)])
    assert not v["passed"]
    assert v["checks"]["goal: finished the stage"]
    assert not v["checks"]["learning: learned to finish, or got faster"]


def test_unstable_or_never_finished_fails():
    assert not learning_verdict([h(0, "finished", 90), h(1, "finished", 85), h(2, "off_stage", 30)],
                                stable_window=2)["passed"]
    v = learning_verdict([h(i, "off_stage", 10) for i in range(5)])
    assert not v["passed"] and "best_time" not in v["summary"]


def test_ignored_attempts_do_not_count():
    hist = [{**h(0, "stuck", 1), "ignored": True}] + [h(i, "finished", 90 - i) for i in range(1, 7)]
    v = learning_verdict(hist)
    assert v["summary"]["attempts"] == 6 and v["summary"]["ignored"] == 1 and v["passed"]
