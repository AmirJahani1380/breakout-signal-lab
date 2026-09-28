from __future__ import annotations

import json
from io import BytesIO, StringIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zipfile import ZipFile

import pandas as pd
import pytest
from playwright.sync_api import Page


def test_event_chart_overlay_matches_api_record(page: Page, viewer_url: str) -> None:
    page.goto(viewer_url)
    page.locator("#symbol").select_option("EURUSD")
    page.get_by_role("button", name="H1").click()
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '1000'")
    page.get_by_role("tab", name="Events").click()
    first = page.locator('#event-list button[data-detector="ema_breakout"]').first
    first.wait_for()
    identifier = first.get_attribute("data-event-id")
    first.click()
    records = page.request.get(f"{viewer_url}/api/v1/events?symbol=EURUSD&timeframe=H1").json()[
        "events"
    ]
    record = next(event for event in records if event["id"] == identifier)
    assert page.locator("#chart").get_attribute("data-selected-event-id") == identifier
    assert (
        float(page.locator("#chart").get_attribute("data-selected-broken-level"))
        == record["broken_level"]
    )
    assert str(record["setup_id"]) in page.locator("#event-details").inner_text()


def test_selected_event_details_remain_visible_on_narrow_chart(page: Page, viewer_url: str) -> None:
    page.set_viewport_size({"width": 460, "height": 900})
    page.goto(viewer_url)
    page.locator("#symbol").select_option("BREAKOUT")
    page.get_by_role("button", name="H1").click()
    page.get_by_role("tab", name="Events").click()
    page.locator("#event-list button").first.click()
    assert page.locator("#event-details").evaluate(
        "element => element.getBoundingClientRect().height >= 50"
    )
    assert page.locator("#event-list").evaluate(
        "element => element.getBoundingClientRect().height >= 50"
    )


def test_fixed_2r_chart_details_agree_with_downloaded_csv(
    page: Page, viewer_url: str, tmp_path: Path
) -> None:
    page.goto(viewer_url)
    page.locator("#symbol").select_option("BREAKOUT")
    page.get_by_role("button", name="H1").click()
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '7'")
    page.get_by_role("tab", name="Events").click()
    assert page.locator("#label-horizon").input_value() == "30"
    assert page.locator("#event-detectors input").evaluate_all(
        "inputs => inputs.map(input => input.value)"
    ) == ["ema_breakout", "swing_breakout"]
    page.locator(".label-settings summary").click()
    page.locator("#label-atr-period").fill("1")
    page.locator("#label-atr-period").press("Tab")
    page.locator("#label-horizon").fill("2")
    page.locator("#label-horizon").press("Tab")
    first = page.locator("#event-list button").first
    first.wait_for()
    first.click()
    identifier = first.get_attribute("data-event-id")
    assert identifier is not None
    records = page.request.get(
        f"{viewer_url}/api/v1/events?symbol=BREAKOUT&timeframe=H1"
        "&label_atr_period=1&label_horizon=2"
    ).json()["events"]
    selected = next(record for record in records if record["id"] == identifier)
    details = page.locator("#event-details").inner_text()
    assert f"label {selected['label_status']}" in details
    assert f"net {float(selected['net_r']):g}R" in details
    assert (
        page.locator("#chart").get_attribute("data-selected-label-status")
        == selected["label_status"]
    )
    assert float(page.locator("#chart").get_attribute("data-selected-stop-price")) == pytest.approx(
        selected["stop_price"]
    )
    assert float(
        page.locator("#chart").get_attribute("data-selected-target-price")
    ) == pytest.approx(selected["target_price"])

    with page.expect_download() as transfer:
        page.get_by_role("button", name="Download labels CSV").click()
    label_archive_path = tmp_path / transfer.value.suggested_filename
    transfer.value.save_as(label_archive_path)
    with ZipFile(label_archive_path) as archive:
        labels = pd.read_csv(archive.open("encountered_events.csv"))
    label_row = labels.loc[labels["id"] == identifier].iloc[0]
    assert label_row["entry_open"] == selected["entry_open"]
    assert label_row["stop_price"] == pytest.approx(selected["stop_price"])
    assert label_row["risk_price"] == pytest.approx(selected["risk_price"])
    assert label_row["target_price"] == pytest.approx(selected["target_price"])
    assert (
        page.locator("#export-labels-status").inner_text()
        == "Encountered events export downloaded."
    )

    def fail_labels_export(route: object) -> None:
        route.fulfill(status=422, json={"detail": "fixture export failure"})  # type: ignore[attr-defined]

    page.route("**/api/v1/events/export?*", fail_labels_export)
    page.get_by_role("button", name="Download labels CSV").click()
    page.get_by_text("Event export failed: fixture export failure").wait_for()
    assert page.locator("#export-labels-status").is_visible()
    page.unroute("**/api/v1/events/export?*", fail_labels_export)

    page.get_by_role("tab", name="Export").click()
    while page.locator("#export-features input:checked").count():
        page.locator("#export-features input:checked").first.uncheck()
    for format in ("csv", "parquet"):
        with page.expect_download() as transfer:
            page.get_by_role("button", name=f"Download events {format.upper()}").click()
        download = transfer.value
        archive_path = tmp_path / download.suggested_filename
        download.save_as(archive_path)
        with ZipFile(archive_path) as archive:
            frame = (
                pd.read_csv(archive.open("encountered_events.csv"))
                if format == "csv"
                else pd.read_parquet(BytesIO(archive.read("encountered_events.parquet")))
            )
        row = frame.loc[frame["id"] == identifier].iloc[0]
        assert row["label_status"] == selected["label_status"]
        assert row["entry_fill"] == pytest.approx(selected["entry_fill"])
        assert row["net_r"] == pytest.approx(selected["net_r"])
        assert json.loads(row["label_config"])["horizon"] == 2
        assert json.loads(row["label_config"])["atr_period"] == 1


def test_label_settings_changed_during_backfill_refresh_chart_and_export(
    page: Page, viewer_url: str, tmp_path: Path
) -> None:
    page.goto(viewer_url)
    page.locator("#symbol").select_option("EURUSD")
    page.get_by_role("button", name="H1").click()
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '1000'")
    page.get_by_role("tab", name="Events").click()
    selected_id = page.locator("#event-list button").first.get_attribute("data-event-id")
    assert selected_id is not None
    held_pages: list[object] = []

    def hold_older_events(route: object) -> None:
        request = route.request  # type: ignore[attr-defined]
        if "before" in parse_qs(urlparse(request.url).query):
            held_pages.append(route)
        else:
            route.continue_()  # type: ignore[attr-defined]

    page.route("**/api/v1/events?*", hold_older_events)
    with page.expect_request(
        lambda request: "/api/v1/events?" in request.url and "before=" in request.url
    ):
        page.evaluate(
            "window.__breakoutChart.timeScale().setVisibleLogicalRange({from: 0, to: 20})"
        )
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '1000'")
    page.wait_for_timeout(50)
    assert len(held_pages) == 1

    page.locator(".label-settings summary").click()
    for selector, value in (
        ("#label-atr-period", "1"),
        ("#label-horizon", "2"),
        ("#label-slippage", "0.1"),
        ("#label-commission", "0.05"),
    ):
        control = page.locator(selector)
        control.fill(value)
        control.press("Tab")
    page.wait_for_function(
        "document.querySelector('#chart').dataset.barCount === '1000' && "
        "document.querySelector('#event-list button') !== null"
    )
    held_pages[0].fulfill(json={"events": [], "swings": [], "has_more": False})  # type: ignore[attr-defined]
    matching = page.locator(f'#event-list button[data-event-id="{selected_id}"]')
    matching.click()
    params = {
        "symbol": "EURUSD",
        "timeframe": "H1",
        "label_atr_period": 1,
        "label_horizon": 2,
        "label_slippage": 0.1,
        "label_commission": 0.05,
    }
    api_events = page.request.get(f"{viewer_url}/api/v1/events", params=params).json()["events"]
    record = next(event for event in api_events if event["id"] == selected_id)
    assert (
        page.locator("#chart").get_attribute("data-selected-label-status") == record["label_status"]
    )
    assert float(page.locator("#chart").get_attribute("data-selected-stop-price")) == pytest.approx(
        record["stop_price"]
    )
    assert f"entry {record['entry_fill']}" in page.locator("#event-details").inner_text()

    page.get_by_role("tab", name="Export").click()
    while page.locator("#export-features input:checked").count():
        page.locator("#export-features input:checked").first.uncheck()
    with page.expect_download() as transfer:
        page.get_by_role("button", name="Download events CSV").click()
    download = transfer.value
    archive_path = tmp_path / download.suggested_filename
    download.save_as(archive_path)
    with ZipFile(archive_path) as archive:
        frame = pd.read_csv(archive.open("encountered_events.csv"))
    exported = frame.loc[frame["id"] == selected_id].iloc[0]
    assert exported["label_status"] == record["label_status"]
    assert exported["entry_fill"] == pytest.approx(record["entry_fill"])
    assert json.loads(exported["label_config"])["slippage"] == 0.1


def test_event_panel_keeps_chart_clear_and_filters_events(page: Page, viewer_url: str) -> None:
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(viewer_url)
    page.locator("#symbol").select_option("EURUSD")
    page.get_by_role("button", name="H1").click()
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '1000'")
    page.get_by_role("tab", name="Events").click()
    panel = page.locator("#events-panel").bounding_box()
    chart = page.locator("#chart").bounding_box()
    assert panel is not None and chart is not None
    assert panel["x"] + panel["width"] < chart["x"]

    first = page.locator('#event-list button[data-detector="ema_breakout"]').first
    first.click()
    assert first.get_attribute("aria-current") == "true"
    assert "EMA close crossing" in first.inner_text()
    selected_date = first.locator(".event-row-top").inner_text()[:10]
    assert " UTC" in first.locator(".event-row-top").inner_text()

    page.locator("#event-search").fill("EMA close")
    assert page.locator('#event-list button[data-detector="swing_breakout"]').count() == 0
    assert page.locator('#event-list button[data-detector="ema_breakout"]').count() > 0
    page.locator("#event-date").fill(selected_date)
    assert page.locator("#event-list button").count() > 0
    assert all(
        selected_date in text
        for text in page.locator("#event-list .event-row-top").all_inner_texts()
    )
    page.get_by_role("tab", name="Export").click()
    export_panel = page.locator("#export-panel").bounding_box()
    chart = page.locator("#chart").bounding_box()
    assert export_panel is not None and chart is not None
    assert export_panel["x"] + export_panel["width"] < chart["x"]


def test_crosshair_details_stay_below_title_on_narrow_screen(page: Page, viewer_url: str) -> None:
    page.set_viewport_size({"width": 480, "height": 800})
    select_timeframe(page, viewer_url, "M15")
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '3'")
    page.get_by_role("tab", name="Indicators").click()
    for identifier in ("candle_range", "body_size", "upper_wick_size", "lower_wick_size"):
        page.locator(f'[data-indicator="{identifier}"] [data-apply]').check()
    page.evaluate(
        """() => window.__breakoutChart.setCrosshairPosition(
            3.5, 1735690500, window.__breakoutChart.panes()[0].getSeries()[0])"""
    )
    page.wait_for_function("document.querySelector('#legend').textContent.includes('Range')")
    title = page.locator("h1").bounding_box()
    legend = page.locator("#legend").bounding_box()
    assert title is not None and legend is not None
    assert title["y"] + title["height"] <= legend["y"]
    assert legend["height"] <= 46


def test_swing_breakouts_and_donchian_channel_in_browser(page: Page, viewer_url: str) -> None:
    page.goto(viewer_url)
    page.locator("#symbol").select_option("BREAKOUT")
    page.get_by_role("button", name="H1").click()
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '7'")
    page.get_by_role("tab", name="Events").click()
    page.locator(".event-settings summary").click()
    for selector, value in (("#swing-left", "1"), ("#swing-right", "1"), ("#swing-lookback", "2")):
        control = page.locator(selector)
        control.fill(value)
        control.press("Tab")
    page.wait_for_function(
        "document.querySelectorAll('#event-list button[data-detector=swing_breakout]').length === 2"
    )
    page.wait_for_load_state("networkidle")
    swing_events = page.locator('#event-list button[data-detector="swing_breakout"]')
    assert swing_events.count() == 2
    swing_events.first.click()
    page.wait_for_function("document.querySelector('#chart').dataset.selectedBrokenLevel === '12'")
    page.get_by_role("tab", name="Indicators").click()
    swing = page.locator('[data-indicator="confirmed_swing_3_3"]')
    swing.locator('[data-period="swing_period"]').fill("1")
    swing.locator('[data-period="swing_period"]').press("Tab")
    page.locator('[data-indicator="confirmed_swing_1_1"]').wait_for()
    page.wait_for_function(
        "document.querySelector('#chart').dataset.barCount === '7' && "
        "document.querySelector('#state').textContent === ''"
    )
    page.locator('[data-indicator="confirmed_swing_1_1"] [data-apply]').check()
    page.wait_for_function("Number(document.querySelector('#chart').dataset.markerCount) > 0")
    period = page.locator('[data-indicator="donchian_upper_20"] [data-period="donchian_period"]')
    period.fill("3")
    period.press("Tab")
    page.locator('[data-indicator="donchian_upper_3"]').wait_for()
    for edge in ("upper", "lower", "middle"):
        page.locator(f'[data-indicator="donchian_{edge}_3"] [data-apply]').check()
    page.wait_for_function(
        "window.__breakoutChart.panes()[0].getSeries().length === 5 && "
        "window.__breakoutChart.panes()[0].getSeries().slice(-3).every(s => s.data().length > 0)"
    )
    series = page.evaluate(
        "window.__breakoutChart.panes()[0].getSeries().slice(-3).map(s => s.data().at(-1).value)"
    )
    assert sorted(series) == [6, 9.5, 13]


def test_feature_filters_control_swing_markers_and_matching_share(
    page: Page, viewer_url: str
) -> None:
    page.goto(viewer_url)
    page.locator("#symbol").select_option("BREAKOUT")
    page.get_by_role("button", name="H1").click()
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '7'")
    page.get_by_role("tab", name="Events").click()
    page.locator(".event-settings summary").click()
    for selector, value in (("#swing-left", "1"), ("#swing-right", "1"), ("#swing-lookback", "2")):
        control = page.locator(selector)
        control.fill(value)
        control.press("Tab")
    page.wait_for_function(
        "document.querySelectorAll('#event-list button[data-detector=swing_breakout]').length === 2"
    )
    total = page.locator("#event-list button").count()
    original_markers = int(page.locator("#chart").get_attribute("data-event-marker-count") or "0")

    page.locator("#add-feature-filter").click()
    first_filter = page.locator(".feature-filter").first
    first_filter.get_by_label("Event feature").select_option("candle_direction")
    first_filter.get_by_label("Filter value").fill("1")
    page.wait_for_function(
        "document.querySelectorAll('#event-list button[data-detector=swing_breakout]').length === 1"
    )
    assert page.locator('#event-list button[data-detector="swing_breakout"]').count() == 1
    matching = page.locator("#event-list button").count()
    assert 0 < matching < total
    assert page.locator("#event-count").inner_text() == (
        f"{matching} / {total} loaded events ({100 * matching / total:.1f}%) match current filters"
    )
    assert int(page.locator("#chart").get_attribute("data-event-marker-count") or "0") == matching
    assert matching < original_markers

    page.locator("#add-feature-filter").click()
    second_filter = page.locator(".feature-filter").nth(1)
    second_filter.get_by_label("Event feature").select_option("atr_20")
    second_filter.get_by_label("Comparison").select_option("gt")
    second_filter.get_by_label("Filter value").fill("1.5")
    page.wait_for_function(
        "document.querySelector('#event-count').textContent.startsWith('0 / 4') && "
        "document.querySelector('#chart').dataset.eventMarkerCount === '0'"
    )
    assert page.locator("#event-list button").count() == 0
    assert page.locator("#chart").get_attribute("data-event-marker-count") == "0"
    assert page.locator("#event-count").inner_text().startswith(f"0 / {total} loaded events")

    second_filter.get_by_role("button", name="Remove").click()
    page.wait_for_function(
        "document.querySelector('#event-count').textContent.startsWith('2 / 4') && "
        "document.querySelector('#chart').dataset.eventMarkerCount === '2'"
    )
    assert page.locator("#event-list button").count() == matching

    page.locator("#clear-event-filters").click()
    assert page.locator("#event-list button").count() == total
    assert page.locator("#chart").get_attribute("data-event-marker-count") == str(original_markers)


def test_three_non_null_feature_conditions_and_period_reload(page: Page, viewer_url: str) -> None:
    page.goto(viewer_url)
    page.get_by_role("tab", name="Indicators").click()
    for setting, value in (("atr_period", "2"), ("ema_period", "2"), ("ema_slope_bars", "1")):
        control = page.locator(f'[data-period="{setting}"]').first
        control.fill(value)
        control.press("Tab")
        page.wait_for_load_state("networkidle")
    page.wait_for_function("eventFeatures.some(feature => feature.name === 'ema_slope_2_1_atr_2')")
    page.locator("#symbol").select_option("BREAKOUT")
    page.get_by_role("button", name="H1").click()
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '7'")
    page.get_by_role("tab", name="Events").click()
    page.locator(".event-settings summary").click()
    for selector, value in (("#swing-left", "1"), ("#swing-right", "1"), ("#swing-lookback", "2")):
        control = page.locator(selector)
        control.fill(value)
        control.press("Tab")
    page.wait_for_function(
        "document.querySelectorAll('#event-list button[data-detector=swing_breakout]').length === 2"
    )
    for name, operator, value in (
        ("candle_direction", "eq", "1"),
        ("ema_slope_2_1_atr_2", "gt", "0.12"),
        ("atr_2", "gt", "1.5"),
    ):
        page.locator("#add-feature-filter").click()
        row = page.locator(".feature-filter").last
        row.get_by_label("Event feature").select_option(name)
        row.get_by_label("Comparison").select_option(operator)
        row.get_by_label("Filter value").fill(value)
    page.wait_for_function(
        "breakoutEvents.every(event => event.feature_values && "
        "'candle_direction' in event.feature_values && "
        "'ema_slope_2_1_atr_2' in event.feature_values && 'atr_2' in event.feature_values)"
    )
    values = page.evaluate("breakoutEvents.map(event => event.feature_values)")
    assert all(
        value["ema_slope_2_1_atr_2"] is not None and value["atr_2"] is not None for value in values
    )
    assert page.locator("#event-count").inner_text().startswith("2 / 5 loaded events (40.0%)")
    assert page.locator("#chart").get_attribute("data-event-marker-count") == "2"
    assert page.locator('#event-list button[data-detector="swing_breakout"]').count() == 1

    page.get_by_role("tab", name="Indicators").click()
    period = page.locator('[data-period="atr_period"]').first
    period.fill("3")
    period.press("Tab")
    page.wait_for_function("eventFeatures.some(feature => feature.name === 'atr_3')")
    page.get_by_role("tab", name="Events").click()
    rows = page.locator(".feature-filter")
    assert rows.count() == 3
    assert rows.nth(1).get_by_label("Event feature").input_value() == "ema_slope_2_1_atr_3"
    assert rows.nth(2).get_by_label("Event feature").input_value() == "atr_3"
    assert [row.get_by_label("Filter value").input_value() for row in rows.all()] == [
        "1",
        "0.12",
        "1.5",
    ]
    page.wait_for_function(
        "breakoutEvents.length === 5 && "
        "breakoutEvents.every(event => event.feature_values && 'atr_3' in event.feature_values)"
    )
    assert page.locator("#event-count").inner_text().startswith("2 / 5 loaded events (40.0%)")

    def omit_selected_feature(route: object) -> None:
        payload = route.fetch().json()  # type: ignore[attr-defined]
        payload["export_features"] = [
            spec for spec in payload["export_features"] if spec.get("selection_key") != "atr"
        ]
        route.fulfill(json=payload)  # type: ignore[attr-defined]

    page.route("**/api/v1/features?*atr_period=4*", omit_selected_feature)
    page.get_by_role("tab", name="Indicators").click()
    period = page.locator('[data-period="atr_period"]').first
    period.fill("4")
    period.press("Tab")
    page.wait_for_function(
        "selectedPeriods.get('atr_period') === '4' && "
        "eventFeatures.every(feature => feature.name !== 'atr_4')"
    )
    page.get_by_role("tab", name="Events").click()
    assert page.locator('.feature-filter[data-invalid="true"]').count() == 1
    assert "unavailable after settings changed" in page.locator("#event-count").inner_text()
    assert page.locator("#chart").get_attribute("data-event-marker-count") == "0"

    page.locator("#clear-event-filters").click()
    assert page.locator("#event-list button").count() == 5
    assert page.locator("#chart").get_attribute("data-event-marker-count") == "5"
    page.locator("#add-feature-filter").click()
    boolean_filter = page.locator(".feature-filter").last
    boolean_filter.get_by_label("Event feature").select_option("is_engulfing")
    assert boolean_filter.get_by_label("Comparison").locator("option").count() == 4
    boolean_filter.get_by_label("Filter value").fill("false")
    page.wait_for_function(
        "breakoutEvents.every(event => event.feature_values && "
        "'is_engulfing' in event.feature_values)"
    )
    expected_boolean = page.evaluate(
        "breakoutEvents.filter(event => event.feature_values.is_engulfing === false).length"
    )
    assert page.locator("#event-list button").count() == expected_boolean
    boolean_filter.get_by_label("Filter value").fill("invalid")
    assert page.locator("#event-list button").count() == 0
    assert "Enter a valid filter value" in page.locator("#event-count").inner_text()
    boolean_filter.get_by_label("Filter value").fill("false")

    page.locator("#add-feature-filter").click()
    missing_filter = page.locator(".feature-filter").last
    missing_filter.get_by_label("Event feature").select_option("rolling_overlap_20")
    missing_filter.get_by_label("Comparison").select_option("missing")
    page.wait_for_function(
        "breakoutEvents.every(event => event.feature_values && "
        "'rolling_overlap_20' in event.feature_values)"
    )
    assert page.locator("#event-list button").count() == expected_boolean


def test_failed_filter_calculation_does_not_replace_base_events(
    page: Page, viewer_url: str
) -> None:
    page.goto(viewer_url)
    page.locator("#symbol").select_option("BREAKOUT")
    page.get_by_role("button", name="H1").click()
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '7'")
    page.get_by_role("tab", name="Events").click()
    total = page.locator("#event-list button").count()
    assert total > 0
    page.route(
        "**/api/v1/events?*features=*",
        lambda route: route.fulfill(status=422, json={"detail": "feature calculation failed"}),
    )
    page.locator("#add-feature-filter").click()
    page.wait_for_function(
        "document.querySelector('#event-count').textContent.includes('feature calculation failed')"
    )
    assert page.locator("#event-list button").count() == 0
    page.locator("#clear-event-filters").click()
    assert page.locator("#event-list button").count() == total


def test_swing_indicator_controls_pivot_dots_on_the_chart(page: Page, viewer_url: str) -> None:
    page.goto(viewer_url)
    page.locator("#symbol").select_option("PIVOT")
    page.get_by_role("button", name="H1").click()
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '11'")
    assert page.locator("#chart").get_attribute("data-marker-count") == "0"

    page.get_by_role("tab", name="Indicators").click()
    swing = page.locator('[data-indicator="confirmed_swing_3_3"]')
    assert swing.count() == 1
    swing.locator("[data-apply]").check()
    page.wait_for_function("document.querySelector('#chart').dataset.markerCount === '2'")
    markers = page.evaluate(
        """() => markerPoints(indicatorDefinitions.find(
            definition => definition.id === 'confirmed_swing_3_3'))"""
    )
    assert {(marker["color"], marker["shape"]) for marker in markers} == {
        ("#42a5f5", "circle"),
        ("#ab7df6", "square"),
    }
    swing.get_by_role("button", name="Hide").click()
    page.wait_for_function("document.querySelector('#chart').dataset.markerCount === '0'")
    swing.get_by_role("button", name="Show").click()
    page.wait_for_function("document.querySelector('#chart').dataset.markerCount === '2'")
    swing.locator("[data-apply]").uncheck()
    page.wait_for_function("document.querySelector('#chart').dataset.markerCount === '0'")

    payload = page.request.get(
        f"{viewer_url}/api/v1/bars?symbol=PIVOT&timeframe=H1&features=confirmed_swing_3_3"
    ).json()
    indicator = next(item for item in payload["indicators"] if item["id"] == "confirmed_swing_3_3")
    assert [point["time"] for point in indicator["points"]] == [1_735_689_780, 1_735_690_020]
    assert all("text" not in point for point in indicator["points"])


def test_symbol_then_timeframe_loads_only_the_selected_chart(page: Page, viewer_url: str) -> None:
    page.goto(viewer_url)
    page.locator("#symbol").select_option("EURUSD")
    assert page.get_by_role("button", name="H1").is_visible()
    assert page.get_by_role("button", name="M15").is_visible()
    page.get_by_role("button", name="H1").click()
    page.locator("#chart canvas").first.wait_for()
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '1000'")
    page.get_by_role("button", name="M15").click()
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '3'")
    assert page.get_by_role("button", name="M15").get_attribute("aria-pressed") == "true"


def test_stored_export_chart_and_inspector(page: Page, viewer_url: str) -> None:
    page.goto(viewer_url)
    page.locator("#data-mode").select_option("stored")
    page.locator("#symbol").select_option("XAUUSDzero")
    page.get_by_role("button", name="D1").click()
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '3'")
    assert (
        page.evaluate("window.__breakoutChart.panes()[0].getSeries()[0].data()[1].close") == 1525.7
    )
    assert "Stored data" in page.locator("#dataset-version").inner_text()
    assert "research_score:v7" in page.locator("#dataset-version").inner_text()
    page.locator("#inspect-time").select_option("1306195200")
    assert "research_score (v7): missing" in page.locator("#inspect-values").inner_text()
    page.locator("#inspect-time").select_option("1306281600")
    assert "research_score (v7): 0" in page.locator("#inspect-values").inner_text()
    page.get_by_role("tab", name="Indicators").click()
    page.locator('[data-indicator="ema_20"] [data-apply]').check()
    assert "ema_20 (v1): 1505.6017645079428" in page.locator("#inspect-values").inner_text()
    assert (
        page.evaluate("window.__breakoutChart.panes()[0].getSeries()[1].data()[1].value")
        == 1505.6017645079428
    )
    assert page.locator("#chart").get_attribute("data-bar-count") == "3"


def test_stored_paging_visibility_and_selection_preserve_values(
    page: Page, viewer_url: str
) -> None:
    page.goto(viewer_url)
    page.locator("#export-features input").first.wait_for(state="attached")
    original_export_count = page.locator("#export-features input").count()
    page.locator("#data-mode").select_option("stored")
    assert page.locator("#export-tab").is_disabled()
    page.locator("#symbol").select_option("XAUUSDzero")
    page.get_by_role("button", name="H1").click()
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '1000'")
    latest_time = str(1_500_000_000 + 1001 * 3600)
    page.locator("#inspect-time").select_option(latest_time)
    assert "research_score (v7): 1001" in page.locator("#inspect-values").inner_text()
    page.evaluate("window.__breakoutChart.timeScale().setVisibleLogicalRange({from: 0, to: 20})")
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '1002'")
    assert "research_score (v7): 1001" in page.locator("#inspect-values").inner_text()
    page.locator("#inspect-time").select_option("1500000000")
    assert "research_score (v7): missing" in page.locator("#inspect-values").inner_text()
    page.get_by_role("tab", name="Indicators").click()
    ema = page.locator('[data-indicator="ema_20"]')
    ema.locator("[data-apply]").check()
    ema.get_by_role("button", name="Hide").click()
    assert "ema_20 (v1): 1000" in page.locator("#inspect-values").inner_text()
    ema.get_by_role("button", name="Show").click()
    page.get_by_role("button", name="D1").click()
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '3'")
    page.get_by_role("button", name="H1").click()
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '1000'")
    page.locator("#inspect-time").select_option(latest_time)
    assert "research_score (v7): 1001" in page.locator("#inspect-values").inner_text()
    page.locator("#data-mode").select_option("source")
    assert page.locator("#export-tab").is_enabled()
    assert page.locator("#export-features input").count() == original_export_count


def test_calculated_periods_survive_viewing_stored_chart_and_export(
    page: Page, viewer_url: str
) -> None:
    page.goto(viewer_url)
    page.get_by_role("tab", name="Indicators").click()
    ema_period = page.locator('[data-indicator="ema_20"] [data-period="ema_period"]')
    ema_period.fill("12")
    ema_period.press("Tab")
    page.locator('[data-indicator="ema_12"]').wait_for()
    atr_period = page.locator('[data-indicator="atr_20"] [data-period="atr_period"]')
    atr_period.fill("15")
    atr_period.press("Tab")
    page.locator('[data-indicator="atr_15"]').wait_for()

    page.locator("#data-mode").select_option("stored")
    page.locator("#symbol").select_option("XAUUSDzero")
    page.get_by_role("button", name="D1").click()
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '3'")
    assert page.locator("[data-period]").count() == 0

    page.locator("#data-mode").select_option("source")
    page.locator('[data-indicator="ema_12"]').wait_for()
    page.locator('[data-indicator="atr_15"]').wait_for()
    assert (
        page.locator('[data-indicator="ema_12"] [data-period="ema_period"]').input_value() == "12"
    )
    assert (
        page.locator('[data-indicator="atr_15"] [data-period="atr_period"]').input_value() == "15"
    )
    page.locator("#symbol").select_option("EURUSD")
    with page.expect_request(
        lambda request: (
            "/api/v1/bars?" in request.url
            and parse_qs(urlparse(request.url).query).get("ema_period") == ["12"]
            and parse_qs(urlparse(request.url).query).get("atr_period") == ["15"]
        )
    ):
        page.get_by_role("button", name="H1").click()
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '1000'")

    page.get_by_role("tab", name="Export").click()
    for checkbox in page.locator("#export-features input").all():
        checkbox.uncheck()
    page.locator('#export-features input[value="ema_12"]').check()
    page.locator('#export-features input[value="atr_15"]').check()
    with page.expect_download() as downloaded:
        page.get_by_role("button", name="Download CSV").click()
    with ZipFile(downloaded.value.path()) as archive:
        columns = archive.read("bar_features.csv").decode().splitlines()[0].split(",")
        metadata = json.loads(archive.read("bar_features.csv.json"))
    assert set(columns[-2:]) == {"ema_12", "atr_15"}
    assert {feature["name"]: feature["parameters"] for feature in metadata["features"]} == {
        "ema_12": {"period": 12},
        "atr_15": {"period": 15},
    }


def test_stale_stored_catalog_response_cannot_replace_calculated_mode(
    page: Page, viewer_url: str
) -> None:
    page.add_init_script(
        """const originalFetch = window.fetch;
        window.__storedCatalogResponses = [];
        window.fetch = (input, init) => {
            if (!String(input).includes('/api/v1/catalog?mode=stored'))
                return originalFetch(input, init);
            return new Promise((resolve) => window.__storedCatalogResponses.push((payload) => {
                resolve(new Response(JSON.stringify(payload), {
                    headers: {'Content-Type': 'application/json'}
                }));
            }));
        };"""
    )
    page.goto(viewer_url)
    page.wait_for_function("document.querySelector('#symbol option[value=EURUSD]') !== null")
    page.locator("#data-mode").select_option("stored")
    page.wait_for_function("window.__storedCatalogResponses.length === 1")
    page.locator("#data-mode").select_option("source")
    page.wait_for_function("!document.querySelector('#symbol').disabled")
    page.evaluate(
        """async payload => {
            window.__storedCatalogResponses[0](payload);
            await new Promise(resolve => setTimeout(resolve, 0));
        }""",
        {"symbols": [{"symbol": "STALE", "timeframes": ["D1"]}]},
    )
    assert page.locator("#data-mode").input_value() == "source"
    assert page.locator("#symbol option[value='EURUSD']").count() == 1
    assert page.locator("#symbol option[value='STALE']").count() == 0


def test_native_multi_file_import_groups_and_charts_selected_exports(
    page: Page, viewer_url: str, tmp_path: Path
) -> None:
    source = (Path(__file__).parent / "fixtures" / "stored_export.csv").read_text()
    metadata = json.loads(
        (Path(__file__).parent / "fixtures" / "stored_export.csv.json").read_text()
    )
    files: list[str] = []
    for asset, timeframe, name in (("XAUUSDzero", "M5", "gold"), ("BRENT", "H1", "oil")):
        suffix = "parquet" if name == "oil" else "csv"
        table = tmp_path / f"{name}.{suffix}"
        export_text = source.replace("XAUUSDzero", asset).replace(",D1,", f",{timeframe},")
        if suffix == "csv":
            table.write_text(export_text)
        else:
            frame = pd.read_csv(StringIO(export_text), float_precision="round_trip")
            for feature in ("ema_20", "atr_20", "research_score"):
                frame[feature] = frame[feature].astype("Float64")
            frame.to_parquet(table, index=False)
        sidecar = {
            **metadata,
            "asset": asset,
            "timeframe": timeframe,
            "dataset_id": f"{asset}/{timeframe}",
        }
        companion = tmp_path / f"{name}.{suffix}.json"
        companion.write_text(json.dumps(sidecar))
        files.extend((str(table), str(companion)))
    bad = tmp_path / "missing_sidecar.csv"
    bad.write_text(source)
    files.append(str(bad))
    page.goto(viewer_url)
    page.locator("#data-mode").select_option("stored")
    picker = page.locator("#stored-files")
    assert picker.get_attribute("type") == "file"
    assert picker.get_attribute("multiple") is not None
    assert picker.get_attribute("accept") == ".csv,.parquet,.json"
    picker.set_input_files(files)
    page.get_by_text("Import finished. Choose a symbol and timeframe below.").wait_for()
    results = page.locator("#stored-import-results").inner_text()
    assert "gold.csv: imported XAUUSDzero/M5" in results
    assert "oil.parquet: imported BRENT/H1" in results
    assert "missing_sidecar.csv: missing missing_sidecar.csv.json" in results
    page.wait_for_function("document.querySelector('#symbol option[value=BRENT]') !== null")
    page.locator("#symbol").select_option("XAUUSDzero")
    assert page.get_by_role("button", name="M5").is_visible()
    session = page.evaluate("sessionStorage.getItem('storedImportSession')")
    page.reload()
    assert page.evaluate("sessionStorage.getItem('storedImportSession')") == session
    page.locator("#data-mode").select_option("stored")
    page.locator("#symbol").select_option("XAUUSDzero")
    page.get_by_role("button", name="M5").click()
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '3'")
    assert "XAUUSDzero/M5" in page.locator("#dataset-version").inner_text()
    page.locator("#inspect-time").select_option("1306281600")
    assert "research_score (v7): 0" in page.locator("#inspect-values").inner_text()
    page.locator("#inspect-time").select_option("1306195200")
    assert "research_score (v7): missing" in page.locator("#inspect-values").inner_text()
    assert "ema_20 (v1): 1503.4861607719367" in page.locator("#inspect-values").inner_text()
    page.locator("#symbol").select_option("BRENT")
    assert page.get_by_role("button", name="H1").is_visible()
    assert page.get_by_role("button", name="M5").count() == 0
    page.get_by_role("button", name="H1").click()
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '3'")
    assert "BRENT/H1" in page.locator("#dataset-version").inner_text()
    assert (
        page.evaluate("window.__breakoutChart.panes()[0].getSeries()[0].data()[1].close") == 1525.7
    )
    page.locator("#inspect-time").select_option("1306281600")
    assert "research_score (v7): 0" in page.locator("#inspect-values").inner_text()
    assert "ema_20 (v1): 1505.6017645079428" in page.locator("#inspect-values").inner_text()


def test_overlapping_picker_change_cannot_replace_active_batch(page: Page, viewer_url: str) -> None:
    page.add_init_script(
        """const originalFetch = window.fetch;
        window.__pendingImports = [];
        window.fetch = (input, init) => {
            if (String(input).includes('/api/v1/stored/import'))
                return new Promise((resolve, reject) =>
                    window.__pendingImports.push(() =>
                        originalFetch(input, init).then(resolve, reject)));
            return originalFetch(input, init);
        };"""
    )
    page.goto(viewer_url)
    page.locator("#data-mode").select_option("stored")
    fixture = Path(__file__).parent / "fixtures"
    picker = page.locator("#stored-files")
    picker.set_input_files(
        [str(fixture / "stored_export.csv"), str(fixture / "stored_export.csv.json")]
    )
    page.wait_for_function("window.__pendingImports.length === 1")
    assert picker.is_disabled()
    page.evaluate(
        """() => {
            const input = document.querySelector('#stored-files');
            const transfer = new DataTransfer();
            transfer.items.add(new File(['bad'], 'second.csv', {type: 'text/csv'}));
            input.files = transfer.files;
            input.dispatchEvent(new Event('change', {bubbles: true}));
        }"""
    )
    assert page.evaluate("window.__pendingImports.length") == 1
    page.evaluate("window.__pendingImports[0]()")
    page.get_by_text("Import finished. Choose a symbol and timeframe below.").wait_for()
    results = page.locator("#stored-import-results").inner_text()
    assert "stored_export.csv: imported XAUUSDzero/D1" in results
    assert "second.csv" not in results
    assert picker.is_enabled()


def test_second_import_clears_previous_stored_selection(
    page: Page, viewer_url: str, tmp_path: Path
) -> None:
    fixture = Path(__file__).parent / "fixtures"
    source = (fixture / "stored_export.csv").read_text()
    metadata = json.loads((fixture / "stored_export.csv.json").read_text())
    next_table = tmp_path / "next.csv"
    next_table.write_text(source.replace(",D1,", ",M5,"))
    next_sidecar = tmp_path / "next.csv.json"
    next_sidecar.write_text(
        json.dumps({**metadata, "timeframe": "M5", "dataset_id": "XAUUSDzero/M5"})
    )
    page.goto(viewer_url)
    page.locator("#data-mode").select_option("stored")
    picker = page.locator("#stored-files")
    picker.set_input_files(
        [str(fixture / "stored_export.csv"), str(fixture / "stored_export.csv.json")]
    )
    page.get_by_text("Import finished. Choose a symbol and timeframe below.").wait_for()
    page.locator("#symbol").select_option("XAUUSDzero")
    page.get_by_role("button", name="D1").click()
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '3'")
    assert "XAUUSDzero/D1" in page.locator("#dataset-version").inner_text()
    picker.set_input_files([str(next_table), str(next_sidecar)])
    page.wait_for_function(
        "document.querySelector('#chart').dataset.barCount === '0' && "
        "document.querySelector('#symbol').value === ''"
    )
    assert page.locator("#chart").is_hidden()
    assert page.locator("#inspect-time option").count() == 0
    assert page.locator("#inspect-values").inner_text() == ""
    assert page.locator("#dataset-version").inner_text() == "Stored data"
    assert page.locator("#legend").inner_text() == "Select a symbol and timeframe."
    page.wait_for_function("!document.querySelector('#symbol').disabled")
    page.locator("#symbol").select_option("XAUUSDzero")
    assert page.get_by_role("button", name="M5").is_visible()


def test_missing_stored_response_metadata_has_actionable_error(page: Page, viewer_url: str) -> None:
    page.route(
        "**/api/v1/bars?*",
        lambda route: route.fulfill(json={"bars": [], "indicators": [], "has_more": False}),
    )
    page.goto(viewer_url)
    page.locator("#data-mode").select_option("stored")
    page.locator("#symbol").select_option("XAUUSDzero")
    page.get_by_role("button", name="D1").click()
    page.get_by_text(
        "Unable to load bars: Stored response is missing feature metadata; "
        "reload the export and sidecar."
    ).wait_for()


def select_timeframe(page: Page, viewer_url: str, timeframe: str = "H1") -> None:
    page.goto(viewer_url)
    page.locator("#symbol").select_option("EURUSD")
    page.get_by_role("button", name=timeframe).click()


def test_chart_renders_legend_resizes_and_prepends_history(page: Page, viewer_url: str) -> None:
    select_timeframe(page, viewer_url)
    page.locator("#chart canvas").first.wait_for()
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '1000'")
    canvases = page.locator("#chart canvas")
    assert canvases.count() >= 4
    assert page.evaluate(
        """() => [...document.querySelectorAll('#chart canvas')].some((canvas) => {
            const pixels = canvas.getContext('2d')
                .getImageData(0, 0, canvas.width, canvas.height).data;
            let candle = false; let volume = false;
            for (let index = 0; index < pixels.length; index += 4) {
                const y = Math.floor(index / 4 / canvas.width);
                candle ||= pixels[index] === 38 && pixels[index + 1] === 166
                    && pixels[index + 2] === 154 && y < canvas.height * 0.7;
                volume ||= pixels[index] === 7 && pixels[index + 1] === 134
                    && pixels[index + 2] === 92 && y > canvas.height * 0.7;
            }
            return candle && volume;
        })"""
    )
    chart_bounds = page.locator("#chart").bounding_box()
    assert chart_bounds is not None
    page.mouse.move(
        chart_bounds["x"] + chart_bounds["width"] / 2,
        chart_bounds["y"] + chart_bounds["height"] / 4,
    )
    page.wait_for_function("document.querySelector('#legend').textContent.includes(' O ')")
    before_width = page.locator("#chart").bounding_box()["width"]
    before_canvas_width = canvases.first.get_attribute("width")
    page.set_viewport_size({"width": 900, "height": 700})
    assert page.locator("#chart").bounding_box()["width"] < before_width
    page.wait_for_function(
        "width => document.querySelector('#chart canvas').getAttribute('width') !== width",
        arg=before_canvas_width,
    )
    page.evaluate("window.__breakoutChart.timeScale().setVisibleLogicalRange({from: 20, to: 70})")
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '1200'")
    chart_range = page.evaluate("window.__breakoutChart.timeScale().getVisibleLogicalRange()")
    assert chart_range["from"] == pytest.approx(220)
    assert chart_range["to"] == pytest.approx(270)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"bars": [], "next_before": None, "has_more": False}, "No bars in this source."),
        (None, "Unable to load bars: unavailable"),
    ],
)
def test_selected_timeframe_empty_and_error_replace_chart(
    page: Page, viewer_url: str, payload: dict[str, object] | None, expected: str
) -> None:
    def respond(route: object) -> None:
        if payload is None:
            route.fulfill(
                status=503, content_type="application/json", body='{"detail":"unavailable"}'
            )
        else:
            route.fulfill(content_type="application/json", json=payload)

    page.route("**/api/v1/bars**", respond)
    select_timeframe(page, viewer_url)
    page.get_by_text(expected, exact=True).wait_for()
    assert page.locator("#chart").is_hidden()


def test_stale_selection_response_does_not_replace_new_chart(page: Page, viewer_url: str) -> None:
    page.add_init_script(
        """const originalFetch = window.fetch;
        window.__barResponses = [];
        window.fetch = (input, init) => {
            if (!String(input).includes('/api/v1/bars')) return originalFetch(input, init);
            return new Promise((resolve) => window.__barResponses.push((payload) => {
                resolve(new Response(JSON.stringify(payload), {
                    headers: {'Content-Type': 'application/json'}
                }));
            }));
        };"""
    )
    page.goto(viewer_url)
    page.locator("#symbol").select_option("EURUSD")
    page.get_by_role("button", name="H1").click()
    page.wait_for_function("window.__barResponses.length === 1")
    page.get_by_text("Loading chart…", exact=True).wait_for()
    assert page.locator("#chart").is_hidden()
    page.get_by_role("button", name="H1").click()
    assert page.evaluate("window.__barResponses.length") == 1
    page.get_by_role("button", name="M15").click()
    page.wait_for_function("window.__barResponses.length === 2")
    page.evaluate(
        "payload => window.__barResponses[1](payload)",
        {
            "bars": [
                {
                    "time": 2,
                    "open": 1,
                    "high": 3,
                    "low": 0,
                    "close": 2,
                    "volume": 5,
                }
            ],
            "next_before": None,
            "has_more": False,
        },
    )
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '1'")
    page.evaluate(
        "payload => window.__barResponses[0](payload)",
        {
            "bars": [
                {
                    "time": 1,
                    "open": 1,
                    "high": 3,
                    "low": 0,
                    "close": 2,
                    "volume": 5,
                },
                {
                    "time": 2,
                    "open": 1,
                    "high": 3,
                    "low": 0,
                    "close": 2,
                    "volume": 5,
                },
            ],
            "next_before": None,
            "has_more": False,
        },
    )
    assert page.locator("#chart").get_attribute("data-bar-count") == "1"
    assert page.get_by_role("button", name="M15").get_attribute("aria-pressed") == "true"


def test_pagination_error_keeps_the_rendered_chart(page: Page, viewer_url: str) -> None:
    bars = [
        {
            "time": 1_735_689_600 + index * 60,
            "open": 1,
            "high": 3,
            "low": 0,
            "close": 2,
            "volume": 5,
        }
        for index in range(120)
    ]
    requests = 0

    def respond(route: object) -> None:
        nonlocal requests
        requests += 1
        if requests == 1:
            route.fulfill(json={"bars": bars, "next_before": bars[0]["time"], "has_more": True})
        else:
            route.fulfill(
                status=503,
                content_type="application/json",
                body='{"detail":"later unavailable"}',
            )

    page.route("**/api/v1/bars**", respond)
    select_timeframe(page, viewer_url)
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '120'")
    page.evaluate("window.__breakoutChart.timeScale().setVisibleLogicalRange({from: 0, to: 20})")
    page.get_by_text("Unable to load bars: later unavailable", exact=True).wait_for()
    assert not page.locator("#chart").is_hidden()
    assert page.locator("#chart").get_attribute("data-bar-count") == "120"


def test_indicators_tab_applies_hides_and_places_series(page: Page, viewer_url: str) -> None:
    select_timeframe(page, viewer_url)
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '1000'")
    page.get_by_role("tab", name="Indicators").click()
    panel = page.get_by_role("tabpanel", name="Indicators")
    assert panel.is_visible()

    volume = panel.locator('[data-indicator="volume"]')
    assert volume.locator("[data-indicator-state]").text_content() == "Applied · Visible"
    volume.get_by_role("button", name="Hide").click()
    assert volume.locator("[data-indicator-state]").text_content() == "Applied · Hidden"
    volume.locator("[data-apply]").uncheck()
    assert volume.locator("[data-indicator-state]").text_content() == "Not applied"
    point_counts = json.loads(
        page.locator("#chart").get_attribute("data-indicator-point-counts") or "{}"
    )
    assert point_counts["volume"] == 0

    ema = panel.locator('[data-indicator="ema_20"]')
    ema.locator("[data-apply]").check()
    assert ema.locator("[data-indicator-state]").text_content() == "Applied · Visible"
    page.wait_for_function(
        "JSON.parse(document.querySelector('#chart').dataset.indicatorPointCounts).ema_20 === 1000"
    )
    point_counts = json.loads(
        page.locator("#chart").get_attribute("data-indicator-point-counts") or "{}"
    )
    assert point_counts["ema_20"] == 1000

    rsi = panel.locator('[data-indicator="rsi_14"]')
    rsi.locator("[data-apply]").check()
    assert rsi.locator("[data-indicator-state]").text_content() == "Applied · Visible"
    page.wait_for_function(
        "JSON.parse(document.querySelector('#chart').dataset.indicatorPointCounts).rsi_14 === 1000"
    )
    assert page.evaluate("window.__breakoutChart.panes().length") == 2
    page.wait_for_function("window.__breakoutChart.panes()[1].getHeight() > 0")
    assert page.evaluate("window.__breakoutChart.panes()[1].getHeight()") == 160
    point_counts = json.loads(
        page.locator("#chart").get_attribute("data-indicator-point-counts") or "{}"
    )
    assert point_counts["rsi_14"] == 1000
    page.wait_for_function(
        """() => [[255, 152, 0], [171, 71, 188]].every((color) =>
            [...document.querySelectorAll('#chart canvas')].some((canvas) => {
                const pixels = canvas.getContext('2d').getImageData(
                    0, 0, canvas.width, canvas.height).data;
                for (let index = 0; index < pixels.length; index += 4) {
                    if (pixels[index] === color[0] && pixels[index + 1] === color[1]
                        && pixels[index + 2] === color[2]) return true;
                }
                return false;
            }))"""
    )
    rsi.locator("[data-apply]").uncheck()
    assert page.evaluate("window.__breakoutChart.panes().length") == 1


def test_export_tab_preselects_features_and_downloads_csv(page: Page, viewer_url: str) -> None:
    select_timeframe(page, viewer_url)
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '1000'")
    page.get_by_role("tab", name="Export").click()
    panel = page.get_by_role("tabpanel", name="Export")
    assert panel.is_visible()
    checkboxes = panel.locator("input[type=checkbox]")
    assert checkboxes.count() > 0
    assert checkboxes.evaluate_all("inputs => inputs.every(input => input.checked)")
    with page.expect_download() as downloaded:
        panel.get_by_role("button", name="Download CSV").click()
    assert downloaded.value.suggested_filename == "bar_features_csv.zip"


def test_feature_labels_and_engulfing_candle_color_toggle(page: Page, viewer_url: str) -> None:
    select_timeframe(page, viewer_url, "M15")
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '3'")
    page.get_by_role("tab", name="Indicators").click()
    panel = page.get_by_role("tabpanel", name="Indicators")
    assert panel.locator('[data-indicator="ema_20"] small').text_content().startswith("line ·")
    assert panel.locator('[data-indicator="rsi_14"] small').text_content().startswith("pane ·")
    assert (
        panel.locator('[data-indicator="candle_range"] small')
        .text_content()
        .startswith("crosshair ·")
    )
    engulfing = panel.locator('[data-indicator="is_engulfing"]')
    assert engulfing.locator("small").text_content().startswith("candle color ·")
    engulfing.locator("[data-apply]").check()
    page.wait_for_function("document.querySelector('#chart').dataset.candleColorCount === '1'")
    page.wait_for_function(
        """() => [...document.querySelectorAll('#chart canvas')].some((canvas) => {
            const pixels = canvas.getContext('2d')
                .getImageData(0, 0, canvas.width, canvas.height).data;
            for (let index = 0; index < pixels.length; index += 4)
                if (pixels[index] === 255 && pixels[index + 1] === 214
                    && pixels[index + 2] === 0) return true;
            return false;
        })"""
    )
    engulfing.locator("[data-apply]").uncheck()
    assert page.locator("#chart").get_attribute("data-candle-color-count") == "0"

    candle_range = panel.locator('[data-indicator="candle_range"]')
    main_series_count = page.evaluate("window.__breakoutChart.panes()[0].getSeries().length")
    candle_range.locator("[data-apply]").check()
    page.wait_for_function(
        """() => window.__breakoutChart.panes()[0].getSeries().length === 2
            && document.querySelector('[data-indicator="candle_range"] [data-indicator-state]')
                .textContent.includes('Applied')"""
    )
    assert main_series_count == 2
    page.evaluate(
        """() => window.__breakoutChart.setCrosshairPosition(
            3.5, 1735690500, window.__breakoutChart.panes()[0].getSeries()[0])"""
    )
    page.wait_for_function("document.querySelector('#legend').textContent.includes('Range 3')")
    candle_range.locator("[data-apply]").uncheck()
    assert "Range" not in page.locator("#legend").text_content()


def test_server_metadata_adds_an_unknown_indicator_without_frontend_changes(
    page: Page, viewer_url: str
) -> None:
    console_errors: list[str] = []
    page.on(
        "console",
        lambda message: console_errors.append(message.text) if message.type == "error" else None,
    )
    bars = [
        {
            "time": 1_735_689_600 + index * 900,
            "open": 1,
            "high": 3,
            "low": 0,
            "close": 2,
            "volume": 5,
        }
        for index in range(3)
    ]
    definition = {
        "id": "custom_signal",
        "label": "Custom Signal",
        "description": "Server-defined test pane",
        "series_type": "LineSeries",
        "pane": "separate",
        "default_applied": False,
        "default_visible": True,
        "series_options": {"color": "#00ffff", "lineWidth": 2},
        "price_scale_options": {},
        "pane_height": 120,
        "scale_range": [0, 10],
        "reference_lines": [],
        "points": [{"time": bar["time"], "value": index + 2} for index, bar in enumerate(bars)],
    }
    invalid_definition = {
        **definition,
        "id": "broken_signal",
        "label": "Broken Signal",
        "series_type": "MissingSeries",
        "default_applied": True,
    }
    page.route(
        "**/api/v1/bars**",
        lambda route: route.fulfill(
            json={
                "bars": bars,
                "indicators": [invalid_definition, definition],
                "next_before": None,
                "has_more": False,
            }
        ),
    )
    select_timeframe(page, viewer_url, "M15")
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '3'")
    page.get_by_role("tab", name="Indicators").click()
    panel = page.get_by_role("tabpanel", name="Indicators")
    assert (
        panel.locator('[data-indicator="broken_signal"] [data-indicator-state]').text_content()
        == "Not applied"
    )
    assert any("Unable to create indicator broken_signal" in message for message in console_errors)
    custom = panel.locator('[data-indicator="custom_signal"]')
    assert custom.get_by_text("Server-defined test pane").is_visible()
    custom.locator("[data-apply]").check()
    page.wait_for_function("window.__breakoutChart.panes()[1]?.getHeight() > 0")
    assert page.evaluate("window.__breakoutChart.panes()[1].getHeight()") == 120
    page.wait_for_function(
        """() => [...document.querySelectorAll('#chart canvas')].some((canvas) => {
            const pixels = canvas.getContext('2d').getImageData(
                0, 0, canvas.width, canvas.height).data;
            for (let index = 0; index < pixels.length; index += 4)
                if (pixels[index] === 0 && pixels[index + 1] === 255
                    && pixels[index + 2] === 255) return true;
            return false;
        })"""
    )


def test_marker_renderer_draws_markers_without_adding_a_line_series(
    page: Page, viewer_url: str
) -> None:
    times = [1_735_689_600 + index * 900 for index in range(3)]
    bars = [
        {"time": time, "open": 1, "high": 3, "low": 0, "close": 2, "volume": 5} for time in times
    ]
    marker = {
        "id": "entry_marker",
        "feature_name": "entry_price",
        "label": "Entry",
        "description": "Entry marker",
        "renderer": "marker",
        "series_type": None,
        "pane": "main",
        "show_in_crosshair": True,
        "default_applied": True,
        "default_visible": True,
        "series_options": {
            "color": "#00ffff",
            "shape": "square",
            "position": "atPriceMiddle",
            "size": 2,
        },
        "price_scale_options": {},
        "pane_height": None,
        "scale_range": None,
        "reference_lines": [],
        "points": [{"time": time, "value": 2} for time in times],
        "values": [{"time": time, "value": 2} for time in times],
    }

    def respond(route: object) -> None:
        query = parse_qs(urlparse(route.request.url).query)  # type: ignore[attr-defined]
        indicators = [
            indicator
            for indicator in route.fetch().json()["indicators"]  # type: ignore[attr-defined]
            if indicator["id"] in {"atr_20", "atr_10"}
        ]
        if query.get("atr_period") != ["10"]:
            indicators.append(marker)
        route.fulfill(  # type: ignore[attr-defined]
            json={
                "bars": bars,
                "indicators": indicators,
                "next_before": None,
                "has_more": False,
            }
        )

    page.route("**/api/v1/bars**", respond)
    select_timeframe(page, viewer_url, "M15")
    page.wait_for_function("document.querySelector('#chart').dataset.markerCount === '3'")
    page.locator("#chart canvas").first.wait_for()
    assert page.evaluate("window.__breakoutChart.panes()[0].getSeries().length") == 1
    page.wait_for_function(
        """() => [...document.querySelectorAll('#chart canvas')].some((canvas) => {
            if (!canvas.width || !canvas.height) return false;
            const pixels = canvas.getContext('2d').getImageData(
                0, 0, canvas.width, canvas.height).data;
            for (let index = 0; index < pixels.length; index += 4)
                if (pixels[index] === 0 && pixels[index + 1] === 255
                    && pixels[index + 2] === 255) return true;
            return false;
        })"""
    )
    page.get_by_role("tab", name="Indicators").click()
    period = page.locator('[data-period="atr_period"]').first
    period.fill("10")
    period.press("Tab")
    page.wait_for_function("document.querySelector('#chart').dataset.markerCount === '0'")
    assert not page.evaluate(
        """() => [...document.querySelectorAll('#chart canvas')].some((canvas) => {
            if (!canvas.width || !canvas.height) return false;
            const pixels = canvas.getContext('2d').getImageData(
                0, 0, canvas.width, canvas.height).data;
            for (let index = 0; index < pixels.length; index += 4)
                if (pixels[index] === 0 && pixels[index + 1] === 255
                    && pixels[index + 2] === 255) return true;
            return false;
        })"""
    )


def test_period_change_keeps_numbered_custom_export_columns_distinct(
    page: Page, viewer_url: str
) -> None:
    def add_custom_features(route: object) -> None:
        response = route.fetch()  # type: ignore[attr-defined]
        payload = response.json()
        payload["export_features"].extend(
            [
                {"name": "score_1"},
                {"name": "score_2"},
                {"name": "atr_10_custom"},
                {"name": "atr_20_custom"},
            ]
        )
        route.fulfill(json=payload)  # type: ignore[attr-defined]

    page.route("**/api/v1/features**", add_custom_features)
    page.goto(viewer_url)
    page.get_by_role("tab", name="Export").click()
    page.locator('#export-features input[value="score_1"]').wait_for()
    for checkbox in page.locator("#export-features input").all():
        checkbox.uncheck()
    page.locator('#export-features input[value="score_1"]').check()
    page.locator('#export-features input[value="atr_10_custom"]').check()
    page.get_by_role("tab", name="Indicators").click()
    period = page.locator('[data-indicator="atr_20"] [data-period="atr_period"]')
    period.fill("10")
    period.press("Tab")
    page.locator('#export-features input[value="atr_10"]').wait_for(state="attached")
    assert page.locator("#export-features input:checked").evaluate_all(
        "inputs => inputs.map(input => input.value)"
    ) == ["score_1", "atr_10_custom"]


def test_rapid_period_changes_ignore_stale_catalog_completions(page: Page, viewer_url: str) -> None:
    select_timeframe(page, viewer_url)
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '1000'")
    page.get_by_role("tab", name="Indicators").click()
    atr = page.locator('[data-indicator="atr_20"]')
    if not atr.locator("[data-apply]").is_checked():
        atr.locator("[data-apply]").check()
    if atr.locator("[data-visibility]").inner_text() == "Hide":
        atr.locator("[data-visibility]").click()
    page.wait_for_load_state("networkidle")

    catalog_routes: list[object] = []
    bar_requests: list[str] = []
    page.on(
        "request",
        lambda request: (
            bar_requests.append(request.url) if "/api/v1/bars?" in request.url else None
        ),
    )

    def hold_catalog(route: object) -> None:
        catalog_routes.append(route)

    page.route("**/api/v1/catalog**", hold_catalog)
    baseline_bar_requests = len(bar_requests)
    period = page.locator('[data-period="atr_period"]').first
    with page.expect_request("**/api/v1/catalog**"):
        period.fill("10")
        period.press("Tab")
    page.wait_for_timeout(20)
    assert len(catalog_routes) == 1
    with page.expect_request("**/api/v1/catalog**"):
        period.fill("11")
        period.press("Tab")
    page.wait_for_timeout(20)
    assert len(catalog_routes) == 2

    catalog_routes[0].fulfill(  # type: ignore[attr-defined]
        json={"symbols": [{"symbol": "STALE", "timeframes": ["D1"]}]}
    )
    page.wait_for_timeout(50)
    assert len(bar_requests) == baseline_bar_requests
    catalog_routes[1].fulfill(  # type: ignore[attr-defined]
        json={
            "symbols": [
                {"symbol": "EURUSD", "timeframes": ["H1"]},
                {"symbol": "LATEST", "timeframes": ["D1"]},
            ]
        }
    )
    page.wait_for_function("document.querySelector('#symbol option[value=LATEST]') !== null")
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '1000'")
    assert page.locator('[data-indicator="atr_11"] [data-apply]').is_checked()
    assert (
        page.locator('[data-indicator="atr_11"] [data-indicator-state]').inner_text()
        == "Applied · Hidden"
    )


def test_period_setting_updates_chart_and_export_feature_names(page: Page, viewer_url: str) -> None:
    page.goto(viewer_url)
    page.get_by_role("tab", name="Indicators").click()
    period = page.locator('[data-indicator="atr_20"] [data-period="atr_period"]')
    period.fill("10")
    period.press("Tab")
    page.wait_for_function("document.querySelector('[data-indicator=atr_10]')")
    page.locator("#symbol").select_option("EURUSD")
    page.get_by_role("button", name="H1").click()
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '1000'")
    assert page.locator('[data-indicator="atr_10"]').count() == 1
    assert page.locator('[data-indicator="atr_20"]').count() == 0
    page.locator('[data-indicator="atr_10"] [data-apply]').check()
    assert (
        page.locator('[data-indicator="atr_10"] [data-indicator-state]').inner_text()
        == "Applied · Visible"
    )
    page.locator('[data-indicator="atr_10"] [data-visibility]').click()
    assert (
        page.locator('[data-indicator="atr_10"] [data-indicator-state]').inner_text()
        == "Applied · Hidden"
    )
    page.get_by_role("tab", name="Export").click()
    export_names = page.locator("#export-features input").evaluate_all(
        "inputs => inputs.map(input => input.value)"
    )
    assert "atr_10" in export_names
    assert "atr_20" not in export_names
    for checkbox in page.locator("#export-features input").all():
        checkbox.uncheck()
    page.locator('#export-features input[value="atr_10"]').check()
    page.get_by_role("tab", name="Indicators").click()
    next_period = page.locator('[data-indicator="atr_10"] [data-period="atr_period"]')
    next_period.fill("11")
    next_period.press("Tab")
    page.wait_for_function("document.querySelector('#export-features input[value=atr_11]')")
    assert page.locator('[data-indicator="atr_11"] [data-apply]').is_checked()
    assert (
        page.locator('[data-indicator="atr_11"] [data-indicator-state]').inner_text()
        == "Applied · Hidden"
    )
    assert page.locator('[data-indicator="atr_11"] [data-visibility]').inner_text() == "Show"
    assert page.locator("#export-features input:checked").evaluate_all(
        "inputs => inputs.map(input => input.value)"
    ) == ["atr_11"]
    export_requests: list[dict[str, list[str]]] = []

    def capture_export(route: object) -> None:
        request = route.request  # type: ignore[attr-defined]
        export_requests.append(parse_qs(urlparse(request.url).query))
        route.fulfill(  # type: ignore[attr-defined]
            status=200,
            body=b"fake archive",
            headers={
                "Content-Type": "application/zip",
                "Content-Disposition": 'attachment; filename="bar_features_csv.zip"',
            },
        )

    page.route("**/api/v1/export**", capture_export)
    page.get_by_role("tab", name="Export").click()
    with page.expect_download():
        page.get_by_role("button", name="Download CSV").click()
    assert len(export_requests) == 1
    assert export_requests[0]["features"] == ["atr_11"]
    assert export_requests[0]["atr_period"] == ["11"]
    assert export_requests[0]["rsi_period"] == ["14"]
    assert export_requests[0]["macd_fast_period"] == ["12"]


def test_new_feature_setting_renders_and_reaches_chart_and_export(
    page: Page, viewer_url: str
) -> None:
    def add_setting(route: object) -> None:
        payload = route.fetch().json()  # type: ignore[attr-defined]
        payload["indicators"][0]["settings"].append(
            {
                "key": "custom_period",
                "label": "Custom period",
                "default": 3,
                "minimum": 2,
                "maximum": 10,
            }
        )
        route.fulfill(json=payload)  # type: ignore[attr-defined]

    page.route("**/api/v1/features**", add_setting)
    page.route("**/api/v1/bars**", add_setting)
    select_timeframe(page, viewer_url)
    page.get_by_role("tab", name="Indicators").click()
    custom = page.locator('[data-period="custom_period"]')
    assert custom.input_value() == "3"
    with page.expect_request(
        lambda request: (
            "/api/v1/bars?" in request.url
            and parse_qs(urlparse(request.url).query).get("custom_period") == ["7"]
        )
    ):
        custom.fill("7")
        custom.press("Tab")

    export_requests: list[dict[str, list[str]]] = []

    def capture_export(route: object) -> None:
        export_requests.append(parse_qs(urlparse(route.request.url).query))  # type: ignore[attr-defined]
        route.fulfill(  # type: ignore[attr-defined]
            status=200,
            body=b"fake archive",
            headers={
                "Content-Type": "application/zip",
                "Content-Disposition": 'attachment; filename="bar_features_csv.zip"',
            },
        )

    page.route("**/api/v1/export**", capture_export)
    page.get_by_role("tab", name="Export").click()
    with page.expect_download():
        page.get_by_role("button", name="Download CSV").click()
    assert export_requests[0]["custom_period"] == ["7"]
