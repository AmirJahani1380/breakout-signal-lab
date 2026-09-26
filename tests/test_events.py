from __future__ import annotations

import io
import random
import zipfile
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app.event_detectors as event_detectors
from app.bars import Bar
from app.event_detectors import EventConfig, EventDetector, detect_events, make_event
from app.features.confirmed_swing import confirmed_swings
from app.main import Settings, create_app


def bars_from_closes(closes: list[float]) -> tuple[Bar, ...]:
    return tuple(
        Bar(index + 1, close, close + 1, close - 1, close, 1) for index, close in enumerate(closes)
    )


def test_ema_close_crossings_and_equal_boundary() -> None:
    bars = bars_from_closes([10, 12, 8, 8, 11])
    events, _ = detect_events(bars, EventConfig(ema_period=2), ["ema_breakout"])
    assert [(event["direction"], event["signal_time"]) for event in events] == [
        ("bearish", 3),
        ("bullish", 5),
    ]
    assert events[0]["broken_level"] == pytest.approx(9.11111111111111)
    equal, _ = detect_events(
        bars_from_closes([10, 10, 11]), EventConfig(ema_period=2), ["ema_breakout"]
    )
    assert equal == ()


def test_ema_ignores_gap_and_wick_then_rearms_on_reversal() -> None:
    initial = bars_from_closes([10, 12])
    gap_and_wick = Bar(3, 8, 13, 7, 12, 1)
    reversal = Bar(4, 8, 9, 7, 8, 1)
    events, _ = detect_events(
        (*initial, gap_and_wick, reversal, Bar(5, 14, 15, 13, 14, 1)),
        EventConfig(ema_period=2),
        ["ema_breakout"],
    )
    assert [(event["direction"], event["signal_time"]) for event in events] == [
        ("bearish", 4),
        ("bullish", 5),
    ]
    repeated, _ = detect_events(
        bars_from_closes([10, 12, 8, 12, 8]),
        EventConfig(ema_period=2),
        ["ema_breakout"],
    )
    assert [(event["direction"], event["signal_time"]) for event in repeated] == [
        ("bearish", 3),
        ("bullish", 4),
        ("bearish", 5),
    ]


def test_swing_confirmation_ties_and_strict_next_bar_eligibility() -> None:
    bars = bars_from_closes([8, 9, 12, 9, 8, 14, 14])
    swings = confirmed_swings(bars, 2, 2)
    assert ("high", 3, 5, 13) in [
        (swing.direction, swing.pivot_time, swing.availability_time, swing.level)
        for swing in swings
    ]
    events, _ = detect_events(bars, EventConfig(swing_left=2, swing_right=2), ["swing_breakout"])
    assert len(events) == 1
    assert (
        events[0]["signal_time"],
        events[0]["availability_time"],
        events[0]["broken_level"],
    ) == (6, 5, 13)
    assert confirmed_swings(bars_from_closes([8, 9, 12, 12, 8]), 2, 2) == ()
    assert (
        detect_events(bars[:5], EventConfig(swing_left=2, swing_right=2), ["swing_breakout"])[0]
        == ()
    )
    bearish, _ = detect_events(
        bars_from_closes([14, 13, 10, 13, 14, 8, 8]),
        EventConfig(swing_left=2, swing_right=2),
        ["swing_breakout"],
    )
    assert [
        (event["direction"], event["signal_time"], event["broken_level"]) for event in bearish
    ] == [("bearish", 6, 9)]
    buffered, _ = detect_events(
        bars, EventConfig(swing_left=2, swing_right=2, buffer=1), ["swing_breakout"]
    )
    assert buffered == ()


def test_swing_ignores_gap_and_wick_and_rearms_after_return_inside_channel() -> None:
    first_five = bars_from_closes([8, 9, 12, 9, 8])
    for opening in (8, 14):
        crossing_attempt = Bar(6, opening, 15, 7, 12, 1)
        crossing_close = Bar(7, 14, 15, 13, 14, 1)
        events, _ = detect_events(
            (*first_five, crossing_attempt, crossing_close),
            EventConfig(swing_left=2, swing_right=2),
            ["swing_breakout"],
        )
        assert [(event["setup_id"], event["signal_time"]) for event in events] == [("high:3", 7)]
    repeated, _ = detect_events(
        bars_from_closes([8, 9, 12, 9, 8, 14, 8, 14]),
        EventConfig(swing_left=2, swing_right=2),
        ["swing_breakout"],
    )
    assert [(event["setup_id"], event["signal_time"]) for event in repeated] == [
        ("high:3", 6),
        ("high:3", 8),
    ]


def test_indexed_swing_crossings_match_close_rule_on_varied_prices() -> None:
    generator = random.Random(19)
    bars = bars_from_closes([float(generator.randrange(8, 32)) for _ in range(600)])
    config = EventConfig(swing_left=2, swing_right=2, buffer=0.5)
    actual, swings = detect_events(bars, config, ["swing_breakout"])
    expected: list[tuple[str, int]] = []
    upper = None
    lower = None
    next_swing = 0
    for previous, current in zip(bars, bars[1:]):
        while next_swing < len(swings) and swings[next_swing].availability_time <= previous.time:
            swing = swings[next_swing]
            if swing.direction == "high" and (upper is None or swing.level > upper.level):
                upper = swing
            elif swing.direction == "low" and (lower is None or swing.level < lower.level):
                lower = swing
            next_swing += 1
        if upper is not None and (
            previous.close < upper.level - config.buffer
            and current.close > upper.level + config.buffer
        ):
            expected.append((f"high:{upper.pivot_time}", current.time))
        if lower is not None and (
            previous.close > lower.level + config.buffer
            and current.close < lower.level - config.buffer
        ):
            expected.append((f"low:{lower.pivot_time}", current.time))
    assert sorted(
        (str(event["setup_id"]), int(event["signal_time"])) for event in actual
    ) == sorted(expected)


def test_swing_channel_uses_only_the_previous_confirmed_edges() -> None:
    bars = bars_from_closes([8, 9, 12, 9, 8, 14, 15, 16])
    events, swings = detect_events(
        bars, EventConfig(swing_left=2, swing_right=2), ["swing_breakout"]
    )
    high = next(swing for swing in swings if swing.direction == "high")
    assert high.availability_time == bars[4].time
    assert [(event["direction"], event["signal_time"]) for event in events] == [
        ("bullish", bars[5].time)
    ]


def test_drop_in_detector_is_discovered_and_exposed_without_other_code_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module_path = tmp_path / "custom_rising.py"
    module_path.write_text(
        """from app.bars import Bar
from app.event_detectors import Event, EventConfig, EventDetector, make_event
from collections.abc import Sequence

def detect(bars: Sequence[Bar], config: EventConfig, dataset_id: str) -> tuple[Event, ...]:
    if len(bars) < 2 or bars[-1].close <= bars[-2].close:
        return ()
    return (make_event("custom_rising", "bullish", bars[-1], bars[-2].close,
                       "last_close", bars[-1].time, {}, dataset_id),)

detector = EventDetector("custom_rising", "Custom rising close", detect)
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(event_detectors, "__path__", [*event_detectors.__path__, str(tmp_path)])
    root = tmp_path / "market"
    root.mkdir()
    (root / "RISING_H1.csv").write_text(
        "time,open,high,low,close,volume\n1,10,11,9,10,1\n2,11,12,10,11,1\n",
        encoding="utf-8",
    )

    with TestClient(create_app(Settings(root))) as client:
        catalog = client.get("/api/v1/features").json()["event_detectors"]
        response = client.get(
            "/api/v1/events",
            params={"symbol": "RISING", "timeframe": "H1", "detectors": "custom_rising"},
        )

    assert {detector["id"] for detector in catalog} >= {"custom_rising"}
    assert response.status_code == 200
    assert response.json()["events"][0]["detector"] == "custom_rising"


@pytest.mark.parametrize(
    ("invalid_field", "invalid_value", "expected_error"),
    [
        ("detector", "different", "different detector identifier"),
        ("setup_id", None, "missing fields"),
        ("broken_level", float("nan"), "broken_level must be a finite number"),
    ],
)
def test_invalid_drop_in_detector_output_has_actionable_api_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_field: str,
    invalid_value: object,
    expected_error: str,
) -> None:
    def invalid_detect(
        bars: Sequence[Bar], config: EventConfig, dataset_id: str
    ) -> tuple[dict[str, object], ...]:
        event = make_event(
            "broken", "bullish", bars[-1], bars[-2].close, "close", bars[-1].time, {}, dataset_id
        )
        if invalid_value is None:
            del event[invalid_field]
        else:
            event[invalid_field] = invalid_value
        return (event,)

    monkeypatch.setattr(
        "app.main.discover_detectors",
        lambda: {"broken": EventDetector("broken", "Broken detector", invalid_detect)},
    )
    root = tmp_path / "market"
    root.mkdir()
    (root / "EURUSD_H1.csv").write_text(
        "time,open,high,low,close,volume\n1,10,11,9,10,1\n2,11,12,10,11,1\n",
        encoding="utf-8",
    )
    with TestClient(create_app(Settings(root))) as client:
        response = client.get(
            "/api/v1/events",
            params={"symbol": "EURUSD", "timeframe": "H1", "detectors": "broken"},
        )
    assert response.status_code == 422
    assert "detector 'broken' event 0" in response.json()["detail"]
    assert expected_error in response.json()["detail"]


def test_swing_indicator_combines_both_directions_as_red_dots() -> None:
    from app.features.confirmed_swing import feature

    closes = [6, 8, 10, 12, 10, 8, 6, 4, 6, 8, 6]
    bars = bars_from_closes(closes)
    definition = feature.views[0].definition(feature.calculate(bars), {bar.time for bar in bars})

    assert len(feature.views) == 1
    assert definition["id"] == "confirmed_swing_3_3"
    assert [point["time"] for point in definition["points"]] == [4, 8]
    assert definition["series_options"] == {
        "position": "atPriceMiddle",
        "shape": "circle",
        "color": "#ef5350",
        "size": 1,
    }
    assert all("text" not in point for point in definition["points"])
    inverted = bars_from_closes([20 - close for close in closes])
    inverted_view = feature.views[0].definition(
        feature.calculate(inverted), {bar.time for bar in inverted}
    )
    assert [point["time"] for point in inverted_view["points"]] == [4, 8]


def test_future_causality_and_configuration_validation() -> None:
    bars = bars_from_closes([8, 9, 12, 9, 8, 14, 14])
    config = EventConfig(ema_period=2, swing_left=2, swing_right=2)
    original, _ = detect_events(bars, config)
    changed, _ = detect_events((*bars, Bar(8, 100, 101, 99, 100, 1), Bar(9, 1, 2, 0, 1, 1)), config)
    assert [event for event in changed if event["signal_time"] <= bars[-1].time] == list(original)
    with pytest.raises(ValueError, match="buffer"):
        EventConfig(buffer=float("nan"))
    with pytest.raises(ValueError, match="swing left"):
        confirmed_swings(bars, 0, 2)


def test_api_paging_and_event_export_match(tmp_path: Path) -> None:
    root = tmp_path / "market"
    root.mkdir()
    source = root / "EURUSD_H1.csv"
    source.write_text(
        "time,open,high,low,close,volume\n"
        + "".join(
            f"{bar.time},{bar.open},{bar.high},{bar.low},{bar.close},{bar.volume}\n"
            for bar in bars_from_closes([8, 9, 12, 9, 8, 14, 14])
        ),
        encoding="utf-8",
    )
    with TestClient(create_app(Settings(root))) as client:
        all_events = client.get(
            "/api/v1/events",
            params={"symbol": "EURUSD", "timeframe": "H1", "swing_left": 2, "swing_right": 2},
        ).json()
        first = client.get(
            "/api/v1/events",
            params={
                "symbol": "EURUSD",
                "timeframe": "H1",
                "swing_left": 2,
                "swing_right": 2,
                "limit": 3,
            },
        ).json()
        second = client.get(
            "/api/v1/events",
            params={
                "symbol": "EURUSD",
                "timeframe": "H1",
                "swing_left": 2,
                "swing_right": 2,
                "limit": 4,
                "before": first["next_before"],
            },
        ).json()
        assert [event["id"] for event in second["events"] + first["events"]] == [
            event["id"] for event in all_events["events"]
        ]
        exported = client.get(
            "/api/v1/events/export",
            params={
                "symbol": "EURUSD",
                "timeframe": "H1",
                "swing_left": 2,
                "swing_right": 2,
                "features": "ema_20",
                "format": "csv",
            },
        )
        assert exported.status_code == 200
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            frame = pd.read_csv(archive.open("encountered_events.csv"))
        assert frame["id"].tolist() == [event["id"] for event in all_events["events"]]
        assert frame["broken_level"].tolist() == pytest.approx(
            [event["broken_level"] for event in all_events["events"]]
        )
        assert "ema_20" in frame


def test_empty_event_export_has_headers(tmp_path: Path) -> None:
    root = tmp_path / "market"
    root.mkdir()
    (root / "FLAT_H1.csv").write_text(
        "time,open,high,low,close,volume\n1,10,11,9,10,1\n2,10,11,9,10,1\n",
        encoding="utf-8",
    )
    with TestClient(create_app(Settings(root))) as client:
        response = client.get(
            "/api/v1/events/export",
            params={"symbol": "FLAT", "timeframe": "H1", "features": "", "format": "csv"},
        )
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        frame = pd.read_csv(archive.open("encountered_events.csv"))
    assert frame.empty
    assert {"id", "detector", "broken_level", "time", "close"} <= set(frame.columns)
