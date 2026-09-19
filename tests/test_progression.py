from anima3.contract import Observation, Player, Skill
from anima3.progression import curriculum_key, gaps, gm_count, progress_scene, training_delta


def obs(**base):
    from anima3.progression import SKILL_IDS
    return Observation(player=Player(serial=1), skills=[Skill(SKILL_IDS[k], v, v, 100.0, 0) for k, v in base.items()])


def test_gaps_and_gm_count():
    o = obs(Mining=100.0, Blacksmith=45.0, Tinkering=76.0)
    g = {x.name: x.gap for x in gaps(o, "miner")}
    assert g["Mining"] == 0.0 and g["Blacksmith"] == 55.0 and g["ItemID"] == 100.0
    assert gm_count(o, "miner") == (1, 7)


def test_progress_scene_names_the_focus_skill():
    line = progress_scene(obs(Mining=90.0, Blacksmith=45.0, Tinkering=76.0), "miner")
    assert "Blacksmith 45.0" in line and "Most room to grow by working: Blacksmith" in line


def test_curriculum_orders_the_verb_that_trains_the_largest_gap_first():
    o = obs(Mining=95.0, Blacksmith=45.0, Tinkering=76.0)
    key = curriculum_key(o, "miner")
    ids = sorted(["mine", "craft:tongs", "craft:dagger", "sell:tongs"], key=key)
    assert ids[0] == "craft:dagger" and ids[1] == "craft:tongs" and ids[-1] == "sell:tongs"


def test_training_delta_reports_only_changes():
    a, b = obs(Mining=45.0, Blacksmith=45.0), obs(Mining=45.3, Blacksmith=45.0)
    assert training_delta(a, b) == {"Mining": 0.3}
