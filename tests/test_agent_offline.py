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


def test_bandage_procedure_survives_critical_health():
    from anima3.contract import BANDAGE_GRAPHIC
    class PickBandage:
        name = "bandager"

        def choose(self, scene, question, options):
            k = "bandage" if "bandage" in options else next(iter(options))
            return Decision(k, {o: (1.0 if o == k else 0.0) for o in options}, 1.0, 1.0, self.name)

    w = FakeBody(); w.player.hits = 12; w.add_pack_item(BANDAGE_GRAPHIC, 3); w.add_hostile(3, 0, aggressive=False)
    ag = Agent(w, Persona(name="G", combat_disposition="pacifist"), PickBandage(), sync=True, pump_ms=0)
    ag.run(4)
    assert any(pid == "bandage" and v == "ok" for _, pid, v in ag.proc_log), ag.proc_log


def test_agent_opens_its_backpack_on_the_first_tick():
    w = FakeBody()
    ag = Agent(w, Persona(name="G"), Scripted(), pump_ms=0)
    ag.tick()
    assert any(x["type"] == "Use" and x["serial"] == 0x4000_0001 for x in w.log), w.log


def test_journal_sequence_survives_trimming():
    w = FakeBody(); sara = w.add_person(2, 0, "Sara")
    ag = Agent(w, Persona(name="G"), Scripted(), pump_ms=0)
    for i in range(2100):
        w.hear(sara, f"line {i}")
        ag._last_obs = w.observe()
        for j in ag._last_obs.new_journal:
            if j.text:
                ag.journal_log.append((i, j.serial, j.text)); ag.journal_seq += 1
        del ag.journal_log[:-2000]
    assert ag.journal_seq == 2100 and len(ag.journal_log) == 2000
    first_held = ag.journal_seq - len(ag.journal_log)
    assert first_held == 100 and ag.journal_log[0][2] == "line 100"


def test_resilient_body_reconnects_after_a_broken_pipe(monkeypatch):
    from anima3 import body as body_mod
    calls = {"spawn": 0, "act": 0}

    class FlakyBridge:
        monitor_url = None

        def __init__(self):
            self.ready = {"player": {"serial": 1}}

        def act(self, action):
            calls["act"] += 1
            if calls["act"] == 1:
                raise body_mod.BodyError("io error: Broken pipe")

        def close(self):
            pass

    def fake_spawn(**kw):
        calls["spawn"] += 1
        return FlakyBridge()

    monkeypatch.setattr(body_mod.BridgeBody, "spawn", staticmethod(fake_spawn))
    rb = body_mod.ResilientBody({"host": "h", "port": 1, "user": "u", "password": "p"}, backoff_s=0)
    rb.act({"type": "Say", "text": "x"})
    assert calls["spawn"] == 2 and rb.reconnects == 1


def test_a_single_option_menu_never_asks_the_model():
    class Counting:
        name = "count"
        calls = 0

        def choose(self, scene, question, options):
            Counting.calls += 1
            k = next(iter(options))
            return Decision(k, {o: 1.0 for o in options}, 1.0, 1.0, self.name)

    w = FakeBody(); w.player.dead = False
    ag = Agent(w, Persona(name="W", talkativeness=0), Counting(), sync=True, pump_ms=0, decide_every=1)
    import anima3.agent as agent_mod
    orig = agent_mod.enumerate_affordances
    agent_mod.enumerate_affordances = lambda *a, **k: orig(*a, **k)[:1]
    try:
        reps = ag.run(3)
    finally:
        agent_mod.enumerate_affordances = orig
    assert Counting.calls == 0 and all(r.reason == "only option" for r in reps)
