import logging
from pathlib import Path

import pandas as pd
import pytest

from app.bars import Bar
from app.features import (
    FeatureDefinition,
    FeatureSpec,
    FeatureTable,
    FeatureViewSpec,
    calculate,
    calculate_requested,
    discover,
)
from app.features.ema import feature as ema_definition
from app.features.macd import feature as macd_definition
from app.features.rsi import calculate as calculate_rsi
from app.features.volume import calculate as calculate_volume


def bars(closes: list[float]) -> tuple[Bar, ...]:
    return tuple(
        Bar(100 + index, close, close, close, close, 10 + index)
        for index, close in enumerate(closes)
    )


def test_feature_metadata_rejects_invalid_boundaries() -> None:
    with pytest.raises(ValueError, match="feature name"):
        FeatureSpec("EMA 20", "Float64")
    with pytest.raises(ValueError, match="warm_up"):
        FeatureSpec("ema", "Float64", warm_up=-1)
    with pytest.raises(ValueError, match="JSON-compatible"):
        FeatureSpec("ema", "Float64", {"bad": float("nan")})
    with pytest.raises(ValueError, match="renderer"):
        FeatureViewSpec("ema", "ema", "EMA", "overlay", "area")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="pane_height"):
        FeatureViewSpec("ema", "ema", "EMA", "overlay", "line", pane_height=0)
    with pytest.raises(ValueError, match="series_options.color"):
        FeatureViewSpec("ema", "ema", "EMA", "overlay", "line", series_options={"color": "red"})
    with pytest.raises(ValueError, match="scaleMargins"):
        FeatureViewSpec(
            "ema",
            "ema",
            "EMA",
            "overlay",
            "line",
            price_scale_options={"scaleMargins": {"top": 0.8, "bottom": 0.3}},
        )
    with pytest.raises(ValueError, match=r"reference_lines\[0\]\.price"):
        FeatureViewSpec(
            "ema",
            "ema",
            "EMA",
            "overlay",
            "line",
            reference_lines=({"price": float("inf")},),
        )


def test_rsi_warm_up_is_null_and_timestamps_remain_aligned() -> None:
    source_bars = bars([float(value) for value in range(1, 17)])
    table = calculate_rsi(source_bars)
    assert table.timestamps == tuple(bar.time for bar in source_bars)
    assert table.frame["rsi_14"].iloc[:14].isna().all()
    assert table.frame["rsi_14"].iloc[14] == 100
    assert str(table.frame["rsi_14"].dtype) == "Float64"


def test_ema_and_macd_have_expected_named_aligned_columns() -> None:
    source_bars = bars([1.0, 2.0, 3.0])
    ema = calculate(ema_definition, source_bars)
    macd = calculate(macd_definition, source_bars)
    assert ema is not None and macd is not None
    assert list(ema.frame) == ["ema_20"]
    assert ema.frame["ema_20"].tolist() == pytest.approx([1, 1.0952380952, 1.2766439909])
    assert list(macd.frame) == ["macd_12_26_9", "macd_signal_12_26_9", "macd_histogram_12_26_9"]
    assert macd.timestamps == (100, 101, 102)
    assert macd.frame.iloc[0].tolist() == pytest.approx([0, 0, 0])


@pytest.mark.parametrize(
    "feature_name", ["macd_12_26_9", "macd_signal_12_26_9", "macd_histogram_12_26_9"]
)
def test_grouped_calculation_returns_only_requested_output(feature_name: str) -> None:
    table = calculate_requested(macd_definition, bars([1.0, 2.0, 3.0]), frozenset({feature_name}))

    assert table is not None
    assert list(table.frame) == [feature_name]


def test_view_mapping_is_independent_from_crosshair_details() -> None:
    table = calculate_volume((Bar(1, 1, 2, 0, 2, 10), Bar(2, 2, 3, 0, 1, 11)))
    rendered = FeatureViewSpec(
        "volume",
        "volume",
        "Volume",
        "bars",
        "histogram",
        show_in_crosshair=False,
        color_feature="volume_up",
    ).definition(table)
    detail_only = FeatureViewSpec(
        "volume_up",
        "volume_up",
        "Up bar",
        "direction",
        None,
        show_in_crosshair=True,
    ).definition(table)
    assert rendered["values"] == []
    assert [point["color"] for point in rendered["points"]] == ["#00d08499", "#ff4d6d99"]
    assert detail_only["series_type"] is None and detail_only["points"] == []
    assert detail_only["source"] == "computed"
    assert detail_only["values"] == [
        {"time": 1, "value": True},
        {"time": 2, "value": False},
    ]


def test_null_color_feature_omits_per_point_color() -> None:
    specs = (
        FeatureSpec("value", "Float64"),
        FeatureSpec("direction", "boolean", warm_up=1),
    )
    table = FeatureTable.from_columns(
        specs, [1, 2], {"value": [10.0, 11.0], "direction": [None, True]}
    )
    payload = FeatureViewSpec(
        "value", "value", "Value", "Colored values", "histogram", color_feature="direction"
    ).definition(table)
    assert payload["points"] == [
        {"time": 1, "value": 10.0},
        {"time": 2, "value": 11.0, "color": "#00d08499"},
    ]


def test_candle_color_view_supplies_values_without_crosshair_details() -> None:
    spec = FeatureSpec("signal", "boolean")
    table = FeatureTable.from_columns((spec,), [1, 2], {"signal": [False, True]})
    payload = FeatureViewSpec(
        "signal",
        "signal",
        "Signal",
        "Candle color",
        None,
        show_in_crosshair=False,
        candle_color="#ffd600",
    ).definition(table)
    assert payload["visualization"] == "candle color"
    assert payload["candle_color"] == "#ffd600"
    assert payload["values"] == [
        {"time": 1, "value": False},
        {"time": 2, "value": True},
    ]


def test_builtin_discovery_has_no_spurious_infrastructure_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="app.features")
    assert len(discover()) == 14
    assert "Skipping feature module" not in caplog.text


def test_marker_and_separate_pane_map_to_distinct_rendering_paths() -> None:
    spec = FeatureSpec("signal_price", "Float64")
    table = FeatureTable.from_columns((spec,), [1], {spec.name: [2.5]})
    marker = FeatureViewSpec(
        "signal_marker",
        spec.name,
        "Signal",
        "Entry marker",
        "marker",
        series_options={"color": "#00ffff", "shape": "square"},
    ).definition(table)
    separate = FeatureViewSpec(
        "signal_line", spec.name, "Signal", "Signal pane", "line", pane="separate"
    ).definition(table)
    assert marker["renderer"] == "marker" and marker["series_type"] is None
    assert separate["renderer"] == "line" and separate["pane"] == "separate"


def test_calculation_rejects_misalignment_and_non_finite_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    spec = FeatureSpec("score", "Float64")
    view = FeatureViewSpec("score", spec.name, "Score", "test", None)
    wrong_time = FeatureDefinition(
        (spec,), lambda _: FeatureTable.from_columns((spec,), [999], {spec.name: [1.0]}), (view,)
    )
    mutable_table = FeatureTable.from_columns((spec,), [100], {spec.name: [1.0]})
    mutable_table.frame.at[100, spec.name] = float("inf")
    non_finite = FeatureDefinition((spec,), lambda _: mutable_table, (view,))
    caplog.set_level(logging.WARNING, logger="app.features")

    assert calculate(wrong_time, bars([1.0])) is None
    assert calculate(non_finite, bars([1.0])) is None
    assert "align exactly" in caplog.text
    assert "values must be finite or null" in caplog.text


def test_feature_table_parquet_round_trip_preserves_contract(tmp_path: Path) -> None:
    specs = (
        FeatureSpec("score", "Float64", warm_up=1),
        FeatureSpec("signal", "boolean", warm_up=1),
        FeatureSpec("count", "Int64", warm_up=1),
    )
    table = FeatureTable.from_columns(
        specs,
        [10, 20, 30],
        {
            "score": [None, 1.5, 2.5],
            "signal": [None, True, False],
            "count": [None, 1, 2],
        },
    )
    path = tmp_path / "features.parquet"
    table.to_parquet(path)
    restored = FeatureTable.from_parquet(path)
    pd.testing.assert_frame_equal(restored.frame, table.frame)
    assert restored.specs == table.specs
    assert restored.timestamps == (10, 20, 30)
