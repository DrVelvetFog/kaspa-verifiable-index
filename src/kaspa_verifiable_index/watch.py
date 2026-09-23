"""Continuous watchdog over the verifiable covenant check.

The one-shot check in `index.py` answers "does the indexer agree with node truth
right now." A live indexer legitimately *lags* the node by a few blocks, so a single
disagreement is not yet an alarm — lag and an attack look identical in one snapshot.
The discriminator is time: lag self-heals within a few cycles; a drain (inflated or
forged state) never reconciles, because node truth never catches up to it.

`CovenantWatcher` processes snapshots in sequence and fires only when a divergence
persists past `persist_window` consecutive observations. Observe-only: it never
touches funds or the chain; it emits events a human or an operator acts on. A live
feed replaces the snapshot iterable with a poller — the engine is identical.
"""

from __future__ import annotations

from dataclasses import dataclass

from .index import get_utxos_by_covenant_id


@dataclass
class Snapshot:
    """One observation cycle for a covenant: node-truth txs + the indexer's claim."""

    covenant_id: str
    transactions: list  # accepted txs visible to a node this cycle
    indexer: dict  # the third-party indexer's document this cycle
    cycle: int = 0  # sequence index / timestamp, for reporting only


@dataclass
class _State:
    streak: int = 0  # consecutive diverged observations
    alerted: bool = False


@dataclass
class WatchEvent:
    kind: str  # "alert" | "cleared"
    covenant_id: str
    cycle: int
    persisted: int  # consecutive diverged cycles at the moment of the event
    verification: dict  # the cross_check verdict (discrepancies, node/indexer claims)
    note: str = ""


class CovenantWatcher:
    """Fires an `alert` only when node-truth/indexer divergence persists past
    `persist_window` consecutive observations, so ordinary indexer lag (which
    self-heals) never trips it. `persist_window` must be calibrated against how many
    blocks the watched indexer normally trails; a window of 1 alerts on lag."""

    def __init__(self, persist_window: int = 3):
        if persist_window < 1:
            raise ValueError("persist_window must be >= 1")
        self.persist_window = persist_window
        self._state: dict[str, _State] = {}

    def observe(self, snap: Snapshot) -> WatchEvent | None:
        result = get_utxos_by_covenant_id(snap.covenant_id, snap.transactions, snap.indexer)
        v = result["verification"]
        st = self._state.setdefault(snap.covenant_id, _State())
        diverged = v["status"] == "failed_closed"

        if not diverged:
            was_alerting = st.alerted
            st.streak = 0
            st.alerted = False
            if was_alerting:
                return WatchEvent("cleared", snap.covenant_id, snap.cycle, 0, v,
                                  "divergence resolved after an alert")
            return None

        st.streak += 1
        if st.streak >= self.persist_window and not st.alerted:
            st.alerted = True
            return WatchEvent(
                "alert", snap.covenant_id, snap.cycle, st.streak, v,
                f"divergence persisted {st.streak} cycles (>= {self.persist_window}); not indexer lag",
            )
        return None


def run_watch(watcher: CovenantWatcher, snapshots) -> list:
    """Feed a sequence of snapshots; return the events fired."""
    events = []
    for snap in snapshots:
        ev = watcher.observe(snap)
        if ev is not None:
            events.append(ev)
    return events


def format_event(ev: WatchEvent) -> str:
    v = ev.verification
    nt = v.get("node_truth", {})
    ic = v.get("indexer_claim", {})
    lines = [
        f"[{ev.kind.upper()}] covenant {ev.covenant_id[:16]}… cycle {ev.cycle} — {ev.note}",
    ]
    if ev.kind == "alert":
        lines += [
            f"  node truth: {nt.get('count')} live / {nt.get('value')}",
            f"  indexer:    {ic.get('count')} live / {ic.get('value')}",
        ]
        lines += [f"  - {d}" for d in v.get("discrepancies", [])]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Demo: a lag sequence (self-heals, no alert) and a drain sequence (alerts once),
# built from synthetic data so `python -m kaspa_verifiable_index.watch` runs alone.
# --------------------------------------------------------------------------

def _demo():
    cid = "4a" * 32
    a, b, zero = "aa" * 32, "bb" * 32, "00" * 32
    node = [
        {"transaction_id": a, "is_accepted": True,
         "inputs": [{"previous_outpoint_hash": zero, "previous_outpoint_index": "0"}],
         "outputs": [{"covenant_id": cid, "amount": 1000, "script_public_key_type": "scripthash",
                      "covenant_authorizing_input": 0}]},
        {"transaction_id": b, "is_accepted": True,
         "inputs": [{"previous_outpoint_hash": a, "previous_outpoint_index": "0"}],
         "outputs": [{"covenant_id": cid, "amount": 990, "script_public_key_type": "scripthash",
                      "covenant_authorizing_input": 0}]},
    ]
    # node truth: a:0 spent by b, so live = {b:0 / 990}

    def indexer(live):  # live = [(outpoint, value)]
        return {"covenant_id": cid, "status": "active", "lineage_complete": True,
                "live_utxos": len(live), "live_value": sum(v for _, v in live),
                "utxos": [{"outpoint": op, "live": True, "value": v} for op, v in live]}

    caught_up = indexer([(f"{b}:0", 990)])
    lagging = indexer([(f"{a}:0", 1000)])          # one step behind: still thinks a:0 is live
    drain = indexer([(f"{b}:0", 990), (f"{a}:0", 1000)])  # phantom: claims spent a:0 is live

    print("== LAG sequence (indexer trails 2 cycles, then catches up); window=3 ==")
    w = CovenantWatcher(persist_window=3)
    lag_seq = [Snapshot(cid, node, lagging, 0), Snapshot(cid, node, lagging, 1),
               Snapshot(cid, node, caught_up, 2)]
    evs = run_watch(w, lag_seq)
    print(f"  events: {len(evs)} (expected 0 — lag must not alert)")

    print("== DRAIN sequence (phantom persists 4 cycles); window=3 ==")
    w = CovenantWatcher(persist_window=3)
    drain_seq = [Snapshot(cid, node, drain, i) for i in range(4)]
    evs = run_watch(w, drain_seq)
    for e in evs:
        print(format_event(e))
    print(f"  events: {len(evs)} (expected 1 alert at cycle 2)")


if __name__ == "__main__":
    _demo()
