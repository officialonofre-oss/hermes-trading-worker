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
uv run python -m hermes_trading.backtest --asset BTC/USDT --days 30  # backtest the live strategy
uv run python -m hermes_trading.reflect --fallback     # validate + apply one reflection cycle
```

There is no test suite, linter, or formatter configured in this repo yet — don't invent
`pytest`/`ruff` invocations that aren't backed by config. Ad hoc verification during
development has been done with one-off scripts run via `uv run python3 - <<'EOF' ... EOF`
(synthetic price series, mocked `httpx` responses) rather than a checked-in test suite.

Note: `main.py` at the repo root is unrelated boilerplate (`print("Hello from hermes-trading!")`)
left over from `uv init`; it is not wired into the worker. The real entry point is
`hermes_trading/run.py`, invoked as a module (this is also what the `Dockerfile` CMD does).

## Secrets

`.env` is gitignored (it was tracked once, in the initial commit — do not re-add it).
`.env.example` is the checked-in template documenting which vars exist. Adapters call
`dotenv.load_dotenv()` themselves on import, so any `hermes_trading.*` entry point picks up
a local `.env` automatically; on Render, env vars come from the dashboard/`render.yaml`
instead, and `.env` doesn't exist there.

## Architecture

**Entry & loop** (`hermes_trading/run.py` → `hermes_trading/loop.py`)
`run.py` loads `state/goal.yaml` for the target asset/objectives, then hands off to
`trading_loop()` in `loop.py`, which runs forever on a 60s cycle:
1. Fetch data from the four adapters (price, onchain, news, macro) — all called every cycle,
   but only price's RSI actually drives the entry decision today; onchain/news/macro are
   fetched and discarded, not yet part of the decision logic (see Adapters below).
2. Reload `state/strategy.yaml` fresh each cycle (so a reflection run picked up between
   cycles takes effect immediately, no restart needed).
3. Close any open position first (`close_open_trades`): checks the current price against
   that trade's stop-loss/take-profit levels and closes it with a real `pnl` if hit.
4. Apply the entry rule (RSI threshold via `evaluate_entry`) and, if triggered **and** there
   is no open position for this asset already (`has_open_position`), record a paper trade.
   The one-position-at-a-time guard exists because nothing else prevents the loop from
   opening a new near-duplicate trade every single cycle while RSI stays under threshold.
5. Write a heartbeat to `state/heartbeat.json`.
6. On exceptions: increment a failure counter, sleep 30s, and retry; after 5 consecutive
   failures the loop breaks (circuit breaker) rather than spinning forever.

**Strategy rules** (`hermes_trading/strategy_rules.py`)
`evaluate_entry(rsi, strategy)` and `evaluate_exit(entry_price, current_price, strategy)` are
pure functions with no I/O. They exist specifically so `loop.py` (live) and `backtest.py`
(historical replay) evaluate a strategy identically — do not reimplement entry/exit logic
inline in either place; import from here instead.

**Adapters** (`hermes_trading/adapters/`)
Each adapter (`price.py`, `onchain.py`, `news.py`, `macro.py`) is an independent async
function returning a dict tagged with `schema_version`, and all degrade gracefully to
plausible fallback data rather than raising, so the loop never crashes on a data-source
outage:
- `price.py` — real data via `yfinance` (`_to_yfinance_ticker` maps `"ETH/USDT"` →
  `"ETH-USD"`), RSI computed by the shared `compute_rsi()` over the fetched window; falls
  back to randomized data on any failure.
- `onchain.py` — real data via Glassnode's active-addresses endpoint if `GLASSNODE_API_KEY`
  is set; falls back to a static `active_addresses: 0` stub if the key is absent or the call
  fails.
- `news.py` — real data via CryptoPanic if `CRYPTOPANIC_API_KEY` is set (sentiment derived
  from aggregate post upvotes/downvotes in `_derive_sentiment`); falls back to a static
  `"neutral"` stub if the key is absent or the call fails.
- `macro.py` — still a hardcoded stub (DXY/fed funds); no provider wired up yet.

Onchain/news being wired up is necessary but not sufficient for them to affect trading:
`loop.py` and `strategy_rules.py` still only look at price/RSI. Feeding these signals into
the actual entry/exit decision is future work, not something to assume is already wired.

**Backtesting** (`hermes_trading/backtest.py`)
Replays historical hourly closes (via `yfinance`) through the exact same `strategy_rules`
functions the live loop uses, enforcing one open position at a time (a backtest-only
simplifying assumption — see the guard note above; live now matches this too).
`RSI_WINDOW = 72` mirrors `price.py`'s `period="3d", interval="1h"` live window. Each run
prints a summary (trade count, win rate, return, score) and appends a record to
`state/backtests.jsonl`.

**Scoring** (`hermes_trading/score.py`)
`score_trades()` turns closed trades + `state/goal.yaml` targets (target return, max
drawdown, min Sharpe) into a single composite score in `[-1, 1]`, weighted 50/30/20 across
return/drawdown/Sharpe. `max_drawdown()` and `sharpe_ratio()` are computed from the actual
closed-trade return series (peak-to-trough equity for drawdown; mean/stdev of per-trade
returns, not annualized, for Sharpe — trades are event-driven, not fixed-period, so there's
no natural annualization factor).

**Reflection / self-tuning** (`hermes_trading/reflect.py`)
A separate CLI (`--fallback` or `--hermes`) that mutates the live strategy rather than just
reading it:
- `propose_change()` proposes one deterministic rule change per run (loosen RSI entry
  threshold, else tighten stop loss, else no change) without touching the live file.
- The candidate is **backtested against the same historical window as the current strategy**
  (via `backtest.fetch_history`/`simulate`/`summarize`) before being committed — it's only
  applied if its score is `>=` the baseline's. If historical data can't be fetched, or
  there's too little of it, the change is skipped rather than applied blind.
- Only on acceptance: archives the current `state/strategy.yaml` to
  `state/history/v{old}.yaml`, then writes the candidate with a bumped `version` (zero-padded,
  e.g. `"01"` → `"02"`).
- Every outcome — accepted or rejected, including "no change needed" — is appended to
  `state/hypotheses.jsonl`, so it's a log of ideas tried, not just ideas adopted.
- `--hermes` (LLM-driven reflection) is a stubbed-out mode — not implemented yet, currently
  just prints a message and exits. Not planned to call an external LLM API in production;
  if built, treat it as development-time tooling rather than a live per-cycle call.
- `goal.yaml.one_variable_only: true` reflects the design intent: reflection should change
  one strategy parameter at a time so its effect can be isolated in scoring.

**State directory** (`state/`) is the persistent, mutable heart of the system, separate from
code:
- `goal.yaml` — static objectives (target return, max drawdown, min Sharpe, reflection cadence).
- `strategy.yaml` — the live, versioned strategy; edited in place by `reflect.py` only on an
  accepted change, read fresh every loop cycle by `loop.py`.
- `history/vNN.yaml` — immutable snapshots of every past strategy version.
- `hypotheses.jsonl` — append-only log of every reflection decision (accepted or rejected)
  and its rationale/backtest scores.
- `backtests.jsonl` — append-only log of every manual `backtest.py` run.
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
