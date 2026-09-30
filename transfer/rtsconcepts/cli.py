"""rtsconcepts inspect <recordings> | bc <recordings>"""

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


def cmd_bc(args) -> int:
    from . import bc
    from .dataset import load_dir, split_by_game

    trajs = load_dir(args.recordings)
    if len({t.game for t in trajs}) < 5:
        log(f"only {len({t.game for t in trajs})} games in {args.recordings}: record more first")
        return 1
    train, held = split_by_game(trajs, holdout=args.holdout, seed=args.seed)
    log(f"training on {len({t.game for t in train})} games, testing on {len({t.game for t in held})} others")
    hidden = tuple(int(h) for h in args.hidden.split(",") if h)
    model = bc.train(train, hidden=hidden, epochs=args.epochs, seed=args.seed, log=log)
    log("")
    log("\n".join(bc.report(bc.score(model, held))))
    model.save(args.out)
    log(f"saved {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rtsconcepts", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("inspect", help="value ranges and decision rates of recordings (zadbot record)")
    s.add_argument("recordings", help="folder with the .jsonl files, e.g. runs/record")
    s.set_defaults(func=cmd_inspect)
    s = sub.add_parser("bc", help="imitation baseline: predict the teacher's next decisions, scored on unseen games")
    s.add_argument("recordings")
    s.add_argument("--out", default=str(Path("runs") / "bc.json"))
    s.add_argument("--hidden", default="64,64", help="hidden layer sizes")
    s.add_argument("--epochs", type=int, default=30)
    s.add_argument("--holdout", type=float, default=0.2, help="share of games kept for the test")
    s.add_argument("--seed", type=int, default=0)
    s.set_defaults(func=cmd_bc)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
