"""Speech triage: what kind of thing was just said, and is it for me?

Laya (a 421M encoder, ~40 ms) classifies the *topic* of a heard line — the one
thing the encoder class measured well. Without Laya installed a keyword fallback
keeps the social verbs alive. "Addressed to me" is a heuristic on purpose: the
encoder's yes/no head was yes-biased in our probes, and a name or a second-person
pronoun within four tiles is a better signal than a probability.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass

KINDS = {
    "greeting": "a hello, a farewell, or a friendly acknowledgement",
    "question": "asks the listener something or requests information",
    "trade": "offers, requests, or negotiates buying, selling, or trading goods",
    "threat": "hostile, insulting, or threatening",
    "chatter": "anything else: remarks, jokes, thinking aloud",
}
AI_VOICE_Q = ("Does this line break character by revealing or behaving as an AI assistant, language model, "
              "or chatbot (refusing as an assistant, mentioning being an AI, lacking access to the game) instead "
              "of speaking as a person living in a medieval fantasy world?")
_SECOND_PERSON = re.compile(r"\b(you|your|thou|thee|thy|ye|friend|stranger|sir|miner|smith)\b", re.IGNORECASE)


@dataclass
class Triage:
    kind: str
    confidence: float
    probs: dict[str, float]
    backend: str


class KeywordTriage:
    name = "keywords"

    def classify(self, text: str) -> Triage:
        t = text.lower()
        if re.search(r"\b(die|kill|scoundrel|thief|fool|attack|coward|or else)\b", t):
            k = "threat"
        elif re.search(r"\b(sell|buy|trade|price|how much|ingot|dagger|tongs)\b", t):
            k = "trade"
        elif re.search(r"\b(hail|hello|hi|greetings|well met|farewell|good day|bye)\b", t):
            k = "greeting"
        elif "?" in t:
            k = "question"
        else:
            k = "chatter"
        return Triage(k, 0.5, {k: 1.0}, self.name)

    def ai_voice(self, text: str) -> float:
        t = text.lower()
        return 1.0 if any(p in t for p in ("language model", "an ai", "as an ai", "i'm an assistant", "i cannot help")) else 0.0


class LayaTriage:
    """Loads lazily on first use (~1 min from cache); call `warmup()` at start."""

    name = "laya"

    def __init__(self) -> None:
        self._router = None
        self._lock = threading.Lock()

    def warmup(self) -> None:
        with self._lock:
            if self._router is None:
                from laya import Router
                self._router = Router(preload=True)

    def classify(self, text: str) -> Triage:
        self.warmup()
        with self._lock:
            a = self._router.predict(text, {"k": {"type": "choice", "instructions": "What kind of utterance is this, said by a person in a medieval fantasy game?", "criteria": KINDS}})["answers"]["k"]
        return Triage(a["choice"], float(a.get("confidence", 0.0)), {k: float(v) for k, v in a["probabilities"].items()}, self.name)

    def ai_voice(self, text: str) -> float:
        self.warmup()
        with self._lock:
            a = self._router.predict(text, {"b": {"type": "noul", "instructions": AI_VOICE_Q,
                                                 "criteria": {"true": "speaks as an AI/assistant/model, or refuses in assistant voice",
                                                              "false": "stays in character as a person in the game world"}}})["answers"]["b"]
        return float(a["noul"])


def build_triage(kind: str = "auto"):
    if kind in ("laya", "auto"):
        try:
            import laya  # noqa: F401
            return LayaTriage()
        except ImportError:
            if kind == "laya":
                raise
    return KeywordTriage()


def addressed_to_me(text: str, my_name: str, speaker_distance: int) -> bool:
    if my_name and my_name.lower() in text.lower():
        return True
    return speaker_distance <= 4 and bool(_SECOND_PERSON.search(text))
