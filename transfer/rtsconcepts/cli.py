"""rtsconcepts inspect | bc | student | curve <recordings>"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def log(msg: str) -> None:
    print(msg, flush=True)


def cmd_inspect(args) -> int:
    from .dataset import load_dir
    from .inspect import inspect

    trajs = load_dir(args.recordings)
    log("\n".join(inspect(trajs)))
    return 0 if trajs else 1


def _split(args):
    from .dataset import load_dir, split_by_game

    trajs = load_dir(args.recordings)
    if len({t.game for t in trajs}) < 5:
        log(f"only {len({t.game for t in trajs})} games in {args.recordings}: record more first")
        return None
    train, held = split_by_game(trajs, holdout=args.holdout, seed=args.seed)
    log(f"training on {len({t.game for t in train})} games, testing on {len({t.game for t in held})} others")
    return train, held


def _sizes(text: str) -> tuple[int, ...]:
    return tuple(int(h) for h in text.split(",") if h)


def cmd_bc(args) -> int:
    from . import bc

    split = _split(args)
    if split is None:
        return 1
    train, held = split
    model = bc.train(train, hidden=_sizes(args.hidden), epochs=args.epochs, seed=args.seed,
                     memory=not args.no_memory, log=log)
    log("")
    log("\n".join(bc.report(bc.score(model, held))))
    model.save(args.out)
    log(f"saved {args.out}")
    return 0


def cmd_student(args) -> int:
    from . import bc, student

    split = _split(args)
    if split is None:
        return 1
    train, held = split
    log("concept student:")
    model = student.train(train, hidden=_sizes(args.hidden), policy_hidden=_sizes(args.policy_hidden),
                          concept_weight=args.concept_weight, epochs=args.epochs, seed=args.seed, log=log)
    s = bc.score(model, held)
    log("")
    log("\n".join(student.report_concepts(student.score_concepts(model, held))))
    log("")
    log("\n".join(bc.report(s, title="the concept student's imitation")))
    if args.compare:
        log("")
        log("the same without the concept bottleneck (bc, features + memory):")
        base = bc.score(bc.train(train, epochs=args.epochs, seed=args.seed), held)
        log(f"  mean AUC {base['mean_auc']:.3f} vs {s['mean_auc']:.3f} with concepts; "
            f"skill {base['mean_skill'] * 100:.1f} % vs {s['mean_skill'] * 100:.1f} %")
    model.save(args.out)
    log(f"saved {args.out}")
    return 0


def cmd_curve(args) -> int:
    """Learning curve: does the imitation still improve with more games?"""
    import numpy as np

    from . import bc

    split = _split(args)
    if split is None:
        return 1
    train, held = split
    games = sorted({t.game for t in train})
    rng = np.random.default_rng(args.seed)
    order = list(rng.permutation(games))
    sizes = sorted({max(5, len(games) >> k) for k in range(args.points)})
    log("")
    log("training games   mean AUC   rare decisions' AUC   skill")
    aucs = []
    for n in sizes:
        keep = set(order[:n])
        model = bc.train([t for t in train if t.game in keep], epochs=args.epochs, seed=args.seed,
                         memory=not args.no_memory)
        s = bc.score(model, held)
        aucs.append(s["mean_auc"])
        log(f"{n:14d}   {s['mean_auc']:8.3f}   {s['mean_auc_rare']:19.3f}   {s['mean_skill'] * 100:5.1f} %")
    if len(aucs) > 1:
        gain = aucs[-1] - aucs[-2]
        log(f"the last doubling: {gain:+.3f} AUC: " + (
            "still rising, more recordings help (DESIGN.md: grow while the curve improves)" if gain > 0.005
            else "about flat, more of the same recordings won't help much"))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rtsconcepts", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("inspect", help="value ranges, decision rates and concept labels of recordings (zadbot record)")
    s.add_argument("recordings", help="folder with the .jsonl files, e.g. runs/record")
    s.set_defaults(func=cmd_inspect)

    def common(s, epochs=80):  # an upper limit: early stopping ends training (about 50-60 passes on 800 games)
        s.add_argument("recordings")
        s.add_argument("--epochs", type=int, default=epochs)
        s.add_argument("--holdout", type=float, default=0.2, help="share of games kept for the test")
        s.add_argument("--seed", type=int, default=0)

    s = sub.add_parser("bc", help="imitation baseline: predict the teacher's next decisions, scored on unseen games")
    common(s)
    s.add_argument("--out", default=str(Path("runs") / "bc.json"))
    s.add_argument("--hidden", default="64,64", help="hidden layer sizes")
    s.add_argument("--no-memory", action="store_true", help="the features only, without the memory of own decisions")
    s.set_defaults(func=cmd_bc)

    s = sub.add_parser("student", help="the concept student: features + memory -> concepts -> decisions")
    common(s)
    s.add_argument("--out", default=str(Path("runs") / "student.json"))
    s.add_argument("--hidden", default="64", help="encoder hidden layer sizes")
    s.add_argument("--policy-hidden", default="64", help="policy hidden layer sizes")
    s.add_argument("--concept-weight", type=float, default=1.0, help="weight of the concept labels in the loss")
    s.add_argument("--compare", action="store_true", help="also train bc on the same games, for the cost of the bottleneck")
    s.set_defaults(func=cmd_student)

    s = sub.add_parser("curve", help="learning curve of bc: the score on unseen games against the number of training games")
    common(s)
    s.add_argument("--points", type=int, default=5, help="training sizes: all games, half, a quarter, ...")
    s.add_argument("--no-memory", action="store_true")
    s.set_defaults(func=cmd_curve)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
