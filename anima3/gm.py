"""GM staging over the same bridge: `[` commands that need a target cursor.

Pattern (live-proven in anima2's control.py): say the command, pump until the
server hands us a target cursor, answer it with TargetGround/TargetObject.
The account must have the access level the command needs (the first account a
fresh ServUO auto-creates is Owner).

    python -m anima3.gm --createworld                 # ServUO's own world generator
    python -m anima3.gm --spawn mongbat --dx 3       # one creature 3 tiles east of the character
"""

from __future__ import annotations

import argparse
import sys
import time

from .body import BridgeBody
from .contract import Observation, chebyshev, say, target_ground, target_object


class Gm:
    def __init__(self, body: BridgeBody, pump_ms: int = 300) -> None:
        self.body = body
        self.pump_ms = pump_ms

    def _await_cursor(self, tries: int = 10) -> bool:
        for _ in range(tries):
            self.body.pump(self.pump_ms)
            if self.body.observe().pending_target:
                return True
        return False

    def command_at(self, command: str, x: int, y: int, z: int) -> bool:
        self.body.act(say(command))
        if not self._await_cursor():
            return False
        self.body.act(target_ground(x, y, z))
        self.body.pump(self.pump_ms)
        return True

    def command_on(self, command: str, serial: int) -> bool:
        self.body.act(say(command))
        if not self._await_cursor():
            return False
        self.body.act(target_object(serial))
        self.body.pump(self.pump_ms)
        return True

    def journal_after(self, command: str, pumps: int = 8) -> list[str]:
        """Say a cursor-less command and collect the journal lines that follow."""
        self.body.act(say(command))
        out: list[str] = []
        for _ in range(pumps):
            self.body.pump(self.pump_ms)
            out += [j.text for j in self.body.observe().new_journal if j.text]
        return out

    def create_world(self, pumps: int = 200) -> list[str]:
        return self.journal_after("[CreateWorld nogump", pumps=pumps)

    def go(self, x: int, y: int, z: int) -> None:
        self.journal_after(f"[Go {x} {y} {z}", pumps=3)

    def set_self(self, prop: str) -> bool:
        return self.command_on(f"[Set {prop}", int(self.body.ready["player"]["serial"]))

    def add_to_pack(self, item: str) -> bool:
        return self.command_on(f"[AddToPack {item}", int(self.body.ready["player"]["serial"]))

    def add_item_at(self, kind: str, x: int, y: int, z: int) -> bool:
        return self.command_at(f"[Add {kind}", x, y, z)

    def add_npc_pinned(self, kind: str, x: int, y: int, z: int) -> int | None:
        """`[Add <npc>` at a spot, find it, and `[Set CantWalk true` so it stays put."""
        before = {m.serial for m in self.body.observe().mobiles}
        if not self.command_at(f"[Add {kind}", x, y, z):
            return None
        for _ in range(6):
            self.body.pump(self.pump_ms)
            obs = self.body.observe()
            new = [m for m in obs.mobiles if m.serial not in before and chebyshev(m.pos, type(m.pos)(x, y, z)) <= 2]
            if new:
                serial = new[0].serial
                self.command_on("[Set CantWalk true", serial)
                return serial
        return None

    def stage_economy(self) -> dict:
        """Self-stage the Minoc ridge economy: forge, anvil, two vendors, skills, tools."""
        from .economy import (
            ANVIL_SPOT,
            FORGE_SPOT,
            MINE_SPOT,
            SMITH_SPOT,
            SMITH_VENDOR_SPOT,
            TINKER_VENDOR_SPOT,
        )
        report: dict = {}
        self.go(SMITH_SPOT.x, SMITH_SPOT.y, SMITH_SPOT.z)
        obs = self.body.observe()
        report["at"] = (obs.player.pos.x, obs.player.pos.y, obs.player.pos.z)
        report["forge"] = self.add_item_at("Forge", FORGE_SPOT.x, FORGE_SPOT.y, FORGE_SPOT.z)
        report["anvil"] = self.add_item_at("Anvil", ANVIL_SPOT.x, ANVIL_SPOT.y, ANVIL_SPOT.z)
        report["blacksmith"] = self.add_npc_pinned("Blacksmith", SMITH_VENDOR_SPOT.x, SMITH_VENDOR_SPOT.y, SMITH_VENDOR_SPOT.z)
        report["tinker"] = self.add_npc_pinned("Tinker", TINKER_VENDOR_SPOT.x, TINKER_VENDOR_SPOT.y, TINKER_VENDOR_SPOT.z)
        for skill in ("Mining", "Blacksmith", "Tinkering"):
            report[skill] = self.set_self(f"Skills.{skill}.Base 45")
        for item in ("Pickaxe", "Tongs", "TinkerTools"):
            report[item] = self.add_to_pack(item)
        self.go(MINE_SPOT.x, MINE_SPOT.y, MINE_SPOT.z)
        obs = self.body.observe()
        report["mine_at"] = (obs.player.pos.x, obs.player.pos.y, obs.player.pos.z)
        report["pack"] = sorted({hex(i.graphic) for i in obs.items if i.container is not None})
        report["ground_near"] = sorted({hex(i.graphic) for i in obs.items if i.container is None and i.distance <= 4})
        report["mobiles_near"] = [(m.name, m.distance) for m in obs.mobiles if m.distance <= 6]
        return report

    def spawn_near(self, kind: str, dx: int, dy: int) -> tuple[bool, Observation]:
        obs = self.body.observe()
        p = obs.player.pos
        ok = self.command_at(f"[Add {kind}", p.x + dx, p.y + dy, p.z)
        for _ in range(4):
            self.body.pump(self.pump_ms)
        return ok, self.body.observe()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="anima3.gm")
    ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--port", type=int, default=2593)
    ap.add_argument("--user", default="anima3"); ap.add_argument("--pass", dest="password", default="anima3")
    ap.add_argument("--createworld", action="store_true")
    ap.add_argument("--spawn", metavar="KIND"); ap.add_argument("--dx", type=int, default=3); ap.add_argument("--dy", type=int, default=0)
    ap.add_argument("--say", metavar="CMD", help="say an arbitrary cursor-less command and print the journal")
    ap.add_argument("--stage-economy", action="store_true", help="forge, anvil, vendors, skills, tools on the Minoc ridge (self)")
    a = ap.parse_args(argv)
    body = BridgeBody.spawn(a.host, a.port, a.user, a.password)
    try:
        gm = Gm(body)
        if a.say:
            for line in gm.journal_after(a.say):
                print("  journal:", line)
        if a.stage_economy:
            for k, v in gm.stage_economy().items():
                print(f"  {k}: {v}")
        if a.createworld:
            t0 = time.time()
            lines = gm.create_world()
            print(f"createworld: {len(lines)} journal lines in {time.time() - t0:.0f}s")
            for line in lines[-12:]:
                print("  ", line)
        if a.spawn:
            ok, obs = gm.spawn_near(a.spawn, a.dx, a.dy)
            print(f"spawn {a.spawn}: cursor={'yes' if ok else 'NO (no target cursor — access level?)'}")
            for m in obs.mobiles[:6]:
                print(f"   mobile {m.name!r} noto={m.notoriety} dist={m.distance} hp={m.hits}/{m.hits_max}")
            for j in obs.new_journal:
                if j.text:
                    print("   journal:", j.text)
    finally:
        body.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
