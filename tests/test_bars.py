import csv
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest
from openpyxl import Workbook

from app.bars import SourceValidationError, load_bars


def write_workbook(
    path: Path,
    rows: list[tuple[object, ...]],
    headers: tuple[str, ...] = ("time", "open", "high", "low", "close", "volume"),
) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    return path


def write_csv(
    path: Path,
    rows: list[tuple[object, ...]],
    headers: tuple[str, ...] = ("time", "open", "high", "low", "close", "volume"),
) -> Path:
    with path.open("w", newline="", encoding="utf-8") as source_file:
        writer = csv.writer(source_file)
        writer.writerow(headers)
        writer.writerows(rows)
    return path


def valid(time: object = "2025-01-01T00:00:00Z") -> tuple[object, ...]:
    return (time, 1, 3, 0, 2, 10)


def test_loads_naive_aware_unix_and_tick_volume(tmp_path: Path) -> None:
    source = write_workbook(
        tmp_path / "bars.xlsx",
        [
            valid("2025-01-01T00:00:00"),
            valid("2025-01-01T00:01:00+00:00"),
            valid(1735689720),
            (1735689780000, 1, 3, 0, 2, 11),
        ],
        ("time", "open", "high", "low", "close", "tick_volume"),
    )

    store = load_bars(source, "UTC")

    assert [bar.time for bar in store.bars] == [1735689600, 1735689660, 1735689720, 1735689780]
    assert store.bars[-1].volume == 11


def test_supported_formats_share_the_same_bar_contract(tmp_path: Path) -> None:
    columns = ("time", "open", "high", "low", "close", "volume")
    rows = [valid(1735689600), valid(1735689660)]
    csv_source = write_csv(tmp_path / "bars.csv", rows)
    parquet_source = tmp_path / "bars.parquet"
    pd.DataFrame(rows, columns=columns).to_parquet(parquet_source, index=False)
    xlsx_source = write_workbook(tmp_path / "bars.xlsx", rows)

    for source in (csv_source, parquet_source, xlsx_source):
        assert [bar.time for bar in load_bars(source).bars] == [1735689600, 1735689660]


def test_volume_column_wins_over_tick_volume(tmp_path: Path) -> None:
    source = write_workbook(
        tmp_path / "bars.xlsx",
        [("2025-01-01T00:00:00Z", 1, 3, 0, 2, 10, 99)],
        ("time", "open", "high", "low", "close", "volume", "tick_volume"),
    )
    assert load_bars(source).bars[0].volume == 10


def test_timestamps_use_configured_timezone_and_preserve_offsets(tmp_path: Path) -> None:
    naive = load_bars(
        write_workbook(tmp_path / "naive.xlsx", [valid("2025-01-01T00:00:00")]),
        "America/New_York",
    )
    aware = load_bars(write_workbook(tmp_path / "aware.xlsx", [valid("2025-01-01T03:30:00+03:30")]))

    assert naive.bars[0].time == 1735707600
    assert aware.bars[0].time == 1735689600


@pytest.mark.parametrize("bad_time", ["nope", "2025-01-01T00:00:00.1Z", 1735689600123])
def test_rejects_bad_timestamps(tmp_path: Path, bad_time: object) -> None:
    with pytest.raises(SourceValidationError, match="time"):
        load_bars(write_workbook(tmp_path / "bars.xlsx", [valid(bad_time)]))


@pytest.mark.parametrize(
    "column,value", [("open", None), ("close", "x"), ("high", float("inf")), ("volume", -1)]
)
def test_rejects_invalid_numbers(tmp_path: Path, column: str, value: object) -> None:
    row = list(valid())
    row[("time", "open", "high", "low", "close", "volume").index(column)] = value
    with pytest.raises(SourceValidationError, match=column):
        load_bars(write_workbook(tmp_path / "bars.xlsx", [tuple(row)]))


@pytest.mark.parametrize(
    "row",
    [
        ("2025-01-01T00:00:00Z", 3, 2, 0, 2, 1),
        ("2025-01-01T00:00:00Z", 1, 3, 2.5, 2, 1),
        ("2025-01-01T00:00:00Z", 1, 1, 2, 1, 1),
    ],
)
def test_rejects_bad_ohlc_geometry(tmp_path: Path, row: tuple[object, ...]) -> None:
    with pytest.raises(SourceValidationError):
        load_bars(write_workbook(tmp_path / "bars.xlsx", [row]))


def test_source_contract_and_order_errors(tmp_path: Path) -> None:
    with pytest.raises(SourceValidationError, match="does not exist"):
        load_bars(tmp_path / "missing.xlsx")
    with pytest.raises(SourceValidationError, match="missing"):
        load_bars(write_workbook(tmp_path / "headers.xlsx", [valid()], ("time", "open")))
    with pytest.raises(SourceValidationError, match="timezone"):
        load_bars(write_workbook(tmp_path / "zone.xlsx", [valid()]), "No/Such_Zone")
    with pytest.raises(SourceValidationError, match="duplicates"):
        load_bars(write_workbook(tmp_path / "duplicate.xlsx", [valid(), valid()]))
    with pytest.raises(SourceValidationError, match="earlier"):
        load_bars(write_workbook(tmp_path / "order.xlsx", [valid(2), valid(1)]))


def test_csv_shape_validation_handles_multiline_records(tmp_path: Path) -> None:
    source = tmp_path / "bars.csv"
    with source.open("w", newline="", encoding="utf-8") as source_file:
        writer = csv.writer(source_file)
        writer.writerow(("time", "open", "high", "low", "close", "volume", "note"))
        writer.writerow((100, 1, 3, 0, 2, 10, "first line\nsecond line"))
        writer.writerow((101, 1, 3, 0, 2, 10, "ordinary"))

    assert [bar.time for bar in load_bars(source).bars] == [100, 101]


@pytest.mark.parametrize("time_kind", ["iso", "datetime", "milliseconds"])
def test_page_cursor_uses_normalized_timestamps(tmp_path: Path, time_kind: str) -> None:
    base = 1_735_689_600
    if time_kind == "iso":
        times: list[object] = [
            datetime.fromtimestamp(base + index, UTC).isoformat() for index in range(20)
        ]
    elif time_kind == "datetime":
        times = [datetime.fromtimestamp(base + index, UTC) for index in range(20)]
    else:
        times = [(base + index) * 1_000 for index in range(20)]
    source = tmp_path / f"EURUSD_{time_kind}.parquet"
    pd.DataFrame(
        [(time, 1, 3, 0, 2, 10) for time in times],
        columns=("time", "open", "high", "low", "close", "volume"),
    ).to_parquet(source, index=False)

    page = load_bars(source).page(base + 10, 5, 2)

    assert [bar.time for bar in page.bars] == list(range(base + 3, base + 10))
    assert [bar.time for bar in page.display_bars] == list(range(base + 5, base + 10))


def test_pages_are_ascending_non_overlapping_and_strip_warm_up(tmp_path: Path) -> None:
    source = write_csv(tmp_path / "EURUSD_H1.csv", [valid(100 + index) for index in range(2_500)])
    store = load_bars(source)

    latest = store.page(None, 1_000, 100)
    older = store.page(latest.display_bars[0].time, 1_000, 100)

    assert [bar.time for bar in latest.display_bars] == list(range(1_600, 2_600))
    assert [bar.time for bar in older.display_bars] == list(range(600, 1_600))
    assert len(latest.bars) == len(older.bars) == 1_100
    assert set(latest.display_bars).isdisjoint(older.display_bars)
