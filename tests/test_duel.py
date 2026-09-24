

def test_alternate_puts_the_challenge_in_bs_mouth():
    from types import SimpleNamespace

    from anima3.duel import ServerDuel

    def fighter(name, serial):
        said = []
        return SimpleNamespace(serial=serial, persona=SimpleNamespace(name=name),
                               body=SimpleNamespace(act=said.append), said=said)
    a, b = fighter("Ilse", 1), fighter("Torvald", 2)
    ServerDuel(a, b, 5, "5x-fists-magic", 3, a_challenges=False).challenge()
    assert not a.said and b.said[0]["text"] == "[Challenge Ilse 5 5x-fists-magic arena:3"


def _res(details, casts=({"energy_bolt": 9}, {"energy_bolt": 7}), state="done"):
    return {"state": state, "rounds": ["Round 1: Ilse defeats Torvald (hp 42%, 72 seconds)."],
            "duel_lines": details, "per": {"Ilse": {"casts_ok": casts[0]}, "Torvald": {"casts_ok": casts[1]}}}


def test_a_clean_match_validates():
    from anima3.duel import validate_match
    res = _res(["Round 1 detail: Ilse hp 42%, Torvald hp 0%, attacks 17/18."])
    assert validate_match(res, {"Ilse": 0, "Torvald": 0}, mage=True) == []


def test_the_failures_that_once_passed_silently_are_void():
    from anima3.duel import validate_match
    frozen = _res(["Round 2 detail: Ilse4 hp 100%, Torvald4 hp 100%, attacks 0/0."], casts=({}, {}))
    got = validate_match(frozen, {"Ilse": 0, "Torvald": 0}, mage=True)
    assert "round 2: attacks 0/0" in got and "Ilse cast nothing" in got and "Torvald cast nothing" in got
    clean = _res(["Round 1 detail: Ilse hp 42%, Torvald hp 0%, attacks 17/18."])
    assert validate_match(clean, {"Ilse": 2, "Torvald": 0}, mage=True) == ["Ilse's bridge reconnected 2x"]
    assert validate_match(clean, {}, missing=["Ilse:BlackPearl"]) == ["staging short of Ilse:BlackPearl"]
    assert validate_match(_res([], state="no-start"), {}) == ["match ended in state no-start"]
