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
| `personas/` | anima v1 YAML personas (miner = Grimm, adventurer = Anima) |

## Not yet

- No economy (mine/smelt/craft/sell/bank) — the verb menu is survival, loot, greet, wander. Adding a verb is one `Affordance` with its actions; the model needs no change.
- Confidence is uncalibrated. The JSONL log is the dataset for calibrating it — or for training the real System One head on outcomes.
- One character per process; no memory beyond "greeted" and "engaged".
