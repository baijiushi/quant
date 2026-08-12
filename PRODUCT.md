# oversell Product Brief

## Product

oversell is a local-first A-share strategy research workbench. It combines TUShare market data, SQLite storage, configurable stock-selection strategies, market breadth, AI-assisted research, entry planning, charts, and backtests in one desktop-oriented web interface.

## Primary user

- An individual A-share researcher who is learning and iterating quantitative strategies.
- Comfortable with financial terms, but should not need to read terminal output or edit configuration files for routine use.
- Works mainly on a desktop browser and values visible progress, reproducibility, and fast parameter iteration.

## Core jobs

1. Choose a strategy, universe, data mode, and trade date without accidental long-running updates.
2. Start, monitor, and safely stop a selection run; understand exactly where it is and why it failed.
3. Scan candidates quickly, compare factor values, and open a stock's chart and entry plan directly.
4. Understand the broad-market risk regime before interpreting a stock signal.
5. Add evidence, request AI scoring, and distinguish sourced facts from model inference.
6. Replay strategies over multiple holding periods with reusable indicator caches.

## Product principles

- Data first: tables, progress, and decisions take precedence over decoration.
- Transparent: every long task exposes its stage, progress, logs, errors, and cancellation state.
- Local first: TUShare credentials stay local; SQLite and generated outputs remain portable with the project.
- Strategy independent: data, strategy, AI review, and backtest layers communicate through standard models.
- Research, not promises: the interface must not imply guaranteed returns or hide missing evidence.
- Safe defaults: repeated parameter experiments default to local data rather than a network refresh.

## Information architecture

- Selection workbench: run setup, task monitor, market breadth, candidates, and failure report.
- AI review: sector regime, candidate scorecards, streamed model output, and source links.
- Research library: user-verified notes from videos, announcements, reports, and public posts.
- Entry and chart: K-line chart, trend/interception plan, position sizing, and historical signal review.
- Backtest: date range, holding periods, cached indicators, metrics, trades, and rankings.

## Constraints

- Vue 3 + TypeScript + Vite frontend, FastAPI backend, SQLite storage, and ECharts charts.
- TDesign Vue Next is the primary component library.
- Simplified Chinese is the interface language.
- Desktop-first at 1280px and above, with a usable compact layout down to mobile widths.
- Single local user in the first version; no authentication or remote multi-user deployment.

