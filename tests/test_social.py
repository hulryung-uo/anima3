from anima3.affordances import enumerate_affordances
from anima3.agent import Agent
from anima3.body import FakeBody
from anima3.decision import Scripted
from anima3.persona import Persona
from anima3.scene import facts
from anima3.speech import clean
from anima3.triage import KeywordTriage, addressed_to_me


def test_keyword_triage_kinds():
    t = KeywordTriage()
    assert t.classify("Hail, friend!").kind == "greeting"
    assert t.classify("Would you sell me some ingots?").kind == "trade"
    assert t.classify("Where is the bank?").kind == "question"
    assert t.classify("I will kill you, thief").kind == "threat"


def test_addressed_to_me_by_name_or_second_person_when_close():
    assert addressed_to_me("Grimm, over here", "Grimm", 9)
    assert addressed_to_me("can you help me?", "Grimm", 3)
    assert not addressed_to_me("what a fine day", "Grimm", 3)
    assert not addressed_to_me("you there", "Grimm", 7)


def test_heard_line_becomes_reply_and_ignore_verbs_then_is_consumed():
    w = FakeBody(); sara = w.add_person(2, 0, "Sara"); w.hear(sara, "Would you sell me a dagger?")
    ag = Agent(w, Persona(name="Grimm", talkativeness=0), Scripted(), pump_ms=0, triage=KeywordTriage())
    ag.tick()
    ids = ag.reports[-1].options
    assert any(i.startswith("reply:") and i.endswith(":answer") for i in ids) and any(i.startswith("ignore:") for i in ids)
    assert not ag.memory["heard_pending"]   # the rule picked reply (first) and consumed it


def test_threat_is_offered_as_wary():
    w = FakeBody(); m = w.add_person(1, 0, "Rogue"); w.hear(m, "Give me your gold or die, fool")
    obs = w.observe()
    ag = Agent(w, Persona(name="Grimm"), Scripted(), pump_ms=0, triage=KeywordTriage())
    ag._hear(obs)
    ids = [a.id for a in enumerate_affordances(obs, facts(obs), ag.persona, ag.memory)]
    assert any(i.endswith(":wary") for i in ids)


def test_clean_strips_narration_and_refusals():
    assert clean('*grunts* "Iron vein\'s thin here."') == "Iron vein's thin here."
    assert clean("Grimm: Good ore today.\nmore") == "Good ore today."
    assert clean("As an AI language model I cannot") is None
