import csv
from pathlib import Path

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


def test_loads_csv_with_the_same_bar_contract(tmp_path: Path) -> None:
    source = write_csv(tmp_path / "bars.csv", [valid(1735689600), valid(1735689660)])
    assert [bar.time for bar in load_bars(source).bars] == [1735689600, 1735689660]


def test_volume_column_wins_over_tick_volume(tmp_path: Path) -> None:
    source = write_workbook(
        tmp_path / "bars.xlsx",
        [("2025-01-01T00:00:00Z", 1, 3, 0, 2, 10, 99)],
        ("time", "open", "high", "low", "close", "volume", "tick_volume"),
    )
    assert load_bars(source).bars[0].volume == 10


def test_naive_time_uses_configured_timezone(tmp_path: Path) -> None:
    store = load_bars(
        write_workbook(tmp_path / "bars.xlsx", [valid("2025-01-01T00:00:00")]),
        "America/New_York",
    )
    assert store.bars[0].time == 1735707600


def test_nonzero_aware_offset_is_preserved(tmp_path: Path) -> None:
    source = write_workbook(tmp_path / "bars.xlsx", [valid("2025-01-01T03:30:00+03:30")])
    assert load_bars(source).bars[0].time == 1735689600


def test_extreme_aware_timestamp_has_cell_context(tmp_path: Path) -> None:
    source = write_workbook(tmp_path / "extreme.xlsx", [valid("9999-12-31T23:59:59-23:59")])
    with pytest.raises(
        SourceValidationError,
        match=r"extreme.xlsx, spreadsheet row 2, column time, value '9999-12-31T23:59:59-23:59'",
    ):
        load_bars(source)


@pytest.mark.parametrize("bad_time", ["nope", "2025-01-01T00:00:00.1Z", 1735689600123])
def test_rejects_bad_timestamps(tmp_path: Path, bad_time: object) -> None:
    with pytest.raises(SourceValidationError, match="time"):
        load_bars(write_workbook(tmp_path / "bars.xlsx", [valid(bad_time)]))


def test_subsecond_timestamp_keeps_direct_validation_message(tmp_path: Path) -> None:
    with pytest.raises(SourceValidationError, match="loses sub-second precision") as error:
        load_bars(write_workbook(tmp_path / "bars.xlsx", [valid("2025-01-01T00:00:00.1Z")]))
    assert "cannot convert" not in str(error.value)


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
    with pytest.raises(SourceValidationError, match="timezone"):
        load_bars(write_workbook(tmp_path / "bad-zone.xlsx", [valid()]), "bad\\x00zone")
    with pytest.raises(SourceValidationError, match="duplicates"):
        load_bars(write_workbook(tmp_path / "duplicate.xlsx", [valid(), valid()]))
    with pytest.raises(SourceValidationError, match="earlier"):
        load_bars(write_workbook(tmp_path / "order.xlsx", [valid(2), valid(1)]))


def test_empty_source_is_valid(tmp_path: Path) -> None:
    assert load_bars(write_workbook(tmp_path / "empty.xlsx", [])).bars == ()


def test_lazy_worksheet_failure_has_source_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = write_workbook(tmp_path / "broken.xlsx", [valid()])

    class BrokenSheet:
        def iter_rows(self, values_only: bool) -> object:
            del values_only
            raise OSError("bad worksheet XML")

    class BrokenBook:
        active = BrokenSheet()

        def close(self) -> None:
            pass

    monkeypatch.setattr("app.bars.load_workbook", lambda *args, **kwargs: BrokenBook())
    with pytest.raises(SourceValidationError, match="broken.xlsx: cannot read XLSX worksheet"):
        load_bars(source)
