from anima3.decision import Decision, Scripted, gate

OPTS = {"flee": "run", "attack:1": "hit", "hold": "wait"}


def test_gate_falls_back_when_choice_outside_menu():
    a = gate(Decision("dance", {"dance": 1.0}, 1.0, 1, "x"), OPTS, 0.3)
    assert a.choice == "flee" and not a.used_model and "outside" in a.reason


def test_gate_falls_back_on_low_confidence_and_admits_otherwise():
    low = gate(Decision("attack:1", {"attack:1": 0.4, "flee": 0.35, "hold": 0.25}, 0.05, 1, "x"), OPTS, 0.3)
    ok = gate(Decision("attack:1", {"attack:1": 0.8, "flee": 0.1, "hold": 0.1}, 0.7, 1, "x"), OPTS, 0.3)
    assert low.choice == "flee" and not low.used_model
    assert ok.choice == "attack:1" and ok.used_model


def test_gate_handles_missing_and_errored_decisions():
    assert gate(None, OPTS, 0.3).choice == "flee"
    assert gate(Decision("", {}, 0, 0, "x", error="boom"), OPTS, 0.3).reason.startswith("error")


def test_scripted_is_the_rule():
    d = Scripted().choose("s", "q", OPTS)
    assert d.choice == "flee" and d.confidence == 1.0
