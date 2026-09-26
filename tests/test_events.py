from __future__ import annotations

import io
import random
import zipfile
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.bars import Bar
from app.event_detectors import EventConfig, detect_events
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


def test_swing_ignores_gap_and_wick_and_each_setup_fires_once() -> None:
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
    assert [(event["setup_id"], event["signal_time"]) for event in repeated] == [("high:3", 6)]


def test_indexed_swing_crossings_match_close_rule_on_varied_prices() -> None:
    generator = random.Random(19)
    bars = bars_from_closes([float(generator.randrange(8, 32)) for _ in range(600)])
    config = EventConfig(swing_left=2, swing_right=2, buffer=0.5)
    actual, swings = detect_events(bars, config, ["swing_breakout"])
    expected: list[tuple[str, int]] = []
    fired: set[str] = set()
    for previous, current in zip(bars, bars[1:]):
        for swing in swings:
            setup = f"{swing.direction}:{swing.pivot_time}"
            if setup in fired or swing.availability_time >= current.time:
                continue
            crossed = (
                previous.close < swing.level - config.buffer
                and current.close > swing.level + config.buffer
                if swing.direction == "high"
                else previous.close > swing.level + config.buffer
                and current.close < swing.level - config.buffer
            )
            if crossed:
                expected.append((setup, current.time))
                fired.add(setup)
    assert sorted(
        (str(event["setup_id"]), int(event["signal_time"])) for event in actual
    ) == sorted(expected)


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
