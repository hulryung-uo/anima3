import json

from anima3.agent import Agent
from anima3.body import FakeBody
from anima3.contract import BANDAGE_GRAPHIC, GOLD_GRAPHIC
from anima3.decision import Decision, Scripted
from anima3.persona import Persona


def world_hostile():
    w = FakeBody(); w.add_pack_item(BANDAGE_GRAPHIC, 5); w.add_hostile(6, 0); w.add_ground_item(GOLD_GRAPHIC, 1, 1, 30)
    return w


def test_rule_only_agent_survives_and_kills_or_flees():
    w = world_hostile()
    ag = Agent(w, Persona(name="W", combat_disposition="neutral"), Scripted(), pump_ms=0)
    ag.run(40)
    assert not w.player.dead
    assert any(m.hits <= 0 for m in w.mobiles) or w.player.hits > 0


class AlwaysHold:
    name = "hold"

    def choose(self, scene, question, options):
        return Decision("hold", {"hold": 1.0}, 1.0, 1.0, self.name)  # insists, even when not on the menu


def test_model_cannot_choose_outside_hard_limits(tmp_path):
    """Even a model that always says 'hold' cannot hold at critical HP next to a hostile:
    the menu shrinks to flee/bandage and 'hold' is not in it."""
    w = FakeBody(); w.player.hits = 10; w.add_hostile(1, 0); w.add_pack_item(BANDAGE_GRAPHIC, 3)
    ag = Agent(w, Persona(name="W"), AlwaysHold(), sync=True, pump_ms=0, log_path=tmp_path / "log.jsonl")
    rep = ag.tick()
    assert rep.chosen in ("flee", "bandage") and not rep.used_model and "outside" in rep.reason
    row = json.loads((tmp_path / "log.jsonl").read_text().splitlines()[0])
    assert row["decision"]["backend"] == "hold" and row["chosen"] == rep.chosen


def test_admitted_choice_becomes_a_plan_that_repeats():
    class PickWalkEast:
        name = "east"

        def choose(self, scene, question, options):
            k = "walk:2" if "walk:2" in options else next(iter(options))
            return Decision(k, {o: (1.0 if o == k else 0.0) for o in options}, 1.0, 1.0, self.name)

    w = FakeBody()
    ag = Agent(w, Persona(name="W", talkativeness=0), PickWalkEast(), sync=True, pump_ms=0, decide_every=100, plan_ticks=3)
    reps = ag.run(4)
    assert reps[0].used_model and reps[0].chosen == "walk:2"
    assert [r.reason for r in reps[1:4]] == ["plan", "plan", "plan"]  # plan_ticks=3 -> valid through tick 4
    assert w.player.pos.x == 104


def test_async_worker_result_is_consumed_and_stale_results_dropped():
    import time

    class Slow:
        name = "slow"

        def choose(self, scene, question, options):
            time.sleep(0.05)
            k = next(iter(options))
            return Decision(k, {o: (1.0 if o == k else 0.0) for o in options}, 1.0, 50.0, self.name)

    w = FakeBody()
    ag = Agent(w, Persona(name="W", talkativeness=0), Slow(), sync=False, pump_ms=0, deadline_s=1.0)
    r1 = ag.tick()
    assert r1.reason.startswith("rule") and not r1.used_model
    time.sleep(0.1)
    r2 = ag.tick()
    assert r2.used_model and r2.backend == "slow"
