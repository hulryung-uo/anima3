from anima3.contract import (
    Observation,
    Terrain,
    attack,
    bandage_target,
    pick_up,
    say,
    walk,
    walk_to,
    war_mode,
)


def test_action_shapes_match_bridge_parser():
    assert walk(2, True) == {"type": "Walk", "dir": 2, "run": True}
    assert walk_to(10, 20) == {"type": "WalkTo", "x": 10, "y": 20}
    assert say("hi") == {"type": "Say", "text": "hi"}
    assert attack(5)["serial"] == 5 and pick_up(7, 3) == {"type": "PickUp", "serial": 7, "amount": 3}
    assert war_mode(True) == {"type": "WarMode", "on": True}
    assert bandage_target(1, 2) == {"type": "BandageTarget", "bandage": 1, "target": 2}


def test_terrain_indexing_row_major_from_origin():
    t = Terrain(origin=(10, 20), side=3, walk=".#....#..")
    assert t.walkable(10, 20) is True and t.walkable(11, 20) is False
    assert t.walkable(10, 22) is False and t.walkable(12, 22) is True
    assert t.walkable(99, 99) is None


def test_observation_from_bridge_like_json_ignores_unknown_keys():
    d = {"player": {"serial": 1, "name": "G", "pos": {"x": 1, "y": 2, "z": 0}, "hits": 5, "hits_max": 10, "extra": 1},
         "mobiles": [{"serial": 2, "name": "a mongbat", "pos": {"x": 3, "y": 2}, "body": 39, "notoriety": 3,
                      "hits": 4, "hits_max": 4, "distance": 2}],
         "items": [{"serial": 3, "graphic": 0x0EED, "amount": 9, "pos": {"x": 1, "y": 3}, "container": None,
                    "layer": 0, "distance": 1}],
         "new_journal": [{"serial": 0, "name": "", "text": "hello", "msg_type": 0, "hue": 0, "cliloc": None}],
         "war": True, "weather": {"kind": 0}, "terrain": None}
    o = Observation.from_json(d)
    assert o.player.hp_pct == 0.5 and o.mobiles[0].hostile and o.on_ground()[0].amount == 9
    assert o.new_journal[0].cliloc == 0 and o.war is True and o.terrain is None


def test_gray_human_is_a_person_not_a_hostile():
    from anima3.contract import Mobile, Pos
    gray_player = Mobile(1, "Bob", Pos(), 0x190, 3, 10, 10, 1)
    murderer = Mobile(2, "Red", Pos(), 0x190, 6, 10, 10, 1)
    gray_rat = Mobile(3, "a rat", Pos(), 0x00EE, 3, 10, 10, 1)
    assert gray_player.person and not gray_player.hostile
    assert murderer.hostile and gray_rat.hostile
