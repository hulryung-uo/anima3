"""Affordances: the closed vocabulary of verbs that are valid *right now*.

The list is rule-ordered — index 0 is what the rule would do on its own — so the
decision gate can always fall back to it. Hard limits live here, not in the model:
a dead character offers nothing, a critically hurt one may only flee or bandage,
and a pacifist persona is never offered `attack`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .contract import (
    DIRECTION_DELTAS,
    DIRECTION_NAMES,
    Observation,
    Pos,
    attack,
    bandage_target,
    direction_toward,
    pick_up,
    say,
    walk,
    war_mode,
)
from .persona import Persona
from .scene import Facts, item_name


@dataclass(frozen=True)
class Affordance:
    id: str
    description: str
    actions: tuple[dict, ...] = field(default_factory=tuple)

    @property
    def is_hold(self) -> bool:
        return not self.actions


HOLD = Affordance("hold", "Do nothing this moment; watch and wait.")


def _walkable(obs: Observation, x: int, y: int) -> bool:
    t = obs.terrain
    if t is None:
        return True
    ok = t.walkable(x, y)
    return True if ok is None else ok


def _step_options(obs: Observation, away_from: Pos | None = None) -> list[tuple[int, str]]:
    """Directions we can step, ordered: away-from-threat first, else compass order."""
    p = obs.player.pos
    dirs = list(range(8))
    if away_from is not None:
        back = (direction_toward(away_from, p)) % 8
        dirs.sort(key=lambda d: min((d - back) % 8, (back - d) % 8))
    out = []
    for d in dirs:
        dx, dy = DIRECTION_DELTAS[d]
        if _walkable(obs, p.x + dx, p.y + dy):
            out.append((d, DIRECTION_NAMES[d]))
    return out


def enumerate_affordances(obs: Observation, f: Facts, persona: Persona, memory: dict) -> list[Affordance]:
    p = obs.player
    if f.dead:
        return []
    out: list[Affordance] = []
    threat = f.nearest_hostile

    def add_bandage() -> None:
        if f.bandages is not None:
            out.append(Affordance("bandage", "Bandage your own wounds (takes a few seconds).",
                                  (bandage_target(f.bandages.serial, p.serial),)))

    def add_flee() -> None:
        steps = _step_options(obs, away_from=threat.pos if threat else None)
        if steps:
            d, name = steps[0]
            out.append(Affordance("flee", f"Run {name}, away from the threat.", (walk(d, run=True),)))

    if threat is not None:
        if f.hp_pct < 0.35:
            add_flee()
            add_bandage()
            return out or [HOLD]
        can_fight = persona.combat_disposition != "pacifist" and (
            persona.combat_disposition != "defensive" or threat.distance <= 2 or memory.get("engaged") == threat.serial)
        if can_fight:
            acts = (attack(threat.serial),) if f.war else (war_mode(True), attack(threat.serial))
            out.append(Affordance(f"attack:{threat.serial}", f"Attack {threat.name or 'the creature'}.", acts))
        add_flee()
        if f.hp_pct < 0.7:
            add_bandage()
        out.append(HOLD)
        return out

    # Peaceful surroundings.
    if f.war:
        out.append(Affordance("stand_down", "Leave war mode; the fight is over.", (war_mode(False),)))
    for it in f.ground_loot[:2]:
        if it.distance <= 2:
            out.append(Affordance(f"pickup:{it.serial}", f"Pick up the {item_name(it)} at your feet.",
                                  (pick_up(it.serial, it.amount),)))
        else:
            d = direction_toward(p.pos, it.pos)
            if _walkable(obs, p.pos.x + DIRECTION_DELTAS[d][0], p.pos.y + DIRECTION_DELTAS[d][1]):
                out.append(Affordance(f"approach:{it.serial}", f"Walk toward the {item_name(it)}.", (walk(d),)))
    greeted: set[int] = memory.setdefault("greeted", set())
    for m in f.people[:1]:
        if m.distance <= 4 and m.serial not in greeted and persona.talkativeness > 0:
            for k, line in enumerate(persona.speech_examples[:2]):
                out.append(Affordance(f"say:{m.serial}:{k}", f'Say to {m.name or "them"}: "{line}"', (say(line),)))
    if f.hp_pct < 0.6:
        add_bandage()
    for d, name in _step_options(obs)[:4]:
        out.append(Affordance(f"walk:{d}", f"Wander {name}.", (walk(d),)))
    out.append(HOLD)
    # Rule order: loot > greet > heal > wander > hold. HOLD first only if nothing else.
    return out
