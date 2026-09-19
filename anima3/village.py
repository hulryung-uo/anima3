"""A village: several characters, one process, one model, one GM.

    python -m anima3.village --roster grimm:miner:economy ragnar:warrior:hunt --ticks 600

Each roster entry is `account:persona:mode`. The GM (owner account) renames each
character to its persona, stages it — the ridge economy for `economy`, a kit and
prey at the ridge's prey spot for `hunt` — then every agent runs on its own thread
against its own bridge, sharing the MLX model (serialized) and the Laya triage.
Agents hear each other; that is the point.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from dataclasses import dataclass

from .agent import Agent
from .body import BridgeBody
from .contract import Pos
from .decision import build_client
from .economy import MINE_SPOT
from .gm import Gm
from .persona import Persona

PREY_SPOT = Pos(2604, 490, 20)      # the open ground south-west of the ridge: 16 tiles from the vein, off the corridor
WARRIOR_STAND = Pos(2605, 488, 20)


@dataclass
class Member:
    account: str
    persona: Persona
    mode: str                        # economy | hunt | idle
    body: BridgeBody | None = None
    agent: Agent | None = None
    serial: int = 0


def parse_roster(specs: list[str]) -> list[Member]:
    out = []
    for spec in specs:
        acct, pname, mode = (spec.split(":") + ["idle"])[:3]
        out.append(Member(acct, Persona.load(pname), mode))
    return out


def spawn_prey(gm: Gm, prey: str, count: int) -> int:
    """Pinned prey (anima2's approach): it cannot wander to the miner or fall off the cliff."""
    n = 0
    for dx, dy in ((2, 2), (-2, 2), (2, -1), (-2, -1))[:count]:
        if gm.add_npc_pinned(prey, PREY_SPOT.x + dx, PREY_SPOT.y + dy, PREY_SPOT.z) is not None:
            n += 1
    return n


def stage(gm: Gm, m: Member, prey: str, prey_count: int) -> dict:
    rep = {"rename": gm.command_on(f"[Set Name {m.persona.name}", m.serial)}
    from .progression import SKILL_NAMES
    for sk in m.body.observe().skills:      # the GM learns what the character already has
        gm._known_skills[(m.serial, SKILL_NAMES.get(sk.id, ""))] = sk.base
    if m.mode == "economy":
        rep.update(gm.stage_economy(m.serial))
    elif m.mode == "hunt":
        gm.go(PREY_SPOT.x, PREY_SPOT.y, PREY_SPOT.z)
        rep["teleport"] = gm.command_on(f"[Set X {WARRIOR_STAND.x} Y {WARRIOR_STAND.y} Z {WARRIOR_STAND.z}", m.serial)
        for sk in Gm.WARRIOR_SKILLS:
            rep[sk] = gm.command_on(f"[Set Skills.{sk}.Base 100", m.serial)
        obs = m.body.observe()
        has_sword = any(i.graphic == 0x13FF for i in obs.items if i.container in (m.serial, obs.backpack_serial()))
        if not has_sword:
            for item in Gm.WARRIOR_KIT:
                rep[item] = gm.command_on(f"[AddToPack {item}", m.serial)
        else:
            rep["kit"] = "already carried"
        rep["prey"] = spawn_prey(gm, prey, prey_count)
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="anima3.village")
    ap.add_argument("--roster", nargs="+", required=True, help="account:persona:mode ...")
    ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--port", type=int, default=2593)
    ap.add_argument("--gm-user", default="anima3"); ap.add_argument("--gm-pass", default="anima3")
    ap.add_argument("--backend", default="qwen"); ap.add_argument("--triage", default="laya")
    ap.add_argument("--no-speech", action="store_true")
    ap.add_argument("--ticks", type=int, default=400); ap.add_argument("--pump-ms", type=int, default=300)
    ap.add_argument("--prey", default="Mongbat"); ap.add_argument("--prey-count", type=int, default=2)
    ap.add_argument("--respawn-every", type=int, default=150, help="ticks between GM prey respawns (0 = never)")
    ap.add_argument("--monitor-base", type=int, default=8801)
    ap.add_argument("--log-dir", default=".logs/village")
    a = ap.parse_args(argv)

    roster = parse_roster(a.roster)
    client = build_client(a.backend)
    if hasattr(client, "warmup"):
        print(f"warmup: {client.name} {client.warmup():.0f} ms", flush=True)
    triage = None
    if a.triage != "off":
        from .triage import build_triage
        triage = build_triage(a.triage)
        if hasattr(triage, "warmup"):
            t0 = time.time(); triage.warmup(); print(f"triage: {triage.name} ready in {time.time() - t0:.0f}s", flush=True)
    speech = None
    if not a.no_speech:
        from .speech import QwenSpeech
        speech = QwenSpeech(client, triage)

    # 1. bodies log in (characters are created on first login)
    for i, m in enumerate(roster):
        m.body = BridgeBody.spawn(a.host, a.port, m.account, m.account, monitor_port=a.monitor_base + i)
        m.serial = int(m.body.ready["player"]["serial"])
        print(f"{m.account}: serial={m.serial} as {m.persona.who} ({m.mode}) monitor={a.monitor_base + i}", flush=True)
        for _ in range(3):
            m.body.pump(a.pump_ms)

    # 2. the GM stages everyone, then stands aside
    gm_body = BridgeBody.spawn(a.host, a.port, a.gm_user, a.gm_pass)
    gm = Gm(gm_body)
    try:
        for m in roster:
            rep = stage(gm, m, a.prey, a.prey_count)
            print(f"staged {m.persona.name}: " + ", ".join(f"{k}={v}" for k, v in rep.items() if k in ("rename", "teleport", "prey", "workplace", "forge")), flush=True)
        gm.go(MINE_SPOT.x + 12, MINE_SPOT.y + 12, MINE_SPOT.z)
        gm.journal_after("[Hide", pumps=2)   # the fixture should not be a person anyone visits

        # 3. agents
        import os
        os.makedirs(a.log_dir, exist_ok=True)
        for m in roster:
            m.agent = Agent(m.body, m.persona, client, decide_every=3, pump_ms=a.pump_ms, economy=(m.mode == "economy"),
                            triage=triage, speech=speech, log_path=f"{a.log_dir}/{m.persona.name.lower()}.jsonl",
                            chronicle_path=f"{a.log_dir}/{m.persona.name.lower()}.chronicle.md")
            m.agent.memory["friends"] = {x.serial for x in roster if x is not m}   # villagers never fight each other
            if m.mode == "hunt":
                m.persona.combat_disposition = m.persona.combat_disposition or "aggressive"
        threads = [threading.Thread(target=m.agent.run, args=(a.ticks,), daemon=True, name=m.persona.name) for m in roster]
        for t in threads:
            t.start()
        # 4. GM keeps prey coming for the hunter(s)
        hunters = [m for m in roster if m.mode == "hunt"]
        deaths: dict[str, int] = {m.persona.name: 0 for m in roster}
        tick = 0
        while any(t.is_alive() for t in threads):
            time.sleep(a.pump_ms / 1000)
            tick += 1
            if tick % 25 == 0:  # the shard's healer, played by the GM: a death is counted, then undone
                for m in roster:
                    r = m.agent.reports[-1] if m.agent.reports else None
                    if r and r.dead and gm.command_on("[Resurrect", m.serial):
                        deaths[m.persona.name] += 1
                        print(f"[gm] resurrected {m.persona.name} (death #{deaths[m.persona.name]}) at tick {tick}", flush=True)
            if a.respawn_every and hunters and tick % a.respawn_every == 0:
                n = spawn_prey(gm, a.prey, a.prey_count)
                print(f"[gm] respawned {n} pinned prey at tick {tick}", flush=True)
            if tick % 100 == 0:
                for m in roster:
                    r = m.agent.reports[-1] if m.agent.reports else None
                    if r:
                        print(f"[{m.persona.name:<7} t={r.tick:4d}] hp={r.hp_pct:4.0%} hostiles={r.hostiles} gold={r.gold} -> {r.chosen} [{r.reason[:22]}]", flush=True)
        for t in threads:
            t.join()
    finally:
        gm_body.close()
        for m in roster:
            if m.body:
                m.body.close()
    for m in roster:
        s = m.agent.summary()
        print(f"\n=== {m.persona.who} ({m.mode}) ===")
        print({k: s[k] for k in ("ticks", "gold", "dead", "min_hp_pct", "model_calls", "model_admitted", "skill_gains", "gm")}, "deaths:", deaths.get(m.persona.name))
        print("speech:", s.get("speech"))
        print("aim:", s.get("aim"))
        print("procedures:", s.get("procedures", [])[-12:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
