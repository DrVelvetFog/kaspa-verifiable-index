"""Node-truth replay must agree with the production indexer on honest data,
and must fail closed the moment an indexer claim diverges from node truth —
the shape of the 2026-09-20 Kasplex indexer drain.
"""

import glob
import json
import os

import pytest

from kaspa_verifiable_index import (
    commitment,
    cross_check,
    get_utxos_by_covenant_id,
    live_utxos_from_node_truth,
)

FX = os.path.join(os.path.dirname(__file__), "fixtures")


def _pairs():
    for kc in sorted(glob.glob(f"{FX}/*-kascov.json")):
        base = os.path.basename(kc)[: -len("-kascov.json")]
        rest = f"{FX}/{base}-rest.json"
        if os.path.exists(rest):
            yield base, rest, kc


PAIRS = list(_pairs())


@pytest.mark.parametrize("base,rest,kascov", PAIRS, ids=[p[0] for p in PAIRS])
def test_node_truth_matches_indexer_on_honest_fixtures(base, rest, kascov):
    idx = json.load(open(kascov))
    txs = json.load(open(rest))
    r = get_utxos_by_covenant_id(idx["covenant_id"], txs, idx, network=idx.get("network"))
    assert r["verification"]["status"] == "verified", r["verification"]["discrepancies"]
    assert r["live_utxo_count"] == idx["live_utxos"]
    assert r["live_value"] == idx["live_value"]


def test_all_fixtures_present():
    assert len(PAIRS) >= 8


# ---- negative controls: each must fail closed --------------------------------

def _counter():
    idx = json.load(open(f"{FX}/tn10-counter-kascov.json"))
    txs = json.load(open(f"{FX}/tn10-counter-rest.json"))
    return idx, txs


def test_fail_closed_when_indexer_claims_a_spent_utxo_is_live():
    # The drain shape: indexer reports value that node truth already spent.
    idx, txs = _counter()
    spent = next(u for u in idx["utxos"] if not u["live"])
    spent["live"] = True
    idx["live_utxos"] += 1
    idx["live_value"] += spent["value"]
    r = get_utxos_by_covenant_id(idx["covenant_id"], txs, idx, network="testnet-10")
    assert r["verification"]["status"] == "failed_closed"
    assert r["verification"]["discrepancies"]


def test_fail_closed_when_indexer_omits_a_live_utxo():
    idx, txs = _counter()
    idx["utxos"] = [u for u in idx["utxos"] if not u["live"]]
    idx["live_utxos"] = 0
    idx["live_value"] = 0
    r = get_utxos_by_covenant_id(idx["covenant_id"], txs, idx, network="testnet-10")
    assert r["verification"]["status"] == "failed_closed"


def test_fail_closed_when_indexer_inflates_value_only():
    idx, txs = _counter()
    idx["live_value"] += 1  # single satoshi of phantom value
    r = get_utxos_by_covenant_id(idx["covenant_id"], txs, idx, network="testnet-10")
    assert r["verification"]["status"] == "failed_closed"


def test_fail_closed_when_lineage_incomplete():
    idx, txs = _counter()
    idx["lineage_complete"] = False
    r = get_utxos_by_covenant_id(idx["covenant_id"], txs, idx, network="testnet-10")
    assert r["verification"]["status"] == "failed_closed"


def test_node_truth_answer_stands_without_an_indexer():
    idx, txs = _counter()
    r = get_utxos_by_covenant_id(idx["covenant_id"], txs, indexer=None, network="testnet-10")
    assert r["live_utxo_count"] == 1
    assert r["verification"]["status"] == "unverified_no_indexer"


# ---- commitment is deterministic and recomputable ----------------------------

def test_commitment_is_order_independent_and_stable():
    idx, txs = _counter()
    live = live_utxos_from_node_truth(txs, idx["covenant_id"])
    a = commitment(live)
    b = commitment(list(reversed(live)))
    assert a == b
    assert len(a["value"]) == 64  # sha256 hex


def test_cross_check_none_indexer_is_explicit():
    v = cross_check([], None)
    assert v["status"] == "unverified_no_indexer"
