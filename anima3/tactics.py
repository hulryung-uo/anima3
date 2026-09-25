"""The duel's slow layer: which playbook the rule should run, asked at phase boundaries.

Experiment 3 settled where a model does not belong: a head choosing every cast loses tempo
to a near-optimal rule (Qwen 30.9%, p < 0.001), and a slow API loses whole matches. Here the
rule casts every spell, at full speed, and a judge is asked only when the fight changes shape
— a round starts, health crosses half, the opponent is paralysed or poisoned, mana runs low,
or some seconds pass. It answers several typed questions about one narrative state in one
call: which playbook (`magic.PLAYBOOKS`), and who is winning (logged, a label to check later).
While it thinks the rule keeps running the last playbook, so thinking costs no tempo.
"""

from __future__ import annotations

from .judge import Asker, Choice, Score
from .magic import PLAYBOOKS, WORDS

PLAYBOOK_Q = Choice(
    instructions=("Which playbook should this mage follow for the next several seconds of the duel? "
                  "Weigh both fighters' health, mana, poison and paralysis, and what the opponent has been casting."),
    criteria=PLAYBOOKS)
MOMENTUM_Q = Score(
    instructions="Who is winning this round right now?",
    criteria=["the opponent is clearly winning", "the round is even", "this mage is clearly winning"])


def _hp_word(p: float) -> str:
    return f"{round(p * 100)}%"


class Tactician:
    """Per fighter. `tick()` runs every agent tick before the menu is built: it applies a
    landed answer to `memory["playbook"]` and, at a boundary, asks the next question."""

    def __init__(self, asker: Asker, *, every_ticks: int = 30, min_gap: int = 4, threshold: float = 0.15,
                 same_gap: int = 15) -> None:
        self.asker, self.every_ticks, self.min_gap, self.threshold = asker, every_ticks, min_gap, threshold
        # A paralysed opponent's frozen flag flickers with every update the shard sends (live: 25
        # rising edges in one 137 s match); the same boundary is not news again for `same_gap` ticks.
        self.same_gap = same_gap
        self._asked: dict[str, int] = {}
        self._opp: int | None = None
        self._flags: dict[str, bool] = {}
        self._last_ask = -10_000
        self.log: list[tuple[int, str, str, float]] = []    # (tick, boundary, playbook, confidence)
        self.boundaries: dict[str, int] = {}

    # --- what changed -----------------------------------------------------------
    def _boundary(self, tick: int, me_hp: float, mana_frac: float, poisoned: bool, opp) -> str | None:
        flags = {"hp_low": me_hp < 0.5, "mana_low": mana_frac < 0.4, "poisoned": poisoned,
                 "opp_low": opp.hits_max > 0 and opp.hits / opp.hits_max < 0.5,
                 "opp_paralyzed": opp.paralyzed, "opp_poisoned": opp.poisoned}
        rising = [k for k, v in flags.items() if v and not self._flags.get(k, False)]
        recovered = self._flags.get("hp_low", False) and me_hp >= 0.7
        self._flags = flags if not recovered else {**flags, "hp_low": False}
        if tick - self._last_ask < self.min_gap:
            return None
        rising = [k for k in rising if tick - self._asked.get(k, -10_000) >= self.same_gap]
        if rising:
            return rising[0]
        if recovered:
            return "hp_recovered"
        if tick - self._last_ask >= self.every_ticks:
            return "timer"
        return None

    # --- the state, in words -------------------------------------------------
    def state(self, obs, f, memory: dict, journal: list[tuple[int, int, str]], procs: list[tuple[int, str, str]], opp) -> str:
        p = obs.player
        rnd, score = memory.get("duel_round"), memory.get("duel_score")
        head = "A mage duel under 5x rules: no weapons, no armour, no potions; spells, meditation and wrestling only."
        if rnd:
            head += f" Round {rnd}" + (f"; rounds won so far — you {score[0]}, the opponent {score[1]}." if score else ".")
        trend = memory.get("hp_trend", 0.0)
        me = (f"You: health {_hp_word(f.hp_pct)} ({'falling fast' if trend < -0.1 else 'falling' if trend < -0.02 else 'rising' if trend > 0.02 else 'steady'}), "
              f"mana {p.mana}/{p.mana_max}" + (", poisoned" if p.poisoned else "") + ".")
        cond = [w for w, on in (("paralyzed", opp.paralyzed), ("poisoned", opp.poisoned)) if on]
        them = (f"Opponent: health about {_hp_word(opp.hits / opp.hits_max) if opp.hits_max else 'unknown'}"
                + (", " + " and ".join(cond) if cond else "") + f", {opp.distance} tiles away. Its mana is not visible.")
        heard = [WORDS[t.strip().lower()] for _, ser, t in journal[-60:] if ser == opp.serial and t.strip().lower() in WORDS][-5:]
        lines = [head, me, them]
        lines.append("The opponent's last spells: " + ", ".join(heard) + "." if heard else "The opponent has not been heard casting lately.")
        mine = [f"{pid.split(':', 1)[1].replace('_', ' ')} {v}" for _, pid, v in procs[-12:] if pid.startswith("cast:")][-5:]
        if mine:
            lines.append("Your last casts: " + ", ".join(mine) + ".")
        pb, since = memory.get("playbook", "standard"), memory.get("playbook_since")
        if since:
            t0, hp0, opp0 = since
            secs = (memory.get("tick", t0) - t0) * 0.3
            opp_now = opp.hits / opp.hits_max if opp.hits_max else opp0
            lines.append(f"Current playbook: {pb}, followed for about {secs:.0f} s — in that time you went "
                         f"{_hp_word(hp0)} to {_hp_word(f.hp_pct)} and the opponent {_hp_word(opp0)} to {_hp_word(opp_now)}.")
        return "\n".join(lines)

    # --- one tick ---------------------------------------------------------------
    def tick(self, agent, obs, f) -> None:
        memory = agent.memory
        tick = memory.get("tick", 0)
        opp_serial = memory.get("duel_opponent")
        landed = self.asker.take()
        if landed is not None:
            _, v, ctx = landed
            a = v.answers.get("playbook")
            if a is not None and a.value in PLAYBOOKS and a.confidence >= self.threshold \
                    and ctx.get("opp") == opp_serial and ctx.get("round") == memory.get("duel_round"):   # never last round's answer
                if a.value != memory.get("playbook"):
                    memory["playbook_since"] = (tick, f.hp_pct, ctx.get("opp_hp", 1.0))
                memory["playbook"] = a.value
                self.log.append((tick, ctx.get("boundary", "?"), a.value, a.confidence))
        if opp_serial is None:
            self._opp = None
            return
        opp = next((m for m in obs.mobiles if m.serial == opp_serial), None)
        if opp is None:
            return
        if opp_serial != self._opp:                   # the bell: a new round starts on the hand rule
            self._opp, self._flags = opp_serial, {}
            memory["playbook"] = "standard"
            memory["playbook_since"] = (tick, f.hp_pct, opp.hits / opp.hits_max if opp.hits_max else 1.0)
            boundary = "round"
            self._boundary(tick, f.hp_pct, obs.player.mana / max(1, obs.player.mana_max), obs.player.poisoned, opp)
        else:
            boundary = self._boundary(tick, f.hp_pct, obs.player.mana / max(1, obs.player.mana_max), obs.player.poisoned, opp)
        if boundary is None or self.asker.busy:
            return
        st = self.state(obs, f, memory, agent.journal_log, agent.proc_log, opp)
        ctx = {"tick": tick, "boundary": boundary, "opp": opp_serial, "round": memory.get("duel_round"),
               "hp": round(f.hp_pct, 3), "opp_hp": round(opp.hits / opp.hits_max, 3) if opp.hits_max else None,
               "mana": obs.player.mana, "playbook": memory.get("playbook", "standard")}
        if self.asker.ask(boundary, st, {"playbook": PLAYBOOK_Q, "momentum": MOMENTUM_Q}, ctx):
            self._last_ask = self._asked[boundary] = tick
            self.boundaries[boundary] = self.boundaries.get(boundary, 0) + 1

    def summary(self) -> dict:
        from collections import Counter
        return {"asks": self.asker.calls, "applied": len(self.log), "late": self.asker.late, "errors": self.asker.errors,
                "playbooks": dict(Counter(pb for _, _, pb, _ in self.log)), "boundaries": dict(self.boundaries),
                "ms": sorted(self.asker.ms)[len(self.asker.ms) // 2] if self.asker.ms else None}
