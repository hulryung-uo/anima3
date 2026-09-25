"""The judge layer and the duel's playbooks: typed questions at boundaries, the rule executes."""

import time
from types import SimpleNamespace

from anima3.contract import Mobile, Observation
from anima3.judge import Answer, Asker, Choice, Noul, RandomJudge, Score, Verdict
from anima3.magic import PLAYBOOKS, spell_order
from anima3.tactics import Tactician


def test_mobiles_read_the_schema_32_condition_and_default_without_it():
    m = Mobile.from_json({"serial": 5, "hits": 10, "hits_max": 25, "poisoned": True, "paralyzed": True, "war_mode": True})
    assert m.poisoned and m.paralyzed and m.war_mode and not m.hidden
    old = Mobile.from_json({"serial": 5})            # a schema-31 bridge: no condition keys at all
    assert not (old.poisoned or old.paralyzed or old.war_mode)


def test_standard_is_the_hand_rule_of_experiments_1_to_3():
    got = spell_order("standard", hp=0.45, poisoned=True, opp_paralyzed=False, opp_poisoned=False, reflect_due=True)
    assert got == ["greater_heal", "cure", "energy_bolt", "explosion", "lightning", "fireball", "harm", "magic_arrow",
                   "paralyze", "poison", "magic_reflection"]
    assert spell_order("no-such-playbook", 1.0, False, False, False, False)[0] == "energy_bolt"


def test_playbooks_react_to_what_the_opponent_shows():
    assert spell_order("control", 0.9, False, opp_paralyzed=False, opp_poisoned=False, reflect_due=False)[0] == "paralyze"
    assert spell_order("control", 0.9, False, opp_paralyzed=True, opp_poisoned=False, reflect_due=False)[:2] == ["explosion", "energy_bolt"]
    assert spell_order("poison", 0.9, False, False, opp_poisoned=False, reflect_due=False)[0] == "poison"
    assert spell_order("poison", 0.9, False, False, opp_poisoned=True, reflect_due=False)[0] == "harm"
    assert spell_order("interrupt", 0.9, False, False, False, False)[0] == "magic_arrow"
    assert spell_order("sustain", 0.7, False, False, False, False)[0] == "greater_heal"
    for pb in PLAYBOOKS:     # every playbook keeps the survival floor
        assert spell_order(pb, 0.3, False, False, False, False)[0] == "greater_heal"


class _Instant:
    """A judge that answers at once with a fixed playbook, remembering what it was shown."""
    name = "fake"

    def __init__(self, pick="control", conf=0.9):
        self.pick, self.conf, self.states = pick, conf, []

    def ask(self, state, questions):
        self.states.append((state, sorted(questions)))
        return Verdict({"playbook": Answer(self.pick, {self.pick: self.conf}, self.conf),
                        "momentum": Answer(1.0, {}, 0.5)}, 1.0, self.name)


def _wait(asker):
    for _ in range(100):
        if not asker.busy:
            return
        time.sleep(0.01)


def _world(opp_hits=25, paralyzed=False, poisoned=False):
    obs = Observation.from_json({"player": {"serial": 1, "hits": 50, "hits_max": 50, "mana": 80, "mana_max": 100},
                                 "mobiles": [{"serial": 2, "name": "Rook", "hits": opp_hits, "hits_max": 25, "distance": 4,
                                              "paralyzed": paralyzed, "poisoned": poisoned}]})
    f = SimpleNamespace(hp_pct=1.0, dead=False)
    return obs, f


def _agent(memory):
    return SimpleNamespace(memory=memory, journal_log=[(1, 2, "Corp Por"), (2, 2, "In Vas Mani"), (3, 9, "Corp Por")],
                           proc_log=[(1, "cast:energy_bolt", "ok"), (2, "cast:explosion", "fizzle")])


def test_the_bell_asks_and_the_answer_sets_the_playbook():
    judge = _Instant("control")
    t = Tactician(Asker(judge))
    mem = {"tick": 10, "duel_opponent": 2, "duel_round": 1, "duel_score": (0, 0)}
    obs, f = _world()
    t.tick(_agent(mem), obs, f)
    assert mem["playbook"] == "standard"          # the round starts on the hand rule while the judge thinks
    _wait(t.asker)
    mem["tick"] = 11
    t.tick(_agent(mem), obs, f)
    assert mem["playbook"] == "control" and t.log[0][1] == "round"
    state, qs = judge.states[0]
    assert qs == ["momentum", "playbook"]
    assert "Energy Bolt, Greater Heal" in state      # the opponent's own power words, not a bystander's
    assert "energy bolt ok, explosion fizzle" in state and "Round 1" in state


def test_a_boundary_asks_again_but_the_clock_alone_waits():
    judge = _Instant("interrupt")
    t = Tactician(Asker(judge), every_ticks=30, min_gap=4)
    mem = {"tick": 10, "duel_opponent": 2, "duel_round": 1}
    obs, f = _world()
    t.tick(_agent(mem), obs, f)
    _wait(t.asker)
    mem["tick"] = 16
    t.tick(_agent(mem), obs, f)                     # nothing changed: no second question
    assert len(judge.states) == 1
    obs, f = _world(paralyzed=True)
    mem["tick"] = 17
    t.tick(_agent(mem), obs, f)
    _wait(t.asker)
    assert len(judge.states) == 2 and t.boundaries.get("opp_paralyzed") == 1
    assert "paralyzed" in judge.states[1][0]


def test_last_rounds_answer_never_lands_in_the_next_round():
    judge = _Instant("poison")
    t = Tactician(Asker(judge))
    mem = {"tick": 10, "duel_opponent": 2, "duel_round": 1}
    obs, f = _world()
    t.tick(_agent(mem), obs, f)
    _wait(t.asker)
    mem.update(tick=40, duel_round=2)               # the round ended and the next bell rang before we looked
    t.tick(_agent(mem), obs, f)
    assert mem["playbook"] == "standard"


def test_an_unsure_judge_leaves_the_rule_alone():
    t = Tactician(Asker(_Instant("control", conf=0.05)), threshold=0.15)
    mem = {"tick": 10, "duel_opponent": 2, "duel_round": 1}
    obs, f = _world()
    t.tick(_agent(mem), obs, f)
    _wait(t.asker)
    mem["tick"] = 11
    t.tick(_agent(mem), obs, f)
    assert mem["playbook"] == "standard" and not t.log


def test_asker_drops_a_second_question_while_one_is_in_flight(tmp_path):
    class Slow(_Instant):
        def ask(self, state, questions):
            time.sleep(0.05)
            return super().ask(state, questions)
    a = Asker(Slow(), log_path=tmp_path / "j.jsonl")
    assert a.ask("x", "s", {"playbook": Choice("q", {"a": "A"})})
    assert not a.ask("y", "s", {"playbook": Choice("q", {"a": "A"})})
    _wait(a)
    key, v, _ = a.take()
    assert key == "x" and v.answers["playbook"].value == "control"
    assert (tmp_path / "j.jsonl").read_text().count("\n") == 1


def test_random_judge_answers_every_type():
    v = RandomJudge(seed=1).ask("s", {"c": Choice("q", {"a": "A", "b": "B"}), "n": Noul("q"), "s": Score("q", ["x", "y", "z"])})
    assert v.answers["c"].value in ("a", "b") and v.answers["n"].value in (0.0, 1.0) and v.answers["s"].value == 1.0


def test_a_flickering_flag_is_not_news_every_time():
    judge = _Instant("control")
    t = Tactician(Asker(judge), every_ticks=1000, min_gap=4, same_gap=15)
    mem = {"tick": 10, "duel_opponent": 2, "duel_round": 1}
    t.tick(_agent(mem), *_world())
    _wait(t.asker)
    for tick, frozen in ((20, True), (21, False), (26, True), (27, False), (36, True)):
        mem["tick"] = tick
        t.tick(_agent(mem), *_world(paralyzed=frozen))
        _wait(t.asker)
    assert t.boundaries == {"round": 1, "opp_paralyzed": 2}     # at 20 and again at 36, not at 26
