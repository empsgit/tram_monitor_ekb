from unittest.mock import AsyncMock
from app.core.eta_calculator import EtaCalculator
from app.core.history import History
from tests.test_history_recording import confirmed, catalog, observe, START


def test_fallback_until_minimum_samples():
    t = confirmed()
    observe(t, 1.2, 80)
    j = t.states["tram"]
    history = History(AsyncMock())
    key = ("run", 1, j.version, 0, 1, 1, 13)
    history.stats[key] = (80, 100, 140, 4)
    assert EtaCalculator().predict(j, catalog()[0], history)[0][1]["eta_source"] == "fallback"
    history.stats[key] = (80, 100, 140, 5)
    estimates = EtaCalculator().predict(j, catalog()[0], history)
    assert estimates[0][1]["eta_source"] == "history"
    assert 79 <= estimates[0][1]["eta_seconds"] <= 81
    assert estimates[1][1]["eta_source"] == "mixed"


def test_stop_eta_zero_and_dwell_excluded_once_elapsed():
    t = confirmed()
    history = History(AsyncMock())
    j = t.states["tram"]
    before = EtaCalculator().predict(j, catalog()[0], history)
    observe(t, 1, 90, speed=0)
    after = EtaCalculator().predict(j, catalog()[0], history)
    assert after[0][1]["eta_seconds"] == 0
    assert before[1][1]["eta_seconds"] - after[1][1]["eta_seconds"] == 20


def test_fallback_does_not_cross_direction_or_version():
    h = History(AsyncMock())
    h.stats[("run", 1, "a", 0, 1, None, None)] = (80, 100, 140, 10)
    assert h.lookup("run", 1, "a", 0, 1, START) is not None
    assert h.lookup("run", 1, "b", 0, 1, START) is None
    assert h.lookup("run", 1, "a", 1, 1, START) is None
