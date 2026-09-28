"""Reproducible, chronological research for the two built-in breakout strategies."""

from __future__ import annotations

import hashlib
import json
import warnings
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.bars import load_bars
from app.event_detectors import EventConfig, detect_events
from app.feature_export import build_feature_frame
from app.labels import LabelConfig, label_events

PREDICTORS = (
    "atr_20_to_close",
    "ema_slope_20_20_atr_20",
    "ema_distance_20_atr_20",
    "candle_range_to_atr_20",
    "body_size_to_atr_20",
    "upper_wick_to_atr_20",
    "lower_wick_to_atr_20",
    "body_to_range_ratio",
    "direction",
)
STRATEGIES = ("ema_breakout", "swing_breakout")
ELIGIBLE_STATUSES = ("target_first", "stop_first", "timeout")


def export_events(source: Path, output: Path) -> dict[str, Path]:
    """Write one complete audit table and sidecar per strategy; refuse replacement."""
    destinations = {
        strategy: (output / f"{strategy}.parquet", output / f"{strategy}.parquet.json")
        for strategy in STRATEGIES
    }
    existing = [path for pair in destinations.values() for path in pair if path.exists()]
    if existing:
        raise FileExistsError(f"research export already exists: {existing[0]}")
    bars = load_bars(source, "UTC", "XAUUSDzero/D1").bars
    features, specs = build_feature_frame(bars, "XAUUSDzero", "D1", PREDICTORS[:-1])
    signal_features = features[["time", *PREDICTORS[:-1]]]
    fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()
    detector_config = EventConfig()
    label_config = LabelConfig()
    output.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    try:
        for strategy, (table, sidecar) in destinations.items():
            events, _ = detect_events(bars, detector_config, [strategy], "XAUUSDzero/D1")
            records = label_events(bars, events, label_config)
            frame = pd.DataFrame.from_records(records)
            if frame.empty:
                raise ValueError(f"{strategy}: no detected events")
            for column in ("configuration", "label_config"):
                frame[column] = frame[column].map(lambda value: json.dumps(value, sort_keys=True))
            frame = frame.merge(
                signal_features,
                left_on="signal_time",
                right_on="time",
                how="left",
                validate="many_to_one",
            ).drop(columns="time")
            frame.insert(0, "asset", "XAUUSDzero")
            frame.insert(1, "timeframe", "D1")
            frame["eligible"] = frame.label_status.isin(ELIGIBLE_STATUSES) & frame.horizon_complete
            frame["target_first"] = frame.label_status.eq("target_first").where(frame.eligible)
            with table.open("xb") as stream:
                created.append(table)
                frame.to_parquet(stream, index=False)
            metadata = {
                "schema": "breakout_ml_events.v1",
                "dataset_id": "XAUUSDzero/D1",
                "source": source.name,
                "source_sha256": fingerprint,
                "strategy": strategy,
                "detector_settings": {
                    "ema_period": detector_config.ema_period,
                    "swing_lookback": detector_config.swing_lookback,
                    "swing_left": detector_config.swing_left,
                    "swing_right": detector_config.swing_right,
                    "buffer": detector_config.buffer,
                },
                "label_settings": label_config.as_dict(),
                "features": [spec.as_dict() for spec in specs],
                "model_predictors": list(PREDICTORS),
                "eligibility": "complete 30-bar horizon and target_first, stop_first, or timeout",
                "target": "target_first=1; stop_first and timeout=0",
                "rows": len(frame),
                "exclusions": frame.loc[~frame.eligible, "label_status"].value_counts().to_dict(),
            }
            with sidecar.open("x", encoding="utf-8") as stream:
                created.append(sidecar)
                json.dump(metadata, stream, indent=2, allow_nan=False)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return {strategy: destinations[strategy][0] for strategy in STRATEGIES}


def eligible_events(frame: pd.DataFrame) -> pd.DataFrame:
    """Return chronological, finite signal-time predictors and supported outcomes."""
    required = [
        "id",
        "signal_time",
        "planned_end_time",
        "eligible",
        "horizon_complete",
        "label_status",
        "target_first",
        *PREDICTORS,
    ]
    missing = set(required) - set(frame.columns)
    if missing:
        raise ValueError(f"event table lacks columns: {sorted(missing)}")
    if not frame.direction.isin(("bullish", "bearish")).all():
        raise ValueError("event direction must be bullish or bearish")
    selected = (
        frame.loc[
            frame.eligible & frame.horizon_complete & frame.label_status.isin(ELIGIBLE_STATUSES)
        ]
        .sort_values(["signal_time", "id"])
        .copy()
    )
    expected_target = selected.label_status.eq("target_first")
    if selected.target_first.isna().any() or not selected.target_first.eq(expected_target).all():
        raise ValueError("eligible target_first disagrees with label_status")
    selected = selected.dropna(subset=["planned_end_time", "target_first", *PREDICTORS])
    if not np.isfinite(selected[list(PREDICTORS[:-1])].to_numpy(dtype=float)).all():
        raise ValueError("model predictors contain non-finite values")
    return selected.reset_index(drop=True)


def chronological_split(events: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """Reserve final 20%; purge development windows reaching its first signal."""
    if len(events) < 2:
        return events.iloc[:0], events.copy(), 0
    cut = int(np.floor(0.8 * len(events)))
    development = events.iloc[:cut]
    test = events.iloc[cut:]
    first_test = int(test.signal_time.iloc[0])
    safe = development.loc[development.planned_end_time < first_test].copy()
    return safe, test.copy(), len(development) - len(safe)


def expanding_folds(events: pd.DataFrame) -> list[tuple[np.ndarray, np.ndarray, int]]:
    """Three expanding folds; purge overlapping 30-bar outcome windows."""
    from sklearn.model_selection import TimeSeriesSplit  # type: ignore[import-untyped]

    if len(events) < 16:
        return []
    folds = []
    for train, validation in TimeSeriesSplit(n_splits=3).split(events):
        first_validation = int(events.signal_time.iloc[validation[0]])
        safe_train = train[events.planned_end_time.iloc[train].to_numpy() < first_validation]
        folds.append((safe_train, validation, len(train) - len(safe_train)))
    return folds


def predictors(events: pd.DataFrame) -> pd.DataFrame:
    """The only model input projection; all audit/outcome fields stay outside it."""
    result = events.loc[:, PREDICTORS[:-1]].astype(float).copy()
    result["direction"] = events.direction.eq("bullish").astype(int).to_numpy()
    return result


def _xgboost(device: str, depth: int, child_weight: int) -> Any:
    from xgboost import XGBClassifier

    return XGBClassifier(
        n_estimators=80,
        max_depth=depth,
        min_child_weight=child_weight,
        learning_rate=0.05,
        subsample=0.9,
        colsample_bytree=0.9,
        objective="binary:logistic",
        eval_metric="logloss",
        tree_method="hist",
        device=device,
        random_state=42,
        n_jobs=2,
    )


def evaluate_models(events: pd.DataFrame) -> dict[str, object]:
    """Select on purged development folds, then score the held-out test once."""
    from sklearn.dummy import DummyClassifier  # type: ignore[import-untyped]
    from sklearn.metrics import brier_score_loss  # type: ignore[import-untyped]
    from sklearn.tree import DecisionTreeClassifier  # type: ignore[import-untyped]

    development, test, test_purged = chronological_split(events)
    folds = expanding_folds(development)
    summary: dict[str, object] = {
        "development_count": len(development),
        "test_count": len(test),
        "test_boundary_purged": test_purged,
        "fold_purged": [purged for _, _, purged in folds],
    }
    if (
        len(folds) != 3
        or any(
            len(train) < 20
            or len(np.unique(development.target_first.iloc[train])) < 2
            or len(np.unique(development.target_first.iloc[validation])) < 2
            for train, validation, _ in folds
        )
        or len(np.unique(test.target_first)) < 2
    ):
        summary["unavailable"] = (
            "too few events or both target classes for three purged folds and test"
        )
        return summary
    x_development, y_development = predictors(development), development.target_first.astype(int)
    x_test, y_test = predictors(test), test.target_first.astype(int)
    candidates: dict[str, Callable[[], Any]] = {
        "prevalence": lambda: DummyClassifier(strategy="prior"),
        "tree": lambda: DecisionTreeClassifier(max_depth=2, min_samples_leaf=10, random_state=42),
    }
    device = "cuda"
    gpu_note = "CUDA requested"
    first_train, _, _ = folds[0]
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=".*No visible GPU.*", category=UserWarning)
            warnings.filterwarnings("ignore", message=".*Device is changed.*", category=UserWarning)
            probe = _xgboost("cuda", 2, 5).fit(
                x_development.iloc[first_train], y_development.iloc[first_train]
            )
        actual = json.loads(probe.get_booster().save_config())["learner"]["generic_param"]["device"]
        if not str(actual).startswith("cuda"):
            device, gpu_note = "cpu", f"CUDA unavailable; XGBoost selected {actual}"
    except Exception as error:
        if not any(word in str(error).lower() for word in ("cuda", "gpu", "device")):
            raise
        device, gpu_note = "cpu", f"CUDA failed: {error}"
    for depth in (2, 3):
        for child_weight in (5, 10):
            candidates[f"xgb_d{depth}_w{child_weight}"] = partial(
                _xgboost, device, depth, child_weight
            )
    scores: dict[str, list[float]] = {}
    oof: dict[str, list[tuple[int, float]]] = {}
    for name, factory in candidates.items():
        scores[name], oof[name] = [], []
        for train, validation, _ in folds:
            model = factory().fit(x_development.iloc[train], y_development.iloc[train])
            probability = model.predict_proba(x_development.iloc[validation])[:, 1]
            scores[name].append(
                float(brier_score_loss(y_development.iloc[validation], probability))
            )
            oof[name].extend(zip(validation.tolist(), probability.tolist(), strict=True))
    selected = min(scores, key=lambda name: (np.mean(scores[name]), name))
    fitted = {
        name: factory().fit(x_development, y_development) for name, factory in candidates.items()
    }
    test_predictions = {name: model.predict_proba(x_test)[:, 1] for name, model in fitted.items()}
    model = fitted[selected]
    test_probability = test_predictions[selected]
    summary.update(
        {
            "device": device,
            "gpu_note": gpu_note,
            "fold_brier": scores,
            "mean_cv_brier": {name: float(np.mean(values)) for name, values in scores.items()},
            "selected": selected,
            "model": model,
            "tree_model": fitted["tree"],
            "oof": pd.DataFrame(oof[selected], columns=["development_index", "probability"]),
            "test_events": test,
            "test_probability": test_probability,
            "test_brier": float(brier_score_loss(y_test, test_probability)),
            "all_test_brier": {
                name: float(brier_score_loss(y_test, probability))
                for name, probability in test_predictions.items()
            },
        }
    )
    return summary
