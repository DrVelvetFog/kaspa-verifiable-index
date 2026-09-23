"""CLI: answer getUtxosByCovenantId from node truth and cross-check an indexer.

    python -m kaspa_verifiable_index --fixture tests/fixtures/tn10-counter
    python -m kaspa_verifiable_index --covenant-id <hex> --rest rest.json [--indexer kascov.json]
"""

import argparse
import json
import sys

from .index import get_utxos_by_covenant_id


def main(argv=None):
    p = argparse.ArgumentParser(prog="kaspa_verifiable_index")
    p.add_argument("--fixture", help="base path of a <base>-rest.json (+ optional <base>-kascov.json)")
    p.add_argument("--covenant-id", help="32-byte covenant id (hex)")
    p.add_argument("--rest", help="node/REST transactions JSON (list)")
    p.add_argument("--indexer", help="third-party indexer JSON (kascov shape) to cross-check")
    args = p.parse_args(argv)

    if args.fixture:
        rest_path = f"{args.fixture}-rest.json"
        indexer_path = f"{args.fixture}-kascov.json"
        txs = json.load(open(rest_path))
        try:
            indexer = json.load(open(indexer_path))
        except FileNotFoundError:
            indexer = None
        covenant_id = (indexer or {}).get("covenant_id") or args.covenant_id
        network = (indexer or {}).get("network", "unknown")
    else:
        if not (args.covenant_id and args.rest):
            p.error("need --fixture, or both --covenant-id and --rest")
        txs = json.load(open(args.rest))
        indexer = json.load(open(args.indexer)) if args.indexer else None
        covenant_id = args.covenant_id
        network = (indexer or {}).get("network", "unknown")

    result = get_utxos_by_covenant_id(covenant_id, txs, indexer, network=network)
    print(json.dumps(result, indent=2))
    # exit non-zero when the cross-check fails closed, so scripts can gate on it
    return 0 if result["verification"]["status"] in ("verified", "unverified_no_indexer") else 2


if __name__ == "__main__":
    sys.exit(main())
