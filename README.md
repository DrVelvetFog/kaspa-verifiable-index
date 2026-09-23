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
