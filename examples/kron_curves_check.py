"""Kron bonding curves: KaspaCom covenant indexer vs node truth (api.kaspa.org).

Read-only. For each curve Kron lists, compare the indexer's live outputs
(paged) with the UTXOs the node reports at the covenant's address. The indexer
files some older covenants under their script hash rather than their covenant
ID, so a covenant-ID miss falls back to the script hash of the curve output in
the genesis transaction.

    python3 examples/kron_curves_check.py out.json
"""
import json, time, urllib.request, urllib.error, sys
from collections import Counter, defaultdict
def get(u, ok404=False):
    for i in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "kaspa-verifiable-index"}), timeout=40) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404 and ok404: return None
            err = e
        except Exception as e: err = e
        time.sleep(2 + i)
    raise err
def indexer_actions(cov):
    acts, off = [], 0
    while True:
        d = get(f"https://indexer.kaspa.com/covenants/{cov}?limit=100&offset={off}", ok404=True)
        if d is None: return None
        page = d.get("actions") or []
        acts += page
        tot = d.get("actionsTotal", len(acts))
        if not page or len(acts) >= tot: return acts
        off += len(page)
def node_utxos(addr):
    return sorted((u["outpoint"]["transactionId"], int(u["outpoint"]["index"]), int(u["utxoEntry"]["amount"])) for u in get(f"https://api.kaspa.org/addresses/{addr}/utxos"))
toks = get("https://api.kron.technology/api/registry/tokens")["tokens"]
out = []
for t in toks:
    cp = t.get("cp") or {}; cov = cp.get("curveCovid"); schema = ((cp.get("templateVersion") or {}).get("schema") or "?")[:8]
    if not cov: out.append(dict(tick=t["tick"], status="no curve id")); continue
    acts = indexer_actions(cov)
    key = "covenantId"
    if acts is None:
        tx = get(f"https://api.kaspa.org/transactions/{cp['genesisTxid']}?inputs=false&outputs=true")
        o = next((o for o in tx.get("outputs") or [] if o.get("covenant_id") == cov), None)
        if not o: out.append(dict(tick=t["tick"], schema=schema, status="indexer 404, not found on chain either")); continue
        spk = o["script_public_key"]
        acts = indexer_actions(spk[4:-2] if spk.startswith("aa20") else spk)
        key = "scriptHash"
        if acts is None:
            node = node_utxos(o["script_public_key_address"])
            out.append(dict(tick=t["tick"], schema=schema, status="MISSING from indexer", chain_sompi=sum(x[2] for x in node)))
            continue
    spent = {(a["inputs"]["previousOutpoint"]["hash"], int(a["inputs"]["previousOutpoint"]["index"])) for a in acts if isinstance(a.get("inputs"), dict) and a["inputs"].get("previousOutpoint")}
    live = sorted((a["txidHex"], int(a["outputs"]["vout"]), int(a["outputs"]["amountSompi"])) for a in acts if isinstance(a.get("outputs"), dict) and a["outputs"].get("amountSompi") is not None and a.get("action") in ("continuation", "deploy"))
    live = [x for x in live if (x[0], x[1]) not in spent]
    addrs = {a["address"] for a in acts if a.get("address")}
    node = sorted(x for ad in addrs for x in node_utxos(ad))
    out.append(dict(tick=t["tick"], schema=schema, key=key, status="agree" if live == node else "DISAGREE", idx_sompi=sum(x[2] for x in live), chain_sompi=sum(x[2] for x in node), actions=len(acts), created=t.get("createdAt","")[:10], graduated=t.get("graduated")))
    time.sleep(0.2)
json.dump(out, open(sys.argv[1], "w"), indent=1)
print(Counter(o["status"] for o in out))
by = defaultdict(Counter)
for o in out: by[o.get("schema")][o["status"]] += 1
for k, v in by.items(): print("schema", k, dict(v))
for o in out:
    if o["status"] not in ("agree",): print(o)
