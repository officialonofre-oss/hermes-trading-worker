# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Hermes is an autonomous crypto paper-trading worker. It runs an infinite loop that pulls
price/on-chain/news/macro data, applies a simple rule-based strategy to decide on trades,
logs paper trades, and periodically "reflects" on performance to mutate its own strategy
file — a self-adjusting trading bot, not a live-execution system (paper mode only; no real
order placement exists in this codebase).

## Commands

Dependency management is via `uv` (see `uv.lock`, `pyproject.toml`, `.python-version` = 3.11).

```bash
uv sync                                          # install dependencies
uv run python -m hermes_trading.run              # run the trading worker (real entry point)
uv run python -m hermes_trading.run --asset ETH/USDT   # override the asset from state/goal.yaml
uv run python -m hermes_trading.reflect --fallback     # run one deterministic reflection cycle
```

There is no test suite, linter, or formatter configured in this repo yet — don't invent
`pytest`/`ruff` invocations that aren't backed by config.

Note: `main.py` at the repo root is unrelated boilerplate (`print("Hello from hermes-trading!")`)
left over from `uv init`; it is not wired into the worker. The real entry point is
`hermes_trading/run.py`, invoked as a module (this is also what the `Dockerfile` CMD does).

## Architecture

**Entry & loop** (`hermes_trading/run.py` → `hermes_trading/loop.py`)
`run.py` loads `state/goal.yaml` for the target asset/objectives, then hands off to
`trading_loop()` in `loop.py`, which runs forever on a 60s cycle:
1. Fetch data from the four adapters (price, onchain, news, macro) — all called every cycle
   regardless of whether the strategy uses them yet.
2. Reload `state/strategy.yaml` fresh each cycle (so a reflection run picked up between
   cycles takes effect immediately, no restart needed).
3. Apply the entry rule (currently a bare RSI threshold check) and, if triggered, record a
   paper trade.
4. Write a heartbeat to `state/heartbeat.json`.
5. On exceptions: increment a failure counter, sleep 30s, and retry; after 5 consecutive
   failures the loop breaks (circuit breaker) rather than spinning forever.

**Adapters** (`hermes_trading/adapters/`)
Each adapter (`price.py`, `onchain.py`, `news.py`, `macro.py`) is an independent async
function returning a dict tagged with `schema_version`. They are designed to degrade
gracefully: `price.py` tries `yfinance` for real BTC-USD data and computes RSI manually
from hourly closes, but falls back to plausible randomized data on any failure so the loop
never crashes on a data-source outage. `onchain.py` and `news.py` currently return static
stub/placeholder data (no real API wired up) — extending them to call GLASSNODE_API_KEY /
NEWS_API_KEY (declared but unused in `.env`) is a likely future task, not something already
working.

**Scoring** (`hermes_trading/score.py`)
`score_trades()` turns closed trades + `state/goal.yaml` targets (target return, max
drawdown, min Sharpe) into a single composite score in `[-1, 1]`, weighted 50/30/20 across
return/drawdown/Sharpe. Note the Sharpe term is a placeholder formula, not a real Sharpe
calculation.

**Reflection / self-tuning** (`hermes_trading/reflect.py`)
A separate CLI (`--fallback` or `--hermes`) that mutates the live strategy rather than just
reading it:
- Archives the current `state/strategy.yaml` to `state/history/v{old}.yaml` before changing anything.
- `--fallback` applies one deterministic rule per run (loosen RSI entry threshold, else
  tighten stop loss) and bumps `strategy.yaml`'s `version` (zero-padded, e.g. `"01"` → `"02"`).
- Appends a record of what changed and why to `state/hypotheses.jsonl` (append-only audit log).
- `--hermes` (LLM-driven reflection) is a stubbed-out mode — not implemented yet, currently
  just prints a message and exits.
- `goal.yaml.one_variable_only: true` reflects the design intent: reflection should change
  one strategy parameter at a time so its effect can be isolated in later scoring.

**State directory** (`state/`) is the persistent, mutable heart of the system, separate from
code:
- `goal.yaml` — static objectives (target return, max drawdown, min Sharpe, reflection cadence).
- `strategy.yaml` — the live, versioned strategy; edited in place by `reflect.py`, read fresh
  every loop cycle by `loop.py`.
- `history/vNN.yaml` — immutable snapshots of every past strategy version.
- `hypotheses.jsonl` — append-only log of every reflection decision and its rationale.
- `trades.jsonl` / `heartbeat.json` — generated at runtime by `loop.py` (not checked in).

Because `strategy.yaml` and `goal.yaml` are read from disk on every cycle/run (not passed
as code constants), changing strategy behavior is normally a data change in `state/`, not
a code change — keep that distinction in mind when a task asks to "adjust the strategy."

## Deployment

Runs as a Render.com background worker (`render.yaml`): built from the `Dockerfile`
(`python:3.11-slim` + `uv`), with `state/` mounted on a persistent disk so strategy
versioning and trade history survive restarts/deploys. `HERMES_TRADING_MODE` defaults to
`paper`; `HERMES_TRADING_I_ACCEPT_RISK` defaults to `false` — there is no live-trading code
path currently, so these are forward-looking safety flags rather than active switches.
