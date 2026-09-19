"""Skill progression toward 7×GM.

ServUO skills run 0–100.0 (Grandmaster at 100); a character's total is capped
(700 by default = seven GM skills). A *profession* names the seven skills it is
building, and which verbs train each. The curriculum does two things: it puts
the skill gaps into the scene so the model sees what is still short, and it
re-orders the rule's menu so the verb that trains the largest gap comes first.
"""

from __future__ import annotations

from dataclasses import dataclass

from .contract import Observation

GM = 100.0
SKILL_NAMES = {0: "Alchemy", 1: "Anatomy", 2: "AnimalLore", 3: "ItemID", 4: "ArmsLore", 5: "Parry", 6: "Begging",
               7: "Blacksmith", 8: "Fletching", 9: "Peacemaking", 10: "Camping", 11: "Carpentry", 12: "Cartography",
               13: "Cooking", 14: "DetectHidden", 15: "Discordance", 16: "EvalInt", 17: "Healing", 18: "Fishing",
               19: "Forensics", 20: "Herding", 21: "Hiding", 22: "Provocation", 23: "Inscribe", 24: "Lockpicking",
               25: "Magery", 26: "MagicResist", 27: "Tactics", 28: "Snooping", 29: "Musicianship", 30: "Poisoning",
               31: "Archery", 32: "SpiritSpeak", 33: "Stealing", 34: "Tailoring", 35: "AnimalTaming", 36: "TasteID",
               37: "Tinkering", 38: "Tracking", 39: "Veterinary", 40: "Swords", 41: "Macing", 42: "Fencing",
               43: "Wrestling", 44: "Lumberjacking", 45: "Mining", 46: "Meditation", 47: "Stealth", 48: "RemoveTrap",
               49: "Necromancy", 50: "Focus", 51: "Chivalry", 52: "Bushido", 53: "Ninjitsu", 54: "Spellweaving",
               55: "Mysticism", 56: "Imbuing", 57: "Throwing"}
SKILL_IDS = {v: k for k, v in SKILL_NAMES.items()}

#: Which verbs (by id prefix) train which skill. Passive skills gain alongside.
TRAINS: dict[str, tuple[int, ...]] = {
    "mine": (45,),
    "smelt": (45,),
    "craft:dagger": (7,),
    "craft:tongs": (37,),
    "attack": (40, 27, 1),          # Swords, Tactics, Anatomy
    "bandage": (17, 1),             # Healing, Anatomy
    "sell": (), "goto": (), "loot": (), "equip": (),
}

#: The seven skills each profession is building. (ItemID/ArmsLore/Parry are the
#: cheap fillers artisans take; a warrior's seven are the classic sword template.)
PROFESSION_GM: dict[str, tuple[int, ...]] = {
    "miner":      (45, 7, 37, 44, 3, 4, 10),     # Mining, Blacksmith, Tinkering, Lumberjacking, ItemID, ArmsLore, Camping
    "blacksmith": (7, 45, 37, 4, 3, 1, 17),
    "tinker":     (37, 45, 7, 3, 24, 4, 1),
    "warrior":    (40, 27, 1, 17, 5, 26, 21),    # Swords, Tactics, Anatomy, Healing, Parry, MagicResist, Hiding
    "adventurer": (40, 27, 1, 17, 45, 7, 37),    # fights and works: the generalist
}


@dataclass
class SkillGap:
    id: int
    name: str
    base: float
    gap: float           # to GM
    trainable_by: tuple[str, ...]


def gaps(obs: Observation, profession: str) -> list[SkillGap]:
    by_id = {s.id: s for s in obs.skills}
    out = []
    for sid in PROFESSION_GM.get(profession, ()):
        s = by_id.get(sid)
        base = s.base if s else 0.0
        verbs = tuple(v for v, ids in TRAINS.items() if sid in ids)
        out.append(SkillGap(sid, SKILL_NAMES.get(sid, str(sid)), base, max(0.0, GM - base), verbs))
    return out


def gm_count(obs: Observation, profession: str) -> tuple[int, int]:
    g = gaps(obs, profession)
    return sum(1 for x in g if x.gap <= 0.0), len(g)


def progress_scene(obs: Observation, profession: str) -> str:
    g = gaps(obs, profession)
    if not g:
        return ""
    done, total = gm_count(obs, profession)
    parts = [f"{x.name} {x.base:.1f}" + ("/GM" if x.gap <= 0 else "") for x in g]
    focus = next((x for x in sorted(g, key=lambda x: -x.gap) if x.trainable_by and x.gap > 0), None)
    line = f"Skills ({done}/{total} at Grandmaster): " + ", ".join(parts) + "."
    if focus:
        line += f" Most room to grow by working: {focus.name} ({focus.gap:.1f} to go)."
    return line


def curriculum_key(obs: Observation, profession: str):
    """A sort key: verbs that train the largest remaining gap come first; others keep order."""
    g = {x.id: x.gap for x in gaps(obs, profession)}

    def key(aff_id: str) -> float:
        best = 0.0
        for prefix, ids in TRAINS.items():
            if aff_id.startswith(prefix):
                best = max([g.get(i, 0.0) for i in ids] or [0.0])
        return -best
    return key


def training_delta(before: Observation, after: Observation) -> dict[str, float]:
    """Skill base changes between two observations, by name (only non-zero)."""
    b = {s.id: s.base for s in before.skills}
    out = {}
    for s in after.skills:
        d = s.base - b.get(s.id, s.base)
        if abs(d) >= 0.05:
            out[SKILL_NAMES.get(s.id, str(s.id))] = round(d, 1)
    return out
