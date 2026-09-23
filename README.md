# kaspa-verifiable-index

A verifiable answer to `getUtxosByCovenantId` on Kaspa — computed from node
truth, with any third-party covenant indexer treated as an untrusted input that
is cross-checked and **fails closed on disagreement**.

## Why

Kaspa nodes prune to ~30 hours and expose no covenant-scoped UTXO lookup
([rusty-kaspa#1128](https://github.com/kaspanet/rusty-kaspa/issues/1128)), so
integrators — wallets, bridges, exchanges — depend on third-party covenant
indexers (kascov, Kasplex-style) whose output they cannot check. On
2026-09-20 the Kasplex KRC-20 indexer was drained through exactly that trust
gap: L1 was fine, the indexer's accounting was not.

`covenant_id` is a first-class field on every UTXO
(`kaspa_consensus_core::utxo::UtxoEntry.covenant_id: Option<Hash>`). So the
answer to "which UTXOs are bound to this covenant" is recomputable from the
accepted transactions alone: a covenant output is *live* iff no accepted
transaction consumes its outpoint. This tool computes that, and uses any
indexer's document only as a claim to verify against node truth.

## What it does

- **Answers `getUtxosByCovenantId` from node truth** — the #1128 endpoint,
  computed by replaying accepted transactions, not by trusting an indexer.
- **Cross-checks a third-party indexer and fails closed** — if the indexer
  reports a live UTXO node truth says is spent (the drain shape), omits one it
  has, inflates value, or reports incomplete lineage, the verdict is
  `failed_closed` and the CLI exits non-zero.
- **Emits a recomputable commitment** — a SHA-256 over the canonical live set
  that anyone holding the same node-truth transactions can reproduce, so the
  answer is checkable offline without trusting this tool.

## Use

```bash
# a real TN10 covenant: node truth agrees with the indexer -> verified
PYTHONPATH=src python -m kaspa_verifiable_index --fixture tests/fixtures/tn10-counter

# node truth answers even with no indexer to check
PYTHONPATH=src python -m kaspa_verifiable_index \
  --covenant-id <hex> --rest node-rest.json
```

`--rest` is the node/REST transaction view (accepted txs with covenant outputs);
`--indexer` is an optional kascov-shape document to cross-check. The process
exits `2` when the cross-check fails closed.

## Tests

```bash
python -m venv .venv && .venv/bin/pip install pytest
PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
```

Eight real TN10 covenants (counter, escrow-2of3, HTLC and variants, swap,
vault) replay from node truth and agree with the production indexer exactly.
Negative controls reproduce the drain shape — a spent UTXO claimed live, an
omitted live UTXO, inflated value, incomplete lineage — and each must fail
closed.

## Watchdog (v0)

`watch.py` runs the check continuously and fires only when a divergence *persists*.
A live indexer legitimately lags the node by a few blocks, so a single disagreement
is not yet an alarm — lag and an attack look identical in one snapshot. The
discriminator is time: lag self-heals within a few cycles; a drain (inflated or
forged state) never reconciles, because node truth never catches up to it.

```python
from kaspa_verifiable_index import CovenantWatcher, Snapshot, run_watch

w = CovenantWatcher(persist_window=3)   # calibrate to the indexer's normal lag
for snap in feed:                       # a poller in production; a sequence in tests
    event = w.observe(snap)
    if event:                           # "alert" (persistent divergence) or "cleared"
        notify(event)                   # observe-only: never touches funds or the chain
```

```bash
PYTHONPATH=src python -m kaspa_verifiable_index.watch   # lag (no alert) + drain (one alert) demo
```

`persist_window` is the whole design: a window of 1 can't tell lag from a drain and
alerts on both (a documented negative-control test). Calibrate it against how many
blocks the watched indexer normally trails. This v0 is driven by replayed sequences;
the live-node feed is the next piece.

## Live feed & calibration

`feed.py` pulls node truth from the Kaspa REST API (`api.kaspa.org` /
`api-tn10.kaspa.org`). The REST `full-transactions` shape is the same one the engine
consumes — outputs carry `covenant_id`, inputs carry `previous_outpoint_hash` /
`previous_outpoint_index` — so live data drops straight in. There is no covenant
endpoint (rusty-kaspa#1128), so a covenant's node truth is reconstructed from its
holding addresses' transactions.

```bash
PYTHONPATH=src python -m kaspa_verifiable_index.feed            # live smoke on mainnet
```

`calibrate.py` turns observed lag into a `persist_window`: the longest divergence run
that self-healed (= indexer lag), plus a margin. An unresolved run at the series end
is a possible drain, not lag, and never inflates the window.

```python
recommend_persist_window(diverged_series)   # -> Calibration(recommended_window=…, note=…)
```

**What's live and what's blocked (honest).** The node-truth feed is real: the smoke
pulls live mainnet state and reconstructs covenant state from the same fields the
tests use. Two things gate an *end-to-end live covenant watch*, and neither is a code
problem:

- **Live covenant activity is rare.** A recent-window scan of mainnet finds no
  covenant outputs today; testnet-10 was reset, so the rehearsal covenants are gone.
- **No public covenant-queryable indexer.** `kascov`'s data API isn't public and
  KaspaCom's serves KRC-721, not covenants — so there is nothing to cross-check against
  yet, which means real indexer-lag can't be measured to set `persist_window`.

The watchtower's brain (check, persistence gate, calibration) and its node-truth eye
are done and tested; it lights up the moment there's a live covenant and one indexer
to compare against.

## Scope & honesty

- **Node truth here is a REST/replay snapshot.** In production the same replay
  runs against a `--utxoindex` node (or a set of them, failing closed on
  disagreement); this repo demonstrates the computation on committed fixtures.
- **The commitment binds the answer to its inputs, not to a consensus root.**
  It lets a second party recompute the same live set from the same transactions;
  it is not a Merkle proof against the node's UTXO-set commitment. That is the
  next layer.
- The TN10 covenant fixtures under `tests/fixtures/` are from
  [explife365/kaspa-frontier-engine](https://github.com/explife365/kaspa-frontier-engine)
  (the integrator rehearsal behind #1128), used here as test vectors.

## License

MIT
