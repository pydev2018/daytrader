from strategy.state_machine import TransitionInput, transition_phase
from strategy.types import Phase


def test_range_to_trend_lock_transition():
    nxt = transition_phase(
        Phase.RANGE,
        TransitionInput(
            trend_on=True,
            trend_off=False,
            stable=False,
            risk_off=False,
            cleanup_done=False,
        ),
    )
    assert nxt == Phase.TREND_LOCK


def test_exhaustion_to_garbage_collect_transition():
    nxt = transition_phase(
        Phase.EXHAUSTION_CONFIRM,
        TransitionInput(
            trend_on=False,
            trend_off=True,
            stable=True,
            risk_off=False,
            cleanup_done=False,
        ),
    )
    assert nxt == Phase.GARBAGE_COLLECT


def test_risk_off_overrides_all():
    nxt = transition_phase(
        Phase.RANGE,
        TransitionInput(
            trend_on=False,
            trend_off=False,
            stable=False,
            risk_off=True,
            cleanup_done=False,
        ),
    )
    assert nxt == Phase.RISK_OFF
