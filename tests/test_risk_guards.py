from risk.guards import RiskConfig, RiskSnapshot, should_risk_off


def test_risk_off_when_spread_too_high():
    cfg = RiskConfig(
        max_spread_pips=2.0,
        max_margin_usage_pct=70.0,
        max_drawdown_pct=20.0,
        max_net_delta_lots=0.5,
    )
    snapshot = RiskSnapshot(
        spread_pips=3.1,
        margin_usage_pct=20.0,
        drawdown_pct=2.0,
        net_delta_lots=0.1,
    )
    assert should_risk_off(snapshot, cfg)


def test_no_risk_off_when_within_limits():
    cfg = RiskConfig(
        max_spread_pips=2.0,
        max_margin_usage_pct=70.0,
        max_drawdown_pct=20.0,
        max_net_delta_lots=0.5,
    )
    snapshot = RiskSnapshot(
        spread_pips=1.2,
        margin_usage_pct=40.0,
        drawdown_pct=3.0,
        net_delta_lots=0.2,
    )
    assert not should_risk_off(snapshot, cfg)
