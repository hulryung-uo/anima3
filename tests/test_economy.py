from anima3.contract import (
    Gump,
    Item,
    Journal,
    Mobile,
    Observation,
    Player,
    Popup,
    Pos,
    ShopSell,
    ShopSellItem,
)
from anima3.economy import (
    DAGGER_GRAPHIC,
    MINE_SPOT,
    SMITH_GUMP_TITLE,
    SMITH_SPOT,
    craft_once,
    econ_facts,
    economy_affordances,
    mine_once,
    sell_once,
    smelt_once,
)

PACK = 0x4000_0001
BACKPACK = Item(PACK, 0x0E75, 1, Pos(), 1, 0x15, 0)   # worn by player serial 1 on layer 0x15


def obs(pos=MINE_SPOT, items=(), journal=(), pending=False, gumps=(), popup=None, shop_sell=None, gold=0, mobiles=()):
    return Observation(player=Player(serial=1, name="G", pos=pos, hits=50, hits_max=50, gold=gold, weight=10, weight_max=100),
                       mobiles=list(mobiles), items=[BACKPACK, *items], new_journal=list(journal), pending_target=pending,
                       gumps=list(gumps), popup=popup, shop_sell=shop_sell)
def pk(serial, graphic, amount=1):
    return Item(serial, graphic, amount, Pos(), PACK, 0x15, 0)
def gd(serial, graphic, pos, dist):
    return Item(serial, graphic, 1, pos, None, 0, dist)


def drive(gen, observations):
    """Feed observations; return (actions sent, verdict)."""
    sent = []
    step = next(gen)
    sent.append(step)
    try:
        for o in observations:
            step = gen.send(o)
            sent.append(step)
    except StopIteration as done:
        return [a for a in sent if a], done.value
    raise AssertionError("procedure did not finish")


def test_mine_once_uses_tool_then_targets_ground_and_reads_verdict():
    mem = {}
    acts, v = drive(mine_once(0x10, mem), [obs(pending=True), obs(journal=[Journal(0, "", "You dig some iron ore", 0, 0, 1007072)])])
    assert acts[0]["type"] == "Use" and acts[1]["type"] == "TargetGround" and v == "ok"
    acts, v = drive(mine_once(0x10, mem), [obs(pending=True), obs(journal=[Journal(0, "", "no metal", 0, 0, 503040)])])
    assert v == "rotate" and mem["probe"] == 1


def test_smelt_once_confirms_by_ingot_count():
    ing = pk(5, 0x1BF2, 3)
    acts, v = drive(smelt_once(0x20, 0x30), [obs(pending=True, items=[ing]), obs(items=[pk(5, 0x1BF2, 8)])])
    assert acts[1] == {"type": "TargetObject", "serial": 0x30} and v == "ok"


def test_craft_once_presses_category_then_item():
    g = Gump(77, 5, f"xmfhtmlgump 10 10 200 20 {SMITH_GUMP_TITLE} 0 0")
    acts, v = drive(craft_once(0x40, SMITH_GUMP_TITLE, 22, 16, DAGGER_GRAPHIC),
                    [obs(), obs(gumps=[g]), obs(gumps=[g]), obs(items=[pk(9, DAGGER_GRAPHIC, 1)])])
    assert [a["type"] for a in acts] == ["Use", "GumpResponse", "GumpResponse"]
    assert acts[1]["button"] == 22 and acts[2]["button"] == 16 and v == "ok"


def test_craft_once_closes_a_stray_gump_and_reads_failure_from_the_gump():
    stray = Gump(3, 9, "{ page 0 }")
    g = Gump(77, 5, f"xmfhtmlgump 10 10 200 20 {SMITH_GUMP_TITLE} 0 0")
    failed = Gump(78, 5, f"xmfhtmlgump {SMITH_GUMP_TITLE} 0 0 xmfhtmlgump 1044043")
    acts, v = drive(craft_once(0x40, SMITH_GUMP_TITLE, 22, 16, DAGGER_GRAPHIC),
                    [obs(gumps=[stray]), obs(), obs(gumps=[g]), obs(gumps=[g]), obs(gumps=[failed])])
    assert acts[0] == {"type": "GumpResponse", "serial": 3, "gump_id": 9, "button": 0}
    assert v == "failed"


def test_sell_once_walks_the_context_menu_and_confirms_gold():
    pop = Popup(0x99, [{"index": 0, "cliloc": 3006103}, {"index": 1, "cliloc": 3006104}])
    shop = ShopSell(0x99, [ShopSellItem(11, DAGGER_GRAPHIC, 2, 10, "dagger"), ShopSellItem(12, 0x1BF2, 5, 2, "ingot")])
    acts, v = drive(sell_once(0x99, {DAGGER_GRAPHIC}), [obs(popup=pop), obs(shop_sell=shop), obs(gold=20)])
    assert acts[1] == {"type": "PopupSelect", "serial": 0x99, "index": 1}
    assert acts[2]["items"] == [{"serial": 11, "amount": 2}] and v == "ok"


def test_economy_menu_rule_order_and_gating():
    forge = gd(2, 0x0FB1, Pos(2608, 473, 20), 1); anvil = gd(3, 0x0FB0, Pos(2608, 475, 20), 1)
    smith = Mobile(50, "Bob the blacksmith", Pos(2610, 473, 20), 0x190, 1, 40, 40, 1)
    o = obs(pos=SMITH_SPOT, items=[pk(4, 0x0E86), pk(5, 0x0FBB), pk(6, 0x1EB8), pk(7, 0x19B9, 4), pk(8, 0x1BF2, 6), pk(9, DAGGER_GRAPHIC, 2), forge, anvil],
            mobiles=[smith])
    ef = econ_facts(o, {})
    ids = [a.id for a in economy_affordances(o, ef, {})]
    assert ids[0] == "sell:daggers"                       # at a buying vendor with goods
    assert "craft:dagger" in ids and "craft:tongs" in ids and "smelt" in ids
    assert "mine" in ids                                   # the smith spot is 2 tiles from the vein: within reach
    o2 = obs(pos=MINE_SPOT, items=[pk(4, 0x0E86)])
    ids2 = [a.id for a in economy_affordances(o2, econ_facts(o2, {}), {})]
    assert ids2 == ["mine"]


def test_economy_menu_never_empties_and_returns_to_the_ridge():
    far = Pos(2650, 500, 20)
    o = obs(pos=far, items=[pk(4, 0x0E86)])
    ids = [a.id for a in economy_affordances(o, econ_facts(o, {}), {})]
    assert ids == ["goto:mine"]
    o2 = obs(pos=MINE_SPOT, items=[])          # no pickaxe at all
    ids2 = [a.id for a in economy_affordances(o2, econ_facts(o2, {}), {})]
    assert ids2 == ["wait:work"]


def test_goto_uses_walkto_and_reissues_when_stalled():
    from anima3.economy import goto
    o = obs(pos=Pos(2600, 474, 20))
    gen = goto(SMITH_SPOT, 0, o)
    first = next(gen)
    assert first == {"type": "WalkTo", "x": SMITH_SPOT.x, "y": SMITH_SPOT.y}
    sent = [first]
    try:
        for _ in range(13):        # no movement at all -> a re-issue after 12 stale ticks
            sent.append(gen.send(o))
    except StopIteration:
        pass
    assert sum(1 for a in sent if a and a["type"] == "WalkTo") == 2
    try:
        gen.send(obs(pos=SMITH_SPOT))
    except StopIteration as done:
        assert done.value == "arrived"
