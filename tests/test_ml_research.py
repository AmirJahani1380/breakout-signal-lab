from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import pytest

from app.bars import load_bars
from app.feature_export import build_feature_frame
from app.labels import LabelConfig
from ML.research import (
    PREDICTORS,
    ModelSettings,
    chronological_split,
    eligible_events,
    expanding_folds,
    export_events,
    predictors,
    selected_feature_names,
)


def test_strategy_exports_are_separate_and_signal_aligned(tmp_path: Path) -> None:
    source = tmp_path / "XAUUSDzero_D1_max_bars.csv"
    lines = ["time,open,high,low,close,tick_volume"]
    for index in range(300):
        center = 100 + index * 0.05 + 5 * math.sin(index / 3)
        opening = center - 0.9
        close = center + 0.9
        lines.append(
            f"{1_000_000 + index * 86400},{opening},{close + 0.5},{opening - 0.5},{close},1"
        )
    source.write_text("\n".join(lines) + "\n", encoding="utf-8")
    paths = export_events(source, tmp_path / "output")
    assert set(paths) == {"ema_breakout", "swing_breakout"}
    assert paths["ema_breakout"] != paths["swing_breakout"]
    bars = load_bars(source).bars
    computed, _ = build_feature_frame(bars, "XAUUSDzero", "D1", PREDICTORS[:-1])
    expected = computed.set_index("time")
    for name, path in paths.items():
        frame = pd.read_parquet(path)
        assert not frame.empty
        assert frame.detector.eq(name).all()
        assert frame.id.is_unique
        sidecar = json.loads(Path(str(path) + ".json").read_text(encoding="utf-8"))
        assert sidecar["strategy"] == name and len(sidecar["source_sha256"]) == 64
        for row in frame.itertuples():
            for feature in PREDICTORS[:-1]:
                actual = getattr(row, feature)
                reference = expected.loc[row.signal_time, feature]
                assert pd.isna(actual) and pd.isna(reference) or actual == pytest.approx(reference)
    with pytest.raises(FileExistsError):
        export_events(source, tmp_path / "output")

    custom = export_events(
        source,
        tmp_path / "custom",
        asset="GOLD",
        strategies=("ema_breakout",),
        feature_names=("body_to_range_ratio",),
        label_config=LabelConfig(horizon=10),
    )
    custom_metadata = json.loads(Path(str(custom["ema_breakout"]) + ".json").read_text())
    assert custom_metadata["dataset_id"] == "GOLD/D1"
    assert custom_metadata["label_settings"]["horizon"] == 10
    assert custom_metadata["model_predictors"] == ["body_to_range_ratio", "direction"]
    assert (
        len(
            eligible_events(
                pd.read_parquet(custom["ema_breakout"]), ("body_to_range_ratio", "direction")
            )
        )
        > 0
    )
    configured_names = selected_feature_names(
        ("atr_to_close", "ema_distance", "body_to_range_ratio"), {"atr_period": 14}
    )
    assert configured_names == (
        "atr_14_to_close",
        "ema_distance_20_atr_14",
        "body_to_range_ratio",
    )
    configured = export_events(
        source,
        tmp_path / "configured",
        strategies=("ema_breakout",),
        feature_names=configured_names,
        feature_settings={"atr_period": 14},
    )
    assert set(configured_names) <= set(pd.read_parquet(configured["ema_breakout"]).columns)
    with pytest.raises(ValueError, match="unknown feature selection keys"):
        selected_feature_names(("unknown",), {})
    with pytest.raises(ValueError, match="unknown feature settings"):
        selected_feature_names(("atr_to_close",), {"atr_period_typo": 14})


def test_outcomes_and_purges_do_not_enter_predictors() -> None:
    with pytest.raises(ValueError, match="test_fraction"):
        ModelSettings(test_fraction=1)
    frame = pd.DataFrame(
        {
            "id": [str(index) for index in range(40)],
            "signal_time": [index * 10 for index in range(40)],
            "planned_end_time": [index * 10 + 25 for index in range(40)],
            "eligible": [True] * 38 + [False, False],
            "horizon_complete": [True] * 37 + [False, True, True],
            "target_first": [index % 2 == 0 for index in range(40)],
            "direction": ["bullish" if index % 2 else "bearish" for index in range(40)],
            "label_status": [
                "target_first" if index % 2 == 0 else "stop_first" for index in range(38)
            ]
            + ["ambiguous", "censored"],
            "net_r": [1.0] * 40,
            **{feature: [float(index) for index in range(40)] for feature in PREDICTORS[:-1]},
        }
    )
    cohort = eligible_events(frame)
    assert len(cohort) == 37
    assert list(predictors(cohort)) == list(PREDICTORS)
    development, test, purged = chronological_split(cohort)
    assert purged > 0
    assert development.planned_end_time.max() < test.signal_time.min()
    for train, validation, purged in expanding_folds(development):
        assert purged > 0
        assert (
            development.planned_end_time.iloc[train].max()
            < development.signal_time.iloc[validation].min()
        )

    malformed_direction = frame.copy()
    malformed_direction.loc[0, "direction"] = "sideways"
    with pytest.raises(ValueError, match="direction must be bullish or bearish"):
        eligible_events(malformed_direction)

    mismatched_target = frame.copy()
    mismatched_target.loc[1, "target_first"] = True
    with pytest.raises(ValueError, match="target_first disagrees with label_status"):
        eligible_events(mismatched_target)
