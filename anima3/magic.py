"""Magery for the ring: a spell table, the cast procedure, and the mage's verb menu.

Casting is the flow anima2 proved live: `CastSpell{id}` → the server opens a target
cursor → `TargetObject{serial}` → the incantation runs for (4 + circle) × 0.25 s and can
fizzle, be disrupted by damage, or be refused for mana/reagents/recovery. The verdict is
read from the journal (fixed clilocs) or from the mana actually spent.
"""

from __future__ import annotations

from dataclasses import dataclass

from .contract import Observation, target_object, use_skill

# --- reagents (art ids) ---------------------------------------------------------
BLACK_PEARL, BLOODMOSS, GARLIC, GINSENG, MANDRAKE, NIGHTSHADE, SPIDERS_SILK, SULFUROUS_ASH = (
    0x0F7A, 0x0F7B, 0x0F84, 0x0F85, 0x0F86, 0x0F88, 0x0F8D, 0x0F8C)
REAGENT_GRAPHICS = frozenset({BLACK_PEARL, BLOODMOSS, GARLIC, GINSENG, MANDRAKE, NIGHTSHADE, SPIDERS_SILK, SULFUROUS_ASH})
REAGENT_NAMES = {BLACK_PEARL: "BlackPearl", BLOODMOSS: "Bloodmoss", GARLIC: "Garlic", GINSENG: "Ginseng",
                 MANDRAKE: "MandrakeRoot", NIGHTSHADE: "Nightshade", SPIDERS_SILK: "SpidersSilk", SULFUROUS_ASH: "SulfurousAsh"}
SPELLBOOK_GRAPHIC = 0x0EFA
#: Pre-AOS mana by circle.
MANA = {1: 4, 2: 6, 3: 9, 4: 11, 5: 14, 6: 20, 7: 40, 8: 50}


@dataclass(frozen=True)
class Spell:
    key: str
    id: int          # wire id (1-based SpellRegistry index)
    circle: int
    reagents: frozenset[int]
    kind: str        # attack | heal | cure | buff | control
    blurb: str

    @property
    def mana(self) -> int:
        return MANA[self.circle]

    @property
    def cast_ticks(self) -> int:          # (4 + circle) * 0.25 s at ~0.3 s a tick, plus recovery
        return 2 + (4 + self.circle)


SPELLS: dict[str, Spell] = {s.key: s for s in [
    Spell("magic_arrow", 5, 1, frozenset({SULFUROUS_ASH}), "attack", "Magic Arrow: a quick, weak bolt (4 mana)."),
    Spell("harm", 12, 2, frozenset({NIGHTSHADE, SPIDERS_SILK}), "attack", "Harm: instant, hurts more up close (6 mana)."),
    Spell("fireball", 18, 3, frozenset({BLACK_PEARL}), "attack", "Fireball (9 mana)."),
    Spell("lightning", 30, 4, frozenset({MANDRAKE, SULFUROUS_ASH}), "attack", "Lightning: instant, solid damage (11 mana)."),
    Spell("energy_bolt", 42, 6, frozenset({BLACK_PEARL, NIGHTSHADE}), "attack", "Energy Bolt: heavy damage, slow cast (20 mana)."),
    Spell("explosion", 43, 6, frozenset({BLOODMOSS, MANDRAKE}), "attack", "Explosion: heavy damage that lands a moment later (20 mana)."),
    Spell("flamestrike", 51, 7, frozenset({SPIDERS_SILK, SULFUROUS_ASH}), "attack", "Flamestrike: the heaviest blow, very slow (40 mana)."),
    Spell("heal", 4, 1, frozenset({GARLIC, GINSENG, SPIDERS_SILK}), "heal", "Heal: a small quick heal on yourself (4 mana)."),
    Spell("greater_heal", 29, 4, frozenset({GARLIC, GINSENG, MANDRAKE, SPIDERS_SILK}), "heal", "Greater Heal: a large heal on yourself (11 mana)."),
    Spell("cure", 11, 2, frozenset({GARLIC, GINSENG}), "cure", "Cure: remove poison from yourself (6 mana)."),
    Spell("poison", 20, 3, frozenset({NIGHTSHADE}), "control", "Poison: poison the opponent, it keeps hurting (9 mana)."),
    Spell("paralyze", 38, 5, frozenset({GARLIC, MANDRAKE, SPIDERS_SILK}), "control", "Paralyze: freeze the opponent for a few seconds (14 mana)."),
    Spell("magic_reflection", 36, 5, frozenset({GARLIC, MANDRAKE, SPIDERS_SILK}), "buff", "Magic Reflection: bounce the next spell cast at you (14 mana)."),
    Spell("reactive_armor", 7, 1, frozenset({GARLIC, SPIDERS_SILK, SULFUROUS_ASH}), "buff", "Reactive Armor: reflect some melee damage (4 mana)."),
]}
SELF_TARGET = {"heal", "cure", "buff"}

FIZZLE, NO_MANA, NO_REAGENTS, RECOVERING, ALREADY, FROZEN, NO_LOS = 502632, 502625, 502630, 502644, 502642, 502643, 500947
VERDICTS = {FIZZLE: "fizzle", NO_MANA: "no mana", NO_REAGENTS: "no reagents", RECOVERING: "recovering",
            502645: "already casting", ALREADY: "already casting", FROZEN: "frozen", 502646: "frozen",
            NO_LOS: "no line of sight", 502647: "no line of sight", 502629: "cannot cast here", 502628: "hands not free"}


def has_reagents(obs: Observation, spell: Spell) -> bool:
    have = {i.graphic for i in obs.own_pack() if i.graphic in REAGENT_GRAPHICS}
    return spell.reagents <= have


def cast_proc(spell: Spell, target_serial: int):
    def proc(obs0, memory):
        mana0 = obs0.player.mana
        obs = yield {"type": "CastSpell", "spell": spell.id}
        for _ in range(8):
            cl = {j.cliloc for j in obs.new_journal}
            for c, v in VERDICTS.items():
                if c in cl:
                    return v
            if obs.pending_target:
                break
            obs = yield None
        if not obs.pending_target:
            return "no cursor"
        obs = yield target_object(target_serial)
        for _ in range(spell.cast_ticks + 4):
            cl = {j.cliloc for j in obs.new_journal}
            for c, v in VERDICTS.items():
                if c in cl:
                    return v
            if obs.player.mana <= mana0 - spell.mana:
                memory["last_cast"] = (memory.get("tick", 0), spell.key)
                return "ok"
            obs = yield None
        return "timeout"
    return proc


def meditate_proc():
    def proc(obs0, memory):
        mana0 = obs0.player.mana
        obs = yield use_skill(46)
        for _ in range(12):
            if obs.player.mana > mana0 + 2:
                return "ok"
            if any(j.cliloc in (500118, 501846, 502628) for j in obs.new_journal):
                return "refused"
            obs = yield None
        return "timeout"
    return proc


def mage_verbs(obs: Observation, f, memory: dict, threat) -> list:
    """The mage's closed menu in a duel, rule-ordered: heal when low, cure when poisoned,
    the heaviest attack the mana allows, meditate when dry and clear, wrestle when cornered."""
    from .affordances import Affordance, _step_options
    from .contract import attack, walk
    p = obs.player
    mana = p.mana
    out: list[Affordance] = []

    def cast(key: str, why: str = "") -> None:
        s = SPELLS[key]
        if mana >= s.mana and has_reagents(obs, s):
            tgt = p.serial if s.kind in SELF_TARGET else threat.serial
            out.append(Affordance(f"cast:{key}", s.blurb + why, procedure=cast_proc(s, tgt)))

    hp = f.hp_pct
    if hp < 0.5:
        cast("greater_heal", " You are below half.")
    if p.poisoned:
        cast("cure", " You are poisoned.")
    for key in ("energy_bolt", "explosion", "lightning", "fireball", "harm", "magic_arrow"):
        cast(key)
    if hp < 0.8 and not any(a.id == "cast:greater_heal" for a in out):
        cast("greater_heal")
    cast("paralyze")
    cast("poison")
    if memory.get("tick", 0) - memory.get("reflect_at", -999) > 60:
        cast("magic_reflection")
    if mana < 0.4 * max(1, p.mana_max) and threat.distance >= 3:
        out.append(Affordance("meditate", f"Meditate to recover mana ({mana}/{p.mana_max}); the opponent is {threat.distance} tiles off.",
                              procedure=meditate_proc()))
    if threat.distance <= 1:
        out.append(Affordance(f"attack:{threat.serial}", f"Wrestle {threat.name or 'the opponent'} hand to hand.", (attack(threat.serial),)))
    steps = _step_options(obs, away_from=threat.pos)
    if steps and threat.distance <= 2:
        out.append(Affordance("kite", f"Step {steps[0][1]}, out of arm's reach, to cast.", (walk(steps[0][0], run=True),)))
    return out
