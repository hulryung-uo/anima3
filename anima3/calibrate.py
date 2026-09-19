"""Calibrate the decision head against outcomes — offline, from the JSONL logs.

    python -m anima3.calibrate .logs/village/*.jsonl .logs/*.jsonl

Label: an admitted decision is *good* when, over the next `horizon` ticks, the
character gained gold or a skill point, or survived a threat without losing more than
a tenth of its health. That is a proxy for "the world got better", not truth; it is the
same proxy anima2's fitness used and it is what we have. Fit: one scalar temperature on
log-probabilities (Guo et al. 2017), chosen to minimise negative log-likelihood of the
label on the top choice's probability. Report: expected calibration error before and
after, and the threshold that keeps 85% precision.
"""

from __future__ import annotations

import glob
import json
import math
import sys
from dataclasses import dataclass


@dataclass
class Sample:
    conf: float          # top probability - runner-up (what the gate uses)
    top_p: float         # top probability
    probs: dict[str, float]
    good: bool


def load(paths: list[str], horizon: int = 30) -> list[Sample]:
    out: list[Sample] = []
    for path in paths:
        rows = []
        with open(path) as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        ticks = [r for r in rows if "scene" in r]
        by_tick = {r["tick"]: r for r in ticks}
        for r in ticks:
            d = r.get("decision")
            if not d or not d.get("probs") or not r.get("used_model") or d.get("backend") == "scripted":
                continue
            later = [by_tick[t] for t in range(r["tick"] + 1, r["tick"] + horizon + 1) if t in by_tick]
            if len(later) < 5:
                continue
            gold_up = later[-1]["gold"] > r["gold"]
            skills_up = any("Skills (" in x["scene"] and _skill_total(x["scene"]) > _skill_total(r["scene"]) for x in later[-3:])
            hp0 = r["hp_pct"]
            survived = all(not x.get("dead") for x in later) and min(x["hp_pct"] for x in later) >= hp0 - 0.1
            threatened = r["hostiles"] > 0
            good = gold_up or skills_up or (threatened and survived)
            ranked = sorted(d["probs"].values(), reverse=True)
            out.append(Sample(ranked[0] - (ranked[1] if len(ranked) > 1 else 0.0), ranked[0], d["probs"], good))
    return out


def _skill_total(scene: str) -> float:
    import re
    m = re.search(r"Skills \([^)]*\): (.*?)\.", scene)
    if not m:
        return 0.0
    return sum(float(x) for x in re.findall(r" ([0-9]+\.[0-9])", m.group(1)))


def scaled(probs: dict[str, float], T: float) -> dict[str, float]:
    logs = {k: math.log(max(v, 1e-9)) / T for k, v in probs.items()}
    m = max(logs.values())
    z = sum(math.exp(v - m) for v in logs.values())
    return {k: math.exp(v - m) / z for k, v in logs.items()}


def nll(samples: list[Sample], T: float) -> float:
    total = 0.0
    for s in samples:
        p = max(scaled(s.probs, T).values())
        p = min(max(p, 1e-6), 1 - 1e-6)
        total -= math.log(p) if s.good else math.log(1 - p)
    return total / max(1, len(samples))


def fit_temperature(samples: list[Sample]) -> float:
    best, best_nll = 1.0, nll(samples, 1.0)
    for T in [x / 10 for x in range(2, 101)]:
        v = nll(samples, T)
        if v < best_nll:
            best, best_nll = T, v
    return best


def ece(samples: list[Sample], T: float, bins: int = 10) -> float:
    buckets: list[list[Sample]] = [[] for _ in range(bins)]
    for s in samples:
        p = max(scaled(s.probs, T).values())
        buckets[min(bins - 1, int(p * bins))].append(s)
    total = 0.0
    for b in buckets:
        if not b:
            continue
        acc = sum(s.good for s in b) / len(b)
        conf = sum(max(scaled(s.probs, T).values()) for s in b) / len(b)
        total += len(b) / len(samples) * abs(acc - conf)
    return total


def threshold_for_precision(samples: list[Sample], T: float, target: float = 0.85) -> tuple[float, float]:
    scored = sorted(((max(scaled(s.probs, T).values()), s.good) for s in samples), reverse=True)
    best = (1.01, 0.0)
    for i in range(1, len(scored) + 1):
        prec = sum(g for _, g in scored[:i]) / i
        if prec >= target:
            best = (scored[i - 1][0], i / len(scored))
    return best


def main(argv: list[str]) -> int:
    paths = [p for a in (argv or [".logs/village/*.jsonl", ".logs/*.jsonl"]) for p in glob.glob(a)]
    samples = load(paths)
    if len(samples) < 20:
        print(f"only {len(samples)} labelled decisions; need more runs")
        return 1
    base = sum(s.good for s in samples) / len(samples)
    T = fit_temperature(samples)
    print(f"decisions: {len(samples)} from {len(paths)} logs | base rate good = {base:.2f}")
    print(f"ECE raw (T=1.0): {ece(samples, 1.0):.3f}   ->   ECE at T={T:.1f}: {ece(samples, T):.3f}")
    for T_ in (1.0, T):
        thr, cov = threshold_for_precision(samples, T_)
        print(f"  T={T_:.1f}: top-probability >= {thr:.2f} keeps 85% precision at {cov:.0%} coverage")
    # by confidence band, raw
    bands = [(0.0, 0.35), (0.35, 0.7), (0.7, 0.9), (0.9, 1.01)]
    for lo, hi in bands:
        b = [s for s in samples if lo <= s.conf < hi]
        if b:
            print(f"  gate conf [{lo:.2f},{hi:.2f}): n={len(b):4d}  good={sum(s.good for s in b)/len(b):.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
