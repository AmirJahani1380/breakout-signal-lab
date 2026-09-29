# Breakout Research

**An event-driven quant research demo, extracted from a larger private project.** It connects validated market bars, signal-time features, breakout events, fixed 2R outcome labels, an interactive chart viewer, and chronological model evaluation. It demonstrates research and software engineering, not a deployable trading strategy.

![Selected daily EMA breakout with modeled entry, stop, target, and broken level](docs/images/chart-events-initial.png)

## What the project does

| Stage | Implementation |
| --- | --- |
| Data | CSV, Parquet, and XLSX ingestion; UTC normalization; explicit OHLCV validation |
| Features | Timestamp-aligned nullable EMA, ATR, RSI, MACD, Donchian, confirmed-swing, candle, overlap, and volume features |
| Events | EMA close crossings and breaks of previously confirmed swing levels |
| Labels | Modeled next-open entry, ATR-buffered stop, fixed 2R target, 30-bar horizon, and explicit gap, ambiguity, and censoring rules |
| Review and research | Interactive event/feature filters and exports; separate EMA and swing cohorts with purged chronological validation and untouched test |

The FastAPI viewer uses pandas, PyArrow, and TradingView Lightweight Charts. The [research notebook](ML/breakout_research.ipynb) uses scikit-learn, XGBoost, and Matplotlib. **Market data is user supplied and is not in this repository.**

The viewer overlays indicators and confirmed swing markers on price bars, lets users inspect labeled events with their entry, stop, target, and broken level, and filters the event list on signal-time features. Chart views and export columns are selected independently. Exports include source and configuration metadata.

![Applied swing, Donchian, EMA, volume, and RSI feature views](docs/images/chart-features.png)

## Research result: XAUUSDzero daily breakouts

**Question:** Can signal-time features rank which EMA or confirmed-swing breakouts reach a fixed 2R target before their stop? The source is one MT5-style historical export (SHA-256 `473808c54e9cb2cdfc292524e59f94414f6aeb1b3fa2d1e9e802f3f19e0a7a89`), not a verified execution record.

The detectors produce 572 EMA and 252 swing events. Modeling uses only events with complete, unambiguous labels and finite predictors: eight normalized features plus direction. Raw prices, timestamps, returns, and outcomes are excluded from predictors. The last 20% of eligible events is reserved for test; development excludes events whose 30-bar outcomes reach the test boundary. Three expanding, purged development folds select a model by mean Brier score. A probability cutoff is chosen by validation lift with at least 10 validation events. Test is assessed once against a development-fitted prevalence baseline.

| Detector | Eligible | Development after purge | OOF validation | Untouched test | Test dates (UTC) |
| --- | ---: | ---: | ---: | ---: | --- |
| EMA | 565 | 449 | 336 | 113 | 2023-04-28 to 2026-04-08 |
| Confirmed swing | 247 | 196 | 147 | 50 | 2023-02-24 to 2026-03-18 |

| Detector | Selected model | Test Brier: baseline / model ↓ | Test AP / prevalence ↑ | Validation cutoff (support) | Test selected (hits) |
| --- | --- | ---: | ---: | ---: | ---: |
| EMA | Prevalence | 0.1996 / 0.1996 | 0.2743 / 0.2743 | 0.3853 (112) | 0 (0) |
| Confirmed swing | Shallow XGBoost | 0.2835 / 0.2329 | 0.7264 / 0.4800 | 0.2474 (80) | 34 (21) |

The swing model ranks this holdout above its prevalence baseline, but **21/34 selected test hits is descriptive**, not an estimate of live performance. EMA selects the prevalence model and its validation cutoff selects no test events, so its condition hit rate and lift are undefined. Average precision (AP) must be read alongside cohort prevalence. The figures show calibration with bin support, precision-recall, and cumulative lift for development and untouched test.

![Swing cohort calibration, precision-recall, and cumulative lift](docs/images/swing-calibration-precision-recall.png)

![EMA cohort calibration, precision-recall, and cumulative lift](docs/images/ema-calibration-precision-recall.png)

The [notebook](ML/breakout_research.ipynb) also reports event accounting, Wilson intervals, feature-bin summaries, tree leaves, and outcome distributions. Its swing outcome figure below separates development and test net R and adverse/favorable excursion. MAE/MFE span the available observation horizon, including bars after an earlier exit, and are not model inputs.

![Swing cohort net R, MAE, and MFE distributions in development and test](docs/images/swing-outcomes.png)

**Limits:** This is one market, timeframe, and historical test period. Overlapping outcomes weaken interval interpretation; the selected sample is small; regimes can change. Fixed barriers and zero modeled spread, slippage, and commission do not capture real fills. There is no portfolio sizing or execution simulation. Reported R values are labels, not realized portfolio returns.

## Run the viewer and notebook

Python 3.11+ is required. Supply CSV, Parquet, or XLSX bars with UTC-compatible timestamps and OHLCV columns. In the viewer, choose a folder of charts in the browser or set `BARS_DATA_ROOT` before launch. Naive timestamps use UTC unless `SOURCE_TIMEZONE` is set. The chart library loads from a CDN.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
python -m uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>. On macOS or Linux, activate with `source .venv/bin/activate`. The browser folder picker uploads files to a temporary server session; choose the folder again after a restart or in a new tab. Names such as `EURUSD_H1.csv` populate symbol and timeframe selectors. Duplicate filenames or symbol/timeframe pairs produce an import error.

For research, install `pip install -e ".[ml]"`, set `SOURCE_PATH` in the notebook's configuration cell (or `BREAKOUT_SOURCE`), and run all cells. That cell exposes feature periods, predictor keys, detector and label settings, model settings, and optional figure output. Source fingerprints and run settings are checked before analysis; move previous exports deliberately before regenerating from another source or configuration.

To run project checks, install `pip install -e ".[dev,ml]"` and, for browser tests, `npm ci` plus `npx playwright install chromium`:

```text
pytest
ruff format --check .
ruff check .
mypy
npm run format:check
npm run lint
npm run typecheck
```
