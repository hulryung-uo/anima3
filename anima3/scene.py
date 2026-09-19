"""Scene rendering: Observation -> a short text a small model can actually read.

This is the accessibility tree of the game, not a screenshot. Numbers that matter
for decisions are stated with their thresholds in words ("low", "adjacent"), because
a 4B model reads *relations* far more reliably than it compares raw integers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .contract import (
    BANDAGE_GRAPHIC,
    GOLD_GRAPHIC,
    INNOCENT_NOTORIETY,
    ORE_GRAPHICS,
    Item,
    Mobile,
    Observation,
)

_NOTO = {1: "innocent", 2: "friend", 3: "attackable", 4: "criminal", 5: "enemy", 6: "murderer", 7: "invulnerable"}
_ITEM_NAMES = {GOLD_GRAPHIC: "gold", BANDAGE_GRAPHIC: "bandages", **{g: "ore" for g in ORE_GRAPHICS}}


@dataclass
class Facts:
    """Derived, machine-checkable facts the affordance rules key off."""

    hp_pct: float
    dead: bool
    poisoned: bool
    war: bool
    hostiles: list[Mobile] = field(default_factory=list)
    people: list[Mobile] = field(default_factory=list)
    ground_loot: list[Item] = field(default_factory=list)
    bandages: Item | None = None
    heard: list[str] = field(default_factory=list)

    @property
    def nearest_hostile(self) -> Mobile | None:
        return self.hostiles[0] if self.hostiles else None


def facts(obs: Observation) -> Facts:
    p = obs.player
    hostiles = sorted((m for m in obs.mobiles if m.hostile), key=lambda m: m.distance)
    people = sorted((m for m in obs.mobiles if m.person and m.notoriety in INNOCENT_NOTORIETY and m.serial != p.serial),
                    key=lambda m: m.distance)
    loot = sorted((i for i in obs.on_ground() if i.graphic in _ITEM_NAMES and i.distance <= 6), key=lambda i: i.distance)
    bandages = next(iter(obs.in_pack(BANDAGE_GRAPHIC)), None)
    heard = [f'{j.name or "someone"}: "{j.text}"' for j in obs.new_journal if j.text][-4:]
    return Facts(hp_pct=p.hp_pct, dead=p.dead, poisoned=p.poisoned, war=obs.war,
                 hostiles=hostiles, people=people, ground_loot=loot, bandages=bandages, heard=heard)


def _hp_word(pct: float) -> str:
    return "critical" if pct < 0.35 else "low" if pct < 0.6 else "hurt" if pct < 0.9 else "healthy"


def _dist_word(d: int) -> str:
    return "adjacent" if d <= 1 else "close" if d <= 3 else "near" if d <= 6 else "far"


def item_name(i: Item) -> str:
    n = _ITEM_NAMES.get(i.graphic, f"item 0x{i.graphic:04X}")
    return f"{i.amount} {n}" if i.amount > 1 else n


def render(obs: Observation, f: Facts, who="") -> str:
    """`who` may be a display string or a Persona (then its temperament is stated too)."""
    p = obs.player
    lines = []
    persona = who if hasattr(who, "who") else None
    name = persona.who if persona else (who or p.name)
    head = f"You are {name}." if name else "You are a character in Ultima Online."
    if f.dead:
        lines.append(head + " You are DEAD (a ghost). You cannot act until resurrected.")
        return "\n".join(lines)
    lines.append(f"{head} Health {p.hits}/{p.hits_max} ({_hp_word(f.hp_pct)}).")
    st = []
    if f.poisoned:
        st.append("You are poisoned.")
    st.append("War mode is on." if f.war else "You are peaceful.")
    st.append(f"You carry {'bandages' if f.bandages else 'no bandages'} and {p.gold} gold.")
    lines.append(" ".join(st))
    if persona is not None and (persona.personality or persona.dislikes):
        bits = []
        if persona.personality:
            bits.append(f"Temperament: {persona.personality}")
        if persona.dislikes:
            bits.append(f"You dislike: {persona.dislikes}.")
        lines.append(" ".join(bits))
    if f.hostiles:
        lines.append("Hostiles: " + "; ".join(
            f"{m.name or 'a creature'} ({_NOTO.get(m.notoriety, 'hostile')}, {_dist_word(m.distance)}, "
            f"{'wounded' if m.hits_max and m.hits < m.hits_max * 0.5 else 'unhurt'})" for m in f.hostiles[:3]))
    else:
        lines.append("No hostiles nearby.")
    if f.people:
        lines.append("People: " + "; ".join(f"{m.name or 'someone'} ({_dist_word(m.distance)})" for m in f.people[:3]))
    if f.ground_loot:
        lines.append("On the ground: " + "; ".join(f"{item_name(i)} ({_dist_word(i.distance)})" for i in f.ground_loot[:3]))
    if f.heard:
        lines.append("Recently heard: " + " | ".join(f.heard))
    return "\n".join(lines)
