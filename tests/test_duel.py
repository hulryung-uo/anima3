

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
