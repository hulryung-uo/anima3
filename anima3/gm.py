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
from .contract import Observation, say, target_ground, target_object


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
    a = ap.parse_args(argv)
    body = BridgeBody.spawn(a.host, a.port, a.user, a.password)
    try:
        gm = Gm(body)
        if a.say:
            for line in gm.journal_after(a.say):
                print("  journal:", line)
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
