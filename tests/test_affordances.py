from anima3.affordances import enumerate_affordances
from anima3.body import FakeBody
from anima3.contract import BANDAGE_GRAPHIC, GOLD_GRAPHIC
from anima3.persona import Persona
from anima3.scene import facts, render

P = Persona(name="T", combat_disposition="neutral", talkativeness=1.0, speech_examples=["hi", "yo"])


def ids(world, persona=P, memory=None):
    obs = world.observe()
    return [a.id for a in enumerate_affordances(obs, facts(obs), persona, memory if memory is not None else {})]


def test_dead_offers_nothing():
    w = FakeBody(); w.player.dead = True
    assert ids(w) == []


def test_critical_hp_with_hostile_only_flee_or_bandage():
    w = FakeBody(); w.player.hits = 10; w.add_hostile(1, 0); w.add_pack_item(BANDAGE_GRAPHIC, 2)
    got = ids(w)
    assert got[0] == "flee" and "bandage" in got and not any(g.startswith("attack") for g in got)


def test_pacifist_is_never_offered_attack():
    w = FakeBody(); w.add_hostile(1, 0)
    got = ids(w, Persona(name="G", combat_disposition="pacifist"))
    assert not any(g.startswith("attack") for g in got) and got[0] == "flee"


def test_defensive_attacks_only_when_close():
    w = FakeBody(); m = w.add_hostile(5, 0)
    far = ids(w, Persona(name="A", combat_disposition="defensive"))
    m.pos = type(m.pos)(w.player.pos.x + 1, w.player.pos.y, 0)
    near = ids(w, Persona(name="A", combat_disposition="defensive"))
    assert not any(g.startswith("attack") for g in far) and near[0].startswith("attack")


def test_peaceful_loot_first_then_greet_then_wander():
    w = FakeBody(); w.add_ground_item(GOLD_GRAPHIC, 1, 0, 5); w.add_person(2, 0)
    got = ids(w)
    assert got[0].startswith("pickup:") and any(g.startswith("say:") for g in got) and any(g.startswith("walk:") for g in got)
    assert got[-1] == "hold"


def test_scene_mentions_threat_and_health_words():
    w = FakeBody(); w.player.hits = 20; w.add_hostile(1, 0)
    obs = w.observe(); f = facts(obs)
    s = render(obs, f, "Grimm, a miner")
    assert "Hostiles:" in s and "adjacent" in s and "low" in s


def test_equip_offered_when_gear_in_pack_and_no_close_threat():
    w = FakeBody(); w.add_pack_item(0x13FF)  # a katana in the pack
    got = ids(w)
    assert got[0].startswith("equip:")
    w.add_hostile(1, 0)
    assert not any(g.startswith("equip:") for g in ids(w))  # not with a hostile adjacent


def test_loot_offered_only_for_own_corpse():
    from anima3.contract import Item, Pos
    w = FakeBody()
    obs = w.observe()
    corpse = Item(0x777, 0x2006, 1, Pos(101, 100, 0), None, 0, 1)
    obs.items.append(corpse)
    from anima3.affordances import enumerate_affordances
    from anima3.scene import facts
    assert not any(a.id.startswith("loot:") for a in enumerate_affordances(obs, facts(obs), P, {}))
    obs.corpse_of = {0x777: 0x999}
    assert any(a.id == "loot:1911" for a in enumerate_affordances(obs, facts(obs), P, {}))


def test_equip_procedure_lifts_then_equips_and_confirms():
    from anima3.agent import Agent
    from anima3.decision import Scripted
    w = FakeBody(); w.add_pack_item(0x13FF)
    ag = Agent(w, Persona(name="W", talkativeness=0), Scripted(), pump_ms=0)
    ag.run(4)
    sent = [a for a in w.log if a["type"] != "AllNames"]
    assert [a["type"] for a in sent[:2]] == ["PickUp", "Equip"] and sent[1]["layer"] == 1
    assert any(v == "ok" for _, pid, v in ag.proc_log if pid.startswith("equip:"))
    assert not any(i.graphic == 0x13FF for i in w.pack) and w.worn


def test_far_threat_does_not_stop_a_healthy_worker():
    w = FakeBody(); w.add_hostile(5, 0)
    mem = {}
    obs = w.observe()
    got = [a.id for a in enumerate_affordances(obs, facts(obs), Persona(name="G", combat_disposition="pacifist"), mem)]
    assert mem.get("threat_far") is True and "flee" in got
    w2 = FakeBody(); w2.add_hostile(2, 0); mem2 = {}
    obs2 = w2.observe(); enumerate_affordances(obs2, facts(obs2), Persona(name="G", combat_disposition="pacifist"), mem2)
    assert not mem2.get("threat_far")


def test_visit_offered_for_a_person_in_the_middle_distance():
    w = FakeBody(); w.add_person(7, 0, "Grimm")
    got = ids(w)
    assert any(g.startswith("visit:") for g in got)
