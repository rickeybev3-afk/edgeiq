# Workspace

## Overview

pnpm workspace monorepo using TypeScript. Each package manages its own dependencies.

## Streamlit Volume Profile Dashboard

A Python Streamlit app (`app.py`) that visualizes Volume Profile structures for small-cap stocks with advanced analysis engine.

### Features
- **4-Tab Layout** — Main Chart | Scanner | Journal | 🧠 Tracker
- Fetch 1-minute historical bars from Alpaca via `alpaca-py` (SIP / IEX feeds)
- Live WebSocket stream mode via Alpaca `StockDataStream` — 2-second auto-refresh
- **MarketBrain** — real-time IB tracker + structure predictor; compares Predicted vs Actual live on each analysis run; persists IB state across Streamlit reruns via session state; color-coded prediction badge shown below Model Prediction
- **Position Management** (sidebar) — Enter/Exit buttons with entry price, shares, structure at entry; live P&L % and $ badge; MFE peak tracking per bar; persists to `trade_state.json`; auto-preloads last analysis ticker + price
- **Position Chart Overlay** — solid white Entry line, cyan dashed MFE line, color-coded P&L annotation badge (all rendered on main candlestick chart when position is open)
- **Accuracy Tracker** (`accuracy_tracker.csv`) — logs Predicted vs Actual + correct/wrong on each exit; 🧠 Tracker tab shows: total/correct/wrong metrics, accuracy % by structure bar chart, full history table with color-coded rows, CSV download
- Initial Balance (IB High/Low: 9:30–10:30 EST) with dynamic live tracking
- Volume Profile histogram (configurable bins), POC gold line, IB dashed lines
- **7-Structure Classification** — priority-ordered:
  1. Double Distribution (two HVNs + LVN gap ≥ 15 cents)
  2. Non-Trend (narrow IB < 20% of day range + anemic volume)
  3. Normal (IB never violated)
  4. Trend Day (IB violated within 2 hrs, price > 2× ATR from IB)
  5. Neutral Extreme (both extremes hit, closing at day H/L)
  6. Neutral (both extremes hit, price back inside IB)
  7. Normal Variation (one side violated, new belly forming)
- **Key Insights box** — styled sub-panel with plain-language explanation under each structure label
- **Structure Probability Meter** — 7 structures with percentage pills (updated scoring for all 7)
- **Dynamic Target Zones** on chart (dotted lines + shaded bands):
  - Coast-to-Coast (C2C) — fires when price violated an IB extreme and returned inside
  - Range Extension (1.5× / 2.0× IB) — fires when TCS > 70%, bullish or bearish
  - Gap Fill — Double Distribution LVN highlighted in yellow, target at next HVN
- **Distance to Target** — sidebar widget showing each active target + % away + direction
- **Target Reached** sound alert (4-note ascending chime) when price hits any target (0.5% tolerance)
- **Trend Confidence Score (TCS)** — 0–100 gauge with sector tailwind bonus
- **RVOL** — time-segmented pace-adjusted 5-day baseline with pattern labels
- **Model Prediction box** — Fake-out / High Conviction / Consolidation
- **Volume Velocity widget** — vol/min + acceleration label
- **Audio/Visual Alert System** — Web Audio API tones (chime, low-tone, target-reached)
- **Pre-Market Gap Scanner** — batch-scans 30-ticker watchlist for gap% + PM RVOL, top-3 results
- **Trade Journal** — persistent CSV journal (`trade_journal.csv`):
  - Captures: Ticker, Price, Timestamp, Structure, TCS, RVOL, Notes, Grade, Grade Reason
  - Auto-grading engine: A / B / C / F with plain-language reason
  - Colored grade badges (green A → red F)
  - Grade discipline equity curve (rolling average over entries)
  - CSV download button

### Dashboard Layout (3 tabs)
- **📈 Main Chart** — Volume Profile chart, all indicators, `💾 LOG ENTRY` expander
- **🔍 Scanner** — Pre-Market Gap Scanner with clickable Load buttons
- **📖 Journal** — Trade Journal with grade circles, Why column, equity curve, CSV export

### Live Pulse Header (visible after any analysis)
- Structure Label card, TCS progress bar + %, RVOL card
- Alert Banner: 🚀 STOCK IN PLAY (TCS ≥ 80% or Runner Mode) or ⚠ CAUTION (TCS ≤ 30%)

### Sidebar Settings
- Alpaca API Key + Secret Key
- Mode: Historical / Live Stream
- Ticker Symbol, Volume Profile Bins
- Sector ETF (IWM, XBI, SMH, QQQ, SPY, XLF, XLE) for tailwind detection
- Enable Audio Alerts + browser unlock button

### Running
```bash
streamlit run app.py --server.port 8080
```

### Dependencies (Python)
- `streamlit`, `alpaca-py`, `plotly`, `pandas`, `numpy`, `pytz`

## Stack

- **Monorepo tool**: pnpm workspaces
- **Node.js version**: 24
- **Package manager**: pnpm
- **TypeScript version**: 5.9
- **API framework**: Express 5
- **Database**: PostgreSQL + Drizzle ORM
- **Validation**: Zod (`zod/v4`), `drizzle-zod`
- **API codegen**: Orval (from OpenAPI spec)
- **Build**: esbuild (CJS bundle)

## Structure

```text
artifacts-monorepo/
├── artifacts/              # Deployable applications
│   └── api-server/         # Express API server
├── lib/                    # Shared libraries
│   ├── api-spec/           # OpenAPI spec + Orval codegen config
│   ├── api-client-react/   # Generated React Query hooks
│   ├── api-zod/            # Generated Zod schemas from OpenAPI
│   └── db/                 # Drizzle ORM schema + DB connection
├── scripts/                # Utility scripts (single workspace package)
│   └── src/                # Individual .ts scripts, run via `pnpm --filter @workspace/scripts run <script>`
├── pnpm-workspace.yaml     # pnpm workspace (artifacts/*, lib/*, lib/integrations/*, scripts)
├── tsconfig.base.json      # Shared TS options (composite, bundler resolution, es2022)
├── tsconfig.json           # Root TS project references
└── package.json            # Root package with hoisted devDeps
```

## TypeScript & Composite Projects

Every package extends `tsconfig.base.json` which sets `composite: true`. The root `tsconfig.json` lists all packages as project references. This means:

- **Always typecheck from the root** — run `pnpm run typecheck` (which runs `tsc --build --emitDeclarationOnly`). This builds the full dependency graph so that cross-package imports resolve correctly. Running `tsc` inside a single package will fail if its dependencies haven't been built yet.
- **`emitDeclarationOnly`** — we only emit `.d.ts` files during typecheck; actual JS bundling is handled by esbuild/tsx/vite...etc, not `tsc`.
- **Project references** — when package A depends on package B, A's `tsconfig.json` must list B in its `references` array. `tsc --build` uses this to determine build order and skip up-to-date packages.

## Root Scripts

- `pnpm run build` — runs `typecheck` first, then recursively runs `build` in all packages that define it
- `pnpm run typecheck` — runs `tsc --build --emitDeclarationOnly` using project references

## Packages

### `artifacts/api-server` (`@workspace/api-server`)

Express 5 API server. Routes live in `src/routes/` and use `@workspace/api-zod` for request and response validation and `@workspace/db` for persistence.

- Entry: `src/index.ts` — reads `PORT`, starts Express
- App setup: `src/app.ts` — mounts CORS, JSON/urlencoded parsing, routes at `/api`
- Routes: `src/routes/index.ts` mounts sub-routers; `src/routes/health.ts` exposes `GET /health` (full path: `/api/health`)
- Depends on: `@workspace/db`, `@workspace/api-zod`
- `pnpm --filter @workspace/api-server run dev` — run the dev server
- `pnpm --filter @workspace/api-server run build` — production esbuild bundle (`dist/index.cjs`)
- Build bundles an allowlist of deps (express, cors, pg, drizzle-orm, zod, etc.) and externalizes the rest

### `lib/db` (`@workspace/db`)

Database layer using Drizzle ORM with PostgreSQL. Exports a Drizzle client instance and schema models.

- `src/index.ts` — creates a `Pool` + Drizzle instance, exports schema
- `src/schema/index.ts` — barrel re-export of all models
- `src/schema/<modelname>.ts` — table definitions with `drizzle-zod` insert schemas (no models definitions exist right now)
- `drizzle.config.ts` — Drizzle Kit config (requires `DATABASE_URL`, automatically provided by Replit)
- Exports: `.` (pool, db, schema), `./schema` (schema only)

Production migrations are handled by Replit when publishing. In development, we just use `pnpm --filter @workspace/db run push`, and we fallback to `pnpm --filter @workspace/db run push-force`.

### `lib/api-spec` (`@workspace/api-spec`)

Owns the OpenAPI 3.1 spec (`openapi.yaml`) and the Orval config (`orval.config.ts`). Running codegen produces output into two sibling packages:

1. `lib/api-client-react/src/generated/` — React Query hooks + fetch client
2. `lib/api-zod/src/generated/` — Zod schemas

Run codegen: `pnpm --filter @workspace/api-spec run codegen`

### `lib/api-zod` (`@workspace/api-zod`)

Generated Zod schemas from the OpenAPI spec (e.g. `HealthCheckResponse`). Used by `api-server` for response validation.

### `lib/api-client-react` (`@workspace/api-client-react`)

Generated React Query hooks and fetch client from the OpenAPI spec (e.g. `useHealthCheck`, `healthCheck`).

### `scripts` (`@workspace/scripts`)

Utility scripts package. Each script is a `.ts` file in `src/` with a corresponding npm script in `package.json`. Run scripts via `pnpm --filter @workspace/scripts run <script>`. Scripts can import any workspace package (e.g., `@workspace/db`) by adding it as a dependency in `scripts/package.json`.
