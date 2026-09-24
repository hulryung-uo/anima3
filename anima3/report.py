"""Summarise a duel experiment: one directory per arm, `<arm>.out` beside it.

    python -m anima3.report .logs/exp2 [--baseline rule]

For each arm: fighter A's round record, its 95% interval and p against a coin, the
difference from the baseline arm, the same numbers with every match that saw a bridge
reconnect left out (a reconnecting fighter stands frozen), and who actually made the
multi-option decisions (the model, the rule while the model was thinking, or the gate).
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re

from .stats import binom_two_sided, two_proportions, wilson

MATCH = re.compile(r"^match +(\d+)/\d+: \S+ (\d+) - (\d+) \S+ \(draws (\d+)\)")


def read_out(path: str) -> list[dict]:
    """Per match: rounds won/lost/drawn and the reconnects logged since the previous match."""
    out, recon = [], 0
    with open(path, errors="replace") as fh:
        lines = fh.readlines()
    for line in lines:
        if "reconnected as" in line:
            recon += 1
        m = MATCH.match(line)
        if m:
            out.append({"match": int(m.group(1)), "w": int(m.group(2)), "l": int(m.group(3)),
                        "d": int(m.group(4)), "recon": recon})
            recon = 0
    return out


def read_matches(arm_dir: str) -> tuple[list[dict], int] | None:
    """Validated runs record every attempt in matches.jsonl: count the valid ones, and the voids."""
    path = f"{arm_dir}/matches.jsonl"
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        recs = [json.loads(x) for x in fh if x.strip()]
    if not recs or "valid" not in recs[0]:
        return None
    # Judge every attempt by today's rules (a run keeps the validator it started with), and take
    # the first valid attempt of each match: a replay exists only because an earlier one was voided.
    import re as _re

    from .duel import validate_match
    first: dict[int, dict] = {}
    voids = 0
    for r in recs:
        recon = {m.group(1): int(m.group(2)) for p in r.get("problems", [])
                 for m in [_re.match(r"(\S+)'s bridge reconnected (\d+)x", p)] if m}
        missing = [p for p in r.get("problems", []) if p.startswith("staging short")]
        if validate_match(r, recon, mage=True, missing=missing):
            voids += 1
        elif r["match"] not in first:
            first[r["match"]] = r
    ok = [{"match": r["match"], "w": r["wins_a"], "l": r["wins_b"], "d": r["draws"], "recon": 0}
          for _, r in sorted(first.items())]
    return ok, voids


def decisions(arm_dir: str) -> dict:
    """Who decided fighter A's multi-option ticks, and how often the pick differed from the rule's."""
    reasons, n, dev = collections.Counter(), 0, 0
    for f in glob.glob(f"{arm_dir}/m*-ilse*.jsonl"):
        with open(f) as fh:
            recs = [json.loads(x) for x in fh]
        for r in recs:
            if "options" not in r:
                continue
            if len(r["options"]) < 2:
                continue
            n += 1
            why = r["reason"]
            reasons["model" if why == "admitted" else "rule, model thinking" if why.startswith("rule") else
                    "gate rejected" if why.startswith("confidence") else why] += 1
            dev += why == "admitted" and r["chosen"] != next(iter(r["options"]))
    return {"n": n, "reasons": dict(reasons), "deviated": dev}


def line(name: str, w: int, l: int, base: tuple[int, int] | None) -> str:
    n = w + l
    if not n:
        return f"  {name:<22} no decided rounds"
    lo, hi = wilson(w, n)
    s = f"  {name:<22} {w:3d}-{l:<3d} {w / n:6.1%}  CI {lo:5.1%}-{hi:5.1%}  p(coin)={binom_two_sided(w, n):.3f}"
    if base and sum(base):
        s += f"  p(vs base)={two_proportions(w, n, base[0], sum(base)):.3f}"
    return s


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="anima3.report")
    ap.add_argument("dir")
    ap.add_argument("--baseline", default="rule")
    args = ap.parse_args(argv)
    arms = sorted(os.path.basename(p)[:-4] for p in glob.glob(f"{args.dir}/*.out"))
    data, voids = {}, {}
    for a in arms:
        validated = read_matches(f"{args.dir}/{a}")
        if validated is not None:
            data[a], voids[a] = validated
        else:
            data[a] = read_out(f"{args.dir}/{a}.out")
    if voids:
        print("validated arms (void attempts excluded): " + ", ".join(f"{a} {v} void" for a, v in voids.items()))

    def tally(ms, clean=False):
        ms = [m for m in ms if not (clean and m["recon"])]
        return sum(m["w"] for m in ms), sum(m["l"] for m in ms), len(ms)

    for clean in (False, True):
        print("\nmatches without a bridge reconnect only:" if clean else "all matches:")
        base = tally(data.get(args.baseline, []), clean)[:2] if args.baseline in data else None
        for a in arms:
            w, l, k = tally(data[a], clean)
            print(line(f"{a} ({k} matches)", w, l, None if a == args.baseline else base))
    print("\nfighter A's multi-option decisions:")
    for a in arms:
        d = decisions(f"{args.dir}/{a}")
        if d["n"]:
            parts = ", ".join(f"{k} {v / d['n']:.0%}" for k, v in sorted(d["reasons"].items(), key=lambda kv: -kv[1]))
            print(f"  {a:<10} {d['n']:5d} ticks: {parts}; model differed from the rule {d['deviated']}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
