"""Persona — who is speaking and how they fight. Same YAML as anima v1/anima2."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Persona:
    name: str
    title: str = ""
    background: str = ""
    personality: str = ""
    speech_style: str = ""
    speech_examples: list[str] = field(default_factory=list)
    interests: str = ""
    dislikes: str = ""
    talkativeness: float = 0.3
    combat_disposition: str = "neutral"   # pacifist | defensive | neutral | aggressive
    profession: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def who(self) -> str:
        return f"{self.name}, {self.title}" if self.title else self.name

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Persona:
        known = {f for f in cls.__dataclass_fields__ if f != "extra"}
        kw: dict[str, Any] = {k: v for k, v in d.items() if k in known}
        for k in ("interests", "dislikes"):
            if isinstance(kw.get(k), list):
                kw[k] = ", ".join(str(x) for x in kw[k])
        for k in ("background", "speech_style"):
            if isinstance(kw.get(k), str):
                kw[k] = " ".join(kw[k].split())
        kw["speech_examples"] = [str(s) for s in (kw.get("speech_examples") or [])]
        kw["extra"] = {k: v for k, v in d.items() if k not in known}
        return cls(**kw)

    @classmethod
    def load(cls, name_or_path: str) -> Persona:
        p = Path(name_or_path)
        if p.suffix in (".yaml", ".yml") and p.exists():
            return cls.from_dict(yaml.safe_load(p.read_text()) or {})
        text = resources.files("anima3").joinpath("personas", f"{name_or_path}.yaml").read_text()
        return cls.from_dict(yaml.safe_load(text) or {})


def bundled() -> list[str]:
    return sorted(p.name[:-5] for p in resources.files("anima3").joinpath("personas").iterdir() if p.name.endswith(".yaml"))
