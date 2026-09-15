from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.bars import Bar


@dataclass(frozen=True, slots=True)
class CandleMeasurements:
    candle_range: tuple[float, ...]
    body_size: tuple[float, ...]
    upper_wick: tuple[float, ...]
    lower_wick: tuple[float, ...]


def measure_candles(bars: Sequence[Bar]) -> CandleMeasurements:
    return CandleMeasurements(
        tuple(bar.high - bar.low for bar in bars),
        tuple(abs(bar.close - bar.open) for bar in bars),
        tuple(bar.high - max(bar.open, bar.close) for bar in bars),
        tuple(min(bar.open, bar.close) - bar.low for bar in bars),
    )
