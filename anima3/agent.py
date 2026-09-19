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
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .affordances import Affordance, enumerate_affordances
from .body import Body
from .contract import all_names, click
from .decision import Admitted, Decision, DecisionClient, gate
from .persona import Persona
from .progression import curriculum_key, gm_count, progress_scene, training_delta
from .scene import facts, render

try:
    from .economy import econ_facts, econ_scene, economy_affordances
except ImportError:  # pragma: no cover
    econ_facts = econ_scene = economy_affordances = None

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
        economy: bool = False, proc_max_ticks: int = 60,
    ) -> None:
        self.economy, self.proc_max_ticks = economy, proc_max_ticks
        self.profession = persona.profession or "adventurer"
        self.skill_log: list[tuple[int, dict[str, float]]] = []   # (tick, {skill: +delta})
        self._prev_obs = None
        self._proc: tuple[str, Any, int] | None = None   # (affordance id, generator, started tick)
        self.proc_log: list[tuple[int, str, str]] = []    # (tick, id, verdict)
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
        if self._prev_obs is not None and obs.skills:
            d = training_delta(self._prev_obs, obs)
            if d:
                self.skill_log.append((self.tick_no, d))
        self._prev_obs = obs if obs.skills else self._prev_obs
        self._last_obs = obs
        self.memory["tick"] = self.tick_no
        if obs.corpse_of:  # death links are transient; remember our kills' corpses
            self.memory.setdefault("my_corpses", set()).update(obs.corpse_of)
        self._learn_names(obs)
        f = facts(obs)
        # An active procedure owns the tick unless danger interrupts it.
        if self._proc is not None:
            pid, gen, started = self._proc
            interrupted = f.dead or (f.hostiles and f.hostiles[0].distance <= 3) or self.tick_no - started > self.proc_max_ticks
            if not interrupted:
                rep = TickReport(self.tick_no, f.hp_pct, f.dead, len(f.hostiles), obs.player.gold, chosen=pid, reason="procedure")
                try:
                    step = gen.send(obs)
                    if step is not None:
                        self.body.act(step)
                    self._log_proc(pid, obs, step)
                except StopIteration as done:
                    self.proc_log.append((self.tick_no, pid, str(done.value)))
                    rep.reason = f"procedure done: {done.value}"
                    self._proc = None
                    if done.value != "ok" and pid.split(":")[0] in ("sell", "craft", "smelt", "goto"):
                        # a refused or failed verb is not retried immediately; let the others run
                        self.memory.setdefault("backoff", {})[pid] = self.tick_no + 40
                self.body.pump(self.pump_ms)
                self.reports.append(rep)
                return rep
            self.proc_log.append((self.tick_no, pid, "interrupted"))
            self._proc = None
        affs = enumerate_affordances(obs, f, self.persona, self.memory)
        econ_lines: list[str] = []
        if self.economy and econ_facts is not None and not f.hostiles and not f.dead:
            ef = econ_facts(obs, self.memory)
            econ = economy_affordances(obs, ef, self.memory)
            econ_lines = econ_scene(ef)
            # curriculum: among admissible work, the verb training the largest skill gap leads
            if obs.skills:
                econ.sort(key=lambda a: curriculum_key(obs, self.profession)(a.id))
                line = progress_scene(obs, self.profession)
                if line:
                    econ_lines.append(line)
            # economy verbs go ahead of wandering/hold, after survival/loot
            keep = [a for a in affs if not a.id.startswith("walk:") and a.id != "hold"]
            affs = keep + econ  # economy mode never offers wandering or idling; `wait:work` is the floor
        rep = TickReport(self.tick_no, f.hp_pct, f.dead, len(f.hostiles), obs.player.gold, options=[a.id for a in affs])
        if not affs:
            rep.reason = "no affordances (dead or nothing valid)"
            self.body.pump(self.pump_ms)
            self.reports.append(rep)
            return rep
        options = {a.id: a.description for a in affs}
        by_id = {a.id: a for a in affs}
        scene = render(obs, f, self.persona)
        if econ_lines:
            scene += "\n" + "\n".join(econ_lines)
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
        if aff.procedure is not None:
            gen = aff.procedure(self._last_obs, self.memory)
            try:
                step = next(gen)  # the first action; the generator then waits for next tick's obs
                if step is not None:
                    self.body.act(step)
                self._proc = (aff.id, gen, self.tick_no)
                self._plan = None  # a procedure, not a plan, now owns the ticks
            except StopIteration as done:
                self.proc_log.append((self.tick_no, aff.id, str(done.value)))
            return
        for action in aff.actions:
            self.body.act(action)
        if aff.id.startswith("say:"):
            self.memory.setdefault("greeted", set()).add(int(aff.id.split(":")[1]))
        elif aff.id.startswith("attack:"):
            self.memory["engaged"] = int(aff.id.split(":")[1])

    def _log_proc(self, pid: str, obs, step) -> None:
        if not self.log_path:
            return
        row = {"tick": self.tick_no, "proc": pid, "step": None if step is None else step.get("type"),
               "journal": [(j.cliloc, j.text[:80]) for j in obs.new_journal if j.text or j.cliloc],
               "gumps": [(g.serial, g.gump_id, sorted(set(re.findall(r"[0-9]{7}", g.layout)))[:12]) for g in obs.gumps],
               "cursor": obs.pending_target, "popup": None if obs.popup is None else len(obs.popup.entries),
               "shop_sell": None if obs.shop_sell is None else len(obs.shop_sell.items)}
        with self.log_path.open("a") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

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

    def skill_gains(self) -> dict[str, float]:
        total: dict[str, float] = {}
        for _, d in self.skill_log:
            for k, v in d.items():
                total[k] = round(total.get(k, 0.0) + v, 1)
        return total

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
                "procedures": [f"{t}:{pid}={v}" for t, pid, v in self.proc_log][-40:],
                "skill_gains": self.skill_gains(),
                "gm": gm_count(self._last_obs, self.profession) if self._last_obs is not None and self._last_obs.skills else None,
                "reasons": reasons}
