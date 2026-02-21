from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RiskSnapshot:
    spread_pips: float
    margin_usage_pct: float
    drawdown_pct: float
    net_delta_lots: float


@dataclass
class RiskConfig:
    max_spread_pips: float
    max_margin_usage_pct: float
    max_drawdown_pct: float
    max_net_delta_lots: float


def should_risk_off(snapshot: RiskSnapshot, cfg: RiskConfig) -> bool:
    if snapshot.spread_pips > cfg.max_spread_pips:
        return True
    if snapshot.margin_usage_pct > cfg.max_margin_usage_pct:
        return True
    if snapshot.drawdown_pct > cfg.max_drawdown_pct:
        return True
    if abs(snapshot.net_delta_lots) > cfg.max_net_delta_lots:
        return True
    return False
