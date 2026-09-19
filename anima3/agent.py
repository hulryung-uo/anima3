"""The two-rate loop.

Fast (every tick): observe → facts → affordances → execute → pump. The rule's
first affordance is always executable on its own, so the character stays alive
with no model at all. Slow (every `decide_every` ticks, or immediately when the
situation changes): ask the decision client to pick from the menu, off-thread,
with a deadline. An admitted pick becomes a short *plan* that is repeated while
it stays valid, so steering is not diluted by the rule between decisions.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .affordances import Affordance, enumerate_affordances
from .body import Body
from .contract import all_names, click
from .decision import Admitted, Decision, DecisionClient, gate
from .persona import Persona
from .scene import facts, render

QUESTION = "What should you do right now?"


@dataclass
class TickReport:
    tick: int
    hp_pct: float
    dead: bool
    hostiles: int
    gold: int
    chosen: str | None = None
    used_model: bool = False
    reason: str = ""
    confidence: float | None = None
    backend: str = ""
    ms: float | None = None
    options: list[str] = field(default_factory=list)


class Agent:
    def __init__(
        self, body: Body, persona: Persona, client: DecisionClient, *,
        decide_every: int = 4, plan_ticks: int = 4, threshold: float = 0.35, deadline_s: float = 1.5,
        pump_ms: int = 250, log_path: str | Path | None = None, sync: bool | None = None,
    ) -> None:
        self.body, self.persona, self.client = body, persona, client
        self.decide_every, self.plan_ticks, self.threshold = decide_every, plan_ticks, threshold
        self.deadline_s, self.pump_ms = deadline_s, pump_ms
        self.sync = (client.name == "scripted") if sync is None else sync
        self.log_path = Path(log_path) if log_path else None
        self.memory: dict = {}
        self.tick_no = 0
        self.reports: list[TickReport] = []
        self._gen = 0
        self._lock = threading.Lock()
        self._pending: tuple[int, float] | None = None          # (gen, started_at)
        self._result: tuple[int, Decision, list[str]] | None = None
        self._plan: tuple[str, int] | None = None                # (affordance id, valid until tick)
        self._last_sig: tuple | None = None
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)

    # --- decision plumbing --------------------------------------------------
    def _start(self, scene: str, options: dict[str, str]) -> None:
        self._gen += 1
        gen = self._gen
        self._pending = (gen, time.monotonic())

        def work() -> None:
            try:
                d = self.client.choose(scene, QUESTION, options)
            except Exception as e:  # noqa: BLE001 — a broken model must never stop the body
                d = Decision("", {}, 0.0, 0.0, getattr(self.client, "name", "?"), error=f"{type(e).__name__}: {e}")
            with self._lock:
                if self._pending and self._pending[0] == gen:
                    self._result = (gen, d, list(options))
                    self._pending = None

        threading.Thread(target=work, daemon=True).start()

    def _take_result(self, options: dict[str, str]) -> Decision | None:
        with self._lock:
            r, self._result = self._result, None
        if r is None:
            return None
        gen, d, keys = r
        return d if gen == self._gen and keys == list(options) else None

    def _signature(self, hostiles: int, hp_pct: float, options: dict[str, str]) -> tuple:
        return (hostiles, round(hp_pct, 1), tuple(options))

    # --- one tick --------------------------------------------------------------
    def tick(self) -> TickReport:
        self.tick_no += 1
        if self.tick_no % 20 == 1:
            self.body.act(all_names())  # names arrive asynchronously; refresh them now and then
        obs = self.body.observe()
        self._learn_names(obs)
        f = facts(obs)
        affs = enumerate_affordances(obs, f, self.persona, self.memory)
        rep = TickReport(self.tick_no, f.hp_pct, f.dead, len(f.hostiles), obs.player.gold, options=[a.id for a in affs])
        if not affs:
            rep.reason = "no affordances (dead or nothing valid)"
            self.body.pump(self.pump_ms)
            self.reports.append(rep)
            return rep
        options = {a.id: a.description for a in affs}
        by_id = {a.id: a for a in affs}
        scene = render(obs, f, self.persona)
        sig = self._signature(len(f.hostiles), f.hp_pct, options)
        changed = sig != self._last_sig
        self._last_sig = sig
        need = changed or (self.tick_no % self.decide_every == 0)

        decision: Decision | None = None
        admitted: Admitted | None = None
        if self.sync:
            if need or self._plan is None:
                decision = self.client.choose(scene, QUESTION, options)
                admitted = gate(decision, options, self.threshold)
        else:
            decision = self._take_result(options)
            if decision is not None:
                admitted = gate(decision, options, self.threshold)
            elif self._pending is not None and time.monotonic() - self._pending[1] > self.deadline_s:
                self._pending = None
                admitted = Admitted(next(iter(options)), False, "deadline")
            elif self._pending is None and need:
                self._start(scene, options)
        if changed:
            self._plan = None  # the world moved; a stale plan must not carry over

        if admitted is not None:
            self._plan = (admitted.choice, self.tick_no + self.plan_ticks)
            rep.chosen, rep.used_model, rep.reason = admitted.choice, admitted.used_model, admitted.reason
        elif self._plan is not None and self._plan[0] in by_id and self.tick_no <= self._plan[1]:
            rep.chosen, rep.reason = self._plan[0], "plan"
        else:
            rep.chosen, rep.reason = next(iter(options)), "rule" if not (self._pending and not self.sync) else "rule (deciding)"
        if decision is not None:
            rep.confidence, rep.backend, rep.ms = decision.confidence, decision.backend, decision.ms

        self._execute(by_id[rep.chosen], f)
        self.body.pump(self.pump_ms)
        self._log(rep, scene, options, decision, admitted)
        self.reports.append(rep)
        return rep

    def _learn_names(self, obs) -> None:
        """Names arrive as journal lines answering a Click; cache them by serial and
        click one unnamed nearby mobile per tick so the scene stops saying 'a creature'."""
        names: dict[int, str] = self.memory.setdefault("names", {})
        serials = {m.serial for m in obs.mobiles}
        for j in obs.new_journal:
            if j.serial in serials and j.text and j.serial not in names:
                names[j.serial] = j.name or j.text
        for m in obs.mobiles:
            if not m.name and m.serial in names:
                m.name = names[m.serial]
        clicked: set[int] = self.memory.setdefault("clicked", set())
        for m in obs.mobiles:
            if not m.name and m.serial not in clicked and m.distance <= 12:
                clicked.add(m.serial)
                self.body.act(click(m.serial))
                break

    def _execute(self, aff: Affordance, f) -> None:
        for action in aff.actions:
            self.body.act(action)
        if aff.id.startswith("say:"):
            self.memory.setdefault("greeted", set()).add(int(aff.id.split(":")[1]))
        elif aff.id.startswith("attack:"):
            self.memory["engaged"] = int(aff.id.split(":")[1])

    def _log(self, rep: TickReport, scene: str, options: dict[str, str], decision: Decision | None, admitted: Admitted | None) -> None:
        if not self.log_path:
            return
        row = {"tick": rep.tick, "scene": scene, "options": options, "chosen": rep.chosen, "reason": rep.reason,
               "used_model": rep.used_model, "hp_pct": round(rep.hp_pct, 3), "hostiles": rep.hostiles, "gold": rep.gold,
               "decision": None if decision is None else {"choice": decision.choice, "probs": decision.probs,
                                                          "confidence": decision.confidence, "ms": decision.ms,
                                                          "backend": decision.backend, "error": decision.error}}
        with self.log_path.open("a") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def run(self, ticks: int, on_tick=None) -> list[TickReport]:
        for _ in range(ticks):
            rep = self.tick()
            if on_tick:
                on_tick(rep)
        return self.reports

    def summary(self) -> dict:
        r = self.reports
        decided = [x for x in r if x.confidence is not None]
        reasons: dict[str, int] = {}
        for x in r:
            reasons[x.reason] = reasons.get(x.reason, 0) + 1
        return {"ticks": len(r), "model_calls": len(decided), "model_admitted": sum(x.used_model for x in r),
                "dead": bool(r and r[-1].dead), "gold": r[-1].gold if r else 0,
                "min_hp_pct": round(min((x.hp_pct for x in r), default=1.0), 2),
                "avg_decision_ms": round(sum(x.ms for x in decided) / len(decided), 0) if decided else None,
                "reasons": reasons}
