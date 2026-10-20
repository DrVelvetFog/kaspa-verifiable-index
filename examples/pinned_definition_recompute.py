#!/usr/bin/env python3
"""Pinned-definition recompute v2 (kaspapulse thread, pinned 2026-10-19).

Definition as pinned by @kaspapulse:
  - COUNTED as covenants: recognized templates + single-output covenants
    checkable against the node.
  - Inscriptions: OUT.
  - Unknown script-hash bucket: its OWN line, not folded in.

v2 fixes vs v1:
  - Verification is ADDRESS-level. A Kaspa P2SH address encodes its locking
    script, so every UTXO at the covenant's address is under that script by
    construction; reconstructing the spk from the indexer's scriptHashHex was
    wrong for covenants whose internal hash differs from the address payload.
  - Addresses hosting several covenants (shared script) are verified as an
    AGGREGATE (sum of all census claims at that address vs node truth); the
    per-covenant split there is indexer-claimed, and flagged as such.
  - verified KAS sums exact matches only.
"""
import json, urllib.request, collections, time, sys

UA   = {"User-Agent": "kaspa-verifiable-index/0.1 (pinned-definition-recompute-v2)"}
KC   = "https://indexer.kaspa.com/covenants"
NODE = "https://api.kaspa.org"
KLYT = "https://kaspalytics.com/api/charts/utxo/covenant-count"
OUT  = sys.argv[1] if len(sys.argv) > 1 else "recompute-2026-10-20-pinned-v2.json"

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

# ---- A. KaspaCom FULL census (active=true) -----------------------------------
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
print(f"census {len(covs)} (x-total-count={total})", flush=True)

# ---- B. buckets per the pinned definition --------------------------------------
recognized   = [c for c in covs if c.get("classificationKind") == "covenant"]
inscriptions = [c for c in covs if c.get("classificationKind") == "inscription"]
rest         = [c for c in covs if c.get("classificationKind") not in ("covenant", "inscription")]
single_canonical = [c for c in rest if (c.get("activeUtxos") or 0) == 1 and c.get("canonicalCovenantIdKnown")]
single_fallback  = [c for c in rest if (c.get("activeUtxos") or 0) == 1 and not c.get("canonicalCovenantIdKnown")]
multi_rest       = [c for c in rest if (c.get("activeUtxos") or 0) != 1]

def summ(lst):
    return {"covenants": len(lst),
            "activeUtxos": sum(c.get("activeUtxos") or 0 for c in lst),
            "sompi": sum(c.get("totalAmountSompi") or 0 for c in lst)}

# ---- C. node truth per ADDRESS --------------------------------------------------
# census aggregate per address (ALL census covenants, so shared addresses get a
# complete expectation, even when their co-tenants are outside the checked slices)
addr_claim = collections.defaultdict(lambda: {"utxos": 0, "sompi": 0, "cids": []})
for c in covs:
    a = addr_claim[c["address"]]
    a["utxos"] += c.get("activeUtxos") or 0
    a["sompi"] += c.get("totalAmountSompi") or 0
    a["cids"].append(c.get("covenantIdHex"))

groups = {"recognized": recognized, "single_canonical": single_canonical,
          "single_fallback": single_fallback}
need_addrs = sorted({c["address"] for g in groups.values() for c in g})
print(f"node-checking {len(need_addrs)} addresses "
      f"({sum(len(g) for g in groups.values())} covenants)", flush=True)

node_addr = {}   # address -> {"utxos": n, "sompi": s}
BATCH = 80
for i in range(0, len(need_addrs), BATCH):
    batch = need_addrs[i:i+BATCH]
    for attempt in range(3):
        try:
            utxos = post(f"{NODE}/addresses/utxos", {"addresses": batch})
            break
        except Exception as e:
            print(f"batch {i}: retry {attempt+1} after {e}", flush=True)
            time.sleep(3 * (attempt + 1))
    else:
        print(f"batch {i}: FAILED after retries — aborting (fail closed)", flush=True)
        sys.exit(2)
    for a in batch:
        node_addr.setdefault(a, {"utxos": 0, "sompi": 0})
    for u in utxos:
        rec = node_addr[u["address"]]
        rec["utxos"] += 1
        rec["sompi"] += int(u.get("utxoEntry", {}).get("amount", 0))
    if (i // BATCH) % 10 == 0:
        print(f"checked {min(i+BATCH, len(need_addrs))}/{len(need_addrs)}", flush=True)
    time.sleep(0.15)
checked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

# ---- D. verdicts (fail closed) ---------------------------------------------------
# exclusive address: this covenant is the only census tenant -> direct per-covenant
# shared address: aggregate check; covenant counts as verified-in-aggregate only
verdict = {g: collections.Counter() for g in groups}
exact_sompi = {g: 0 for g in groups}
diverg = {g: [] for g in groups}
shared_checked = {}
for g, lst in groups.items():
    for c in lst:
        addr = c["address"]; claim = addr_claim[addr]; node = node_addr[addr]
        exclusive = len(claim["cids"]) == 1
        ok = (node["utxos"] == claim["utxos"] and node["sompi"] == claim["sompi"])
        if exclusive:
            if ok:
                verdict[g]["exact"] += 1
                exact_sompi[g] += node["sompi"]
            elif node["utxos"] == 0:
                verdict[g]["gone_at_check"] += 1
                diverg[g].append({"cid": c.get("covenantIdHex"), "addr": addr,
                                  "claim": {"utxos": claim["utxos"], "sompi": claim["sompi"]},
                                  "node": node, "why": "address empty at check time"})
            else:
                verdict[g]["mismatch"] += 1
                diverg[g].append({"cid": c.get("covenantIdHex"), "addr": addr,
                                  "claim": {"utxos": claim["utxos"], "sompi": claim["sompi"]},
                                  "node": node, "why": "count/amount differs"})
        else:
            key = "aggregate_ok" if ok else "aggregate_diverges"
            verdict[g][f"shared_script_{key}"] += 1
            if addr not in shared_checked:
                shared_checked[addr] = {"tenants": len(claim["cids"]),
                                        "claim": {"utxos": claim["utxos"], "sompi": claim["sompi"]},
                                        "node": node, "ok": ok}

# ---- E. Kaspalytics context --------------------------------------------------------
klyt_now = klyt_date = None
try:
    _, kl = get(KLYT)
    labels = kl.get("labels") or []
    for ds in (kl.get("datasets") or []):
        if "covenant" in str(ds.get("label", "")).lower():
            data = ds.get("data") or []
            if data:
                klyt_now = int(float(data[-1])); klyt_date = labels[-1] if labels else None
except Exception as e:
    klyt_now = f"error:{e}"

# ---- F. the three pinned lines -------------------------------------------------------
fb_multi    = [c for c in multi_rest if not c.get("canonicalCovenantIdKnown")]
canon_multi = [c for c in multi_rest if c.get("canonicalCovenantIdKnown")]
report = {
    "pinned_definition": "count = recognized templates + single-output covenants checkable "
                         "against the node; inscriptions out; unknown script-hash bucket on "
                         "its own line",
    "census_at": census_at, "checked_at": checked_at,
    "kaspacom_total": {"covenants": len(covs),
                       "activeUtxos": sum(c.get("activeUtxos") or 0 for c in covs)},
    "LINE1_counted_covenants": {
        "definition": "recognized templates + single-output covenants with canonical "
                      "covenant IDs; every one checked against node truth at its address "
                      "(P2SH address == locking script), fail closed",
        "in_slice_at_census": len(recognized) + len(single_canonical),
        "recognized": dict(verdict["recognized"]),
        "single_canonical": dict(verdict["single_canonical"]),
        "node_verified_exact": verdict["recognized"]["exact"] + verdict["single_canonical"]["exact"],
        "verified_kas_exact_only": (exact_sompi["recognized"] + exact_sompi["single_canonical"]) / 1e8,
    },
    "LINE2_inscriptions_excluded": {**summ(inscriptions),
        "protocols": dict(collections.Counter(c.get("inscriptionProtocol") or "unspecified"
                                              for c in inscriptions))},
    "LINE3_scripthash_bucket": {
        "definition": "covenants with NO canonical covenant ID (identitySource="
                      "scriptHashFallback) — own line, never folded into line 1",
        "single_output": {"in_slice_at_census": len(single_fallback),
                          **dict(verdict["single_fallback"]),
                          "verified_kas_exact_only": exact_sompi["single_fallback"] / 1e8},
        "multi_output_not_checked_per_covenant": summ(fb_multi),
    },
    "REMAINDER_multi_output_canonical_not_checked_per_covenant": summ(canon_multi),
    "shared_script_addresses": {a: v for a, v in sorted(shared_checked.items(),
                                                        key=lambda kv: -kv[1]["tenants"])[:10]},
    "shared_script_addresses_total": len(shared_checked),
    "divergences": {g: v for g, v in diverg.items() if v},
    "kaspalytics_context": {"covenant_utxos_now": klyt_now, "as_of": klyt_date},
}
print(json.dumps(report, indent=2, default=str))
json.dump(report, open(OUT, "w"), indent=2, default=str)
print("wrote", OUT, flush=True)
