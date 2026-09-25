"""CLI.  Offline:  python -m anima3 --offline hostile --backend qwen
        Live:     python -m anima3 --user anima3 --pass anima3 --monitor 8801   (shard: ANIMA3_HOST, default uo.hulryung.com)
"""

from __future__ import annotations

import argparse
import sys
import time

from .agent import Agent, TickReport
from .body import DEFAULT_HOST, DEFAULT_PORT, BridgeBody, FakeBody
from .contract import BANDAGE_GRAPHIC, GOLD_GRAPHIC
from .decision import build_client
from .persona import Persona, bundled


def scenario(name: str) -> FakeBody:
    w = FakeBody()
    w.add_pack_item(BANDAGE_GRAPHIC, 5)
    if name == "hostile":
        w.add_hostile(6, 0)
        w.add_ground_item(GOLD_GRAPHIC, 1, 1, 30)
    elif name == "town":
        w.add_person(2, 0, "Sara")
        w.add_ground_item(GOLD_GRAPHIC, 3, -2, 12)
    elif name == "ambush":
        w.player.hits = 14
        for dx, dy in ((2, 0), (0, 2), (-2, 1)):
            w.add_hostile(dx, dy, hits=30, hits_max=30, damage=4)
    else:
        raise SystemExit(f"unknown scenario {name!r} (hostile|town|ambush)")
    return w


def _print(rep: TickReport) -> None:
    tag = "DEAD" if rep.dead else f"hp={rep.hp_pct:4.0%}"
    src = f"{rep.backend} conf={rep.confidence:.2f} {rep.ms:4.0f}ms" if rep.confidence is not None else ""
    if rep.reason.startswith("procedure done"):
        src = rep.reason
    how = ("MODEL" if rep.used_model and rep.backend != "scripted" else rep.reason)
    print(f"t={rep.tick:3d} {tag} hostiles={rep.hostiles} gold={rep.gold:4d} → {rep.chosen or '-':<22} [{how}] {src}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="anima3")
    ap.add_argument("--offline", metavar="SCENARIO", help="hostile | town | ambush (no server)")
    ap.add_argument("--host", default=DEFAULT_HOST); ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--user", default="anima3"); ap.add_argument("--pass", dest="password", default="anima3")
    ap.add_argument("--data-dir", default=None); ap.add_argument("--monitor", type=int, default=None)
    ap.add_argument("--backend", default="qwen", help="qwen | jeff | scripted")
    ap.add_argument("--persona", default="miner", help=f"bundled: {', '.join(bundled())} — or a YAML path")
    ap.add_argument("--ticks", type=int, default=60); ap.add_argument("--pump-ms", type=int, default=250)
    ap.add_argument("--threshold", type=float, default=0.35); ap.add_argument("--decide-every", type=int, default=4)
    ap.add_argument("--log", default=".logs/run.jsonl"); ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--sync", action="store_true", help="decide on the tick thread (deterministic; blocks the body)")
    ap.add_argument("--stage-spawn", metavar="KIND", help="live only: `[Add KIND` near the character before playing (owner account)")
    ap.add_argument("--stage-dx", type=int, default=4); ap.add_argument("--stage-dy", type=int, default=0)
    ap.add_argument("--wait-ticks", type=int, default=0, help="live: pump this many ticks before acting (lets a GM stage this character)")
    ap.add_argument("--triage", default="off", help="off | auto | laya | keywords — classify heard speech into social verbs")
    ap.add_argument("--speech", action="store_true", help="generate in-character replies with the local model (screened by triage)")
    ap.add_argument("--economy", action="store_true", help="enable mine/smelt/craft/sell verbs (Minoc ridge)")
    ap.add_argument("--disposition", choices=["pacifist", "defensive", "neutral", "aggressive"], help="override the persona's combat_disposition")
    a = ap.parse_args(argv)

    persona = Persona.load(a.persona)
    triage = None
    if a.triage != "off":
        from .triage import build_triage
        triage = build_triage(a.triage)
        if hasattr(triage, "warmup"):
            import time as _t
            t0 = _t.time(); triage.warmup(); print(f"triage: {triage.name} ready in {_t.time() - t0:.0f}s")
    if a.disposition:
        persona.combat_disposition = a.disposition
    client = build_client(a.backend)
    speech = None
    if a.speech:
        from .speech import QwenSpeech
        speech = QwenSpeech(client, triage)
    if hasattr(client, "warmup"):
        print(f"warmup: {client.name} loaded in {client.warmup():.0f} ms")
    if a.offline:
        body = scenario(a.offline)
        pump_ms = a.pump_ms
    else:
        body = BridgeBody.spawn(a.host, a.port, a.user, a.password, data_dir=a.data_dir, monitor_port=a.monitor)
        pump_ms = a.pump_ms
        pl = body.ready.get('player', {})
        print(f"ready: {pl.get('name')} serial={pl.get('serial')} schema={body.ready.get('schema_version')}", flush=True)
        for _ in range(a.wait_ticks):
            body.pump(a.pump_ms)
        if a.monitor is not None:
            time.sleep(0.5)
            print(f"monitor: {body.monitor_url or '(not reported yet)'}")
        if a.stage_spawn:
            from .gm import Gm
            ok, obs = Gm(body).spawn_near(a.stage_spawn, a.stage_dx, a.stage_dy)
            near = ", ".join(f"{m.name or '?'}@{m.distance}" for m in obs.mobiles[:4]) or "nobody"
            print(f"staged: [Add {a.stage_spawn} → cursor={'yes' if ok else 'NO'}; nearby: {near}")
    agent = Agent(body, persona, client, decide_every=a.decide_every, threshold=a.threshold,
                  pump_ms=pump_ms, log_path=a.log, sync=True if a.sync else None, economy=a.economy, triage=triage, speech=speech)
    print(f"anima3: {persona.who} | backend={client.name} | {'offline:' + a.offline if a.offline else a.host}")
    try:
        agent.run(a.ticks, on_tick=None if a.quiet else _print)
    finally:
        body.close()
    print("summary:", agent.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
