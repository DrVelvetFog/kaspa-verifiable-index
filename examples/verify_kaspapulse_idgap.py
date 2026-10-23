#!/usr/bin/env python3
"""Verify kaspapulse's 10-23 claims about the 33 shared scripts:
  (a) only ~103 of the claimed outputs carry an on-chain covenant_id,
  (b) under 100 distinct on-chain ids,
  (c) 13 of the 33 scripts carry none,
  (d) 1,127 of the 1,173 census tenant ids have no live output with that id
      on their script.
Method: node truth via api.kaspa.org — utxos per script address, then each
UTXO's creating tx's output.covenant_id. Fail closed on fetch errors.
"""
import json, time, urllib.request, collections, sys

UA = {"User-Agent": "kaspa-verifiable-index/0.1 (idgap-verify)"}
NODE = "https://api.kaspa.org"
SRC = "examples/shared_scripts_2026-10-22.json"
OUT = sys.argv[1] if len(sys.argv) > 1 else "idgap_2026-10-23.json"

def get(url, timeout=60, tries=3):
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception as e:
            if a == tries - 1:
                raise
            time.sleep(2 * (a + 1))

def post(url, payload, timeout=120, tries=3):
    for a in range(tries):
        try:
            body = json.dumps(payload).encode()
            req = urllib.request.Request(url, data=body, headers={**UA, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception as e:
            if a == tries - 1:
                raise
            time.sleep(2 * (a + 1))

shared = json.load(open(SRC))["shared_scripts"]["list"]
addrs = [s["address"] for s in shared]
census_ids_by_addr = {s["address"]: {t["covenantIdHex"].lower() for t in s["tenant_covenant_ids"] if t.get("covenantIdHex")} for s in shared}
total_census_ids = sum(len(v) for v in census_ids_by_addr.values())
print(f"{len(addrs)} scripts, {total_census_ids} census tenant ids", flush=True)

# 1. utxos per address
utxos_by_addr = {}
for i in range(0, len(addrs), 40):
    batch = addrs[i:i+40]
    res = post(f"{NODE}/addresses/utxos", {"addresses": batch})
    for a in batch:
        utxos_by_addr.setdefault(a, [])
    for u in res:
        utxos_by_addr[u["address"]].append((u["outpoint"]["transactionId"], int(u["outpoint"]["index"])))
    time.sleep(0.2)
snap_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
n_utxos = sum(len(v) for v in utxos_by_addr.values())
txids = sorted({t for v in utxos_by_addr.values() for (t, _) in v})
print(f"{n_utxos} live utxos across the 33 scripts, {len(txids)} distinct creating txs, snapshot {snap_at}", flush=True)

# 2. covenant_id per output, from each creating tx
cov = {}  # (txid, index) -> covenant_id or None
for n, tx in enumerate(txids):
    t = get(f"{NODE}/transactions/{tx}?inputs=false&outputs=true")
    for o in t.get("outputs") or []:
        cov[(tx, int(o["index"]))] = (o.get("covenant_id") or None)
    if n % 50 == 0:
        print(f"tx {n}/{len(txids)}", flush=True)
    time.sleep(0.12)

# 3. aggregate
per_script = {}
onchain_ids_all = set()
outputs_with_id = 0
for a, ups in utxos_by_addr.items():
    ids_here = set()
    with_id = 0
    for key in ups:
        cid = cov.get(key)
        if cid:
            with_id += 1
            ids_here.add(cid.lower())
    outputs_with_id += with_id
    onchain_ids_all |= ids_here
    census = census_ids_by_addr[a]
    per_script[a] = {
        "live_utxos": len(ups),
        "outputs_with_onchain_id": with_id,
        "distinct_onchain_ids": len(ids_here),
        "census_tenant_ids": len(census),
        "census_ids_alive_onchain": len(census & ids_here),
    }

scripts_with_none = sum(1 for v in per_script.values() if v["outputs_with_onchain_id"] == 0)
census_alive = sum(v["census_ids_alive_onchain"] for v in per_script.values())
report = {
    "snapshot_at": snap_at,
    "scripts": len(addrs),
    "live_utxos_total": n_utxos,
    "outputs_with_onchain_covenant_id": outputs_with_id,
    "distinct_onchain_ids": len(onchain_ids_all),
    "scripts_with_zero_id_outputs": scripts_with_none,
    "census_tenant_ids_total": total_census_ids,
    "census_ids_with_live_onchain_output_on_their_script": census_alive,
    "census_ids_WITHOUT": total_census_ids - census_alive,
    "kaspapulse_claims": {"outputs_with_id": 103, "distinct_ids": "<100", "scripts_with_none": 13, "ids_without": 1127, "of_ids": 1173},
    "per_script": per_script,
}
json.dump(report, open(OUT, "w"), indent=2)
print(json.dumps({k: report[k] for k in report if k != "per_script"}, indent=1), flush=True)
print("wrote", OUT, flush=True)
