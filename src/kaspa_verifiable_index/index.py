"""Verifiable getUtxosByCovenantId over Kaspa node truth.

Kaspa nodes prune to ~30h and expose no covenant-scoped UTXO lookup
(rusty-kaspa#1128), so integrators depend on third-party covenant indexers
(kascov, Kasplex-style) whose output they cannot check. The Kasplex KRC-20
indexer was drained on 2026-09-20 through exactly that trust gap.

This module answers getUtxosByCovenantId from *node truth* — the accepted
transactions themselves — and treats any third-party indexer as an untrusted
input to cross-check, failing closed when it disagrees.

Node truth: covenant_id is a first-class field on every UTXO
(kaspa_consensus_core::utxo::UtxoEntry.covenant_id: Option<Hash>). A covenant
output is *live* iff no accepted transaction consumes its outpoint. That is the
whole computation; everything else here is the proof envelope around it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class Utxo:
    outpoint: str  # "transaction_id:output_index"
    amount: int
    script_public_key_type: str
    covenant_authorizing_input: int | None


def _outpoint(txid: str, index: int) -> str:
    return f"{txid}:{index}"


def live_utxos_from_node_truth(transactions: list[dict], covenant_id: str) -> list[Utxo]:
    """Replay accepted transactions and return the live UTXOs bound to covenant_id.

    `transactions` is the node/REST view: each has transaction_id, is_accepted,
    inputs[{previous_outpoint_hash, previous_outpoint_index}], and
    outputs[{covenant_id, amount, ...}] positional by output index.
    """
    consumed: set[str] = set()
    candidates: dict[str, Utxo] = {}

    for tx in transactions:
        if not tx.get("is_accepted", False):
            continue  # rejected txs never touch the UTXO set
        for inp in tx.get("inputs", []):
            consumed.add(_outpoint(inp["previous_outpoint_hash"], int(inp["previous_outpoint_index"])))
        for idx, out in enumerate(tx.get("outputs", [])):
            if out.get("covenant_id") != covenant_id:
                continue
            op = _outpoint(tx["transaction_id"], idx)
            candidates[op] = Utxo(
                outpoint=op,
                amount=int(out["amount"]),
                script_public_key_type=out.get("script_public_key_type", ""),
                covenant_authorizing_input=out.get("covenant_authorizing_input"),
            )

    live = [u for op, u in candidates.items() if op not in consumed]
    live.sort(key=lambda u: u.outpoint)
    return live


def commitment(utxos: list[Utxo]) -> dict:
    """A recomputable commitment over the live set.

    Anyone holding the same node-truth transactions can reproduce this exact
    digest, so the endpoint's answer is checkable offline without trusting us.
    """
    canonical = "\n".join(f"{u.outpoint} {u.amount}" for u in sorted(utxos, key=lambda u: u.outpoint))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return {"alg": "sha256", "value": digest, "canonical_lines": len(utxos)}


def _indexer_live(indexer: dict) -> dict:
    """Reduce a kascov-style indexer document to its live claim."""
    live = [u for u in indexer.get("utxos", []) if u.get("live")]
    return {
        "count": indexer.get("live_utxos", len(live)),
        "value": indexer.get("live_value", sum(u.get("value", 0) for u in live)),
        "outpoints": sorted(u["outpoint"] for u in live),
        "lineage_complete": bool(indexer.get("lineage_complete", False)),
        "status": indexer.get("status"),
    }


def cross_check(live: list[Utxo], indexer: dict | None) -> dict:
    """Compare node truth against the third-party indexer. Fail closed on any gap."""
    node = {
        "count": len(live),
        "value": sum(u.amount for u in live),
        "outpoints": sorted(u.outpoint for u in live),
    }
    if indexer is None:
        return {"indexer": None, "node_truth": node, "status": "unverified_no_indexer",
                "note": "No indexer supplied; node-truth answer stands on its own."}

    claim = _indexer_live(indexer)
    discrepancies = []
    if claim["count"] != node["count"]:
        discrepancies.append(f"live count: node={node['count']} indexer={claim['count']}")
    if claim["value"] != node["value"]:
        discrepancies.append(f"live value: node={node['value']} indexer={claim['value']}")
    if set(claim["outpoints"]) != set(node["outpoints"]):
        only_node = sorted(set(node["outpoints"]) - set(claim["outpoints"]))
        only_idx = sorted(set(claim["outpoints"]) - set(node["outpoints"]))
        if only_node:
            discrepancies.append(f"indexer omits live outpoints node truth has: {only_node}")
        if only_idx:
            discrepancies.append(f"indexer reports live outpoints node truth does not: {only_idx}")
    if not claim["lineage_complete"]:
        discrepancies.append("indexer reports lineage_complete=false")

    agrees = not discrepancies
    return {
        "indexer": "kascov",
        "node_truth": node,
        "indexer_claim": {"count": claim["count"], "value": claim["value"]},
        "agrees": agrees,
        "lineage_complete": claim["lineage_complete"],
        "discrepancies": discrepancies,
        # fail closed: anything short of full agreement is not "verified"
        "status": "verified" if agrees else "failed_closed",
    }


def get_utxos_by_covenant_id(
    covenant_id: str,
    transactions: list[dict],
    indexer: dict | None = None,
    network: str = "unknown",
) -> dict:
    """The endpoint. Node truth answers; the indexer cross-check is the trust signal."""
    live = live_utxos_from_node_truth(transactions, covenant_id)
    return {
        "covenant_id": covenant_id,
        "network": network,
        "source": {"kind": "node-truth-replay", "accepted_txs": sum(1 for t in transactions if t.get("is_accepted"))},
        "live_utxos": [u.__dict__ for u in live],
        "live_utxo_count": len(live),
        "live_value": sum(u.amount for u in live),
        "commitment": commitment(live),
        "verification": cross_check(live, indexer),
    }
