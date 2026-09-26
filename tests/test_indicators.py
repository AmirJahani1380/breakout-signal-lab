import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import features as feature_package
from app.bars import Bar
from app.features.atr import make_feature as make_atr_feature
from app.features.ema import calculate as calculate_default_ema
from app.features.ema import make_feature as make_ema_feature
from app.features.macd import make_feature as make_macd_feature
from app.features.normalized_candles import make_feature as make_normalized_feature
from app.features.rolling_overlap import make_feature as make_overlap_feature
from app.features.rsi import calculate as calculate_default_rsi
from app.features.rsi import make_feature as make_rsi_feature
from app.main import Settings, create_app
from tests.test_bars import write_workbook


def price_bars(closes: list[float]) -> tuple[Bar, ...]:
    return tuple(Bar(index, close, close, close, close, 1) for index, close in enumerate(closes))


def test_ema_20_is_empty_or_seeded_by_first_close() -> None:
    assert calculate_default_ema(()).frame.empty
    values = calculate_default_ema(price_bars([1.0, 2.0, 3.0])).frame["ema_20"].tolist()
    assert values == pytest.approx((1.0, 1.0952380952, 1.2766439909))


def test_rsi_14_handles_warmup_flat_and_directional_prices() -> None:
    assert calculate_default_rsi(price_bars([1.0] * 14)).frame["rsi_14"].isna().all()
    assert calculate_default_rsi(price_bars([1.0] * 15)).frame["rsi_14"].iloc[-1] == 50
    assert (
        calculate_default_rsi(price_bars([float(value) for value in range(15)]))
        .frame["rsi_14"]
        .iloc[-1]
        == 100
    )
    assert (
        calculate_default_rsi(price_bars([float(value) for value in range(15, 0, -1)]))
        .frame["rsi_14"]
        .iloc[-1]
        == 0
    )


def test_rsi_14_uses_wilder_smoothing_after_seed() -> None:
    closes = [float(value) for value in range(1, 16)] + [14.0]
    assert calculate_default_rsi(price_bars(closes)).frame["rsi_14"].iloc[-1] == pytest.approx(
        92.8571428571
    )


def test_period_settings_change_specs_labels_and_calculated_values() -> None:
    source = price_bars([float(value) for value in range(1, 41)])
    configured = (
        make_atr_feature(10),
        make_rsi_feature(7),
        make_ema_feature(9),
        make_overlap_feature(12),
        make_macd_feature(5, 34, 6),
    )
    assert [spec.name for spec in configured[0].specs] == ["atr_10"]
    assert configured[0].specs[0].parameters == {"period": 10}
    assert configured[0].calculate(source).frame["atr_10"].iloc[9] == pytest.approx(0.9)
    assert configured[1].specs[0].name == "rsi_7"
    assert configured[1].calculate(source).frame["rsi_7"].iloc[7] == 100
    assert (
        configured[2].calculate(source).frame["ema_9"].iloc[1]
        != calculate_default_ema(source).frame["ema_20"].iloc[1]
    )
    assert configured[3].specs[0].name == "rolling_overlap_12"
    assert configured[4].specs[0].parameters == {
        "fast_period": 5,
        "slow_period": 34,
        "signal_period": 6,
    }
    assert configured[4].specs[1].parameters == configured[4].specs[0].parameters
    assert [view.label for view in configured[4].views] == [
        "MACD 5/34/6",
        "MACD 5/34/6 signal",
        "MACD 5/34/6 histogram",
    ]
    assert "5/34" in configured[4].views[0].description
    assert "6-period" in configured[4].views[1].description
    assert "6-period" in configured[4].views[2].description
    relative = make_normalized_feature(10)
    atr_view = next(view for view in relative.views if view.identifier == "candle_range_to_atr_10")
    assert atr_view.label == "Candle Range To Atr 10"
    assert atr_view.selection_key == "candle_range_to_atr"


@pytest.mark.parametrize(
    ("factory", "arguments", "message"),
    [
        (make_atr_feature, (0,), "ATR period"),
        (make_ema_feature, (True,), "EMA period"),
        (make_rsi_feature, (1.5,), "RSI period"),
        (make_overlap_feature, (1,), "rolling overlap period"),
        (make_macd_feature, (26, 12, 9), "fast period must be below slow period"),
        (make_normalized_feature, (0,), "ATR period for normalized candles"),
    ],
)
def test_period_factories_reject_invalid_settings(
    factory: object, arguments: tuple[object, ...], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        factory(*arguments)  # type: ignore[operator]


def test_discovers_test_feature_and_skips_invalid_modules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "custom_signal.py").write_text(
        """from app.features import FeatureDefinition, FeatureSpec, FeatureTable, FeatureViewSpec
spec = FeatureSpec("custom_signal", "Float64")
def calculate(bars):
    return FeatureTable.from_columns((spec,), [bar.time for bar in bars],
                                     {spec.name: [bar.close * 2 for bar in bars]})
feature = FeatureDefinition(
    (spec,), calculate, (FeatureViewSpec(
        "custom_signal", spec.name, "Custom Signal", "Test pane", "line",
        pane="separate", series_options={"color": "#00ffff"}, pane_height=120,
        scale_range=(0, 10),
    ),),
)
""",
        encoding="utf-8",
    )
    (tmp_path / "invalid.py").write_text("feature = {'id': 'invalid'}\n", encoding="utf-8")
    (tmp_path / "invalid_chart.py").write_text(
        """from app.features import FeatureDefinition, FeatureSpec, FeatureViewSpec
spec = FeatureSpec("invalid_chart", "Float64")
feature = FeatureDefinition((spec,), lambda bars: (),
    (FeatureViewSpec("invalid_chart", "missing", "Invalid chart", "Bad", "line"),))
""",
        encoding="utf-8",
    )
    (tmp_path / "invalid_style.py").write_text(
        """from app.features import FeatureDefinition, FeatureSpec, FeatureViewSpec
spec = FeatureSpec("invalid_style", "Float64")
feature = FeatureDefinition((spec,), lambda bars: (),
    (FeatureViewSpec("invalid_style", spec.name, "Invalid", "Bad color", "line",
                     series_options={"color": "red"}),))
""",
        encoding="utf-8",
    )
    (tmp_path / "broken.py").write_text("raise RuntimeError('broken import')\n", encoding="utf-8")
    monkeypatch.setattr(feature_package, "__path__", [str(tmp_path)])
    caplog.set_level(logging.WARNING, logger="app.features")

    root = tmp_path / "market"
    root.mkdir()
    write_workbook(root / "EURUSD_H1.xlsx", [(1, 1, 3, 0, 2, 5)])
    with TestClient(create_app(Settings(root))) as client:
        response = client.get("/api/v1/bars?symbol=EURUSD&timeframe=H1").json()

    assert [definition["id"] for definition in response["indicators"]] == ["custom_signal"]
    assert response["indicators"][0]["points"] == [{"time": 1, "value": 4.0}]
    assert "Skipping feature module app.features.broken: broken import" in caplog.text
    assert "must export a FeatureDefinition" in caplog.text
    assert "references an unknown feature" in caplog.text
    assert "series_options.color must be a 6- or 8-digit hex color" in caplog.text
