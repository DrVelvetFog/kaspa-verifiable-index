#!/usr/bin/env python3
"""Answer kaspapulse's two asks (thread, 2026-10-21/22):
1. The 87: line 2 said "811 outputs — 464 KRC-721, 201 KNS, 59 KRC-20";
   the split counted COVENANTS (724), the 811 counted OUTPUTS. The extra
   87 outputs sit in inscription covenants holding >1 unspent output.
   -> produce per-protocol covenant AND output counts + the multi-output list.
2. Shared-script ID list: addresses hosting >=2 census covenants, with
   tenant covenant IDs + per-tenant claims + node-truth aggregate, to diff
   against their 115 ids / 2,058 outputs / 50 shared scripts.
Fresh census (the indexer is live-only; 10-20 census can't be re-queried),
node truth from api.kaspa.org, fail closed on fetch errors.
"""
import json, urllib.request, collections, time, sys

UA   = {"User-Agent": "kaspa-verifiable-index/0.1 (kaspapulse-87-and-shared-scripts)"}
KC   = "https://indexer.kaspa.com/covenants"
NODE = "https://api.kaspa.org"
OUT  = sys.argv[1] if len(sys.argv) > 1 else "shared_scripts_2026-10-22.json"

def get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return dict(r.headers), json.loads(r.read())

def post(url, payload, timeout=120):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={**UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

# ---- full active census ----
covs, offset, total = [], 0, None
while True:
    hdrs, body = get(f"{KC}?active=true&limit=500&offset={offset}")
    if total is None:
        total = int(hdrs.get("x-total-count") or hdrs.get("X-Total-Count") or 0)
    if not body:
        break
    covs.extend(body); offset += len(body)
    if total and offset >= total:
        break
    time.sleep(0.08)
census_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
print(f"census {len(covs)} rows (x-total-count={total}) at {census_at}", flush=True)

# ---- 1. inscriptions: covenants vs outputs per protocol ----
insc = [c for c in covs if c.get("classificationKind") == "inscription"]
per_proto = collections.defaultdict(lambda: {"covenants": 0, "outputs": 0})
multi = []
for c in insc:
    p = c.get("inscriptionProtocol") or "unspecified"
    n = c.get("activeUtxos") or 0
    per_proto[p]["covenants"] += 1
    per_proto[p]["outputs"] += n
    if n > 1:
        multi.append({"covenantIdHex": c.get("covenantIdHex"),
                      "protocol": p, "activeUtxos": n,
                      "address": c.get("address"),
                      "canonicalCovenantIdKnown": bool(c.get("canonicalCovenantIdKnown"))})
multi.sort(key=lambda m: -m["activeUtxos"])
tot_cov = sum(v["covenants"] for v in per_proto.values())
tot_out = sum(v["outputs"] for v in per_proto.values())
print(f"inscriptions: {tot_cov} covenants / {tot_out} outputs; "
      f"{len(multi)} multi-output covenants carry {sum(m['activeUtxos']-1 for m in multi)} extra outputs", flush=True)
for p, v in sorted(per_proto.items()):
    print(f"  {p}: {v['covenants']} covenants / {v['outputs']} outputs", flush=True)

# ---- 2. shared scripts: addresses with >=2 census tenants ----
by_addr = collections.defaultdict(list)
for c in covs:
    by_addr[c["address"]].append(c)
shared_addrs = {a: lst for a, lst in by_addr.items() if len(lst) >= 2}
need = sorted(shared_addrs)
print(f"shared scripts: {len(need)} addresses host >=2 covenants", flush=True)

node_addr = {}
BATCH = 80
for i in range(0, len(need), BATCH):
    batch = need[i:i+BATCH]
    for attempt in range(3):
        try:
            utxos = post(f"{NODE}/addresses/utxos", {"addresses": batch})
            break
        except Exception as e:
            print(f"batch {i}: retry {attempt+1} after {e}", flush=True)
            time.sleep(3 * (attempt + 1))
    else:
        print("node batch FAILED after retries — aborting (fail closed)", flush=True)
        sys.exit(2)
    for a in batch:
        node_addr.setdefault(a, {"utxos": 0, "sompi": 0})
    for u in utxos:
        rec = node_addr[u["address"]]
        rec["utxos"] += 1
        rec["sompi"] += int(u.get("utxoEntry", {}).get("amount", 0))
    time.sleep(0.15)
checked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

shared = []
for a, lst in shared_addrs.items():
    claim_u = sum(c.get("activeUtxos") or 0 for c in lst)
    claim_s = sum(c.get("totalAmountSompi") or 0 for c in lst)
    node = node_addr[a]
    shared.append({
        "address": a,
        "tenants": len(lst),
        "claimed_outputs": claim_u,
        "claimed_sompi": claim_s,
        "node_utxos": node["utxos"],
        "node_sompi": node["sompi"],
        "aggregate_matches_node": node["utxos"] == claim_u and node["sompi"] == claim_s,
        "tenant_covenant_ids": [
            {"covenantIdHex": c.get("covenantIdHex"),
             "canonicalCovenantIdKnown": bool(c.get("canonicalCovenantIdKnown")),
             "classificationKind": c.get("classificationKind"),
             "activeUtxos": c.get("activeUtxos") or 0,
             "totalAmountSompi": c.get("totalAmountSompi") or 0}
            for c in sorted(lst, key=lambda c: -(c.get("activeUtxos") or 0))],
    })
shared.sort(key=lambda s: -s["tenants"])
n_ids = sum(s["tenants"] for s in shared)
n_out = sum(s["claimed_outputs"] for s in shared)
print(f"shared-script totals: {len(shared)} scripts / {n_ids} covenant ids / "
      f"{n_out} claimed outputs (their split: 50 / 115 / 2,058)", flush=True)

report = {
    "purpose": "kaspapulse thread follow-up: the 87-output question on line 2, and the shared-script ID list",
    "census_at": census_at, "checked_at": checked_at,
    "note_on_timing": "the 10-20 posted lines came from the 2026-10-20T11:32:29Z census; "
                      "the indexer serves only current state, so this file is a fresh census — "
                      "numbers can differ slightly from the posted lines",
    "the_87_explained": {
        "posted_line2": "811 outputs — 464 KRC-721, 201 KNS, 59 KRC-20 (census 2026-10-20T11:32:29Z)",
        "resolution": "the protocol split counted COVENANTS (724), the 811 counted OUTPUTS; "
                      "the other 87 outputs sit in inscription covenants holding more than one "
                      "unspent output. All labels are indexer-classified (KaspaCom), "
                      "classified-not-verified until spend.",
        "fresh_census_per_protocol": {p: v for p, v in sorted(per_proto.items())},
        "fresh_totals": {"covenants": tot_cov, "outputs": tot_out,
                         "multi_output_covenants": len(multi),
                         "extra_outputs_beyond_one_each": sum(m["activeUtxos"] - 1 for m in multi)},
        "multi_output_inscription_covenants": multi,
    },
    "shared_scripts": {
        "definition": "census addresses hosting >=2 covenant IDs; a Kaspa P2SH address IS its "
                      "locking script, so tenants share one script and the node can only verify "
                      "the aggregate — the per-tenant split is the indexer's claim",
        "totals": {"scripts": len(shared), "covenant_ids": n_ids, "claimed_outputs": n_out},
        "kaspapulse_split_for_diff": {"scripts": 50, "covenant_ids": 115, "outputs": 2058},
        "list": shared,
    },
}
json.dump(report, open(OUT, "w"), indent=2)
print("wrote", OUT, flush=True)
