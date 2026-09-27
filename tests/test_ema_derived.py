from __future__ import annotations

import csv
import json
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import Page

from app.bars import Bar
from app.features.atr import atr_values
from app.features.ema import make_feature as make_ema
from app.features.ema_distance import make_feature as make_distance
from app.features.ema_slope import make_feature as make_slope
from app.main import Settings, create_app
from tests.test_bars import write_csv


def test_ema_distance_and_slope_divide_by_aligned_atr_and_support_absolute() -> None:
    closes = (3.0, 2.0, 1.0, 2.0, 3.0)
    bars = [
        Bar(index + 1, close, close + index + 1, close - 1, close, 1)
        for index, close in enumerate(closes)
    ]
    ema = make_ema(2).calculate(bars).frame["ema_2"].tolist()
    atr = atr_values(bars, 2)
    distance = make_distance(2, 2).calculate(bars).frame["ema_distance_2_atr_2"]
    slope = make_slope(2, 1, 2).calculate(bars).frame["ema_slope_2_1_atr_2"]
    absolute_distance = (
        make_distance(2, 2, True).calculate(bars).frame["ema_distance_2_atr_2_absolute"]
    )
    absolute_slope = make_slope(2, 1, 2, True).calculate(bars).frame["ema_slope_2_1_atr_2_absolute"]

    assert pd.isna(distance.iloc[0]) and pd.isna(slope.iloc[0])
    assert distance.iloc[1:].tolist() == pytest.approx(
        [(closes[i] - ema[i]) / atr[i] for i in range(1, len(bars))]
    )
    assert slope.iloc[1:].tolist() == pytest.approx(
        [(ema[i] - ema[i - 1]) / atr[i] for i in range(1, len(bars))]
    )
    assert distance.iloc[1] < 0 and slope.iloc[1] < 0
    assert absolute_distance.iloc[1:].tolist() == pytest.approx(distance.iloc[1:].abs().tolist())
    assert absolute_slope.iloc[1:].tolist() == pytest.approx(slope.iloc[1:].abs().tolist())
    assert make_slope(2, 20, 2).calculate(bars).frame["ema_slope_2_20_atr_2"].isna().all()
    assert make_distance(2, 3).calculate(bars).frame["ema_distance_2_atr_3"].isna().sum() == 2


def test_ema_derived_values_are_null_when_atr_is_zero() -> None:
    bars = [Bar(index + 1, 5, 5, 5, 5, 1) for index in range(4)]
    assert make_distance(2, 2).calculate(bars).frame.iloc[:, 0].isna().all()
    assert make_slope(2, 1, 2).calculate(bars).frame.iloc[:, 0].isna().all()


@pytest.mark.parametrize("invalid", [0, -1, True, 1.5, "1"])
def test_ema_slope_rejects_invalid_bar_count(invalid: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        make_slope(20, invalid)  # type: ignore[arg-type]


@pytest.mark.parametrize("factory", [make_distance, make_slope])
def test_ema_derived_factories_reject_invalid_atr_and_absolute(factory: object) -> None:
    with pytest.raises(ValueError, match="ATR period"):
        factory(20, atr_period=0)  # type: ignore[operator]
    with pytest.raises(ValueError, match="absolute must be a boolean"):
        factory(20, absolute=1)  # type: ignore[operator]


def test_ema_features_flow_through_catalog_chart_and_export(tmp_path: Path) -> None:
    root = tmp_path / "market"
    root.mkdir()
    rows = [
        (index + 1, close, close + 1, close - 1, close, 10)
        for index in range(150)
        for close in [100 + index / 10]
    ]
    write_csv(root / "EURUSD_H1.csv", rows)
    settings = {
        "ema_period": 9,
        "atr_period": 5,
        "ema_slope_bars": 1,
        "ema_distance_absolute": 1,
        "ema_slope_absolute": 1,
    }
    selected = ["ema_distance_9_atr_5_absolute", "ema_slope_9_1_atr_5_absolute"]

    with TestClient(create_app(Settings(root))) as client:
        catalog_response = client.get("/api/v1/features", params=settings)
        chart_response = client.get(
            "/api/v1/bars",
            params={
                "symbol": "EURUSD",
                "timeframe": "H1",
                "features": ",".join(selected),
                **settings,
            },
        )
        export_response = client.get(
            "/api/v1/export",
            params={
                "symbol": "EURUSD",
                "timeframe": "H1",
                "features": ",".join(selected),
                "format": "csv",
                **settings,
            },
        )
        invalid_slope = client.get("/api/v1/features", params={"ema_slope_bars": 0})
        invalid_absolute = client.get("/api/v1/features", params={"ema_slope_absolute": 2})

    assert (
        catalog_response.status_code
        == chart_response.status_code
        == export_response.status_code
        == 200
    )
    assert invalid_slope.status_code == invalid_absolute.status_code == 422
    catalog = catalog_response.json()
    offered = {feature["name"]: feature for feature in catalog["export_features"]}
    assert set(selected) <= offered.keys()
    assert offered[selected[0]]["parameters"] == {"period": 9, "atr_period": 5, "absolute": 1}
    assert offered[selected[1]]["parameters"] == {
        "period": 9,
        "slope_bars": 1,
        "atr_period": 5,
        "absolute": 1,
    }
    views = {view["id"]: view for view in chart_response.json()["indicators"]}
    assert all(views[name]["pane"] == "separate" for name in selected)
    assert all(all(point["value"] >= 0 for point in views[name]["points"]) for name in selected)

    with ZipFile(BytesIO(export_response.content)) as archive:
        exported = list(csv.DictReader(archive.read("bar_features.csv").decode().splitlines()))
        metadata = json.loads(archive.read("bar_features.csv.json"))
    assert metadata["export_warm_up_rows"] == 100
    assert [feature["name"] for feature in metadata["features"]] == selected
    assert len(exported) == 50
    for name in selected:
        first_time = int(exported[0]["time"])
        matching_point = next(
            point for point in views[name]["points"] if point["time"] == first_time
        )
        assert float(exported[0][name]) == pytest.approx(matching_point["value"])


def test_absolute_controls_are_checkboxes_in_browser(page: Page, viewer_url: str) -> None:
    page.goto(viewer_url)
    page.get_by_role("tab", name="Indicators").click()
    distance = page.locator('[data-indicator="ema_distance_20_atr_20"]')
    checkbox = distance.locator('[data-period="ema_distance_absolute"]')
    checkbox.wait_for()
    assert checkbox.get_attribute("type") == "checkbox"
    assert not checkbox.is_checked()
    checkbox.check()
    updated = page.locator('[data-indicator="ema_distance_20_atr_20_absolute"]')
    updated.wait_for()
    assert updated.locator('[data-period="ema_distance_absolute"]').is_checked()
    slope_checkbox = page.locator(
        '[data-indicator="ema_slope_20_20_atr_20"] [data-period="ema_slope_absolute"]'
    )
    assert slope_checkbox.get_attribute("type") == "checkbox"
    slope_checkbox.check()
    slope_absolute = page.locator('[data-indicator="ema_slope_20_20_atr_20_absolute"]')
    slope_absolute.wait_for()
    assert slope_absolute.locator('[data-period="ema_slope_absolute"]').is_checked()
