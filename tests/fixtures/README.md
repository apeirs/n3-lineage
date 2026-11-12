# Test fixtures

RPC answers recorded from Neo N3 mainnet (COZ public NeoGo nodes) on 2026-11-12, with the chain read up to block
14,829,383 (`--until 14829383`). The offline tests replay them, so they need no network and always see the same
chain.

| file                   | recorded with                                                                 |
|------------------------|-------------------------------------------------------------------------------|
| `router-history.json`  | `history 0xf970f4ccecd765b63732b821775dc38c25d74f23 --until 14829383` (FlamingoSwapRouter, 11 updates) |
| `policy-history.json`  | `history 0xcc5e4edd9f5f8dba8bb65734541df7a1c081c67b --until 14829383` (PolicyContract, 2 hardfork updates; includes the 502 at block 7,299,999) |
| `router-check.json`    | `check 0xf970f4ccecd765b63732b821775dc38c25d74f23 builds/router-v3/...`        |
| `status.json`          | `status` on the router, PolicyContract and FLM                                 |
| `status-missing.json`  | `status` on a hash that is not deployed (`Unknown contract`)                   |
| `builds/router-v3`, `builds/router-v11` | the router's manifest and NEF at v3 and v11, rebuilt byte-exact from the on-chain `ContractState` |

To re-record, add `--record tests/fixtures/<file>.json` to the same command. Keys are
`[method, params]`; values are the node's `result`, or `{"__error__": ..., "code": ...}` for an error.
