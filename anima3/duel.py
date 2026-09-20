"""A refereed duel between two characters, under the old pit rules.

    python -m anima3.duel --a anima3d1:duelist_a:qwen --b anima3d2:duelist_b:scripted \\
        --rules 5x --weapon katana --armor leather --rounds 3

ServUO's own PVP Arena System is High-Seas-only (`Enabled => Core.HS`) and this shard
plays the T2A era, so the GM referees: it stages both fighters to the template, sets
them five tiles apart on the open ground, and calls the round when one falls
(Felucca: "anything goes"). 5x / 7x and the weapon rule were never enforced by the
engine in the old days either — players agreed them and the staging enforces them here.

Each side names its own decision backend, which makes a duel the cleanest benchmark
this project has: rule against model, model against model, jeff against Qwen.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from dataclasses import dataclass, field

from .agent import Agent
from .body import BridgeBody
from .contract import Pos
from .decision import build_client
from .gm import Gm
from .persona import Persona

ARENA = (Pos(2602, 488, 20), Pos(2607, 488, 20))     # the open ground south-west of the ridge

TEMPLATES = {
    "5x": {"Swords": 100, "Tactics": 100, "Anatomy": 100, "Healing": 100, "MagicResist": 100},
    "7x": {"Swords": 100, "Tactics": 100, "Anatomy": 100, "Healing": 100, "MagicResist": 100, "Parry": 100, "Hiding": 100},
}
STATS = {"Str": 100, "Dex": 100, "Int": 25}
WEAPONS = {"katana": ("Katana", 0x13FF, 1), "broadsword": ("Broadsword", 0x0F5E, 1), "vikingsword": ("VikingSword", 0x13B9, 1),
           "halberd": ("Halberd", 0x143E, 2), "bardiche": ("Bardiche", 0x0F4D, 2), "fists": (None, None, 0)}
ARMOR = {"leather": ["LeatherChest", "LeatherLegs", "LeatherArms", "LeatherGloves", "LeatherGorget", "LeatherCap"],
         "plate": ["PlateChest", "PlateLegs", "PlateArms", "PlateGloves", "PlateGorget", "PlateHelm"], "none": []}


@dataclass
class Fighter:
    account: str
    persona: Persona
    backend: str
    body: BridgeBody | None = None
    serial: int = 0
    agent: Agent | None = None
    wins: int = 0
    rounds: list[dict] = field(default_factory=list)


def parse(spec: str) -> Fighter:
    acct, pname, backend = (spec.split(":") + ["scripted"])[:3]
    return Fighter(acct, Persona.load(pname), backend)


def stage(gm: Gm, fx: Fighter, spot: Pos, rules: str, weapon: str, armor: str) -> dict:
    rep = {"rename": gm.command_on(f"[Set Name {fx.persona.name}", fx.serial)}
    template = dict(TEMPLATES[rules])
    wname, wgraphic, _ = WEAPONS[weapon]
    if wname is None:
        template.pop("Swords"); template["Wrestling"] = 100
    for sk, v in template.items():
        gm.command_on(f"[Set Skills.{sk}.Base {v}", fx.serial)
    for st, v in STATS.items():
        gm.command_on(f"[Set {st} {v}", fx.serial)
    obs = fx.body.observe()
    have = {i.graphic for i in obs.items if i.container in (fx.serial, obs.backpack_serial())}
    if wname and wgraphic not in have:
        rep["weapon"] = gm.command_on(f"[AddToPack {wname}", fx.serial)
    if rules == "7x" and weapon != "fists" and WEAPONS[weapon][2] == 1 and 0x1B73 not in have:
        rep["shield"] = gm.command_on("[AddToPack Buckler", fx.serial)
    for piece in ARMOR[armor]:
        gm.command_on(f"[AddToPack {piece}", fx.serial)
    if not any(i.graphic == 0x0E21 for i in obs.own_pack()):
        gm.command_on("[AddToPack Bandage 100", fx.serial)
    gm.command_on("[Set CantWalk false", fx.serial)
    return rep


def reset(gm: Gm, fx: Fighter, spot: Pos) -> None:
    gm.command_on("[Resurrect", fx.serial)
    gm.command_on(f"[Set X {spot.x} Y {spot.y} Z {spot.z}", fx.serial)
    gm.command_on("[Set Hits 100", fx.serial)
    gm.command_on("[Set Stam 100", fx.serial)
    gm.command_on("[Set Criminal false", fx.serial)
    gm.command_on("[Set Kills 0", fx.serial)


def run_round(a: Fighter, b: Fighter, clients: dict, max_ticks: int, pump_ms: int, log_dir: str, n: int, speech) -> dict:
    for fx, other in ((a, b), (b, a)):
        fx.agent = Agent(fx.body, fx.persona, clients[fx.backend], decide_every=2, pump_ms=pump_ms,
                         log_path=f"{log_dir}/round{n}-{fx.persona.name.lower()}.jsonl", speech=speech,
                         triage=None, reflect_every=0)
        fx.agent.memory["duel_opponent"] = other.serial
        fx.agent.memory["duel"] = True
    stop = threading.Event()

    def loop(fx: Fighter) -> None:
        while not stop.is_set() and fx.agent.tick_no < max_ticks:
            r = fx.agent.tick()
            if r.dead:
                stop.set()
                break

    threads = [threading.Thread(target=loop, args=(fx,), daemon=True, name=fx.persona.name) for fx in (a, b)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    res = {"round": n, "seconds": round(time.time() - t0, 1)}
    for fx in (a, b):
        r = fx.agent.reports[-1]
        res[fx.persona.name] = {"dead": r.dead, "hp": round(r.hp_pct, 2), "ticks": r.tick,
                                "bandages": sum(1 for x in fx.agent.reports if x.chosen == "bandage"),
                                "attacks": sum(1 for x in fx.agent.reports if str(x.chosen).startswith("attack:")),
                                "model": (fx.agent.summary()["model_calls"], fx.agent.summary()["model_admitted"])}
    dead = [fx for fx in (a, b) if fx.agent.reports[-1].dead]
    if len(dead) == 1:
        winner = a if dead[0] is b else b
        winner.wins += 1
        res["winner"] = winner.persona.name
    else:
        res["winner"] = None
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="anima3.duel")
    ap.add_argument("--a", required=True, help="account:persona:backend"); ap.add_argument("--b", required=True)
    ap.add_argument("--rules", choices=list(TEMPLATES), default="5x"); ap.add_argument("--weapon", choices=list(WEAPONS), default="katana")
    ap.add_argument("--armor", choices=list(ARMOR), default="leather")
    ap.add_argument("--rounds", type=int, default=3); ap.add_argument("--max-ticks", type=int, default=400)
    ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--port", type=int, default=2593)
    ap.add_argument("--gm-user", default="anima3"); ap.add_argument("--gm-pass", default="anima3")
    ap.add_argument("--pump-ms", type=int, default=250); ap.add_argument("--speech", action="store_true")
    ap.add_argument("--log-dir", default=".logs/duel")
    args = ap.parse_args(argv)

    a, b = parse(args.a), parse(args.b)
    clients = {k: build_client(k) for k in {a.backend, b.backend}}
    for c in clients.values():
        if hasattr(c, "warmup"):
            c.warmup()
    speech = None
    if args.speech:
        from .speech import QwenSpeech
        qc = clients.get("qwen") or build_client("qwen")
        speech = QwenSpeech(qc, None)
    import os
    os.makedirs(args.log_dir, exist_ok=True)
    for fx in (a, b):
        fx.body = BridgeBody.spawn(args.host, args.port, fx.account, fx.account)
        fx.serial = int(fx.body.ready["player"]["serial"])
        for _ in range(3):
            fx.body.pump(args.pump_ms)
    gm_body = BridgeBody.spawn(args.host, args.port, args.gm_user, args.gm_pass)
    gm = Gm(gm_body)
    try:
        gm.go(ARENA[0].x + 2, ARENA[0].y + 3, ARENA[0].z)
        for fx, spot in ((a, ARENA[0]), (b, ARENA[1])):
            rep = stage(gm, fx, spot, args.rules, args.weapon, args.armor)
            print(f"staged {fx.persona.name} ({fx.backend}, {args.rules}, {args.weapon}, {args.armor}): {rep}", flush=True)
        gm.journal_after("[Hide", pumps=2)
        print(f"\n== {a.persona.name} ({a.backend}) vs {b.persona.name} ({b.backend}) — {args.rules}, {args.weapon}, {args.armor}, best of {args.rounds} ==", flush=True)
        results = []
        for n in range(1, args.rounds + 1):
            for fx, spot in ((a, ARENA[0]), (b, ARENA[1])):
                reset(gm, fx, spot)
            for _ in range(4):
                a.body.pump(args.pump_ms); b.body.pump(args.pump_ms)
            res = run_round(a, b, clients, args.max_ticks, args.pump_ms, args.log_dir, n, speech)
            results.append(res)
            ra, rb = res[a.persona.name], res[b.persona.name]
            print(f"round {n}: winner={res['winner'] or 'draw'} in {max(ra['ticks'], rb['ticks'])} ticks ({res['seconds']}s) | "
                  f"{a.persona.name} hp={ra['hp']:.0%} bandages={ra['bandages']} model={ra['model']} | "
                  f"{b.persona.name} hp={rb['hp']:.0%} bandages={rb['bandages']} model={rb['model']}", flush=True)
        print(f"\nfinal: {a.persona.name} ({a.backend}) {a.wins} — {b.wins} {b.persona.name} ({b.backend})")
        for fx in (a, b):
            reset(gm, fx, ARENA[0] if fx is a else ARENA[1])
    finally:
        gm_body.close()
        for fx in (a, b):
            if fx.body:
                fx.body.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
