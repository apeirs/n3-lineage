# Tutorial: the upgrade history of any Neo N3 contract, with curl and 30 lines of Python

This is the technique `n3-lineage` uses, step by step, so you can reuse it in your own tools. Every snippet below
was run against mainnet on 2026-11-12, and the output shown is what it printed. You need `curl`, `jq` and
Python 3. The example contract is FlamingoSwapRouter, `0xf970f4ccecd765b63732b821775dc38c25d74f23`.

## 1. The current state only tells you "11"

```bash
RPC=https://mainnet1.neo.coz.io:443
curl -s $RPC -H 'content-type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"getcontractstate","params":["0xf970f4ccecd765b63732b821775dc38c25d74f23"]}' | jq '.result | {id, updatecounter, name: .manifest.name}'
```

```json
{
  "id": 29,
  "updatecounter": 11,
  "name": "FlamingoSwapRouter"
}
```

`updatecounter` is how many times `ContractManagement.update` has replaced this contract. It never goes down. The
node keeps no list of when those 11 updates happened.

## 2. A script that reads the counter at any past block

NeoGo nodes have `invokescripthistoric [height, script]`: it runs a script against the chain state *after* block
`height`. So we need a script that calls `ContractManagement.getContract(hash)` and returns only the counter
(field 1 of the `ContractState` array), or null if the contract did not exist yet.

```python
import base64

CM = "fffdc93764dbaddd97c48f252a53ea4643faa3fd"      # ContractManagement
ROUTER = "f970f4ccecd765b63732b821775dc38c25d74f23"  # FlamingoSwapRouter

def le(h):  # script hashes are stored little-endian in the VM
    return bytes.fromhex(h)[::-1]

script = (
    b"\x0c\x14" + le(ROUTER)          # PUSHDATA1 20 bytes: the contract hash
    + b"\x11\xc0"                     # PUSH1, PACK      -> args = [hash]
    + b"\x1f"                         # PUSH15           -> CallFlags.All
    + b"\x0c\x0bgetContract"          # PUSHDATA1 "getContract"
    + b"\x0c\x14" + le(CM)            # PUSHDATA1 ContractManagement hash
    + b"\x41\x62\x7d\x5b\x52"         # SYSCALL System.Contract.Call
    + b"\x4a\xd8\x24\x04\x11\xce\x40" # DUP ISNULL JMPIF+4 PUSH1 PICKITEM RET
)
print(base64.b64encode(script).decode())
```

```
DBQjT9cljMNddyG4Mje2ZdfszPRw+RHAHwwLZ2V0Q29udHJhY3QMFP2j+kNG6lMqJY/El92t22Q3yf3/QWJ9W1JK2CQEEc5A
```

The last line is the trick: if `getContract` returned null, jump straight to `RET` and return null; otherwise
pick item 1 (the counter) so the node sends back one integer instead of the whole manifest.

Try it at a few heights:

```bash
RPC=https://mainnet1.neo.coz.io:443
S=DBQjT9cljMNddyG4Mje2ZdfszPRw+RHAHwwLZ2V0Q29udHJhY3QMFP2j+kNG6lMqJY/El92t22Q3yf3/QWJ9W1JK2CQEEc5A
for H in 500000 600000 1000000 3000000; do
  printf '%s ' $H
  curl -s $RPC -H 'content-type: application/json' \
    -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"invokescripthistoric\",\"params\":[$H,\"$S\"]}" \
    | jq -c '.result.stack[0]'
done
```

```
500000 {"type":"Any"}
600000 {"type":"Integer","value":"0"}
1000000 {"type":"Integer","value":"2"}
3000000 {"type":"Integer","value":"11"}
```

Not deployed at 500,000; version 0 at 600,000; version 2 by block 1,000,000; all 11 updates done by 3,000,000.

## 3. Bisect, many intervals per request

Because the counter only goes up, any interval whose two ends differ contains at least one update. Split every
such interval at its midpoint, and send all the midpoints of one round as a single JSON-RPC batch. The number of
rounds is about log2(chain height), whatever the number of updates:

```python
import json, urllib.request

RPC = "https://mainnet1.neo.coz.io:443"
SCRIPT = "DBQjT9cljMNddyG4Mje2ZdfszPRw+RHAHwwLZ2V0Q29udHJhY3QMFP2j+kNG6lMqJY/El92t22Q3yf3/QWJ9W1JK2CQEEc5A"

def batch(heights):
    body = [{"jsonrpc": "2.0", "id": i, "method": "invokescripthistoric", "params": [h, SCRIPT]}
            for i, h in enumerate(heights)]
    req = urllib.request.Request(RPC, json.dumps(body).encode(),
                                 {"content-type": "application/json", "user-agent": "tutorial"})
    out = sorted(json.load(urllib.request.urlopen(req, timeout=30)), key=lambda r: r["id"])
    return {h: (int(r["result"]["stack"][0]["value"]) if r["result"]["stack"][0]["type"] == "Integer" else None)
            for h, r in zip(heights, out)}

tip = 14_829_000
c = batch([0, tip])
work, changes, rounds = [(0, tip)], [], 0
while work:
    rounds += 1
    mids = [(a + b) // 2 for a, b in work]
    c.update(batch(mids))
    nxt = []
    for (a, b), m in zip(work, mids):
        for lo, hi in ((a, m), (m, b)):
            if c[lo] != c[hi]:
                (changes.append((hi, c[lo], c[hi])) if hi - lo == 1 else nxt.append((lo, hi)))
    work = nxt
print(f"{rounds} batched rounds")
for h, before, after in sorted(changes):
    print(f"block {h:>9,}: updatecounter {before} -> {after}")
```

```
24 batched rounds
block   596,838: updatecounter None -> 0
block   661,945: updatecounter 0 -> 1
block   678,636: updatecounter 1 -> 2
block 1,162,922: updatecounter 2 -> 3
block 1,492,580: updatecounter 3 -> 4
block 1,495,220: updatecounter 4 -> 5
block 1,500,414: updatecounter 5 -> 6
block 2,064,598: updatecounter 6 -> 7
block 2,636,688: updatecounter 7 -> 8
block 2,636,709: updatecounter 8 -> 9
block 2,642,827: updatecounter 9 -> 10
block 2,642,845: updatecounter 10 -> 11
```

Twelve events, each to the exact block, in 16 seconds. Note v10 and v11 four minutes apart, and v8 and v9 21
blocks apart: these are the kind of quick follow-up fixes nobody would find by eye.

Two caveats that `n3-lineage` handles and this sketch does not:

- A node may be unable to answer for one particular height. NeoGo 0.123 returns 502 for every historic call at
  mainnet block 7,299,999, the block before the Echidna hardfork. Probe a neighbouring height instead, and if the
  interval cannot be narrowed further, use the application logs (step 4) to pick the block.
- Several updates in one block show up as a jump of more than one.

## 4. Find the transaction and its sender

ContractManagement emits `Deploy`, `Update` or `Destroy` with the contract hash (little-endian, base64) as the
first argument. Check the transactions of the block you found:

```bash
RPC=https://mainnet1.neo.coz.io:443
rpc() { curl -s $RPC -H 'content-type: application/json' -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"$1\",\"params\":$2}"; }
# every tx in block 1,495,220 (the router's v5)
for TX in $(rpc getblock '[1495220, 1]' | jq -r '.result.tx[].hash'); do
  rpc getapplicationlog "[\"$TX\"]" | jq -c --arg tx "$TX" '.result.executions[].notifications[]
    | select(.contract == "0xfffdc93764dbaddd97c48f252a53ea4643faa3fd")
    | {tx: $tx, event: .eventname, contract_le_b64: .state.value[0].value}'
done
```

```
{"tx":"0xf237b33920f7a93994800280b2e607c5ea05da597bce0fbb4d22d04f86d914eb","event":"Update","contract_le_b64":"I0/XJYzDXXchuDI3tmXX7Mz0cPk="}
```

`I0/XJYzDXXchuDI3tmXX7Mz0cPk=` reversed is `0xf970f4ccecd765b63732b821775dc38c25d74f23`, our router. The tx's
`sender` field in `getblock` gives who signed it. In a busy block, check first the transactions whose script
contains the contract hash; that is nearly always the one.

Native contracts (PolicyContract, NeoToken, ...) are different: they change at hardfork heights with no
transaction at all. Their `Update` notification is in the block's own application log
(`getapplicationlog <blockhash>`, trigger `OnPersist`), and `getversion` lists the hardfork heights.

## 5. Read what changed

Drop the last 7 bytes of the script from step 2 and the node returns the whole `ContractState`: `[id,
updatecounter, hash, nef, manifest]`, with the manifest as a VM struct. Compare the method names on both sides of
the v5 update:

```python
import base64, json, urllib.request

RPC = "https://mainnet1.neo.coz.io:443"
# the getContract script from step 2, without the 7-byte "return only the counter" tail
SCRIPT = base64.b64encode(base64.b64decode(
    "DBQjT9cljMNddyG4Mje2ZdfszPRw+RHAHwwLZ2V0Q29udHJhY3QMFP2j+kNG6lMqJY/El92t22Q3yf3/QWJ9W1JK2CQEEc5A")[:-7]).decode()

def methods_at(height):
    body = {"jsonrpc": "2.0", "id": 1, "method": "invokescripthistoric", "params": [height, SCRIPT]}
    req = urllib.request.Request(RPC, json.dumps(body).encode(),
                                 {"content-type": "application/json", "user-agent": "tutorial"})
    state = json.load(urllib.request.urlopen(req, timeout=30))["result"]["stack"][0]["value"]
    # ContractState = [id, updatecounter, hash, nef, manifest]
    # manifest     = [name, groups, features, supportedstandards, abi, permissions, trusts, extra]
    # abi          = [methods, events];  method = [name, parameters, returntype, offset, safe]
    abi = state[4]["value"][4]["value"]
    return {base64.b64decode(m["value"][0]["value"]).decode() for m in abi[0]["value"]}

before, after = methods_at(1_495_219), methods_at(1_495_220)
print("removed:", sorted(before - after))
print("added:  ", sorted(after - before))
```

```
removed: ['addAmount', 'removeAmount', 'startUpdate']
added:   ['onNEP17Payment']
```

The `nef` item is the raw NEF3 file as bytes (magic `NEF3`, compiler string, method tokens, script, checksum), so
the compiler, the script hash and the external calls (method tokens) of every past version come out of the same
call.

That is the whole technique: about 25 batched calls to find the blocks, one log lookup per block, one state read
per version. `n3-lineage history <contract>` does all of it, plus the diff and severity grading on top.
