import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import indicators as indicator_package
from app.bars import Bar
from app.indicators.ema20 import calculate as ema_20
from app.indicators.rsi14 import calculate as rsi_14
from app.main import Settings, create_app
from tests.test_bars import write_workbook


def price_bars(closes: list[float]) -> tuple[Bar, ...]:
    return tuple(Bar(index, close, close, close, close, 1) for index, close in enumerate(closes))


def test_ema_20_is_empty_or_seeded_by_first_close() -> None:
    assert ema_20(()) == ()
    values = [point.value for point in ema_20(price_bars([1.0, 2.0, 3.0]))]
    assert values == pytest.approx((1.0, 1.0952380952, 1.2766439909))


def test_rsi_14_handles_warmup_flat_and_directional_prices() -> None:
    assert rsi_14(price_bars([1.0] * 14)) == ()
    assert rsi_14(price_bars([1.0] * 15))[0].value == 50
    assert rsi_14(price_bars([float(value) for value in range(15)]))[0].value == 100
    assert rsi_14(price_bars([float(value) for value in range(15, 0, -1)]))[0].value == 0


def test_rsi_14_uses_wilder_smoothing_after_seed() -> None:
    closes = [float(value) for value in range(1, 16)] + [14.0]
    assert rsi_14(price_bars(closes))[-1].value == pytest.approx(92.8571428571)


def test_discovers_test_plugin_and_skips_invalid_modules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "custom_signal.py").write_text(
        """from app.indicators import Indicator, IndicatorPoint
def calculate(bars):
    return tuple(IndicatorPoint(bar.time, bar.close * 2) for bar in bars)
indicator = Indicator(
    identifier="custom_signal", label="Custom Signal", description="Test pane",
    series_type="LineSeries", pane="separate", calculate=calculate,
    series_options={"color": "#00ffff"}, pane_height=120, scale_range=(0, 10),
)
""",
        encoding="utf-8",
    )
    (tmp_path / "invalid.py").write_text("indicator = {'id': 'invalid'}\n", encoding="utf-8")
    (tmp_path / "invalid_chart.py").write_text(
        """from app.indicators import Indicator
indicator = Indicator(
    identifier="invalid_chart", label="Invalid chart", description="Bad line",
    series_type="LineSeries", pane="main", calculate=lambda bars: (),
    default_applied=True, reference_lines=({"price": "bad"},),
)
""",
        encoding="utf-8",
    )
    (tmp_path / "broken.py").write_text("raise RuntimeError('broken import')\n", encoding="utf-8")
    monkeypatch.setattr(indicator_package, "__path__", [str(tmp_path)])
    caplog.set_level(logging.WARNING, logger="app.indicators")

    root = tmp_path / "market"
    root.mkdir()
    write_workbook(root / "EURUSD_H1.xlsx", [(1, 1, 3, 0, 2, 5)])
    with TestClient(create_app(Settings(root))) as client:
        response = client.get("/api/v1/bars?symbol=EURUSD&timeframe=H1").json()

    assert [definition["id"] for definition in response["indicators"]] == ["custom_signal"]
    assert response["indicators"][0]["points"] == [{"time": 1, "value": 4.0}]
    assert "Skipping indicator module app.indicators.broken: broken import" in caplog.text
    assert "must export an Indicator" in caplog.text
    assert "reference_lines[0].price must be a finite number" in caplog.text
