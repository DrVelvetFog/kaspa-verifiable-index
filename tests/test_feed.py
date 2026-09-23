"""The live REST feed must hand the engine data in exactly the shape it already
consumes. Tested against a captured real api.kaspa.org payload (no network in tests)
and a covenant lineage in the same REST field shape.
"""

import json
import os

from kaspa_verifiable_index.index import get_utxos_by_covenant_id, live_utxos_from_node_truth

FX = os.path.join(os.path.dirname(__file__), "fixtures")


def test_engine_runs_on_captured_live_rest_payload():
    txs = json.load(open(f"{FX}/live-mainnet-full-transactions.json"))
    # the real REST full-transactions shape is exactly what the engine reads
    assert all("is_accepted" in t and "transaction_id" in t for t in txs)
    assert all("covenant_id" in o for t in txs for o in t["outputs"])
    # a covenant not present resolves to an empty live set without error
    r = get_utxos_by_covenant_id("de" * 32, txs, indexer=None, network="kaspa-mainnet")
    assert r["live_utxo_count"] == 0
    assert r["verification"]["status"] == "unverified_no_indexer"


def test_covenant_reconstruction_on_rest_field_shape():
    # a genesis -> transition lineage using the REST field names (index as int,
    # previous_outpoint_hash/index, covenant_id on outputs) verified from openapi
    cid = "ab" * 32
    txs = [
        {"transaction_id": "11" * 32, "is_accepted": True,
         "inputs": [{"previous_outpoint_hash": "00" * 32, "previous_outpoint_index": 0}],
         "outputs": [{"covenant_id": cid, "amount": 1000, "script_public_key_type": "scripthash",
                      "covenant_authorizing_input": 0}]},
        {"transaction_id": "22" * 32, "is_accepted": True,
         "inputs": [{"previous_outpoint_hash": "11" * 32, "previous_outpoint_index": 0, "covenant_id": cid}],
         "outputs": [{"covenant_id": cid, "amount": 990, "script_public_key_type": "scripthash",
                      "covenant_authorizing_input": 0}]},
    ]
    live = live_utxos_from_node_truth(txs, cid)
    assert [u.outpoint for u in live] == ["22" * 32 + ":0"]
    assert live[0].amount == 990
