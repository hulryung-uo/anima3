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
