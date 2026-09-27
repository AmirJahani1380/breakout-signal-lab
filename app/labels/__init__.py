"""Fixed 2R outcomes for already detected events."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite
from typing import cast

from app.bars import Bar
from app.event_detectors import Event
from app.features.atr import atr_values


@dataclass(frozen=True, slots=True)
class LabelConfig:
    horizon: int = 20
    atr_period: int = 20
    atr_buffer: float = 0.05
    slippage: float = 0.0
    commission: float = 0.0

    def __post_init__(self) -> None:
        if (
            isinstance(self.horizon, bool)
            or not isinstance(self.horizon, int)
            or not 1 <= self.horizon <= 1000
        ):
            raise ValueError("label_horizon must be an integer between 1 and 1000")
        if (
            isinstance(self.atr_period, bool)
            or not isinstance(self.atr_period, int)
            or not 1 <= self.atr_period <= 1000
        ):
            raise ValueError("label_atr_period must be an integer between 1 and 1000")
        for name in ("atr_buffer", "slippage", "commission"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                or value < 0
            ):
                raise ValueError(f"label_{name} must be a finite non-negative number")

    def as_dict(self) -> dict[str, int | float]:
        return {
            "horizon": self.horizon,
            "atr_period": self.atr_period,
            "atr_buffer": self.atr_buffer,
            "slippage": self.slippage,
            "commission": self.commission,
        }


def _barrier_exit(bar: Bar, direction: int, stop: float, target: float) -> tuple[str, float] | None:
    # The opening print precedes any unknown intrabar path and fills at its actual gap price.
    if direction * (bar.open - stop) <= 0:
        return "stop_first", bar.open
    if direction * (bar.open - target) >= 0:
        return "target_first", bar.open
    stop_hit = direction * ((bar.low if direction == 1 else bar.high) - stop) <= 0
    target_hit = direction * ((bar.high if direction == 1 else bar.low) - target) >= 0
    if stop_hit and target_hit:
        return "ambiguous", stop  # Conservative stop-first fill; status retains uncertainty.
    if stop_hit:
        return "stop_first", stop
    if target_hit:
        return "target_first", target
    return None


def label_events(
    bars: Sequence[Bar], events: Sequence[Event], config: LabelConfig = LabelConfig()
) -> list[dict[str, object]]:
    """Label full-source events; the immediate next bar is the only eligible entry."""
    positions: dict[int, int] = {bar.time: index for index, bar in enumerate(bars)}
    atr = atr_values(bars, config.atr_period)
    results: list[dict[str, object]] = []
    for event in events:
        signal_index = positions[cast(int, event["signal_time"])]
        direction = 1 if event["direction"] == "bullish" else -1
        signal_atr = atr[signal_index]
        record: dict[str, object] = {
            **event,
            "label_config": config.as_dict(),
            "label_status": "unfilled",
            "label_reason": None,
            "entry_time": None,
            "entry_open": None,
            "entry_fill": None,
            "signal_atr": signal_atr if signal_atr is not None and isfinite(signal_atr) else None,
            "stop_price": None,
            "target_price": None,
            "risk_price": None,
            "planned_end_time": None,
            "observation_end_time": None,
            "exit_time": None,
            "exit_fill": None,
            "gross_r": None,
            "net_r": None,
            "horizon_complete": False,
            "mae_price": None,
            "mfe_price": None,
            "mae_r": None,
            "mfe_r": None,
        }
        entry_index = signal_index + 1
        if entry_index == len(bars):
            record["label_reason"] = "no next source bar"
            results.append(record)
            continue
        entry = bars[entry_index]
        record["entry_time"] = entry.time
        record["entry_open"] = entry.open
        if signal_atr is None or not isfinite(signal_atr) or signal_atr <= 0:
            record.update(
                label_status="invalid_entry",
                label_reason="signal ATR unavailable, zero, or non-finite",
            )
            results.append(record)
            continue
        entry_fill = entry.open + direction * config.slippage
        signal = bars[signal_index]
        stop = (
            (signal.low - config.atr_buffer * signal_atr)
            if direction == 1
            else (signal.high + config.atr_buffer * signal_atr)
        )
        risk = direction * (entry_fill - stop)
        if not all(isfinite(value) for value in (entry_fill, stop, risk)):
            record.update(
                label_status="invalid_entry",
                label_reason="derived entry fill, stop, or risk is non-finite",
            )
            results.append(record)
            continue
        record.update(entry_fill=entry_fill, stop_price=stop, risk_price=risk)
        if risk <= 0:
            record.update(
                label_status="invalid_entry", label_reason="entry fill is not beyond stop"
            )
            results.append(record)
            continue
        target = entry_fill + direction * 2 * risk
        if not isfinite(target):
            record.update(label_status="invalid_entry", label_reason="derived target is non-finite")
            results.append(record)
            continue
        record["target_price"] = target
        final_index = min(len(bars), entry_index + config.horizon) - 1
        window = bars[entry_index : final_index + 1]
        record["planned_end_time"] = (
            bars[entry_index + config.horizon - 1].time
            if entry_index + config.horizon <= len(bars)
            else None
        )
        record["observation_end_time"] = window[-1].time
        record["horizon_complete"] = len(window) == config.horizon
        adverse = max(
            0.0,
            max(
                direction * (entry_fill - (bar.low if direction == 1 else bar.high))
                for bar in window
            ),
        )
        favorable = max(
            0.0,
            max(
                direction * ((bar.high if direction == 1 else bar.low) - entry_fill)
                for bar in window
            ),
        )
        if not all(
            isfinite(value) for value in (adverse, favorable, adverse / risk, favorable / risk)
        ):
            record.update(
                label_status="invalid_entry", label_reason="derived excursion is non-finite"
            )
            results.append(record)
            continue
        record.update(
            mae_price=adverse, mfe_price=favorable, mae_r=adverse / risk, mfe_r=favorable / risk
        )
        for bar in window:
            outcome = _barrier_exit(bar, direction, stop, target)
            if outcome is not None:
                status, exit_price = outcome
                break
        else:
            if not record["horizon_complete"]:
                record.update(
                    label_status="censored", label_reason="holding horizon exceeds available bars"
                )
                results.append(record)
                continue
            status, exit_price = "timeout", window[-1].close
            bar = window[-1]
        exit_fill = exit_price - direction * config.slippage
        gross_r = direction * (exit_fill - entry_fill) / risk
        net_r = gross_r - 2 * config.commission / risk
        if not all(isfinite(value) for value in (exit_fill, gross_r, net_r)):
            record.update(
                label_status="invalid_entry", label_reason="derived exit or R is non-finite"
            )
            results.append(record)
            continue
        record.update(
            label_status=status,
            exit_time=bar.time,
            exit_fill=exit_fill,
            gross_r=gross_r,
            net_r=net_r,
        )
        results.append(record)
    return results
