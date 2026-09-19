"""The slow layer's voice: one in-character line, generated, then screened.

Generation is the one place a *generative* model is used. It runs off-thread
with a deadline; the fast loop never waits for it. Every line is screened by
the triage backend's AI-voice detector (Laya's recall on that was 4/4 in our
probes) — a line that reads like an assistant is dropped, and the character
simply says nothing. Silence is always in character.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass

from .persona import Persona

MAX_CHARS = 110
_SYSTEM = ("You are {who} in the world of Ultima Online. Personality: {personality}. Speech style: {style}. "
           "Examples of how you talk: {examples}. Answer with ONE short line of speech only, in character, "
           "no quotes, no narration, no modern words.")


@dataclass
class Line:
    text: str | None
    reason: str
    ms: float


class QwenSpeech:
    """Generates with the same MLX model the decision client already loaded."""

    def __init__(self, decision_client, triage=None, ai_threshold: float = 0.85) -> None:
        self._dc = decision_client
        self._triage = triage
        self.ai_threshold = ai_threshold
        self._lock = threading.Lock()

    def say(self, persona: Persona, scene: str, heard: str | None, intent: str) -> Line:
        import time

        from mlx_lm import generate
        from mlx_lm.sample_utils import make_sampler
        t0 = time.perf_counter()
        self._dc._load()
        ex = " | ".join(persona.speech_examples[:3]) or "plain, short sentences"
        sys_ = _SYSTEM.format(who=persona.who, personality=persona.personality or "plainspoken",
                              style=persona.speech_style or "direct", examples=ex)
        user = f"Situation:\n{scene}\n\n"
        if heard:
            user += f"Someone just said to you: \"{heard}\"\n"
        user += f"What you want to do: {intent}\nYour line:"
        msgs = [{"role": "system", "content": sys_}, {"role": "user", "content": user}]
        prompt = self._dc._tok.apply_chat_template(msgs, add_generation_prompt=True, enable_thinking=False, tokenize=False)
        with self._lock:
            out = generate(self._dc._model, self._dc._tok, prompt=prompt, max_tokens=40, verbose=False,
                           sampler=make_sampler(temp=0.7))
        text = clean(out)
        ms = (time.perf_counter() - t0) * 1000
        if not text:
            return Line(None, "empty", ms)
        if self._triage is not None:
            p = self._triage.ai_voice(text)
            if p >= self.ai_threshold:
                return Line(None, f"screened (ai-voice {p:.2f})", ms)
        return Line(text, "ok", ms)


def clean(raw: str) -> str | None:
    line = raw.strip().splitlines()[0] if raw.strip() else ""
    line = re.sub(r"^(\*[^*]*\*\s*)", "", line)             # drop *actions*
    line = line.strip().strip('"“”').strip()
    line = re.sub(r"^[A-Z][a-z]+:\s*", "", line)              # "Grimm: ..."
    m = re.search(r'"([^"]{3,})"?', line)                      # narration + a quoted line -> the line
    if m and re.match(r"^[A-Z][a-z]+ [a-z]+[^\"]{0,40}\"", line):
        line = m.group(1).strip()
    if not line or line.lower().startswith(("as an ai", "i'm sorry", "i am an ai")):
        return None
    return line[:MAX_CHARS]
