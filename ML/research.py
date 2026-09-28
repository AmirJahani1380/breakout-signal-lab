"""Reproducible, chronological research for the two built-in breakout strategies."""

from __future__ import annotations

import hashlib
import json
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.bars import load_bars
from app.event_detectors import EventConfig, detect_events
from app.feature_export import build_feature_frame
from app.features import discover
from app.features.configuration import configure_features, settings_catalog
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


def selected_feature_names(
    feature_keys: tuple[str, ...], feature_settings: dict[str, int]
) -> tuple[str, ...]:
    """Resolve stable feature selection keys to columns at the configured periods."""
    if not feature_keys or len(set(feature_keys)) != len(feature_keys):
        raise ValueError("feature_keys must contain unique feature selection keys")
    registered = discover()
    unknown_settings = set(feature_settings) - {
        setting.key for setting in settings_catalog(registered)
    }
    if unknown_settings:
        raise ValueError(f"unknown feature settings: {sorted(unknown_settings)}")
    definitions = configure_features(registered, feature_settings)
    columns = {
        spec.selection_key or spec.name: spec.name
        for definition in definitions
        for spec in definition.specs
    }
    unknown = set(feature_keys) - columns.keys()
    if unknown:
        raise ValueError(f"unknown feature selection keys: {sorted(unknown)}")
    return tuple(columns[key] for key in feature_keys)


@dataclass(frozen=True)
class ModelSettings:
    test_fraction: float = 0.2
    validation_folds: int = 3
    minimum_train_events: int = 20
    tree_depth: int = 2
    tree_leaf_events: int = 10
    xgb_depths: tuple[int, ...] = (2, 3)
    xgb_child_weights: tuple[int, ...] = (5, 10)
    xgb_estimators: int = 80
    xgb_learning_rate: float = 0.05
    xgb_subsample: float = 0.9
    xgb_column_sample: float = 0.9
    xgb_device: str = "auto"
    xgb_jobs: int = 2
    random_seed: int = 42

    def __post_init__(self) -> None:
        if not 0 < self.test_fraction < 1 or self.validation_folds < 2:
            raise ValueError("test_fraction must be in (0, 1) and validation_folds at least 2")
        if not self.xgb_depths or not self.xgb_child_weights:
            raise ValueError("XGBoost candidate depths and child weights must not be empty")
        if self.xgb_device not in {"auto", "cpu", "cuda"} or self.xgb_jobs < 1:
            raise ValueError("xgb_device must be auto, cpu, or cuda and xgb_jobs positive")
        if (
            min(
                self.minimum_train_events,
                self.tree_depth,
                self.tree_leaf_events,
                self.xgb_estimators,
                *self.xgb_depths,
                *self.xgb_child_weights,
            )
            < 1
        ):
            raise ValueError("model counts and depths must be positive")
        if (
            not 0 < self.xgb_learning_rate <= 1
            or not 0 < self.xgb_subsample <= 1
            or not 0 < self.xgb_column_sample <= 1
        ):
            raise ValueError("XGBoost rates must be in (0, 1]")


def export_events(
    source: Path,
    output: Path,
    *,
    asset: str = "XAUUSDzero",
    timeframe: str = "D1",
    strategies: tuple[str, ...] = STRATEGIES,
    feature_names: tuple[str, ...] = PREDICTORS[:-1],
    feature_settings: dict[str, int] | None = None,
    detector_config: EventConfig = EventConfig(),
    label_config: LabelConfig = LabelConfig(),
    source_timezone: str = "UTC",
) -> dict[str, Path]:
    """Write one complete audit table and sidecar per strategy; refuse replacement."""
    if (
        not strategies
        or len(set(strategies)) != len(strategies)
        or set(strategies) - set(STRATEGIES)
    ):
        raise ValueError("strategies must contain unique supported breakout detectors")
    if (
        not feature_names
        or len(set(feature_names)) != len(feature_names)
        or "direction" in feature_names
    ):
        raise ValueError("feature_names must contain unique registered columns, without direction")
    destinations = {
        strategy: (output / f"{strategy}.parquet", output / f"{strategy}.parquet.json")
        for strategy in strategies
    }
    existing = [path for pair in destinations.values() for path in pair if path.exists()]
    if existing:
        raise FileExistsError(f"research export already exists: {existing[0]}")
    dataset_id = f"{asset}/{timeframe}"
    bars = load_bars(source, source_timezone, dataset_id).bars
    definitions = configure_features(discover(), feature_settings or {})
    features, specs = build_feature_frame(bars, asset, timeframe, feature_names, definitions)
    signal_features = features[["time", *feature_names]]
    fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    try:
        for strategy, (table, sidecar) in destinations.items():
            events, _ = detect_events(bars, detector_config, [strategy], dataset_id)
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
            frame.insert(0, "asset", asset)
            frame.insert(1, "timeframe", timeframe)
            frame["eligible"] = frame.label_status.isin(ELIGIBLE_STATUSES) & frame.horizon_complete
            frame["target_first"] = frame.label_status.eq("target_first").where(frame.eligible)
            with table.open("xb") as stream:
                created.append(table)
                frame.to_parquet(stream, index=False)
            metadata = {
                "schema": "breakout_ml_events.v1",
                "dataset_id": dataset_id,
                "source": source.name,
                "source_sha256": fingerprint,
                "strategy": strategy,
                "detector_settings": {
                    "ema_period": detector_config.ema_period,
                    "swing_lookback": detector_config.swing_lookback,
                    "swing_left": detector_config.swing_left,
                    "swing_right": detector_config.swing_right,
                    "buffer": detector_config.buffer,
                    **detector_config.detector_settings,
                },
                "label_settings": label_config.as_dict(),
                "features": [spec.as_dict() for spec in specs],
                "feature_settings": feature_settings or {},
                "model_predictors": [*feature_names, "direction"],
                "eligibility": (
                    f"complete {label_config.horizon}-bar horizon and target_first, "
                    "stop_first, or timeout"
                ),
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
    return {strategy: destinations[strategy][0] for strategy in strategies}


def eligible_events(
    frame: pd.DataFrame, predictor_names: tuple[str, ...] = PREDICTORS
) -> pd.DataFrame:
    """Return chronological, finite signal-time predictors and supported outcomes."""
    if len(predictor_names) < 2 or predictor_names[-1] != "direction":
        raise ValueError("predictor_names must end with direction after feature columns")
    required = [
        "id",
        "signal_time",
        "planned_end_time",
        "eligible",
        "horizon_complete",
        "label_status",
        "target_first",
        *predictor_names,
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
    selected = selected.dropna(subset=["planned_end_time", "target_first", *predictor_names])
    if not np.isfinite(selected[list(predictor_names[:-1])].to_numpy(dtype=float)).all():
        raise ValueError("model predictors contain non-finite values")
    return selected.reset_index(drop=True)


def chronological_split(
    events: pd.DataFrame, test_fraction: float = 0.2
) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """Reserve final 20%; purge development windows reaching its first signal."""
    if len(events) < 2:
        return events.iloc[:0], events.copy(), 0
    if not 0 < test_fraction < 1:
        raise ValueError("test_fraction must be in (0, 1)")
    cut = int(np.floor((1 - test_fraction) * len(events)))
    development = events.iloc[:cut]
    test = events.iloc[cut:]
    first_test = int(test.signal_time.iloc[0])
    safe = development.loc[development.planned_end_time < first_test].copy()
    return safe, test.copy(), len(development) - len(safe)


def expanding_folds(
    events: pd.DataFrame, count: int = 3
) -> list[tuple[np.ndarray, np.ndarray, int]]:
    """Three expanding folds; purge overlapping 30-bar outcome windows."""
    from sklearn.model_selection import TimeSeriesSplit  # type: ignore[import-untyped]

    if count < 2:
        raise ValueError("validation fold count must be at least 2")
    if len(events) < (count + 1) * 4:
        return []
    folds = []
    for train, validation in TimeSeriesSplit(n_splits=count).split(events):
        first_validation = int(events.signal_time.iloc[validation[0]])
        safe_train = train[events.planned_end_time.iloc[train].to_numpy() < first_validation]
        folds.append((safe_train, validation, len(train) - len(safe_train)))
    return folds


def predictors(events: pd.DataFrame, predictor_names: tuple[str, ...] = PREDICTORS) -> pd.DataFrame:
    """The only model input projection; all audit/outcome fields stay outside it."""
    result = events.loc[:, predictor_names[:-1]].astype(float).copy()
    result["direction"] = events.direction.eq("bullish").astype(int).to_numpy()
    return result


def _xgboost(device: str, depth: int, child_weight: int, settings: ModelSettings) -> Any:
    from xgboost import XGBClassifier

    return XGBClassifier(
        n_estimators=settings.xgb_estimators,
        max_depth=depth,
        min_child_weight=child_weight,
        learning_rate=settings.xgb_learning_rate,
        subsample=settings.xgb_subsample,
        colsample_bytree=settings.xgb_column_sample,
        objective="binary:logistic",
        eval_metric="logloss",
        tree_method="hist",
        device=device,
        random_state=settings.random_seed,
        n_jobs=settings.xgb_jobs,
    )


def evaluate_models(
    events: pd.DataFrame,
    settings: ModelSettings = ModelSettings(),
    predictor_names: tuple[str, ...] = PREDICTORS,
) -> dict[str, object]:
    """Select on purged development folds, then score the held-out test once."""
    from sklearn.dummy import DummyClassifier  # type: ignore[import-untyped]
    from sklearn.metrics import brier_score_loss  # type: ignore[import-untyped]
    from sklearn.tree import DecisionTreeClassifier  # type: ignore[import-untyped]

    development, test, test_purged = chronological_split(events, settings.test_fraction)
    folds = expanding_folds(development, settings.validation_folds)
    summary: dict[str, object] = {
        "development_count": len(development),
        "test_count": len(test),
        "test_boundary_purged": test_purged,
        "fold_purged": [purged for _, _, purged in folds],
    }
    if (
        len(folds) != settings.validation_folds
        or any(
            len(train) < settings.minimum_train_events
            or len(np.unique(development.target_first.iloc[train])) < 2
            or len(np.unique(development.target_first.iloc[validation])) < 2
            for train, validation, _ in folds
        )
        or len(np.unique(test.target_first)) < 2
    ):
        summary["unavailable"] = "too few events or both target classes for purged folds and test"
        return summary
    x_development, y_development = (
        predictors(development, predictor_names),
        development.target_first.astype(int),
    )
    x_test, y_test = predictors(test, predictor_names), test.target_first.astype(int)
    candidates: dict[str, Callable[[], Any]] = {
        "prevalence": lambda: DummyClassifier(strategy="prior"),
        "tree": lambda: DecisionTreeClassifier(
            max_depth=settings.tree_depth,
            min_samples_leaf=settings.tree_leaf_events,
            random_state=settings.random_seed,
        ),
    }
    device = settings.xgb_device
    gpu_note = "Device chosen in config"
    first_train, _, _ = folds[0]
    if device == "auto":
        device = "cuda"
        gpu_note = "CUDA requested"
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore", message=".*No visible GPU.*", category=UserWarning
                )
                warnings.filterwarnings(
                    "ignore", message=".*Device is changed.*", category=UserWarning
                )
                probe = _xgboost(
                    "cuda", settings.xgb_depths[0], settings.xgb_child_weights[0], settings
                ).fit(x_development.iloc[first_train], y_development.iloc[first_train])
            actual = json.loads(probe.get_booster().save_config())["learner"]["generic_param"][
                "device"
            ]
            if not str(actual).startswith("cuda"):
                device, gpu_note = "cpu", f"CUDA unavailable; XGBoost selected {actual}"
        except Exception as error:
            if not any(word in str(error).lower() for word in ("cuda", "gpu", "device")):
                raise
            device, gpu_note = "cpu", f"CUDA failed: {error}"
    for depth in settings.xgb_depths:
        for child_weight in settings.xgb_child_weights:
            candidates[f"xgb_d{depth}_w{child_weight}"] = partial(
                _xgboost, device, depth, child_weight, settings
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
