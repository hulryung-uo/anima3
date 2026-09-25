"""System One judgements: several typed questions about one state, answered with probabilities.

The decision head (`decision.py`) picks one verb per tick, and experiments 2-3 measured what
that costs: a head deciding every tick can only lose tempo against a near-optimal rule. A
judge is the other shape — the one TypeSafe's Jev was built for and measured best at (14/14
on "is this in character", 3/3 on numeric state, where the open imitations scored 6/14 and
1/3). It answers *several* questions about one state in one call:

- `Choice` — one of named options, with the whole distribution;
- `Noul`   — the probability of yes;
- `Score`  — a position on a graded scale (0 .. len(criteria)-1).

It is asked at boundaries (a round starts, health crosses half, the opponent is paralysed),
never per tick, and never blocks the body: `Asker` runs it off-thread and the rule keeps
acting on the last answer until a new one lands. Every question and answer is logged, so the
logs are a dataset of (state, judgement, later outcome).
"""

from __future__ import annotations

import json
import random
import string
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Choice:
    instructions: str
    criteria: dict[str, str]


@dataclass(frozen=True)
class Noul:
    instructions: str
    criteria: dict[str, str] = field(default_factory=lambda: {"true": "yes", "false": "no"})


@dataclass(frozen=True)
class Score:
    instructions: str
    criteria: list[str]


Question = Choice | Noul | Score


@dataclass
class Answer:
    value: str | float        # the chosen key (Choice), P(yes) (Noul), or the scale position (Score)
    probs: dict[str, float]
    confidence: float


@dataclass
class Verdict:
    answers: dict[str, Answer]
    ms: float
    backend: str
    error: str | None = None


class Judge(Protocol):
    name: str

    def ask(self, state: str, questions: dict[str, Question]) -> Verdict: ...


def _margin(probs: dict[str, float]) -> float:
    ranked = sorted(probs.values(), reverse=True)
    return ranked[0] - (ranked[1] if len(ranked) > 1 else 0.0)


class JevJudge:
    """TypeSafe's Jev (cloud): every question in one `/v1/systemone` call.
    Instructions and criteria must be English (Korean instructions collapsed a 0.82 answer to 0.03)."""

    name = "jev"

    def __init__(self, api_key: str | None = None) -> None:
        from typesafe_sdk import TypeSafeClient

        from .decision import _typesafe_key
        key = _typesafe_key(api_key)
        if not key:
            raise ValueError("cloud Jev needs a TYPESAFE_API_KEY (env or ~/dev/jev/.env)")
        self._client = TypeSafeClient(api_key=key)

    def ask(self, state: str, questions: dict[str, Question]) -> Verdict:
        import typesafe_sdk as ts
        t0 = time.perf_counter()
        qs = {}
        for k, q in questions.items():
            if isinstance(q, Choice):
                qs[k] = ts.Choice(instructions=q.instructions, criteria=q.criteria)
            elif isinstance(q, Noul):
                qs[k] = ts.Noul(instructions=q.instructions, criteria=q.criteria)
            else:
                qs[k] = ts.Score(instructions=q.instructions, criteria=q.criteria)
        res = self._client.system_one(state=state, questions=qs).answers
        out: dict[str, Answer] = {}
        for k, q in questions.items():
            a = res[k]
            probs = {str(x): float(p) for x, p in (getattr(a, "probabilities", None) or {}).items()}
            if isinstance(q, Choice):
                out[k] = Answer(a.choice, probs, _margin(probs) if probs else float(a.confidence))
            elif isinstance(q, Noul):          # a noul carries only P(yes): its distance from a coin is the confidence
                out[k] = Answer(float(a.noul), probs, abs(2 * float(a.noul) - 1))
            else:
                out[k] = Answer(float(a.score), probs, float(a.confidence))
        return Verdict(out, (time.perf_counter() - t0) * 1000, self.name)


class QwenJudge:
    """The local logprob head answering the same typed questions, one forward pass each:
    a Choice reads the option letters, a Noul reads A (yes) against B (no), a Score takes the
    expected grade over the scale's letters. Free, ~150 ms a question, weaker on register."""

    name = "qwen"

    def __init__(self, qwen=None) -> None:
        from .decision import QwenLogprob
        self._q = qwen or QwenLogprob()

    def warmup(self) -> float:
        return self._q.warmup()

    def ask(self, state: str, questions: dict[str, Question]) -> Verdict:
        t0 = time.perf_counter()
        out: dict[str, Answer] = {}
        for k, q in questions.items():
            if isinstance(q, Choice):
                d = self._q.choose(state, q.instructions, q.criteria)
                out[k] = Answer(d.choice, d.probs, d.confidence)
            elif isinstance(q, Noul):
                d = self._q.choose(state, q.instructions, {"true": q.criteria.get("true", "yes"), "false": q.criteria.get("false", "no")})
                out[k] = Answer(d.probs["true"], d.probs, abs(2 * d.probs["true"] - 1))
            else:
                opts = {string.ascii_lowercase[i]: c for i, c in enumerate(q.criteria)}
                d = self._q.choose(state, q.instructions, opts)
                exp = sum(i * d.probs[L] for i, L in enumerate(opts))
                out[k] = Answer(exp, d.probs, d.confidence)
        return Verdict(out, (time.perf_counter() - t0) * 1000, self.name)


class RandomJudge:
    """The control arm: a uniformly random choice, a coin for every yes/no, the middle of
    every scale. A judge that cannot beat this is not judging."""

    name = "random"

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)

    def ask(self, state: str, questions: dict[str, Question]) -> Verdict:
        out: dict[str, Answer] = {}
        for k, q in questions.items():
            if isinstance(q, Choice):
                keys = list(q.criteria)
                pick = self._rng.choice(keys)
                out[k] = Answer(pick, {x: 1.0 / len(keys) for x in keys}, 1.0)
            elif isinstance(q, Noul):
                out[k] = Answer(float(self._rng.random() < 0.5), {}, 1.0)
            else:
                out[k] = Answer((len(q.criteria) - 1) / 2, {}, 1.0)
        return Verdict(out, 0.0, self.name)


def build_judge(kind: str) -> Judge:
    kind = kind.lower()
    if kind in ("jev", "typesafe", "cloud"):
        return JevJudge()
    if kind in ("qwen", "mlx", "local"):
        return QwenJudge()
    if kind == "random":
        return RandomJudge()
    raise ValueError(f"unknown judge {kind!r}")


class Asker:
    """One judge call in flight at a time, off-thread; the caller polls `take()` every tick.

    A new `ask` while one is pending is dropped, not queued: the state it describes will be
    stale by the time the first answer lands, and the next boundary will ask again."""

    def __init__(self, judge: Judge, log_path: str | Path | None = None, deadline_s: float = 3.0) -> None:
        self.judge, self.deadline_s = judge, deadline_s
        self.log_path = Path(log_path) if log_path else None
        self._lock = threading.Lock()
        self._pending: tuple[str, float] | None = None       # (key, started)
        self._result: tuple[str, Verdict, dict] | None = None
        self.calls = self.late = self.errors = 0
        self.ms: list[float] = []
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._pending is not None

    def ask(self, key: str, state: str, questions: dict[str, Question], context: dict | None = None) -> bool:
        with self._lock:
            if self._pending is not None:
                return False
            self._pending = (key, time.monotonic())
        self.calls += 1

        def work() -> None:
            try:
                v = self.judge.ask(state, questions)
            except Exception as e:  # noqa: BLE001 — a broken judge must never stop the body
                v = Verdict({}, 0.0, getattr(self.judge, "name", "?"), error=f"{type(e).__name__}: {e}")
            with self._lock:
                started = self._pending[1] if self._pending else time.monotonic()
                self._pending = None
                late = time.monotonic() - started > self.deadline_s
                if v.error:
                    self.errors += 1
                elif late:
                    self.late += 1
                else:
                    self._result = (key, v, context or {})
                self.ms.append(v.ms)
            self._write(key, state, questions, v, context, late)

        threading.Thread(target=work, daemon=True).start()
        return True

    def take(self) -> tuple[str, Verdict, dict] | None:
        with self._lock:
            r, self._result = self._result, None
        return r

    def _write(self, key: str, state: str, questions: dict, v: Verdict, context: dict | None, late: bool) -> None:
        if not self.log_path:
            return
        row = {"t": time.time(), "key": key, "state": state, "questions": sorted(questions), "backend": v.backend,
               "ms": round(v.ms, 1), "error": v.error, "late": late, "context": context or {},
               "answers": {k: {"value": a.value, "confidence": round(a.confidence, 3),
                               "probs": {x: round(p, 3) for x, p in a.probs.items()}} for k, a in v.answers.items()}}
        with self._lock, self.log_path.open("a") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
