from __future__ import annotations

import io
import json
import zipfile

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.bars import Bar
from app.event_detectors import make_event
from app.labels import LabelConfig, label_events
from app.main import Settings, create_app


def bar(time: int, opening: float, high: float, low: float, close: float) -> Bar:
    return Bar(time, opening, high, low, close, 1)


def outcome(source: list[Bar], direction: str = "bullish", **settings: object) -> dict[str, object]:
    event = make_event("fixture", direction, source[0], None, "setup", 1, {}, "test")
    return label_events(source, [event], LabelConfig(atr_period=1, **settings))[0]  # type: ignore[arg-type]


def test_long_short_entry_bar_hits_and_costs() -> None:
    long = outcome(
        [bar(1, 10, 11, 9, 10), bar(2, 10, 15, 9.5, 14)],
        atr_buffer=0,
        horizon=1,
        slippage=0.1,
        commission=0.05,
    )
    assert long["label_status"] == "target_first"
    assert long["entry_fill"] == pytest.approx(10.1)
    assert long["stop_price"] == 9
    assert long["target_price"] == pytest.approx(12.3)
    assert long["gross_r"] == pytest.approx((12.3 - 0.1 - 10.1) / 1.1)
    assert long["net_r"] == pytest.approx((12.3 - 0.1 - 10.1 - 0.1) / 1.1)
    assert long["mfe_price"] == pytest.approx(4.9)
    assert long["horizon_complete"] is True

    short = outcome(
        [bar(1, 10, 11, 9, 10), bar(2, 10, 10.5, 7, 8)], "bearish", horizon=1, atr_buffer=0
    )
    assert short["label_status"] == "target_first"
    assert short["target_price"] == 8
    assert short["gross_r"] == 2


def test_opening_gaps_precede_intrabar_and_dual_hit_is_ambiguous() -> None:
    signal = bar(1, 10, 11, 9, 10)
    gap_target = outcome(
        [signal, bar(2, 10, 10.5, 9.5, 10), bar(3, 13, 14, 8, 12)], horizon=2, atr_buffer=0
    )
    assert (gap_target["label_status"], gap_target["exit_fill"]) == ("target_first", 13)
    gap_stop = outcome(
        [signal, bar(2, 10, 10.5, 9.5, 10), bar(3, 8, 15, 7, 12)], horizon=2, atr_buffer=0
    )
    assert (gap_stop["label_status"], gap_stop["exit_fill"]) == ("stop_first", 8)
    dual = outcome([signal, bar(2, 10, 13, 8, 10)], horizon=1, atr_buffer=0)
    assert (dual["label_status"], dual["exit_fill"], dual["gross_r"]) == ("ambiguous", 9, -1)
    short_gap = outcome(
        [signal, bar(2, 10, 10.5, 9.5, 10), bar(3, 7, 12, 6, 10)],
        "bearish",
        horizon=2,
        atr_buffer=0,
    )
    assert (short_gap["label_status"], short_gap["exit_fill"]) == ("target_first", 7)
    short_stop = outcome(
        [signal, bar(2, 10, 10.5, 9.5, 10), bar(3, 12, 13, 7, 10)],
        "bearish",
        horizon=2,
        atr_buffer=0,
    )
    assert (short_stop["label_status"], short_stop["exit_fill"]) == ("stop_first", 12)


def test_horizon_timeout_censoring_unfilled_invalid_risk_and_excursions() -> None:
    signal = bar(1, 10, 11, 9, 10)
    timeout = outcome([signal, bar(2, 10, 10.5, 9.5, 10.2)], horizon=1, atr_buffer=0)
    assert timeout["label_status"] == "timeout"
    assert timeout["exit_time"] == 2
    assert timeout["gross_r"] == pytest.approx(0.2)
    censored = outcome([signal, bar(2, 10, 10.5, 9.5, 10.2)], horizon=2, atr_buffer=0)
    assert censored["label_status"] == "censored"
    assert censored["net_r"] is None
    assert censored["horizon_complete"] is False
    assert censored["mae_r"] == pytest.approx(0.5)
    assert censored["observation_end_time"] == 2
    early_exit = outcome([signal, bar(2, 10, 13, 9.5, 12)], horizon=3, atr_buffer=0)
    assert early_exit["label_status"] == "target_first"
    assert early_exit["horizon_complete"] is False
    assert outcome([signal], horizon=1)["label_status"] == "unfilled"
    invalid = outcome([signal, bar(2, 8, 10, 7, 9)], atr_buffer=0)
    assert invalid["label_status"] == "invalid_entry"
    no_atr = label_events(
        [signal, bar(2, 10, 11, 9, 10)],
        [make_event("fixture", "bullish", signal, None, "a", 1, {})],
    )[0]
    assert no_atr["label_status"] == "invalid_entry"


def test_event_api_and_csv_parquet_exports_share_labels(tmp_path) -> None:
    root = tmp_path / "market"
    root.mkdir()
    (root / "TEST_H1.csv").write_text(
        "time,open,high,low,close,volume\n"
        "1,1,2,0,1,1\n2,1,3,0,2,1\n3,2,4,1,3,1\n"
        "4,3,5,2,4,1\n5,4,8,3,7,1\n6,7,8,6,7,1\n",
        encoding="utf-8",
    )
    params = {
        "symbol": "TEST",
        "timeframe": "H1",
        "detectors": "three_bullish_candles",
        "label_atr_period": 1,
        "label_horizon": 2,
    }
    with TestClient(create_app(Settings(root))) as client:
        events = client.get("/api/v1/events", params=params).json()["events"]
        assert events
        for invalid in ({"label_horizon": 0}, {"label_atr_buffer": -1}, {"label_slippage": "nan"}):
            response = client.get("/api/v1/events", params={**params, **invalid})
            assert response.status_code == 422
            assert "label_" in response.json()["detail"]
        for format in ("csv", "parquet"):
            response = client.get("/api/v1/events/export", params={**params, "format": format})
            assert response.status_code == 200
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                frame = (
                    pd.read_csv(archive.open(f"encountered_events.{format}"))
                    if format == "csv"
                    else pd.read_parquet(io.BytesIO(archive.read("encountered_events.parquet")))
                )
                metadata = json.loads(archive.read(f"encountered_events.{format}.json"))
            assert metadata["schema"] == "encountered_events.v2"
            assert frame["id"].tolist() == [event["id"] for event in events]
            assert frame["label_status"].tolist() == [event["label_status"] for event in events]
            assert frame["entry_fill"].tolist() == [event["entry_fill"] for event in events]
            assert "mae_r" in frame and "observation_end_time" in frame


def test_finite_source_prices_that_overflow_target_stay_exportable(tmp_path) -> None:
    root = tmp_path / "market"
    root.mkdir()
    (root / "LARGE_H1.csv").write_text(
        "time,open,high,low,close,volume\n"
        "1,1e307,2e307,1e307,2e307,1\n"
        "2,2e307,3e307,2e307,3e307,1\n"
        "3,3e307,4e307,3e307,4e307,1\n"
        "4,1.3e308,1.4e308,1.2e308,1.3e308,1\n",
        encoding="utf-8",
    )
    params = {
        "symbol": "LARGE",
        "timeframe": "H1",
        "detectors": "three_bullish_candles",
        "label_atr_period": 1,
    }
    with TestClient(create_app(Settings(root))) as client:
        response = client.get("/api/v1/events", params=params)
        assert response.status_code == 200
        records = response.json()["events"]
        assert len(records) == 1
        assert records[0]["label_status"] == "invalid_entry"
        assert records[0]["label_reason"] == "derived target is non-finite"
        assert records[0]["target_price"] is None
        assert records[0]["net_r"] is None
        for format in ("csv", "parquet"):
            export = client.get("/api/v1/events/export", params={**params, "format": format})
            assert export.status_code == 200
            with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
                frame = (
                    pd.read_csv(archive.open("encountered_events.csv"))
                    if format == "csv"
                    else pd.read_parquet(io.BytesIO(archive.read("encountered_events.parquet")))
                )
            assert frame.loc[0, "id"] == records[0]["id"]
            assert frame.loc[0, "label_status"] == "invalid_entry"
            assert pd.isna(frame.loc[0, "target_price"])
            assert pd.isna(frame.loc[0, "net_r"])


def test_other_finite_input_overflows_are_invalid_without_nonfinite_fields() -> None:
    atr_overflow = outcome([bar(1, 0, 1e308, -1e308, 0), bar(2, 1, 2, 0, 1)], horizon=1)
    assert atr_overflow["label_status"] == "invalid_entry"
    assert atr_overflow["signal_atr"] is None

    excursion_overflow = outcome(
        [
            bar(1, 1e308, 1.01e308, 0.9e308, 1e308),
            bar(2, 1e308, 1.2e308, -1e308, 1e308),
        ],
        horizon=1,
        atr_buffer=0,
    )
    assert excursion_overflow["label_status"] == "invalid_entry"
    assert excursion_overflow["mae_price"] is None
    assert excursion_overflow["mae_r"] is None

    net_overflow = outcome(
        [bar(1, 0, 1e-300, 0, 1e-300), bar(2, 1e-300, 2e-300, 0, 1e-300)],
        horizon=1,
        atr_buffer=0,
        commission=1e308,
    )
    assert net_overflow["label_status"] == "invalid_entry"
    assert net_overflow["gross_r"] is None
    assert net_overflow["net_r"] is None


def test_full_csv_and_parquet_exports_match_paged_api_and_label_arithmetic(tmp_path) -> None:
    root = tmp_path / "market"
    root.mkdir()
    lines = ["time,open,high,low,close,volume"]
    for index in range(1200):
        opening = 10 if index == 0 else (10 if index % 2 else 12)
        close = 10 if index % 2 == 0 else 12
        lines.append(
            f"{1_000_000 + index * 60},{opening},{max(opening, close) + 1},"
            f"{min(opening, close) - 1},{close},1"
        )
    (root / "FULL_H1.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    params = {
        "symbol": "FULL",
        "timeframe": "H1",
        "detectors": "ema_breakout",
        "ema_period": 2,
        "label_horizon": 3,
        "label_atr_period": 2,
        "label_slippage": 0.1,
        "label_commission": 0.02,
    }
    with TestClient(create_app(Settings(root))) as client:
        api_events: list[dict[str, object]] = []
        before = None
        while True:
            page_params = {**params, **({"before": before} if before is not None else {})}
            response = client.get("/api/v1/events", params=page_params)
            assert response.status_code == 200
            payload = response.json()
            api_events.extend(payload["events"])
            if not payload["has_more"]:
                break
            before = payload["next_before"]
        assert len(api_events) > 100
        by_id = {event["id"]: event for event in api_events}
        assert len(by_id) == len(api_events)

        label_fields = (
            "label_status",
            "label_reason",
            "entry_time",
            "entry_open",
            "entry_fill",
            "signal_atr",
            "stop_price",
            "target_price",
            "risk_price",
            "planned_end_time",
            "observation_end_time",
            "exit_time",
            "exit_fill",
            "gross_r",
            "net_r",
            "horizon_complete",
            "mae_price",
            "mfe_price",
            "mae_r",
            "mfe_r",
        )
        for format in ("csv", "parquet"):
            response = client.get("/api/v1/events/export", params={**params, "format": format})
            assert response.status_code == 200
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                frame = (
                    pd.read_csv(archive.open("encountered_events.csv"))
                    if format == "csv"
                    else pd.read_parquet(io.BytesIO(archive.read("encountered_events.parquet")))
                )
            assert set(frame["id"]) == set(by_id)
            assert len(frame) == len(api_events)
            assert frame["label_status"].notna().all()
            for row in frame.to_dict("records"):
                event = by_id[row["id"]]
                assert json.loads(row["label_config"]) == event["label_config"]
                for name in label_fields:
                    expected = event[name]
                    actual = row[name]
                    if expected is None:
                        assert pd.isna(actual), (row["id"], name)
                    elif isinstance(expected, bool):
                        assert actual == expected, (row["id"], name)
                    elif isinstance(expected, (int, float)):
                        assert actual == pytest.approx(expected), (row["id"], name)
                    else:
                        assert actual == expected, (row["id"], name)
                if event["risk_price"] is not None and event["target_price"] is not None:
                    direction = 1 if event["direction"] == "bullish" else -1
                    assert event["target_price"] == pytest.approx(
                        event["entry_fill"] + direction * 2 * event["risk_price"]
                    )
                if event["gross_r"] is not None:
                    direction = 1 if event["direction"] == "bullish" else -1
                    assert event["gross_r"] == pytest.approx(
                        direction * (event["exit_fill"] - event["entry_fill"]) / event["risk_price"]
                    )
                    assert event["net_r"] == pytest.approx(
                        event["gross_r"] - 0.04 / event["risk_price"]
                    )
                if event["label_status"] in ("censored", "unfilled", "invalid_entry"):
                    assert event["gross_r"] is None and event["net_r"] is None
