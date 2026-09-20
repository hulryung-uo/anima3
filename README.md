# anima3

> A thin **System One** brain that drives [anima-client](https://github.com/hulryung-uo/anima-client) directly.

anima2 is 80K lines of rules with an LLM that *writes* JSON to pick from a list.
anima3 inverts it: the body's Observation becomes a short **text scene** (an
accessibility tree, not a screenshot), code enumerates the **verbs that are valid
right now** (a closed vocabulary with hard safety limits baked in), and a local
model picks one by reading the **log-probabilities of the option letters** — it
never generates text, so there is nothing to parse and no way to answer outside
the menu. Every pick carries a probability distribution; a low-confidence pick
falls back to the rule's own first verb. Every decision is logged as JSONL.

```
 anima-agent bridge (Rust, NDJSON)  ──observe──▶  scene.py   ──▶  affordances.py  ──▶  decision.py  ──▶  agent.py
 login · world · A* · packets       ◀───act─────  text scene       closed verb menu      logprob pick      gate · plan · log
                                    ◀───pump────                   + hard limits         (Qwen / jeff)     two-rate loop
```

## Why this shape — what was measured first

Everything here follows from tests run before a line of anima3 was written
(see [`hulryung/jev-testbed`](https://github.com/hulryung/jev-testbed)):

| Finding | Consequence |
|---|---|
| A model shown only *option names* (anima2's steering) is biased by wording, not deciding: GLiFormer → `fetch_gold` 0.60, Qwen → `buy_reagent` 0.94, both blind | The scene **must** carry state. `scene.py` states magnitudes as relations ("adjacent", "low"). |
| jeff/GLiFormer (400M encoder) reads presence/absence but not magnitude; on numeric JSON it is *confidently flat* (conf 0.6, state ignored); on "did this line break character?" it is anti-correlated (6/14) | jeff is **not** the default. It stays pluggable (`--backend jeff`) so the comparison can be re-run. |
| Qwen3-4B in logprob mode reads magnitudes (caught `gold 90 < cost 100`), 11/14 on the register test, **96–150 ms** per decision on an M5, $0 | Default backend. Off-thread with a deadline; never in the fast path. |
| Raw logprobs are overconfident on genuinely ambiguous states (0.81) | Confidence is a **gate**, not a truth. Threshold is a knob; calibration against outcomes is future work. |

## What it does today

- **Survive**: a dead character offers no verbs; below 35% HP next to a hostile the menu is *only* flee/bandage — the model cannot pick anything else, by construction.
- **Fight or not, by persona**: `pacifist` is never offered `attack`; `defensive` only when the threat is within 2 tiles.
- **Loot, greet, wander**: gold/ore on the ground, a greeting line from the persona's own `speech_examples` (closed vocabulary — a character cannot break character), terrain-aware wandering (`obs.terrain` walkability).
- **Two-rate loop**: fast tick = observe → menu → act → pump; slow = model decision every N ticks or on change, off-thread, deadline-bounded. An admitted pick becomes a short *plan* so steering is not diluted between decisions.
- **GM staging** (`anima3.gm`): `[CreateWorld nogump`, `[Add <creature>` — the same say → cursor → target pattern anima2 proved live.

## Run

```bash
uv venv --python 3.12 && uv pip install -e ".[qwen,jeff,dev]"
uv run pytest -q                                   # 17 tests, no server

# offline pocket world (FakeBody), local model:
uv run python -m anima3 --offline hostile --backend qwen --persona adventurer
uv run python -m anima3 --offline ambush  --backend qwen --persona miner
uv run python -m anima3 --offline town    --backend scripted        # the rule alone

# live: needs ServUO on :2593 and the bridge built in ../anima-client
( cd ../anima-client && cargo build --release -p anima-net && cp target/release/anima-agent target/release/anima-bridge )
uv run python -m anima3.gm --createworld            # once, as the owner account
uv run python -m anima3.gm --spawn Mongbat --dx 4   # something to meet
uv run python -m anima3 --backend qwen --persona adventurer --ticks 120 --monitor 8801
#   watch: http://127.0.0.1:8801/   (read-only spectator of the same session)
```

The model path defaults to `~/dev/jev/models/Qwen3-4B-4bit` (`ANIMA3_MLX_MODEL` to override).
`--backend jeff` needs `TYPESAFE_API_KEY` (+ `TYPESAFE_BASE_URL` for a self-hosted jeff).

## Measured

**Offline, Qwen backend** (`.logs/*-qwen.jsonl`), 20 ticks each:

| Scenario | Rule alone | With the model | Note |
|---|---|---|---|
| `hostile` (defensive adventurer, mongbat approaching) | flees forever, never engages | model **holds** (conf 0.55–0.70), the rule attacks when adjacent, kill at t=15, loots 25 gold | emergent: the rule alone cannot produce this |
| `ambush` (pacifist miner at 28% HP, 3 hostiles) | flees to **4%** HP | model picks **bandage** (conf 0.95) at critical HP, alternates with flee, min **18%** | the hard limit shrank the menu to flee/bandage; the model chose *which* |
| `town` (person + gold) | picks up gold, greets, wanders north forever | same start, then holds (conf 1.00) | passive but valid; temperament line was added to the scene afterwards |

Low-confidence picks (0.00 / 0.12 / 0.24) were rejected by the gate and the rule
acted instead. Decision latency 105–182 ms, mean ≈ 130 ms, warmup ≈ 1–2.5 s.

**Live, ServUO 127.0.0.1:2593, bridge schema 31**: login, observe, act, pump and
the spectator monitor all work with both backends; terrain reading forced a
direction change at a wall (walk north → walk east). Model decisions on the shard:
10/10 admitted, 117 ms mean. See *Live fight* below.

## Live fight (ServUO 127.0.0.1:2593, player-level account, Qwen backend, neutral disposition)

A GM session (`anima3.gm --spawn Mongbat`) put a mongbat near the start point while a
Player-level account (`anima3p`) played. Watched at `http://127.0.0.1:8802/`.

- Names resolved by click-to-name: the scene went from *a creature* to **a mongbat** (t=38).
- Approach worked on real terrain: far → near → close → **adjacent** (t=42–47), war mode on, attacking.
- One mongbat was killed by a **town guard** at t=66 (*"Thou hast suffered thy punishment, scoundrel."*) — the start point is a guarded town; the next staging step is `[Set X/Y` the player outside the guard zone.
- The other mongbat was **wounded** by our character from t=128; our HP never fell below 99% (a new character vs a mongbat).
- {'ticks': 150, 'model_calls': 51, 'model_admitted': 48, 'dead': False, 'gold': 1000, 'min_hp_pct': 0.99, 'avg_decision_ms': 155.0, 'reasons': {'rule (deciding)': 5, 'admitted': 48, 'plan': 94, 'confidence 0.20 < 0.35': 2, 'confidence 0.23 < 0.35': 1}}

Two live lessons that were not visible offline: `Attack{serial}` beyond one tile does
nothing (hence approach-then-attack), and staff accounts are ignored by monster AI
(hence the separate Player account for the agent — the same control ≠ play split anima2 kept).

## Economy: mine → smelt → craft → sell (live, earns gold)

`--economy` adds production verbs on anima2's calibrated Minoc ridge. Mechanics are
deterministic **procedures** (generators fed one Observation per tick: use tool →
wait for cursor → target → read the server's verdict); the model only chooses which
admissible verb comes next. Stage once with `python -m anima3.gm --stage-economy`
(owner account: forge, anvil, a pinned Blacksmith and Tinker, skills, tools).

```bash
uv run python -m anima3 --user anima3 --pass anima3 --economy --backend qwen --persona miner --ticks 400 --monitor 8801
```

| Run | Ticks | Gold | What happened |
|---|---|---|---|
| #6, Qwen | 400 (~2 min) | **1042 → 1187 (+145)** | tongs crafted in batches of 4 and sold (+28 each), daggers forged (3 ingots) and sold (10 g each, 4×), ingots ran 24 → 0 → mined and smelted back to 14; 48 procedures ok, 3 craft failures |
| #7, Qwen, from empty stock | 200 | 1330 → 1380 (+50) | mine 15, smelt 5, craft 5 tongs + 2 daggers, 5 sales — the loop restarts itself |

**Rule vs jeff vs Qwen, 150 ticks each, run back to back on the same character** (so the
world state carries over — the rule run inherited a 13-ingot stockpile; not a controlled A/B):

| Backend | Gold | Procedures ok | Model calls → admitted | Note |
|---|---|---|---|---|
| rule only | +76 | 17 (craft 9, sell 5) | — | spent the inherited stockpile |
| jeff | +21 | 1 | 62 → **0** | every pick was `walk:*` at confidence 0.01–0.10; a stock-empty menu had let the rule wander off the ridge (fixed since: a worker never wanders, `goto:*` uses fixed spots, `wait:work` is the floor) |
| Qwen | +46 | 21 (mine 8, craft 5, sell 5) | 6 → 5 | `mine` when stock was gone, `goto:forge` 0.97 with ore in hand |

Procedures own most ticks, so the model is consulted rarely (6–15 calls per run) and
its picks were the sensible ones; low-confidence picks (0.10–0.32) were rejected by the
gate and the rule acted. jeff contributed no admitted decision in any run.

**What the shard taught this loop** (each cost a live run): `[Add Forge` lands on the
ground's own height — it sat at **z=43 on the cliff** above the smith spot and
`DefBlacksmithy` ignored it (every attempt → 1044267) until `[Set Z 20`; `container is
not None` counted the vendors' stock and worn gear as "pack" (filter by the own backpack);
the newbie-kit dagger is unsellable; the starting smith hammer is not a crafting tool
(tongs are); the CraftGump reports outcomes inside the re-shown gump (1044043 fail,
1044154 made), not the journal; `Ore.cs` answers 501990 for an impure smelt (still a
success); a craft gump left open blocks every other tool.

Not yet: buying replacement tools when they wear out, banking, more recipes, and a
Player-level worker (the economy ran on the owner account because no monster is
involved; vendors and crafting treat staff like anyone else).

## Warrior: a Player-level character that hunts, equips, and loots (live)

The GM stages *another* character by serial (`python -m anima3.gm --stage-warrior SERIAL`:
teleport to anima2's unguarded pocket at 2587,408, Swordsmanship/Tactics/Anatomy/Healing 100,
Katana + plate + 200 bandages, two Mongbats beside it) while the Player-level account
plays (`--user anima3p --wait-ticks 40` so the staging lands before the first action).

| Run | Result |
|---|---|
| #1 | both mongbats killed (t≈76, 91), min HP 97.5%, no deaths — but `Equip` was chosen 39× (it never took) and nothing was looted |
| #2, two-packet equip | katana and plate equipped (lift → EquipReq on the item's layer), kills at t=15 and t=43, corpses opened and gold lifted — but gold stayed 1000: a lift is not a pickup |
| #3, lift → drop into backpack | kills by t=22, **two corpses looted, gold 1024 → 1030**; the 24 came from run #2's lifted gold bouncing into the pack at logout |

What the shard taught here: UO equips in two packets (PickUp then EquipReq — the server
uses the item's own layer), a pickup is also two packets (PickUp then Drop into the
backpack), and `corpse_of` death links are transient — remember them or you never loot.
A procedure that is interrupted by a hostile within 3 tiles (equipping mid-approach)
simply resumes later; three interruptions, two successes.

Left open: after the fight the idle menu (wander/hold) draws 0.1–0.3 confidence from the
model, so ~95% of post-fight calls fall back to the rule — the survival-mode idle menu
needs the same "workplace" floor the economy got.

## Village: several characters living together (live)

```bash
uv run python -m anima3.village --roster anima3m:miner:economy anima3w:warrior:hunt --ticks 2000
```

One process, one MLX model (forward passes serialized), one Laya triage, one GM. Each
roster entry is `account:persona:mode`. The GM renames each character to its persona,
stages it (the ridge economy for a worker; kit, skills and pinned prey on the open ground
south-west of the ridge for a hunter), hides, then resurrects anyone who dies and respawns
prey. Every agent runs on its own thread against its own bridge — and hears the others.

Three layers cooperate:

| Layer | Model | Latency | Does |
|---|---|---|---|
| triage | **Laya** (421M encoder) | ~40 ms | classifies a heard line — greeting / question / trade / threat — into `reply:` / `ignore:` verbs |
| decision | **Qwen3-4B, logprob** | ~150 ms | picks one verb from the closed menu; procedures execute it |
| voice | **Qwen3-4B, generative** | 1–3 s, off-thread | the reply itself; every 150 ticks an in-character *aim* placed in the scene, and a private chronicle |

Every generated line is screened by Laya's AI-voice detector before it is spoken.

### What a run looks like (village #6, 1000 ticks, no deaths)

- **Grimm** (miner): resurrected at 30% HP, bandaged himself to 65% (Healing +0.7), then mined,
  smelted, forged three daggers and sold them — Mining +3.0, Blacksmith +0.5, Tinkering +0.4.
- **Ragnar** (sellsword): killed and looted every respawn, gold +106, never below 97% HP.
- Grimm's chronicle: *"Mined sixteen good veins. Smelted three impure, two clean. Crafted one
  blade. Mining up a bit. Tools still sharp. Keep going. Work speaks louder than words."*
- Ragnar's: *"Ridge clear. For now. Mongbat again. Stupid thing. Killed two. Gold adds."*

### Village #9 (2000 ticks, ~10 min, no deaths, after the fixes below)

| | gold | skills | notes |
|---|---|---|---|
| Grimm | 1230 → **1747** | Mining 49.4 → **52.0**, Blacksmith +0.7, Tinkering +1.1 | sold a stack of tongs for 292, forged and sold daggers, mined the vein between |
| Ragnar | 1655 → **1925** | Swords +1.2 | 545 decisions, every respawn killed and looted, greeted Grimm at tick 1 |

Of 972 logged model decisions across all runs, 76% were admitted and 69% of those differed
from the rule's first verb — but most of that steering was *into `hold`* (walk→hold 220,
sell→hold 45, equip→hold 59). The genuine re-orderings were in the economy menus
(craft→mine 19, flee→mine 10, craft→smelt 8). `hold` now competes only with wandering.
Median confidence is 1.00: the logprob head is overconfident and still uncalibrated.

`python -m anima3.calibrate` labels each admitted decision by a proxy outcome (over the next 30
ticks: gold or a skill point gained, or a threat survived without losing a tenth of health) and
fits a temperature. On 505 decisions: base rate 0.35, **ECE 0.585 raw** — decisions made at
confidence ≥ 0.9 were good 39% of the time, at 0.7–0.9 only 5%. Temperature scaling reaches 0.30
at best. The confidence measures how clearly the model read the menu, not whether the pick will
pay off; a head trained on outcomes is what would change that, and these logs are its dataset.
The label is a crude proxy (procedures outlast the horizon; `hold` never scores), so treat the
numbers as a direction, not a verdict.

### Skill progression toward 7×GM

`progression.py` names each profession's seven skills — all of them reachable by verbs this
brain has: work skills by the economy and combat, the rest (ArmsLore, ItemID, Hiding,
Meditation, Anatomy…) by `train:<Skill>` practice verbs that invoke the skill (`UseSkill`)
and target gear or a bystander when the skill asks. The curriculum re-orders admissible work
so the largest gap trains first; the scene states the gaps; a hunter practises between fights.
A miner whose pickaxe wears out (50 swings) tinkers a new one from four ingots.

Village #10, 3000 ticks (~25 min, no deaths): Grimm Mining 52.0 → **55.3**, gold +483;
Ragnar Swords +1.9, **ArmsLore +9.5, Hiding +9.6** (idle time turned into practice), gold +293.
Grandmaster is hours of running away, not minutes — the mechanism is what is verified here.

### What the shard taught the village (each cost a run)

- **The warrior murdered the miner.** Serial 12475 — the "mongbat" Ragnar fixated on for
  600 ticks — was Grimm. He had looted a corpse that was not his kill, went **criminal**,
  and an aggressive warrior attacked him: four of village #5's five deaths. Now only
  attributed kills are looted and roster members are `friends` who are never threats.
- **Distance is x/y only.** A mongbat twenty tiles down the cliff read as *adjacent*; the
  server had no line of sight and never swung. Targets on another level are noted, never chased.
- **The ridge is a corridor.** Three tiles wide south of the vein; prey staged in it wandered
  to the miner. Prey now sits on the open ground at (2604,490), pinned (`[Set CantWalk true`).
- **Pinning pinned the warrior.** He teleported in beside the spawn a tick earlier and was
  taken for the new mongbat — four runs of "lost" chases before `[Get CantWalk` said `True`.
- **Weight.** Three staged kits plus loot put him at 252/250 stones: UO refuses every step,
  and a near-full pack drains a 12-stamina character in two steps. Surplus is put down from 75%.
- **Staging resets skills.** `[Set Skills.Mining.Base 45` every run erased the gains; the GM now keeps a trained skill.
- Fresh characters resurrect at ~20% HP: the economy kit includes bandages.

## Duels: PvP under the old pit rules, refereed by the shard

```bash
uv run python -m anima3.duel --referee server --a anima3d1:duelist_a:qwen --b anima3d2:duelist_b:scripted \
    --rules 5x --weapon katana --armor leather --rounds 3
```

Two Player-level characters, each with its **own decision backend** (`scripted` = the rule,
`qwen`, `jeff`), fight in the shard's arena. ServUO's built-in PVP Arena System is
High-Seas-only, so a T2A-compatible duel service was added to the shard
(`Scripts/Services/Dueling`, built in a sibling session): a fenced 9×5 ring at (2598–2606,
489–493), `[Challenge <name> <rounds> <rules>` / `[Accept` spoken by the fighters themselves,
5x/7x enforced as "the eight duelling skills sum to ≤ 500/700", a weapon token enforced by
unequipping, no criminal flags (the two are *enemy* to each other for the match), items kept on
death, resurrection at the marks, 5-second countdowns, a 180 s round limit, and fixed
`[Duel] …` journal lines the brain reads as state: `FIGHT!` sets the opponent, `Round N:` clears
it, `Match:` ends. `--referee gm` keeps the older script-refereed mode (open ground, no arena).

Watch from a spectator account in anima-client's own renderer: `target/release/play 127.0.0.1
2593 anima3spec anima3spec 8090 web ~/dev/uo/uo-resource`, then `[Set X 2602 Y 495 Z 20` and
`[Set Blessed true` on it — the seat against the south fence sees the whole floor, and it stays
up between matches (a bridge's `--monitor` view lives only while that bridge runs).

**Batch under the shard's referee** (5x, katana, leather, best of five, same character pair,
Kael on the left):

| Kael's backend | vs Rook (rule) | rounds | Kael's bandages |
|---|---|---|---|
| rule | **Kael 3 – 1** | 162 s, 70 s, 112 s, 84 s — ~40 swings each | 2 |
| **Qwen** (logprob) | **Rook 2 – 0**, three 180 s draws | draws end with both under 40% | **0** |
| **jeff** (encoder) | **Rook 3 – 0** | 65 s, 45 s, 71 s | 0 |

The rule-vs-rule match shows the setup is symmetric (the dice decide). Against the same rule,
the raw Qwen head never bandages — when *attack* was on its menu it chose it **115 of 115
times** in the first match, at 35–45% health included — so it loses the slugfests and only
draws by running under 35%. jeff, half of whose picks fell back to the rule anyway, lost every
round. Neither decision head adds tactical judgement here; the one thing the hand rule knows
(bind your wounds under 45%) is exactly what decides a 5x duel.

What the ring taught: gear goes to the corpse on death in Felucca (the shard's duel service now
keeps it); re-applying a bandage every tick cancels the previous one, so bandaging is a
procedure that waits for "You finish applying the bandages"; a referee that de-duplicates
journal lines by text drops the second `FIGHT!`.

## Layout

| File | Role |
|---|---|
| `contract.py` | typed views over the bridge JSON (schema 31) + action builders |
| `body.py` | `BridgeBody` (NDJSON subprocess, monitor) · `FakeBody` (offline world) |
| `scene.py` | Observation → text scene + derived `Facts` |
| `affordances.py` | the closed verb menu, rule-ordered, hard limits |
| `decision.py` | `Scripted` · `QwenLogprob` (MLX) · `JeffChoice` · `gate()` |
| `agent.py` | two-rate loop, off-thread decisions, plans, JSONL log |
| `gm.py` | GM staging over the same bridge |
| `personas/` | YAML personas: miner Grimm, adventurer Anima, warrior Ragnar |
| `progression.py` | skills, profession GM sets, curriculum ordering |
| `triage.py` · `speech.py` | Laya speech triage · generated replies, aims, chronicle |
| `village.py` | several characters, one process, GM staging/resurrection |
| `duel.py` | refereed PvP: 5x/7x templates, weapon/armour rules, per-side backends; server or GM referee |
| `calibrate.py` | outcome-labelled temperature scaling over the decision logs |

## Not yet

- No economy (mine/smelt/craft/sell/bank) — the verb menu is survival, loot, greet, wander. Adding a verb is one `Affordance` with its actions; the model needs no change.
- Confidence is uncalibrated. The JSONL log is the dataset for calibrating it — or for training the real System One head on outcomes.
- One character per process; no memory beyond "greeted" and "engaged".
