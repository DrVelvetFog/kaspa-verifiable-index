"""Live node-truth feed over the Kaspa REST API (api.kaspa.org / api-tn10.kaspa.org).

The REST `full-transactions` endpoint returns transactions in the same shape the
engine already consumes — outputs carry `covenant_id`, `amount`,
`script_public_key_type`, `covenant_authorizing_input`, and inputs carry
`previous_outpoint_hash` / `previous_outpoint_index` — so live data drops straight
into `live_utxos_from_node_truth` with no mapping. There is no covenant-scoped
endpoint (rusty-kaspa#1128), so a covenant's node truth is reconstructed from the
transactions of the addresses that hold it, exactly the integrator path #1128 asks
about.

Read-only, stdlib-only (urllib). This is the poller half of the watchdog; the engine
and the persistence gate are unchanged.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from .index import get_utxos_by_covenant_id, live_utxos_from_node_truth

MAINNET = "https://api.kaspa.org"
TESTNET10 = "https://api-tn10.kaspa.org"


class KaspaRestFeed:
    def __init__(self, base_url: str = MAINNET, timeout: int = 20):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str, params: dict | None = None):
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "kaspa-verifiable-index/0.1"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read())

    def network_info(self) -> dict:
        return self._get("/info/network")

    def full_transactions(self, address: str, limit: int = 50) -> list:
        """Accepted transactions touching `address`, already in the engine's shape."""
        return self._get(
            f"/addresses/{address}/full-transactions",
            {"limit": limit, "resolve_previous_outpoints": "light"},
        )

    def utxos(self, address: str) -> list:
        """The indexer-style live UTXO set the REST server maintains for `address`."""
        return self._get(f"/addresses/{address}/utxos")


def live_utxos_by_covenant_id(feed: KaspaRestFeed, covenant_id: str, addresses: list[str],
                              indexer: dict | None = None) -> dict:
    """Reconstruct a covenant's live set from node truth across its holding addresses,
    de-duplicating shared transactions, then run the verifiable check."""
    seen, txs = set(), []
    for addr in addresses:
        for tx in feed.full_transactions(addr):
            tid = tx.get("transaction_id")
            if tid and tid not in seen:
                seen.add(tid)
                txs.append(tx)
    network = feed.network_info().get("networkName", "unknown")
    return get_utxos_by_covenant_id(covenant_id, txs, indexer, network=network)


def scan_recent_for_covenants(feed: KaspaRestFeed, blocks: int = 20) -> list[str]:
    """Walk back from the tips looking for any output that carries a covenant_id.
    Returns the distinct covenant_ids seen — usually empty on mainnet today, which is
    itself the finding: live covenant activity is still rare."""
    info = feed.network_info()
    frontier = list(info.get("tipHashes", []))[:1]
    seen_blocks, covenants = set(), set()
    while frontier and len(seen_blocks) < blocks:
        h = frontier.pop()
        if h in seen_blocks:
            continue
        seen_blocks.add(h)
        try:
            blk = feed._get(f"/blocks/{h}", {"includeTransactions": "true"})
        except urllib.error.HTTPError:
            continue
        for t in blk.get("transactions", []):
            for o in t.get("outputs", []):
                cid = o.get("covenant") or o.get("covenant_id")
                if cid:
                    covenants.add(cid if isinstance(cid, str) else json.dumps(cid))
        parents = ((blk.get("verboseData") or {}).get("mergeSetBluesHashes") or [])
        frontier.extend(parents[:2])
    return sorted(covenants)


def poll_watch(watcher, snapshot_fn, cycles: int, on_event=None):
    """Drive a CovenantWatcher for `cycles` iterations. `snapshot_fn(i)` returns the
    Snapshot for cycle i (it does the live fetching); the caller controls the sleep so
    this stays testable. Returns the events fired."""
    events = []
    for i in range(cycles):
        snap = snapshot_fn(i)
        ev = watcher.observe(snap)
        if ev is not None:
            events.append(ev)
            if on_event:
                on_event(ev)
    return events


# --------------------------------------------------------------------------
# Live smoke: prove the feed talks to real Kaspa, and report whether any live
# covenant activity exists to watch right now.  `python -m kaspa_verifiable_index.feed`
# --------------------------------------------------------------------------

def _smoke(base_url: str = MAINNET):
    feed = KaspaRestFeed(base_url)
    info = feed.network_info()
    print(f"network: {info.get('networkName')}  blockCount={info.get('blockCount')}  "
          f"tips={len(info.get('tipHashes', []))}")

    covs = scan_recent_for_covenants(feed, blocks=20)
    if covs:
        print(f"live covenants seen in the recent window: {len(covs)}")
        for c in covs[:5]:
            print(f"  {c}")
        print("→ a real covenant is watchable; supply its holding addresses to "
              "live_utxos_by_covenant_id and add an indexer claim to compare.")
    else:
        print("live covenants seen in the recent window: 0")
        print("→ covenant activity is still rare on this network; the node-truth feed "
              "works, but an end-to-end live covenant watch needs an active covenant "
              "and a covenant-queryable indexer (neither is publicly available today).")


if __name__ == "__main__":
    import sys
    _smoke(TESTNET10 if "--testnet" in sys.argv else MAINNET)
