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

from .body import DEFAULT_HOST, DEFAULT_PORT, BridgeBody
from .contract import Observation, chebyshev, say, target_ground, target_object


class Gm:
    def __init__(self, body: BridgeBody, pump_ms: int = 300) -> None:
        self.body = body
        self.pump_ms = pump_ms
        self._known_skills: dict[tuple[int, str], float] = {}   # (serial, name) -> base, told by the village

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

    def add_npc_pinned(self, kind: str, x: int, y: int, z: int, *, exclude: set[int] | None = None) -> int | None:
        """`[Add <npc>` at a spot, find it, and `[Set CantWalk true` so it stays put.

        Never pins a person: the warrior once teleported in beside the spawn a tick
        earlier, was taken for the new mongbat, and could not walk for four runs."""
        from .contract import HUMAN_BODIES
        exclude = set(exclude or ()) | {int(self.body.ready["player"]["serial"])}
        before = {m.serial for m in self.body.observe().mobiles}
        if not self.command_at(f"[Add {kind}", x, y, z):
            return None
        for _ in range(10):
            self.body.pump(self.pump_ms)
            obs = self.body.observe()
            new = [m for m in obs.mobiles if m.serial not in before and m.serial not in exclude
                   and m.body not in HUMAN_BODIES and chebyshev(m.pos, type(m.pos)(x, y, z)) <= 3]
            if new:
                serial = new[0].serial
                self.command_on("[Set CantWalk true", serial)
                return serial
        return None

    def stage_economy(self, target: int | None = None, *, workplace: bool = True, skill: int = 45) -> dict:
        """Stage the Minoc ridge economy. `workplace=True` places forge, anvil and the two
        pinned vendors unless a forge is already there; the character (`target` serial, or
        self) gets skills, tools and a teleport to the vein."""
        from .economy import (
            ANVIL_SPOT,
            FORGE_GRAPHICS,
            FORGE_SPOT,
            MINE_SPOT,
            SMITH_SPOT,
            SMITH_VENDOR_SPOT,
            TINKER_VENDOR_SPOT,
        )
        report: dict = {}
        me = int(self.body.ready["player"]["serial"])
        who = target or me
        self.go(SMITH_SPOT.x, SMITH_SPOT.y, SMITH_SPOT.z)
        obs = self.body.observe()
        report["at"] = (obs.player.pos.x, obs.player.pos.y, obs.player.pos.z)
        if workplace:
            if any(i.container is None and i.distance <= 3 and i.graphic in FORGE_GRAPHICS for i in obs.items):
                report["workplace"] = "already staged"
            else:
                report["forge"] = self.add_item_at("Forge", FORGE_SPOT.x, FORGE_SPOT.y, FORGE_SPOT.z)
                report["anvil"] = self.add_item_at("Anvil", ANVIL_SPOT.x, ANVIL_SPOT.y, ANVIL_SPOT.z)
                # `[Add` lands on the ground's own height; a forge on the cliff above the ridge
                # (z=43, live-caught) is invisible to DefBlacksmithy's anvil/forge check.
                report["leveled"] = self.level_forge_anvil(SMITH_SPOT.z)
                report["blacksmith"] = self.add_npc_pinned("Blacksmith", SMITH_VENDOR_SPOT.x, SMITH_VENDOR_SPOT.y, SMITH_VENDOR_SPOT.z)
                report["tinker"] = self.add_npc_pinned("Tinker", TINKER_VENDOR_SPOT.x, TINKER_VENDOR_SPOT.y, TINKER_VENDOR_SPOT.z)
        for sk in ("Mining", "Blacksmith", "Tinkering"):
            # never lower a skill the character has already trained past the floor
            report[sk] = self.command_on(f"[Set Skills.{sk}.Base {skill}", who) if not self._skill_at_least(who, sk, skill) else "kept"
        for item in ("Pickaxe", "Tongs", "TinkerTools", "Bandage 50"):
            report[item] = self.command_on(f"[AddToPack {item}", who)
        if who != me:
            report["teleport"] = self.command_on(f"[Set X {MINE_SPOT.x} Y {MINE_SPOT.y} Z {MINE_SPOT.z}", who)
            return report
        self.go(MINE_SPOT.x, MINE_SPOT.y, MINE_SPOT.z)
        obs = self.body.observe()
        report["mine_at"] = (obs.player.pos.x, obs.player.pos.y, obs.player.pos.z)
        report["pack"] = sorted({hex(i.graphic) for i in obs.own_pack()})
        return report

    def _skill_at_least(self, serial: int, name: str, floor: float) -> bool:
        me = int(self.body.ready["player"]["serial"])
        if serial != me:
            return bool(self._known_skills.get((serial, name), 0.0) >= floor)
        from .progression import SKILL_IDS
        obs = self.body.observe()
        return any(s.id == SKILL_IDS.get(name) and s.base >= floor for s in obs.skills)

    def ground_near(self, radius: int = 3) -> list:
        obs = self.body.observe()
        return [i for i in obs.items if i.container is None and i.distance <= radius]

    def level_forge_anvil(self, z: int) -> list[str]:
        """`[Set Z z` on every forge/anvil item within 3 tiles; returns what was found."""
        from .economy import ANVIL_GRAPHICS, FORGE_GRAPHICS
        out = []
        for i in self.ground_near(3):
            if i.graphic in FORGE_GRAPHICS or i.graphic in ANVIL_GRAPHICS:
                kind = "forge" if i.graphic in FORGE_GRAPHICS else "anvil"
                ok = self.command_on(f"[Set Z {z}", i.serial) if i.pos.z != z else True
                out.append(f"{kind} 0x{i.graphic:04X} at ({i.pos.x},{i.pos.y},{i.pos.z}) -> z={z}: {ok}")
        return out

    HUNTING_SPOT = (2587, 408, 20)   # anima2's calibrated, unguarded pocket near Minoc
    WARRIOR_SKILLS = ("Swordsmanship", "Tactics", "Anatomy", "Healing")
    WARRIOR_KIT = ("Katana", "PlateChest", "PlateLegs", "PlateArms", "Bandage 200")

    def stage_warrior(self, target: int, prey: str = "Mongbat", prey_count: int = 2, skill: int = 100, kit: bool = True) -> dict:
        """Stage another (Player-level) character for a hunt, then put prey beside it."""
        x, y, z = self.HUNTING_SPOT
        self.go(x, y, z)
        me = self.body.observe().player.pos
        report = {"gm_at": (me.x, me.y, me.z)}
        report["teleport"] = self.command_on(f"[Set X {me.x} Y {me.y} Z {me.z}", target)
        for sk in (self.WARRIOR_SKILLS if kit else ()):
            report[sk] = self.command_on(f"[Set Skills.{sk}.Base {skill}", target)
        for item in (self.WARRIOR_KIT if kit else ()):
            report[item] = self.command_on(f"[AddToPack {item}", target)
        for _ in range(3):
            self.body.pump(self.pump_ms)
        obs = self.body.observe()
        report["target_seen"] = [(m.name, m.distance) for m in obs.mobiles if m.serial == target]
        spawned = 0
        for k, (dx, dy) in enumerate(((3, 0), (0, 3), (-3, 0), (0, -3))[:prey_count]):
            if self.command_at(f"[Add {prey}", me.x + dx, me.y + dy, me.z):
                spawned += 1
        report["prey_spawned"] = spawned
        # the GM steps away so it is not what the prey stands next to
        self.go(me.x + 8, me.y + 8, me.z)
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
    ap.add_argument("--host", default=DEFAULT_HOST); ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--user", default="anima3"); ap.add_argument("--pass", dest="password", default="anima3")
    ap.add_argument("--createworld", action="store_true")
    ap.add_argument("--spawn", metavar="KIND"); ap.add_argument("--dx", type=int, default=3); ap.add_argument("--dy", type=int, default=0)
    ap.add_argument("--say", metavar="CMD", help="say an arbitrary cursor-less command and print the journal")
    ap.add_argument("--go", nargs=3, type=int, metavar=("X", "Y", "Z"), help="`[Go X Y Z` (self) before anything else")
    ap.add_argument("--ground", action="store_true", help="list ground items within 3 tiles (graphic, x, y, z)")
    ap.add_argument("--level-forge-anvil", type=int, metavar="Z", help="`[Set Z` on nearby forge/anvil items")
    ap.add_argument("--pack", action="store_true", help="list pack items (graphic, amount, serial)")
    ap.add_argument("--add", metavar="ITEM", action="append", default=[], help="`[AddToPack ITEM` on self")
    ap.add_argument("--probe-tool", metavar="GRAPHIC", action="append", default=[], help="Use the pack tool with this graphic (hex) and dump raw gump JSON for a few ticks")
    ap.add_argument("--set-self", metavar="PROP", action="append", default=[], help="`[Set PROP` on own character, e.g. Skills.Tinkering.Base 75")
    ap.add_argument("--rename", nargs=2, metavar=("SERIAL", "NAME"), help="`[Set Name NAME` on a character")
    ap.add_argument("--stage-warrior", type=int, metavar="SERIAL", help="teleport/skill/kit another character at the hunting pocket and spawn prey")
    ap.add_argument("--prey", default="Mongbat"); ap.add_argument("--prey-count", type=int, default=2)
    ap.add_argument("--no-kit", action="store_true", help="stage-warrior: teleport and prey only (character already kitted)")
    ap.add_argument("--stage-economy-for", type=int, metavar="SERIAL", help="skills, tools and a teleport to the vein for another character")
    ap.add_argument("--stage-economy", action="store_true", help="forge, anvil, vendors, skills, tools on the Minoc ridge (self)")
    a = ap.parse_args(argv)
    body = BridgeBody.spawn(a.host, a.port, a.user, a.password)
    try:
        gm = Gm(body)
        if a.say:
            for line in gm.journal_after(a.say):
                print("  journal:", line)
        if a.go:
            gm.go(*a.go)
            o = body.observe()
            print(f"  at ({o.player.pos.x}, {o.player.pos.y}, {o.player.pos.z}); ground items within 2: " + ", ".join(f"0x{i.graphic:04X}@{i.distance}" for i in o.items if i.container is None and i.distance <= 2))
        if a.ground:
            for i in gm.ground_near(3):
                print(f"  ground 0x{i.graphic:04X} at ({i.pos.x},{i.pos.y},{i.pos.z}) dist={i.distance} serial={i.serial}")
        if a.level_forge_anvil is not None:
            for line in gm.level_forge_anvil(a.level_forge_anvil):
                print("  " + line)
            for i in gm.ground_near(3):
                print(f"  now 0x{i.graphic:04X} at ({i.pos.x},{i.pos.y},{i.pos.z})")
        for prop in a.set_self:
            print(f"  [Set {prop}: {gm.set_self(prop)}")
        for item in a.add:
            print(f"  [AddToPack {item}: {gm.add_to_pack(item)}")
        if a.pack:
            raw = body.observe_raw()
            bp = Observation.from_json(raw).backpack_serial()
            for i in sorted((i for i in raw["items"] if i.get("container") == bp), key=lambda i: i.get("graphic", 0)):
                print(f"  pack 0x{i.get('graphic', 0):04X} x{i.get('amount', 1):<3} serial={i.get('serial')}")
        for probe in a.probe_tool:
            import json as _json

            from .contract import gump_response, use
            graphic = int(probe, 16)
            raw = body.observe_raw()
            bp = Observation.from_json(raw).backpack_serial()
            tool = next((i for i in raw["items"] if i.get("graphic") == graphic and i.get("container") == bp), None)
            print(f"probe tool 0x{graphic:04X}: {('found serial ' + str(tool['serial'])) if tool else 'NOT in pack'}")
            if tool:
                body.act(use(tool["serial"]))
                for t in range(6):
                    body.pump(300)
                    raw = body.observe_raw()
                    js = [(j.get("cliloc"), (j.get("text") or "")[:60]) for j in raw.get("new_journal") or []]
                    for gd in raw.get("gumps") or []:
                        lay = gd.get("layout") or ""
                        els = gd.get("elements") or []
                        print(f"  t{t} gump serial={gd.get('serial')} id={gd.get('gump_id')} layout[{len(lay)}]={lay[:220]!r}")
                        print(f"     elements[{len(els)}]: {_json.dumps(els[:6])[:600]}")
                    if js:
                        print(f"  t{t} journal={js}")
                for gd in raw.get("gumps") or []:
                    body.act(gump_response(gd["serial"], gd["gump_id"], 0))
                body.pump(300)
        if a.rename:
            print(f"  rename {a.rename[0]} -> {a.rename[1]}: {gm.command_on(f'[Set Name {a.rename[1]}', int(a.rename[0]))}")
        if a.stage_warrior:
            for k, v in gm.stage_warrior(a.stage_warrior, a.prey, a.prey_count, kit=not a.no_kit).items():
                print(f"  {k}: {v}")
        if a.stage_economy_for:
            for k, v in gm.stage_economy(a.stage_economy_for).items():
                print(f"  {k}: {v}")
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
