"""The production economy: mine → smelt → craft → sell, as multi-tick procedures
behind closed verbs.

Mechanics are deterministic code (packets do not tolerate improvisation); the
model only chooses *which* admissible verb comes next — mine more, smelt now,
craft, or walk to the vendor. Every constant below is ServUO-confirmed, most of
them live-proven by anima2 on this same ridge.
"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any

from .contract import (
    DIRECTION_DELTAS,
    Item,
    Observation,
    Pos,
    chebyshev,
    direction_toward,
    gump_response,
    popup_request,
    popup_select,
    sell_items,
    target_ground,
    target_object,
    use,
    walk,
)

# --- Items (graphics) ----------------------------------------------------------
PICKAXE_GRAPHICS = frozenset({0x0E85, 0x0E86, 0x0F39, 0x0F3A})
ORE_GRAPHICS = frozenset({0x19B7, 0x19B8, 0x19B9, 0x19BA})
INGOT_GRAPHICS = frozenset({0x1BEF, 0x1BF0, 0x1BF1, 0x1BF2})
SMITH_TOOL_GRAPHICS = frozenset({0x13E3, 0x0FBB, 0x0FBC})      # hammer / tongs
TONGS_GRAPHICS = frozenset({0x0FBB, 0x0FBC})
TINKER_TOOL_GRAPHICS = frozenset({0x1EB8, 0x1EBC})
DAGGER_GRAPHIC = 0x0F52
GOLD_GRAPHIC = 0x0EED
FORGE_GRAPHICS = frozenset({0x0FB1, 0x2DD8, 0xA531, 0xA535}) | frozenset(range(0x197A, 0x19AA))
ANVIL_GRAPHICS = frozenset({0x0FAF, 0x0FB0})
SELLABLE = {DAGGER_GRAPHIC: "dagger", 0x0FBB: "tongs", 0x0FBC: "tongs"}

# --- Server verdicts (cliloc ids) ---------------------------------------------
MINE_PRODUCTIVE = frozenset({503043, 503042, *range(1007072, 1007081)})
MINE_NO_RESOURCE = frozenset({503040})
MINE_INVALID = frozenset({501862, 500237, 500446})
PACK_FULL = frozenset({1010481})
SMELT_OK, SMELT_TOO_SMALL, SMELT_TOO_HARD, SMELT_IMPURE = 501988, 501987, 501986, 501990
CRAFT_NO_METAL, CRAFT_FAIL, CRAFT_FAIL_NO_LOSS, CRAFT_PROXIMITY = 1044037, 1044043, 1044157, 1044267
CRAFT_MADE = 1044154  # "You create the item." (CraftGump status line)
SMITH_GUMP_TITLE, TINKER_GUMP_TITLE = 1044002, 1044007
SELL_CLILOC = 3_006_104

# --- Craft gump buttons: 1 + type + index*7 (ServUO CraftGump) -----------------
SMITH_CATEGORY_BLADED, SMITH_DAGGER = 22, 16
TINKER_CATEGORY_TOOLS, TINKER_TONGS, TINKER_SCISSORS = 15, 86, 2
DAGGER_COST, TONGS_COST = 3, 1

# --- The Minoc ridge (z=20), calibrated live by anima2 --------------------------
MINE_SPOT = Pos(2611, 474, 20)
SMITH_SPOT = Pos(2609, 474, 20)
FORGE_SPOT = Pos(2608, 473, 20)
ANVIL_SPOT = Pos(2608, 475, 20)
SMITH_VENDOR_SPOT = Pos(2610, 473, 20)
TINKER_VENDOR_SPOT = Pos(2610, 475, 20)
REACH = 2
PROBE_RING = [(dx, dy) for dx in (-1, 0, 1, -2, 2) for dy in (-1, 0, 1, -2, 2) if (dx, dy) != (0, 0)]


@dataclass
class EconFacts:
    ore: int = 0
    ingots: int = 0
    daggers: int = 0
    tongs: int = 0
    gold: int = 0
    weight_pct: float = 0.0
    pickaxe: Item | None = None
    smith_tool: Item | None = None
    tinker_tool: Item | None = None
    forge: Item | None = None
    anvil: Item | None = None
    vendors: dict[str, Any] = field(default_factory=dict)   # name -> Mobile
    sellables: list[Item] = field(default_factory=list)
    at_mine: int = 99
    at_smith: int = 99

    @property
    def forge_near(self) -> bool:
        return self.forge is not None and self.forge.distance <= REACH

    @property
    def anvil_near(self) -> bool:
        return self.anvil is not None and self.anvil.distance <= REACH


def econ_facts(obs: Observation, memory: dict) -> EconFacts:
    p = obs.player
    unsellable = memory.get("unsellable", set())
    pack = obs.own_pack()
    ground = [i for i in obs.items if i.container is None]
    f = EconFacts(gold=p.gold, weight_pct=(p.weight / p.weight_max) if p.weight_max else 0.0)
    f.ore = sum(i.amount for i in pack if i.graphic in ORE_GRAPHICS)
    f.ingots = sum(i.amount for i in pack if i.graphic in INGOT_GRAPHICS)
    f.daggers = sum(i.amount for i in pack if i.graphic == DAGGER_GRAPHIC and i.serial not in unsellable)
    f.tongs = sum(i.amount for i in pack if i.graphic in TONGS_GRAPHICS and i.serial not in unsellable)
    if f.smith_tool is not None and f.smith_tool.graphic in TONGS_GRAPHICS:
        f.tongs -= 1  # one pair stays as the smithing tool
    f.pickaxe = next((i for i in pack if i.graphic in PICKAXE_GRAPHICS), None)
    f.smith_tool = (next((i for i in pack if i.graphic in TONGS_GRAPHICS), None)
                    or next((i for i in pack if i.graphic in SMITH_TOOL_GRAPHICS), None))
    f.tinker_tool = next((i for i in pack if i.graphic in TINKER_TOOL_GRAPHICS), None)
    f.forge = min((i for i in ground if i.graphic in FORGE_GRAPHICS), key=lambda i: i.distance, default=None)
    f.anvil = min((i for i in ground if i.graphic in ANVIL_GRAPHICS), key=lambda i: i.distance, default=None)
    f.sellables = [i for i in pack if i.graphic in SELLABLE]
    names = memory.get("names", {})
    for m in obs.mobiles:
        n = (m.name or names.get(m.serial, "")).lower()
        for key in ("blacksmith", "tinker"):
            if key in n and (key not in f.vendors or m.distance < f.vendors[key].distance):
                f.vendors[key] = m
    f.at_mine = chebyshev(p.pos, MINE_SPOT)
    f.at_smith = chebyshev(p.pos, SMITH_SPOT)
    return f


def _dist_word(d: int) -> str:
    return "here" if d == 0 else "adjacent" if d <= 1 else "within reach" if d <= 2 else f"{d} tiles away"


def econ_scene(f: EconFacts) -> list[str]:
    tools = [n for n, t in (("a pickaxe", f.pickaxe), ("smith's tongs", f.smith_tool), ("tinker tools", f.tinker_tool)) if t]
    lines = [(f"Pack: {f.ore} ore, {f.ingots} iron ingots, {f.daggers} daggers, {f.tongs} tongs for sale; "
              f"weight {f.weight_pct:.0%} of capacity. Tools: {', '.join(tools) or 'none'}.")]
    places = [f"the ore vein is {_dist_word(f.at_mine)}"]
    if f.forge:
        places.append(f"the forge is {_dist_word(f.forge.distance)}")
    if f.anvil:
        places.append(f"the anvil is {_dist_word(f.anvil.distance)}")
    for key, m in f.vendors.items():
        places.append(f"the {key} vendor is {_dist_word(m.distance)}")
    lines.append("Places: " + "; ".join(places) + ".")
    return lines


# --- Procedures: generators that receive a fresh Observation each tick ---------
# `yield action_dict` sends an action; `yield None` just waits a tick.
# `return "verdict"` ends the procedure.
Proc = Generator[dict | None, Observation, str]


def _clilocs(obs: Observation) -> set[int]:
    return {j.cliloc for j in obs.new_journal}


def _await(obs: Observation, pred, ticks: int) -> Generator[None, Observation, Observation | None]:
    for _ in range(ticks):
        if pred(obs):
            return obs
        obs = yield None
    return None


def mine_once(tool_serial: int, memory: dict) -> Proc:
    obs = yield use(tool_serial)
    obs = yield from _await(obs, lambda o: o.pending_target, 8)
    if obs is None:
        return "no cursor"
    k = memory.get("probe", 0)
    dx, dy = PROBE_RING[k % len(PROBE_RING)]
    p = obs.player.pos
    obs = yield target_ground(p.x + dx, p.y + dy, p.z, 0)
    for _ in range(16):
        cl = _clilocs(obs)
        if cl & MINE_PRODUCTIVE:
            return "ok"
        if cl & PACK_FULL:
            return "pack full"
        if cl & (MINE_NO_RESOURCE | MINE_INVALID):
            memory["probe"] = k + 1
            return "rotate"
        obs = yield None
    memory["probe"] = k + 1
    return "timeout"


def smelt_once(ore_serial: int, forge_serial: int) -> Proc:
    obs = yield use(ore_serial)
    obs = yield from _await(obs, lambda o: o.pending_target, 8)
    if obs is None:
        return "no cursor"
    before = sum(i.amount for i in obs.own_pack() if i.graphic in INGOT_GRAPHICS)
    obs = yield target_object(forge_serial)
    for _ in range(10):
        now = sum(i.amount for i in obs.own_pack() if i.graphic in INGOT_GRAPHICS)
        cl = _clilocs(obs)
        if now > before or SMELT_OK in cl:
            return "ok"
        if SMELT_IMPURE in cl:
            return "ok (impure)"
        if cl & {SMELT_TOO_SMALL, SMELT_TOO_HARD}:
            return "cannot smelt"
        obs = yield None
    return "timeout"


def craft_once(tool_serial: int, title_cliloc: int, category_btn: int, item_btn: int,
               output_graphics) -> Proc:
    outs = {output_graphics} if isinstance(output_graphics, int) else set(output_graphics)

    def count(o: Observation) -> int:
        return sum(i.amount for i in o.own_pack() if i.graphic in outs)

    obs = yield None
    for g in list(obs.gumps):  # a craft gump left open blocks every other tool
        obs = yield gump_response(g.serial, g.gump_id, 0)
    obs = yield use(tool_serial)
    obs = yield from _await(obs, lambda o: any(g.has_cliloc(title_cliloc) for g in o.gumps), 10)
    if obs is None:
        return "no gump"
    g = next(g for g in obs.gumps if g.has_cliloc(title_cliloc))
    before = count(obs)
    obs = yield gump_response(g.serial, g.gump_id, category_btn)
    obs = yield from _await(obs, lambda o: any(x.has_cliloc(title_cliloc) for x in o.gumps), 10)
    if obs is None:
        return "no category page"
    g = next(x for x in obs.gumps if x.has_cliloc(title_cliloc))
    obs = yield gump_response(g.serial, g.gump_id, item_btn)
    for _ in range(14):
        cl = _clilocs(obs)
        # ServUO's CraftGump reports the outcome inside the re-shown gump, not the journal
        for x in obs.gumps:
            if x.has_cliloc(title_cliloc):
                cl |= {c for c in (CRAFT_NO_METAL, CRAFT_FAIL, CRAFT_FAIL_NO_LOSS, CRAFT_PROXIMITY, CRAFT_MADE) if x.has_cliloc(c)}
        if count(obs) > before or CRAFT_MADE in cl:
            return "ok"
        if CRAFT_NO_METAL in cl:
            return "not enough metal"
        if CRAFT_PROXIMITY in cl:
            return "need forge and anvil"
        if cl & {CRAFT_FAIL, CRAFT_FAIL_NO_LOSS}:
            return "failed"
        obs = yield None
    return "timeout"


def sell_once(vendor_serial: int, graphics: set[int], memory: dict | None = None, keep: int | None = None) -> Proc:
    memory = {} if memory is None else memory
    obs = yield popup_request(vendor_serial)
    obs = yield from _await(obs, lambda o: o.popup is not None and o.popup.serial == vendor_serial, 10)
    if obs is None:
        return "no popup"
    idx = obs.popup.index_of(SELL_CLILOC, "sell")
    if idx is None:
        return "vendor does not buy"
    gold0 = obs.player.gold
    obs = yield popup_select(vendor_serial, idx)
    obs = yield from _await(obs, lambda o: o.shop_sell is not None and o.shop_sell.vendor == vendor_serial, 12)
    if obs is None:
        return "no sell window"
    offer = [(i.serial, i.amount) for i in obs.shop_sell.items if i.graphic in graphics and i.serial != keep]
    if not offer:
        listed = {i.serial for i in obs.shop_sell.items}
        memory.setdefault("unsellable", set()).update(
            i.serial for i in obs.own_pack() if i.graphic in graphics and i.serial not in listed)
        return "nothing they want"
    obs = yield sell_items(vendor_serial, offer)
    obs = yield from _await(obs, lambda o: o.player.gold > gold0, 12)
    return "ok" if obs is not None else "unconfirmed"


def goto(target: Pos, reach: int, obs0: Observation) -> Proc:
    obs = obs0
    for _ in range(60):
        p = obs.player.pos
        if chebyshev(p, target) <= reach:
            return "arrived"
        d = direction_toward(p, target)
        dx, dy = DIRECTION_DELTAS[d]
        t = obs.terrain
        if t is not None and t.walkable(p.x + dx, p.y + dy) is False:
            # try the two neighbouring directions
            for alt in ((d + 1) % 8, (d - 1) % 8):
                ax, ay = DIRECTION_DELTAS[alt]
                if t.walkable(p.x + ax, p.y + ay) is not False:
                    d = alt
                    break
            else:
                return "blocked"
        obs = yield walk(d, run=True)
    return "gave up"


# --- The economy verb menu -------------------------------------------------------
def economy_affordances(obs: Observation, ef: EconFacts, memory: dict, *, batch: int = 3):
    """Rule-ordered economy verbs. Index 0 is what the rule would do alone."""
    from .affordances import Affordance  # local import: affordances imports nothing from here

    out = []
    smith = ef.vendors.get("blacksmith")
    tinker = ef.vendors.get("tinker")

    # 1. sell finished goods when standing at a buying vendor
    if ef.daggers and smith is not None and smith.distance <= REACH:
        out.append(Affordance("sell:daggers", f"Sell your {ef.daggers} daggers to the blacksmith.",
                              procedure=lambda o, m, v=smith.serial: sell_once(v, {DAGGER_GRAPHIC}, m)))
    if ef.tongs and tinker is not None and tinker.distance <= REACH:
        out.append(Affordance("sell:tongs", f"Sell your {ef.tongs} tongs to the tinker.",
                              procedure=lambda o, m, v=tinker.serial, k=(ef.smith_tool.serial if ef.smith_tool else None): sell_once(v, set(TONGS_GRAPHICS), m, k)))
    # 2. walk to a vendor once a batch is ready
    if ef.daggers >= batch and smith is not None and smith.distance > REACH:
        out.append(Affordance("goto:blacksmith", "Walk to the blacksmith vendor to sell daggers.",
                              procedure=lambda o, m, t=smith.pos: goto(t, REACH, o)))
    if ef.tongs >= batch and tinker is not None and tinker.distance > REACH:
        out.append(Affordance("goto:tinker", "Walk to the tinker vendor to sell tongs.",
                              procedure=lambda o, m, t=tinker.pos: goto(t, REACH, o)))
    # 3. craft
    if ef.tinker_tool and ef.ingots >= TONGS_COST:
        out.append(Affordance("craft:tongs", f"Craft tongs with the tinker tools (uses {TONGS_COST} ingot; you have {ef.ingots}).",
                              procedure=lambda o, m, t=ef.tinker_tool.serial: craft_once(
                                  t, TINKER_GUMP_TITLE, TINKER_CATEGORY_TOOLS, TINKER_TONGS, TONGS_GRAPHICS)))
    if ef.smith_tool and ef.ingots >= DAGGER_COST and ef.forge_near and ef.anvil_near:
        out.append(Affordance("craft:dagger", f"Forge a dagger at the anvil (uses {DAGGER_COST} ingots; you have {ef.ingots}).",
                              procedure=lambda o, m, t=ef.smith_tool.serial: craft_once(
                                  t, SMITH_GUMP_TITLE, SMITH_CATEGORY_BLADED, SMITH_DAGGER, DAGGER_GRAPHIC)))
    # 4. smelt
    piles = [i for i in obs.own_pack() if i.graphic in ORE_GRAPHICS and i.amount >= 2]
    if piles and ef.forge_near:
        ore = max(piles, key=lambda i: i.amount)
        out.append(Affordance("smelt", f"Smelt your {ef.ore} ore into ingots at the forge.",
                              procedure=lambda o, m, s=ore.serial, f=ef.forge.serial: smelt_once(s, f)))
    if (ef.ore or (ef.ingots >= DAGGER_COST and ef.smith_tool)) and ef.forge is not None and not ef.forge_near:
        out.append(Affordance("goto:forge", "Walk to the forge and anvil.",
                              procedure=lambda o, m: goto(SMITH_SPOT, 0, o)))
    # 5. mine
    if ef.pickaxe and ef.weight_pct < 0.9:
        if ef.at_mine <= REACH:
            out.append(Affordance("mine", "Swing the pickaxe at the rock for more ore.",
                                  procedure=lambda o, m, t=ef.pickaxe.serial: mine_once(t, m)))
        else:
            out.append(Affordance("goto:mine", "Walk to the ore vein.",
                                  procedure=lambda o, m: goto(MINE_SPOT, 0, o)))
    # a verb that just failed waits its backoff out (see Agent: 40 ticks)
    backoff = memory.get("backoff", {})
    now = memory.get("tick", 0)
    out = [a for a in out if backoff.get(a.id, -1) <= now]
    # rule order: sell > goto vendor > craft > smelt > goto forge > mine
    order = ["sell:", "goto:blacksmith", "goto:tinker", "craft:", "smelt", "goto:forge", "mine", "goto:mine"]
    out.sort(key=lambda a: next((k for k, pre in enumerate(order) if a.id.startswith(pre)), 99))
    return out
