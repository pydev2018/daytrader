from __future__ import annotations

from dataclasses import dataclass

from .types import Phase


@dataclass
class TransitionInput:
    trend_on: bool
    trend_off: bool
    stable: bool
    risk_off: bool
    cleanup_done: bool


def transition_phase(current: Phase, sig: TransitionInput) -> Phase:
    if sig.risk_off:
        return Phase.RISK_OFF

    if current == Phase.INIT:
        return Phase.RANGE

    if current == Phase.RANGE:
        if sig.trend_on:
            return Phase.TREND_LOCK
        return Phase.RANGE

    if current == Phase.TREND_LOCK:
        if sig.trend_off:
            return Phase.EXHAUSTION_CONFIRM
        return Phase.TREND_LOCK

    if current == Phase.EXHAUSTION_CONFIRM:
        if sig.trend_on:
            return Phase.TREND_LOCK
        if sig.stable:
            return Phase.GARBAGE_COLLECT
        return Phase.EXHAUSTION_CONFIRM

    if current == Phase.GARBAGE_COLLECT:
        if sig.cleanup_done:
            return Phase.RECENTER
        return Phase.GARBAGE_COLLECT

    if current == Phase.RECENTER:
        return Phase.RANGE

    if current == Phase.RISK_OFF:
        if not sig.risk_off:
            return Phase.INIT
        return Phase.RISK_OFF

    return current
