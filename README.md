# n3-lineage

Every deploy and update of a Neo N3 contract, with what each one changed, from public RPC only.

```
pip install git+https://github.com/apeirs/n3-lineage
n3-lineage history 0xf970f4ccecd765b63732b821775dc38c25d74f23
```

No keys, no indexer, no database, no dependencies beyond the Python standard library (3.10+).

## Why

A Neo N3 contract can be replaced in place with `ContractManagement.update`. Its hash stays the same, but its
methods, events, permissions and code can all change. Today the chain only tells you the current state and an
`updatecounter`:

- Explorers show the current manifest, not the history. Dora has open requests for exactly this:
  [CityOfZion/dora#690](https://github.com/CityOfZion/dora/issues/690) ("amount of times it was updated, who
  updated, transaction ids of each update") and [#706](https://github.com/CityOfZion/dora/issues/706) (updates of
  native contracts at hardforks are not shown).
- The node has no "list the updates of contract X" RPC either
  ([neo-project/neo#2788](https://github.com/neo-project/neo/issues/2788) has been open since 2022).
- Deployment tools compare builds inside their own workspace, not against what is live on chain.

So if you integrate with someone else's contract, you cannot easily see when its interface changed under you. And
if you maintain one, nothing warns you before you push an update that drops a method your users call.

`n3-lineage` answers four questions:

| command   | question                                                                 |
|-----------|--------------------------------------------------------------------------|
| `history` | When was this contract deployed and updated, by which tx, and what changed each time? |
| `diff`    | What is different between two versions (on chain or local build files)?  |
| `check`   | Will `update` accept my new build, and what will it break for existing callers? |
| `status`  | Is the contract still the version I audited or integrated? (for CI / cron) |

## Output (real, mainnet, 2026-11-12)

### `history`

```
$ n3-lineage history 0xf970f4ccecd765b63732b821775dc38c25d74f23
FlamingoSwapRouter  0xf970f4ccecd765b63732b821775dc38c25d74f23  (mainnet, read at block 14,829,531)
11 update(s) since deploy at block 596,838 (2021-11-18)

v0    deploy   block     596,838  2021-11-18 09:27Z  tx 0x044ddb63..92e3  by NXpYrXFRFc9P1gB2jY43erQDUSKd2cV4jr
      compiler 'Neo.Compiler.CSharp 3.0.3', script sha256 5c88a65ff1ec8f8a.., 16 methods, upgrade path: token
v1    update   block     661,945  2021-11-30 03:23Z  tx 0xd2b63042..17a7  by NdDvLrbtqeCVQkaLstAwh3md8SYYwqWRaE
      info      nef           compiler 'Neo.Compiler.CSharp 3.0.3' -> 'Neo.Compiler.CSharp 3.1.0'
      info      nef           script changed: 1992 -> 2060 bytes (+68), sha256 5c88a65ff1ec.. -> 3b319fa7fb45..
...
v4    update   block   1,492,580  2022-05-04 13:55Z  tx 0xa4fe7574..cf4f  by NdDvLrbtqeCVQkaLstAwh3md8SYYwqWRaE
      breaking  methods       signature update(ByteArray nefFile, String manifest, Any data) -> Void  =>  update(ByteArray nefFile, String manifest) -> Void
      info      nef           script changed: 2567 -> 2631 bytes (+64), sha256 c88bfd035ce7.. -> dafff59f5855..
v5    update   block   1,495,220  2022-05-05 02:10Z  tx 0xf237b339..14eb  by NdDvLrbtqeCVQkaLstAwh3md8SYYwqWRaE
      breaking  methods       removed addAmount(Hash160 address) -> Boolean
      breaking  methods       removed removeAmount(Hash160 address) -> Boolean
      breaking  methods       removed startUpdate(Integer blockHeight) -> Boolean
      info      methods       added onNEP17Payment(Hash160 sender, Integer amountIn, Any data) -> Void
      info      nef           script changed: 2631 -> 2335 bytes (-296), sha256 dafff59f5855.. -> 778a43914115..
      info      nef           dropped method token LedgerContract.currentIndex/0
...
v7    update   block   2,064,598  2022-08-22 10:13Z  tx 0x93aa2fb5..73d1  by NdDvLrbtqeCVQkaLstAwh3md8SYYwqWRaE
      breaking  methods       removed addAmount(Hash160 address) -> Boolean
      breaking  methods       removed removeAmount(Hash160 address) -> Boolean
      breaking  methods       removed startUpdate(Integer blockHeight) -> Boolean
      risk      nef           new external call (method token): ContractManagement.getContract/1
      info      methods       added addLiquidity(Hash160 tokenA, Hash160 tokenB, Integer amountADesired, Integer amountBDesired, Integer amountAMin, Integer amountBMin, Integer deadLine) -> Array
      info      methods       added onNEP17Payment(Hash160 sender, Integer amountIn, Any data) -> Void
      info      methods       added removeLiquidity(Hash160 tokenA, Hash160 tokenB, Integer liquidity, Integer amountAMin, Integer amountBMin, Integer deadLine) -> Array
      info      methods       added swapTokenInForTokenOut(Integer amountIn, Integer amountOutMin, Array paths, Integer deadLine) -> Boolean
      info      methods       added swapTokenOutForTokenIn(Integer amountOut, Integer amountInMax, Array paths, Integer deadLine) -> Boolean
      info      nef           compiler 'Neo.Compiler.CSharp 3.1.0' -> 'Neo.Compiler.CSharp 3.3.0'
      info      nef           script changed: 2644 -> 4415 bytes (+1771), sha256 f623e011f671.. -> a9434ab27388..
      info      nef           dropped method token LedgerContract.currentIndex/0
...
v10   update   block   2,642,827  2022-12-14 06:43Z  tx 0xceb45d36..42ec  by NdDvLrbtqeCVQkaLstAwh3md8SYYwqWRaE
      (no manifest or code change)
v11   update   block   2,642,845  2022-12-14 06:47Z  tx 0xbda85061..b1fe  by NdDvLrbtqeCVQkaLstAwh3md8SYYwqWRaE
      info      nef           script changed: 4420 -> 4415 bytes (-5), sha256 3ae6d7b55581.. -> bbaf8ef20a79..
```

That run took 22 seconds and 56 HTTP round trips against `mainnet1-5.neo.coz.io`. Add `--json` for machine
output (`--json --full` adds every manifest and NEF header), `-v` to print the build line for every version.

Native contracts work too (they change at hardforks, in the block's OnPersist step, not in a transaction):

```
$ n3-lineage history PolicyContract
PolicyContract  0xcc5e4edd9f5f8dba8bb65734541df7a1c081c67b  (mainnet, read at block 14,829,539)
2 update(s) since genesis

v0    genesis  block           0  -  OnPersist
      compiler 'neo-core-v3.0', script sha256 4adc3083aae379c4.., 11 methods, upgrade path: native (hardforks)
v1    update   block   7,300,000  2025-05-15 16:59Z  OnPersist (hardfork Echidna)
      info      methods       added getMaxTraceableBlocks() -> Integer [safe]
      info      methods       added getMaxValidUntilBlockIncrement() -> Integer [safe]
      info      methods       added getMillisecondsPerBlock() -> Integer [safe]
      info      methods       added setMaxTraceableBlocks(Integer value) -> Void
      info      methods       added setMaxValidUntilBlockIncrement(Integer value) -> Void
      info      methods       added setMillisecondsPerBlock(Integer value) -> Void
      info      events        added event MillisecondsPerBlockChanged(Integer old, Integer new)
      info      nef           script changed: 77 -> 119 bytes (+42), sha256 4adc3083aae3.. -> 289601f31a12..
v2    update   block   8,800,000  2026-02-03 17:07Z  OnPersist (hardfork Faun)
      info      methods       added getBlockedAccounts() -> InteropInterface [safe]
      info      methods       added getExecPicoFeeFactor() -> Integer [safe]
      info      methods       added getWhitelistFeeContracts() -> InteropInterface [safe]
      info      methods       added recoverFund(Hash160 account, Hash160 token) -> Boolean
      info      methods       added removeWhitelistFeeContract(Hash160 contractHash, String method, Integer argCount) -> Void
      info      methods       added setWhitelistFeeContract(Hash160 contractHash, String method, Integer argCount, Integer fixedFee) -> Void
      info      events        added event RecoveredFund(Hash160 account)
      info      events        added event WhitelistFeeChanged(Hash160 contract, String method, Integer argCount, Any fee)
      info      nef           script changed: 119 -> 161 bytes (+42), sha256 289601f31a12.. -> 097eb920d8e9..
```

A contract can be named by hash, by id (`n3-lineage history 29`) or, for native contracts, by name.

### `diff`

Any two versions, by counter (`@v4`) or block height (`@1500000`), or a local build:

```
$ n3-lineage diff 0xf970f4ccecd765b63732b821775dc38c25d74f23@v4 0xf970f4ccecd765b63732b821775dc38c25d74f23@v5 -q
FlamingoSwapRouter v4 @1,492,580  ->  FlamingoSwapRouter v5 @1,495,220
  breaking  methods       removed addAmount(Hash160 address) -> Boolean
  breaking  methods       removed removeAmount(Hash160 address) -> Boolean
  breaking  methods       removed startUpdate(Integer blockHeight) -> Boolean
  info      methods       added onNEP17Payment(Hash160 sender, Integer amountIn, Any data) -> Void
  info      nef           script changed: 2631 -> 2335 bytes (-296), sha256 dafff59f5855.. -> 778a43914115..
  info      nef           dropped method token LedgerContract.currentIndex/0

3 breaking, 0 risk, 3 info
```

`n3-lineage diff <contract>` alone compares the last update with the one before it. `--strict` exits 1 when
anything is breaking or risky.

### `check`: before you sign an update

Point it at the deployed contract and your new `.manifest.json` (the `.nef` next to it is picked up). Here, a
deliberately wrong build, the router's v3 from 2022, checked against the live v11:

```
$ n3-lineage check 0xf970f4ccecd765b63732b821775dc38c25d74f23 build/FlamingoSwapRouter.manifest.json
update check: FlamingoSwapRouter 0xf970f4ccecd765b63732b821775dc38c25d74f23 v11 (mainnet)  <-  build/FlamingoSwapRouter.manifest.json + build/FlamingoSwapRouter.nef
  changes:
    breaking  methods       signature update(ByteArray nefFile, String manifest) -> Void  =>  update(ByteArray nefFile, String manifest, Any data) -> Void
    breaking  methods       removed addLiquidity(Hash160 tokenA, Hash160 tokenB, Integer amountADesired, Integer amountBDesired, Integer amountAMin, Integer amountBMin, Integer deadLine) -> Array
    breaking  methods       removed onNEP17Payment(Hash160 sender, Integer amountIn, Any data) -> Void
    breaking  methods       removed removeLiquidity(Hash160 tokenA, Hash160 tokenB, Integer liquidity, Integer amountAMin, Integer amountBMin, Integer deadLine) -> Array
    breaking  methods       removed swapTokenInForTokenOut(Integer amountIn, Integer amountOutMin, Array paths, Integer deadLine) -> Boolean
    breaking  methods       removed swapTokenOutForTokenIn(Integer amountOut, Integer amountInMax, Array paths, Integer deadLine) -> Boolean
    risk      nef           new external call (method token): LedgerContract.currentIndex/0
    info      methods       added addAmount(Hash160 address) -> Boolean
    info      methods       added removeAmount(Hash160 address) -> Boolean
    info      methods       added startUpdate(Integer blockHeight) -> Boolean
    info      nef           compiler 'Neo.Compiler.CSharp 3.3.0' -> 'Neo.Compiler.CSharp 3.1.0'
    info      nef           script changed: 4415 -> 2567 bytes (-1848), sha256 bbaf8ef20a79.. -> c88bfd035ce7..
    info      nef           dropped method token ContractManagement.getContract/1

passes the on-chain checks; 6 breaking, 1 risk, 6 info change(s)
```

Errors are things `ContractManagement.update` would reject or that make the update impossible: a name change, a
manifest over 65,535 bytes, non-empty `features`, duplicate methods/events/permissions/trusts/groups/standards,
duplicate parameter names, a bad NEF checksum, a method offset outside the script, or a deployed contract with no
path to `update` at all. Warnings cover a new build that can never be upgraded again, an identical build, NEP-17
and NEP-11 shape mistakes, and a `_deploy` with the wrong signature. With `--strict`, breaking or risky changes
also fail the run, which makes it a CI step for contract repos.

### `status`: pin what you integrate with

```
$ n3-lineage status 0xf970f4ccecd765b63732b821775dc38c25d74f23 0xf0151f528127558851b39c2cd8aa47da7418ab28 --expect 11
FlamingoSwapRouter       0xf970f4ccecd765b63732b821775dc38c25d74f23  v11  sha256 bbaf8ef20a79c8dd..  upgrade path: token
FLM                      0xf0151f528127558851b39c2cd8aa47da7418ab28  v2  sha256 33be1a46327c321c..  upgrade path: token  CHANGED
$ echo $?
3
$ n3-lineage status 0xf970f4ccecd765b63732b821775dc38c25d74f23 --expect 11 --expect-sha bbaf8ef2
FlamingoSwapRouter       0xf970f4ccecd765b63732b821775dc38c25d74f23  v11  sha256 bbaf8ef20a79c8dd..  upgrade path: token
$ echo $?
0
```

Put that in a cron job or a scheduled CI workflow next to your dApp, and you hear about an upstream update the day
it lands instead of when a call starts to FAULT.

## Severity

| severity   | meaning                                                                                     |
|------------|---------------------------------------------------------------------------------------------|
| `breaking` | existing callers, indexers or wallets can stop working: removed or re-typed methods and events, a method that lost `safe` (read-only callers FAULT), a dropped standard |
| `risk`     | the contract gained power or lost a safety property: new call permissions, wider `trusts`, new external calls (method tokens), group changes, gaining or losing the path to `update` |
| `info`     | everything else: added methods and events, compiler and script changes, `extra` fields   |

"Upgrade path" is read from the NEF: `token` means a `CALLT` method token for `ContractManagement.update` (what
the C# and NeoGo compilers emit), `dynamic` means the script embeds the ContractManagement hash and the string
`update`, and none means the contract has no visible way to replace itself.

## How it works

1. `updatecounter` only ever goes up. A NeoGo node's `invokescripthistoric` runs a script against the state
   after any past block, so a 72-byte script that calls `ContractManagement.getContract` and returns only the
   counter tells you the version at that height.
2. Batched bisection: every round asks for the midpoints of all open intervals in one JSON-RPC batch. Each update
   is pinned to its exact block in about 24 rounds, however many updates there were.
3. For each of those blocks, `getapplicationlog` finds the transaction that emitted ContractManagement's
   `Deploy`/`Update` notification for this contract, and its sender. Native contracts are matched in the block's
   own log (OnPersist) and labelled with the hardfork from `getversion`.
4. The full `ContractState` (NEF and manifest) is read at each of those heights and decoded from the VM stack, and
   consecutive versions are diffed.

[TUTORIAL.md](TUTORIAL.md) walks through the same technique with `curl` and a few lines of Python.

## Options

```
-n, --network mainnet|testnet   default mainnet (COZ public nodes, with failover)
--rpc URL                       use your own node(s); repeatable. Must be NeoGo with historic calls enabled
--until HEIGHT                  read history up to this block instead of the tip
--json                          machine-readable output
-q / -v                         no progress on stderr / more detail
```

Exit codes: `0` ok, `1` findings (`check` errors, `--strict`), `2` usage or RPC error, `3` `status` changed.

## Limits

- History needs a node that serves `invokescripthistoric`, a NeoGo extension that needs the node to keep old
  state (`KeepOnlyLatestState: false`, the default). The COZ public mainnet and testnet nodes do. C# neo-cli
  nodes do not have this method; `status` and `check` work against any node.
- NeoGo 0.123 returns HTTP 502 for every historic call at mainnet block 7,299,999 (the block before the Echidna
  hardfork). The bisection routes around unanswerable heights and finds the block from the application logs
  instead.
- A contract that was deployed and destroyed between two probes, or destroyed and redeployed (which changes the
  hash anyway), cannot be seen by counter bisection. Destroyed contracts have no `getcontractstate` to start from.
- The diff reads manifests and NEF metadata. It does not decompile or compare the bytecode itself beyond size and
  hash.

## Development

```
PYTHONPATH=src:tests python -m unittest discover -s tests -t .           # offline, about 0.1 s
N3_LIVE=1 PYTHONPATH=src:tests python -m unittest tests.test_live        # against public mainnet
```

The offline tests replay RPC answers recorded from mainnet (see [tests/fixtures](tests/fixtures/README.md)). Any
command can record or replay its RPC traffic with the hidden `--record FILE` / `--replay FILE` options, so a
fixture is one command away.

## License

Apache-2.0. See [LICENSE](LICENSE).
