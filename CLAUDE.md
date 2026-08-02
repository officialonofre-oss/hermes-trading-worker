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
uv run python -m hermes_trading.dashboard              # generate state/dashboard.html
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
1. Fetch data from the four adapters (price, onchain, news, macro) every cycle. Macro is
   still fetched and discarded (see Adapters below); price/onchain/news all feed the entry
   decision now (see Strategy rules).
2. Reload `state/strategy.yaml` fresh each cycle (so a reflection run picked up between
   cycles takes effect immediately, no restart needed).
3. Close any open position first (`close_open_trades`): checks the current price against
   that trade's stop-loss/take-profit levels and closes it with a real `pnl` if hit.
4. Apply the entry rule (`evaluate_entry`, RSI + sentiment + onchain trend) and, if triggered
   **and** there is no open position for this asset already (`has_open_position`), record a
   paper trade with the signals that led to it (`paper_trade(..., signals={...})`). The
   one-position-at-a-time guard exists because nothing else prevents the loop from opening a
   new near-duplicate trade every single cycle while RSI stays under threshold.
5. Write a heartbeat to `state/heartbeat.json`. This is not just a liveness ping — it also
   records the current signal readings and each entry gate's state (via `explain_entry`),
   so a long quiet stretch is legible ("RSI 45.2, needs < 28") instead of looking like a
   stalled worker. The dashboard's "Right Now" panel renders it.
6. On exceptions: increment a failure counter, sleep 30s, and retry; after 5 consecutive
   failures the loop breaks (circuit breaker) rather than spinning forever.

**Strategy rules** (`hermes_trading/strategy_rules.py`)
`evaluate_entry(rsi, strategy, sentiment="neutral", onchain_trend="unknown")` and
`evaluate_exit(entry_price, current_price, strategy)` are pure functions with no I/O. They
exist specifically so `loop.py` (live) and `backtest.py` (historical replay) evaluate a
strategy identically — do not reimplement entry/exit logic inline in either place; import
from here instead.

`explain_entry(...)` applies the same four gates as `evaluate_entry` but reports each one's
pass/block state instead of short-circuiting, so the dashboard can show *why* no trade
fired. **These two must stay in agreement** — `evaluate_entry` returns a decision exactly
when `explain_entry` reports zero blocking gates. If you change one, change the other, and
re-run the exhaustive property check (all combinations of rsi/threshold/direction/sentiment/
trend) that asserts the equivalence.

RSI is the primary signal (`entry.threshold` in `strategy.yaml`); sentiment and onchain
trend are a **veto, not a confirmation requirement** — `sentiment == "bearish"` or
`onchain_trend == "declining"` blocks an otherwise-valid entry, but the default/missing
values (`"neutral"`/`"unknown"`) never do. This is deliberate: a brief outage on either free
data source shouldn't silently stop the strategy from trading at all. Both signals are
computed by pure, shared classifier functions (`news.classify_sentiment`,
`onchain.classify_trend`) so live and backtest always agree — see Adapters and Backtesting.

**Adapters** (`hermes_trading/adapters/`)
Each adapter (`price.py`, `onchain.py`, `news.py`, `macro.py`) is an independent async
function returning a dict tagged with `schema_version`, and all degrade gracefully to
plausible fallback data rather than raising, so the loop never crashes on a data-source
outage:
- `price.py` — real data via `yfinance` (`_to_yfinance_ticker` maps `"ETH/USDT"` →
  `"ETH-USD"`), RSI computed by the shared `compute_rsi()` over the fetched window; falls
  back to randomized data on any failure.
- `onchain.py` — real data via Coin Metrics' free Community API (active-addresses metric,
  `AdrActCnt`), no key required; falls back to a static `active_addresses: 0` stub if the
  call fails or returns no usable data. (Glassnode was evaluated first but its free/low
  tier only exposes a 50-calls/day "Light API" — too limited for a 60s polling loop — so
  Coin Metrics' free tier was used instead.) `fetch_onchain` also fetches a
  `TREND_LOOKBACK_DAYS` (12-day) window of daily values and classifies a `trend`
  (`declining`/`stable`/`growing`/`unknown`) via `classify_trend()`, comparing the latest
  value to the one 7 entries back — a positional-window comparison, same style as
  `price.py`'s RSI, not a date-matching lookup.
- `news.py` — real data via the Alternative.me Crypto Fear & Greed Index, no key required;
  maps the 0-100 index into `bearish`/`neutral`/`bullish` via `classify_sentiment()`. This is
  a market-wide index, not per-asset, so every symbol gets the same value. (CryptoPanic was
  evaluated first but its usable API tier is $50/week — too expensive for what this needs
  — so the free Fear & Greed Index was used instead.) Falls back to a static `"neutral"`
  stub if the call fails or returns no usable data.
- `macro.py` — still a hardcoded stub (DXY/fed funds); no provider wired up yet.

**Backtesting** (`hermes_trading/backtest.py`)
Replays historical hourly closes (via `yfinance`) through the exact same `strategy_rules`
functions the live loop uses, enforcing one open position at a time (a backtest-only
simplifying assumption — see the guard note above; live now matches this too).
`RSI_WINDOW = 72` mirrors `price.py`'s `period="3d", interval="1h"` live window. To keep the
sentiment/onchain veto backtest-faithful, `fetch_sentiment_history()` and
`fetch_onchain_trend_history()` pull the same free APIs' historical series (daily
resolution) and build `{date: classification}` lookups that `simulate()` joins to each
hourly bar by date; both return `{}` on failure so a data-source outage degrades the same
way live does (veto simply never fires) rather than crashing the backtest. These functions
do their own synchronous `httpx` calls rather than reusing the async `fetch_onchain`/
`fetch_news` — same pattern price history fetching already uses (own fetch, shared pure
classifier). Each run prints a summary (trade count, win rate, return, score) and appends a
record to `state/backtests.jsonl`.

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
  (via `backtest.fetch_history`/`fetch_sentiment_history`/`fetch_onchain_trend_history`/
  `simulate`/`summarize`, fetched once and reused for both the baseline and candidate runs)
  before being committed — it's only applied if its score is `>=` the baseline's. If
  historical price data can't be fetched, or there's too little of it, the change is skipped
  rather than applied blind.
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

**Dashboard** (`hermes_trading/dashboard.py` + `dashboard_template.html`)
Read-only: reads `state/*.jsonl`/`*.yaml` and renders `state/dashboard.html`, a static
single-page view of what the bot has traded, why (the `signals` recorded on each trade —
RSI/sentiment/onchain trend), and reflection/backtest history. `build_data()` assembles a
plain dict (reusing `score.max_drawdown`/`sharpe_ratio` for the summary tiles); `render()`
does a single string substitution of that dict as JSON into `__DASHBOARD_DATA__` in the
template — no templating engine dependency. **`render()` escapes `<` to `<` in that
JSON before embedding it** — every field today is a fixed enum or admin-set config, but the
page is publicly reachable, so the first free-text field anyone adds (a real headline, an
API error string) would otherwise be a stored-XSS vector via `</script>`. Keep the escape
if you touch `render()`. The "Right Now" panel is driven by `heartbeat.json` and is always
real (never sample) — it's hidden entirely if the loop hasn't completed a cycle yet. The
rest of the template's JS falls back to
hardcoded sample data **per section** (trades/hypotheses/backtests independently) when a
given `state/*.jsonl` is still empty, so early-history real data (e.g. an existing
`hypotheses.jsonl` entry) is never masked just because `trades.jsonl` happens to be empty
— never assume the whole page is sample just because one section shows a sample banner.
Regenerate after state changes; it does not auto-refresh itself, and `state/dashboard.html`
is gitignored (generated, like `trades.jsonl`).

**Debug server** (`hermes_trading/debug_server.py`)
A small stdlib-only (no new dependency) read-only HTTP server, started by `run.py` in a
background thread alongside `trading_loop()` so the live worker is inspectable without
shelling in. Routes: `/health` (always open), `/dashboard` (same `dashboard.build_data()`/
`render()` as the CLI tool, live), `/state/{trades,hypotheses,backtests}` (JSON arrays from
the matching `.jsonl`), `/state/heartbeat` (JSON). Deliberately whitelists exact file keys
rather than accepting a path from the request — no path-traversal surface. All routes except
`/health` are gated by `DEBUG_TOKEN` if that env var is set; unset, they're open.

Auth accepts three credentials, in order: an `Authorization: Bearer <token>` header (for
curl/API use), an `hermes_debug` cookie, or a `?token=` query param. The query-param path is
deliberately **one-shot**: it responds `302` with an `HttpOnly` cookie and redirects to the
same path with the query stripped, so the token lands in browser history and request logs
once at setup rather than on every visit. Practically this means: paste
`/dashboard?token=…` once on a phone, then bookmark the clean `/dashboard` and it keeps
working. `Secure` is set on the cookie only when `X-Forwarded-Proto: https` (which Render
sets), so local http testing still works. Token comparison uses `hmac.compare_digest`. **Important:** this only
becomes internet-reachable if the Render service is a Web Service — Render does not route
public traffic to Background Workers, hence `render.yaml`'s `type: web` (see Deployment
below). The server binds `$PORT` (falling back to 8080) either way, so it also works for
local testing regardless of Render service type.

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
- `dashboard.html` — generated by `dashboard.py` (not checked in).

Because `strategy.yaml` and `goal.yaml` are read from disk on every cycle/run (not passed
as code constants), changing strategy behavior is normally a data change in `state/`, not
a code change — keep that distinction in mind when a task asks to "adjust the strategy."

## Deployment

Runs as a Render.com Web Service (`render.yaml`, `type: web` — not a Background Worker;
see the note below on why): built from the `Dockerfile` (`python:3.11-slim` + `uv`), with
`state/` mounted on a persistent disk so strategy versioning and trade history survive
restarts/deploys. It's a "web" service because of the debug server (below), not because
the trading loop itself serves HTTP — `run.py` still runs `trading_loop()` as its main
work, the debug server is just a background thread alongside it. `HERMES_TRADING_MODE`
defaults to `paper`; `HERMES_TRADING_I_ACCEPT_RISK` defaults to `false` — there is no
live-trading code path currently, so these are forward-looking safety flags rather than
active switches.

**Persistent-disk seeding gotcha:** Render's disk mounts at `/app/state` — the same path
the Dockerfile would otherwise bake default state files into. On first boot the disk is
empty and *shadows* whatever the image put there, so a plain `COPY state ./state` means
`goal.yaml` etc. are invisible at runtime even though they're clearly present in the image
(this crashed the very first real deploy with `FileNotFoundError: state/goal.yaml`, from
`run.py`'s `load_goal()`, which had no fallback). The fix: the Dockerfile copies defaults to
`./state_defaults` instead, and `docker-entrypoint.sh` seeds `/app/state` from there only
when `goal.yaml` is missing — so first boot gets seeded, but a disk that already has real
(possibly reflection-evolved) state is never overwritten. Keep this seed-if-missing logic
if `state_defaults`/the entrypoint ever change; don't revert to copying straight into
`./state` in the Dockerfile.

**Worker vs Web Service history:** this originally deployed as `type: worker`, which
immediately made the debug server unreachable (Render doesn't route public traffic to
Background Workers). Render cannot change an existing service's type in place — only
delete-and-recreate — so getting to the current `type: web` config required deliberately
deleting and recreating the live Render service, not just editing `render.yaml`. If this
ever needs to move back to a plain worker (no debug server), the same rule applies: changing
the YAML alone won't touch the live service.
