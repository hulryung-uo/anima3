"""Affordances: the closed vocabulary of verbs that are valid *right now*.

The list is rule-ordered — index 0 is what the rule would do on its own — so the
decision gate can always fall back to it. Hard limits live here, not in the model:
a dead character offers nothing, a critically hurt one may only flee or bandage,
and a pacifist persona is never offered `attack`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .contract import (
    DIRECTION_DELTAS,
    DIRECTION_NAMES,
    Observation,
    Pos,
    attack,
    bandage_target,
    direction_toward,
    drop,
    equip,
    pick_up,
    say,
    use,
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
    #: A multi-tick procedure factory `(obs, memory) -> generator`; when set, the
    #: agent runs it to completion instead of emitting `actions` once.
    procedure: Any = None

    @property
    def is_hold(self) -> bool:
        return not self.actions and self.procedure is None


HOLD = Affordance("hold", "Do nothing this moment; watch and wait.")

#: Gear worth wearing, by graphic -> (name, equip layer). Layers per ServUO (anima2 live-proven).
GEAR_GRAPHICS = {0x13FF: ("katana", 0x01), 0x1415: ("plate chest", 0x0D), 0x1411: ("plate legs", 0x04),
                 0x1410: ("plate arms", 0x13), 0x1413: ("plate gorget", 0x0A), 0x1414: ("plate gloves", 0x07),
                 0x1412: ("plate helm", 0x06)}


def _equip_proc(serial: int, layer: int):
    """UO equips in two packets: lift the item (PickUp), then EquipReq on the layer."""
    def proc(obs0, memory):
        obs = yield pick_up(serial, 1)
        obs = yield equip(serial, layer)
        for _ in range(6):
            if not any(i.serial == serial for i in obs.own_pack()):
                return "ok"
            obs = yield None
        memory.setdefault("equip_failed", set()).add(serial)
        return "failed"
    return proc
CORPSE_GRAPHIC = 0x2006
GOLD = 0x0EED


def _unequipped_gear(obs: Observation, memory: dict):
    failed = memory.get("equip_failed", set())
    return [i for i in obs.own_pack() if i.graphic in GEAR_GRAPHICS and i.serial not in failed]


def _take_proc(serial: int, amount: int):
    """A UO pickup is two packets: lift onto the cursor, then drop into the backpack."""
    def proc(obs0, memory):
        bp = obs0.backpack_serial()
        gold0 = obs0.player.gold
        obs = yield pick_up(serial, amount)
        obs = yield drop(serial, bp)
        for _ in range(6):
            if obs.player.gold > gold0 or any(i.serial == serial for i in obs.own_pack()):
                return "ok"
            obs = yield None
        return "unconfirmed"
    return proc


def _loot_proc(corpse_serial: int):
    """Open the corpse, then lift its gold and drop it into the backpack."""
    def proc(obs0, memory):
        obs = yield use(corpse_serial)
        for _ in range(8):
            gold = [i for i in obs.items if i.container == corpse_serial and i.graphic == GOLD]
            if gold:
                memory.setdefault("looted", set()).add(corpse_serial)
                verdict = yield from _take_proc(gold[0].serial, gold[0].amount)(obs, memory)
                return verdict
            obs = yield None
        memory.setdefault("looted", set()).add(corpse_serial)
        return "empty"
    return proc


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

    # Gear first: a sword in the pack is worth one tick even with a threat a few tiles out.
    gear = _unequipped_gear(obs, memory)
    if gear and f.hp_pct >= 0.35 and (threat is None or threat.distance > 1):
        g = gear[0]
        name, layer = GEAR_GRAPHICS[g.graphic]
        out.append(Affordance(f"equip:{g.serial}", f"Put on the {name}.", procedure=_equip_proc(g.serial, layer)))
    if threat is not None:
        if f.hp_pct < 0.35:
            add_flee()
            add_bandage()
            return out or [HOLD]
        can_fight = persona.combat_disposition != "pacifist" and (
            persona.combat_disposition != "defensive" or threat.distance <= 2 or memory.get("engaged") == threat.serial)
        if can_fight:
            who = threat.name or "the creature"
            if threat.distance <= 1:
                acts = (attack(threat.serial),) if f.war else (war_mode(True), attack(threat.serial))
                out.append(Affordance(f"attack:{threat.serial}", f"Attack {who}.", acts))
            else:
                d = direction_toward(p.pos, threat.pos)
                dx, dy = DIRECTION_DELTAS[d]
                if _walkable(obs, p.pos.x + dx, p.pos.y + dy):
                    acts = (walk(d, run=True),) if f.war else (war_mode(True), walk(d, run=True))
                    out.append(Affordance(f"attack:{threat.serial}", f"Close in on {who} and fight.", acts))
        add_flee()
        if f.hp_pct < 0.7:
            add_bandage()
        out.append(HOLD)
        return out

    # Peaceful surroundings.
    if f.war:
        out.append(Affordance("stand_down", "Leave war mode; the fight is over.", (war_mode(False),)))
    looted: set[int] = memory.setdefault("looted", set())
    my_corpses: set[int] = memory.setdefault("my_corpses", set())
    my_corpses.update(obs.corpse_of)
    mine = [i for i in obs.items if i.graphic == CORPSE_GRAPHIC and i.container is None
            and i.serial in my_corpses and i.serial not in looted and i.distance <= 6]
    for c in sorted(mine, key=lambda i: i.distance)[:1]:
        if c.distance <= 2:
            out.append(Affordance(f"loot:{c.serial}", "Loot the corpse of your kill.", procedure=_loot_proc(c.serial)))
        else:
            d = direction_toward(p.pos, c.pos)
            out.append(Affordance(f"loot:{c.serial}", "Walk to the corpse of your kill.", (walk(d),)))
    for it in f.ground_loot[:2]:
        if it.distance <= 2:
            out.append(Affordance(f"pickup:{it.serial}", f"Pick up the {item_name(it)} at your feet.",
                                  procedure=_take_proc(it.serial, it.amount)))
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
