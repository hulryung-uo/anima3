from anima3.contract import Observation, Player, Skill
from anima3.progression import (
    curriculum_key,
    gaps,
    gm_count,
    progress_scene,
    train_verbs,
    training_delta,
)


def obs(**base):
    from anima3.progression import SKILL_IDS
    return Observation(player=Player(serial=1), skills=[Skill(SKILL_IDS[k], v, v, 100.0, 0) for k, v in base.items()])


def test_gaps_and_gm_count():
    o = obs(Mining=100.0, Blacksmith=45.0, Tinkering=76.0)
    g = {x.name: x.gap for x in gaps(o, "miner")}
    assert g["Mining"] == 0.0 and g["Blacksmith"] == 55.0 and g["ItemID"] == 100.0
    assert gm_count(o, "miner") == (1, 7)


def test_progress_scene_names_the_focus_skill():
    line = progress_scene(obs(Mining=90.0, Blacksmith=45.0, Tinkering=76.0, ArmsLore=80.0, ItemID=80.0, Hiding=80.0, Meditation=80.0), "miner")
    assert "Blacksmith 45.0" in line and "Most room to grow: Blacksmith" in line


def test_curriculum_orders_the_verb_that_trains_the_largest_gap_first():
    o = obs(Mining=95.0, Blacksmith=45.0, Tinkering=76.0)
    key = curriculum_key(o, "miner")
    ids = sorted(["mine", "craft:tongs", "craft:dagger", "sell:tongs"], key=key)
    assert ids[0] == "craft:dagger" and ids[1] == "craft:tongs" and ids[-1] == "sell:tongs"


def test_training_delta_reports_only_changes():
    a, b = obs(Mining=45.0, Blacksmith=45.0), obs(Mining=45.3, Blacksmith=45.0)
    assert training_delta(a, b) == {"Mining": 0.3}


def test_train_verbs_largest_gap_first_and_rate_limited():
    from anima3.body import FakeBody
    from anima3.contract import BANDAGE_GRAPHIC
    w = FakeBody(); w.add_pack_item(BANDAGE_GRAPHIC, 5)
    o = w.observe(); o.skills = obs(Mining=60.0, Hiding=10.0, Meditation=40.0, ArmsLore=0.0, ItemID=50.0).skills
    mem = {"tick": 100}
    ids = [a.id for a in train_verbs(o, "miner", mem)]
    assert ids == ["train:ArmsLore", "train:Hiding"]
    mem["train_next"] = 200
    assert train_verbs(o, "miner", mem) == []


def test_train_procedure_uses_skill_then_targets_when_needed():
    from anima3.body import FakeBody
    from anima3.progression import _train_proc
    w = FakeBody(); o = w.observe()
    gen = _train_proc(21, "none", None)(o, {"tick": 5}); first = next(gen)
    assert first == {"type": "UseSkill", "skill": 21}
    gen2 = _train_proc(4, "item", 0x77)(o, {"tick": 5}); assert next(gen2)["type"] == "UseSkill"
    o.pending_target = True
    assert gen2.send(o) == {"type": "TargetObject", "serial": 0x77}
