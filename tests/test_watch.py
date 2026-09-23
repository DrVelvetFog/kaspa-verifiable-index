"""The watchdog must tell indexer *lag* (self-heals, benign) apart from a *drain*
(persists, must alert), using real TN10 covenant data for the caught-up state and
mutating the indexer document to produce lag and drain sequences.
"""

import json
import os

from kaspa_verifiable_index.watch import CovenantWatcher, Snapshot, run_watch

FX = os.path.join(os.path.dirname(__file__), "fixtures")


def _counter():
    idx = json.load(open(f"{FX}/tn10-counter-kascov.json"))
    txs = json.load(open(f"{FX}/tn10-counter-rest.json"))
    return idx, txs


# node truth for the counter: genesis 6d0a:0 -> 7964:0 -> 480f:0 (only 480f:0 is live).
LIVE = "480fc61819f73dc476cbc5b12fb974bd711457f07e4d07b074ca19bdb77a5dcd:0"
LIVE_VALUE = 197961400
PREV = "796445b99363dee7540d7dd75287c5a980d110bab007cee8d913ae254541410d:0"  # spent one step back
PREV_VALUE = 198162500
GENESIS = "6d0acd6fcbaf68bca1568a3cbbafe0f3c1d72c4f6ea0edc6f6c013a59cb5d591:0"  # spent at genesis
GENESIS_VALUE = 198363600


def _indexer(live):
    """A kascov-shape doc claiming `live` = [(outpoint, value)] as the live set."""
    return {
        "covenant_id": "4a95a59dc79c3f46f35db91453f26785750450606836d82c48c1affdd71ed70a",
        "status": "active",
        "lineage_complete": True,
        "live_utxos": len(live),
        "live_value": sum(v for _, v in live),
        "utxos": [{"outpoint": op, "live": True, "value": v} for op, v in live],
    }


CAUGHT_UP = _indexer([(LIVE, LIVE_VALUE)])          # agrees with node truth
LAGGING = _indexer([(PREV, PREV_VALUE)])            # trails: still shows the spent PREV as live
DRAIN = _indexer([(LIVE, LIVE_VALUE), (GENESIS, GENESIS_VALUE)])  # phantom: spent GENESIS claimed live
FORGERY = _indexer([(LIVE, LIVE_VALUE), ("de" * 32 + ":7", 500000)])  # outpoint node truth never had


def _seq(txs, indexer_states):
    cid = "4a95a59dc79c3f46f35db91453f26785750450606836d82c48c1affdd71ed70a"
    return [Snapshot(cid, txs, idx, i) for i, idx in enumerate(indexer_states)]


def test_lag_does_not_alert():
    """Two cycles of lag, then the indexer catches up. With window 3, no alert."""
    _, txs = _counter()
    w = CovenantWatcher(persist_window=3)
    events = run_watch(w, _seq(txs, [LAGGING, LAGGING, CAUGHT_UP]))
    assert events == []


def test_drain_alerts_exactly_once_at_the_window():
    """A persistent phantom fires one alert, at the moment it crosses the window."""
    _, txs = _counter()
    w = CovenantWatcher(persist_window=3)
    events = run_watch(w, _seq(txs, [DRAIN, DRAIN, DRAIN, DRAIN]))
    assert len(events) == 1
    ev = events[0]
    assert ev.kind == "alert"
    assert ev.cycle == 2  # 0-indexed: the 3rd consecutive diverged cycle
    assert ev.persisted == 3
    assert ev.verification["status"] == "failed_closed"


def test_forgery_flavor_alerts_too():
    """An outpoint node truth never had (the signature-bypass shape) also persists."""
    _, txs = _counter()
    w = CovenantWatcher(persist_window=2)
    events = run_watch(w, _seq(txs, [FORGERY, FORGERY, FORGERY]))
    assert len(events) == 1 and events[0].kind == "alert"


def test_drain_then_recovery_clears():
    """Alert while wrong, one 'cleared' event when the indexer is corrected."""
    _, txs = _counter()
    w = CovenantWatcher(persist_window=3)
    events = run_watch(w, _seq(txs, [DRAIN, DRAIN, DRAIN, CAUGHT_UP]))
    kinds = [e.kind for e in events]
    assert kinds == ["alert", "cleared"]


def test_steady_verified_is_silent():
    _, txs = _counter()
    w = CovenantWatcher(persist_window=3)
    events = run_watch(w, _seq(txs, [CAUGHT_UP] * 5))
    assert events == []


def test_window_of_one_false_positives_on_lag():
    """The negative control for the whole design: with no persistence window, the
    watchdog can't tell lag from a drain and alerts on benign lag. This is why the
    window exists and must be calibrated, not left at 1."""
    _, txs = _counter()
    w = CovenantWatcher(persist_window=1)
    events = run_watch(w, _seq(txs, [LAGGING, CAUGHT_UP]))
    # window=1 fires immediately on the lag (false positive), then clears when it heals.
    assert [e.kind for e in events] == ["alert", "cleared"]


def test_flapping_indexer_realerts_after_recovery():
    """Diverge -> alert -> recover -> diverge again should alert again, not stay silent."""
    _, txs = _counter()
    w = CovenantWatcher(persist_window=2)
    events = run_watch(w, _seq(txs, [DRAIN, DRAIN, CAUGHT_UP, DRAIN, DRAIN]))
    assert [e.kind for e in events] == ["alert", "cleared", "alert"]
