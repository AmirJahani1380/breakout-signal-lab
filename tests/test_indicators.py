import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import features as feature_package
from app.bars import Bar
from app.features.ema_20 import calculate as calculate_ema_20
from app.features.rsi_14 import calculate as calculate_rsi_14
from app.main import Settings, create_app
from tests.test_bars import write_workbook


def price_bars(closes: list[float]) -> tuple[Bar, ...]:
    return tuple(Bar(index, close, close, close, close, 1) for index, close in enumerate(closes))


def test_ema_20_is_empty_or_seeded_by_first_close() -> None:
    assert calculate_ema_20(()).frame.empty
    values = calculate_ema_20(price_bars([1.0, 2.0, 3.0])).frame["ema_20"].tolist()
    assert values == pytest.approx((1.0, 1.0952380952, 1.2766439909))


def test_rsi_14_handles_warmup_flat_and_directional_prices() -> None:
    assert calculate_rsi_14(price_bars([1.0] * 14)).frame["rsi_14"].isna().all()
    assert calculate_rsi_14(price_bars([1.0] * 15)).frame["rsi_14"].iloc[-1] == 50
    assert (
        calculate_rsi_14(price_bars([float(value) for value in range(15)])).frame["rsi_14"].iloc[-1]
        == 100
    )
    assert (
        calculate_rsi_14(price_bars([float(value) for value in range(15, 0, -1)]))
        .frame["rsi_14"]
        .iloc[-1]
        == 0
    )


def test_rsi_14_uses_wilder_smoothing_after_seed() -> None:
    closes = [float(value) for value in range(1, 16)] + [14.0]
    assert calculate_rsi_14(price_bars(closes)).frame["rsi_14"].iloc[-1] == pytest.approx(
        92.8571428571
    )


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
