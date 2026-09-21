"""Strategy learning between matches: the slow layer reads the result and the playbook and
writes the next standing aim — the same reflection loop the village uses, pointed at wins."""

from __future__ import annotations

import re
from pathlib import Path


def summarize(res: dict, wins: dict, me: str, them: str) -> str:
    p = res["per"][me]
    rounds = "; ".join(res["rounds"])
    return (f"Result: {me} {wins[me]} - {wins[them]} {them}, draws {wins['draw']}. Rounds: {rounds or 'none'}. "
            f"Your spells that landed: {p['casts_ok'] or 'none'}. Failed casts: {p['cast_fail'] or 'none'}. "
            f"Meditations: {p['meditations']}.")


def next_aim(speech, persona, aim: str | None, res: dict, wins: dict, me: str, them: str, playbook: str, n: int) -> str:
    entry = summarize(res, wins, me, them)
    path = Path(playbook)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(f"## match {n}\n\naim: {aim or '(none)'}\n\n{entry}\n\n")
    history = re.findall(r"## match (\d+)\n\naim: (.*?)\n\n(Result: .*?)\n", path.read_text())[-6:]
    hist = "\n".join(f"- match {k}: aim \"{a[:120]}\" -> {r[:90]}" for k, a, r in history) or "- none yet"
    ask = (f"You have been duelling {them}, mage against mage, 5x rules, no potions.\n"
           f"Your standing tactic was: {aim or 'none'}\n"
           f"This match: {entry}\n"
           f"Earlier matches:\n{hist}\n"
           "In ONE sentence, in character, state the tactic you will hold to in the next match — what to cast first, "
           "when to heal, when to meditate, when to close in. Be concrete; change what lost, keep what won.")
    line = speech._gen(persona, "In the duelling pit, between matches.", ask, max_tokens=70, screen=False)
    text = (line.text or aim or "Open with Energy Bolt; Greater Heal below half; meditate only when the opponent is far.")
    text = re.sub(r"^(Tactic|Aim|Next)[:\-]\s*", "", text).strip()[:220]
    with path.open("a") as fh:
        fh.write(f"next aim: {text}\n\n")
    return text
