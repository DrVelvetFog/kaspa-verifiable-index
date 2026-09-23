"""Calibrate the watchdog's persist_window against observed indexer lag.

The watchdog fires only when node-truth/indexer divergence persists past
`persist_window` consecutive cycles. Set it too low and benign lag false-alarms; too
high and a real drain goes unnoticed longer. The right value is a function of the
data: how long the watched indexer actually trails node truth in practice.

Feed this a series of per-cycle divergence booleans (from running the check on the
live pair over time). A divergence run that later clears is *lag* — the indexer caught
up. A run still open at the end of the series is *unresolved* and is NOT treated as
lag (it could be a real drain). The recommended window is the longest self-healed run
plus a margin, so ordinary lag never trips it but anything longer does.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Calibration:
    samples: int
    observed_max_lag: int  # longest divergence run that self-healed
    self_healed_runs: list[int]
    unresolved_tail: int  # length of an open divergence run at the series end, if any
    recommended_window: int
    note: str


def recommend_persist_window(diverged: list[bool], margin: int = 1, floor: int = 2) -> Calibration:
    """`diverged[i]` is whether node truth and the indexer disagreed at cycle i."""
    runs, cur = [], 0
    for d in diverged:
        if d:
            cur += 1
        elif cur:
            runs.append(cur)
            cur = 0
    unresolved = cur  # a run still open at the end never proved itself lag

    healed = runs  # every run in `runs` ended with a return to agreement
    observed = max(healed) if healed else 0
    recommended = max(observed + margin, floor)

    if not diverged:
        note = "no samples; window left at the floor until live data exists"
    elif not healed and not unresolved:
        note = "no divergence observed; floor window until lag is measured"
    elif not healed and unresolved:
        note = (f"only an unresolved divergence ({unresolved} cycles) — treated as a "
                f"possible drain, not lag; floor window, keep measuring")
    else:
        note = (f"longest self-healed lag was {observed} cycles; window set to "
                f"{recommended} (= {observed} + {margin} margin)")
    return Calibration(len(diverged), observed, healed, unresolved, recommended, note)
