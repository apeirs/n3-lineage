"""Reconstruct the deploy/update history of a Neo N3 contract from public RPC.

Technique: `updatecounter` only ever goes up. Ask a NeoGo node for the counter
at historical heights (`invokescripthistoric`), bisect every interval where it
changed, and you get the exact block of each update, using about 25 batched
round trips no matter how many updates there were. Then read that block's
application logs to find the transaction (or the OnPersist step, for native
contracts that change at a hardfork) and its sender.
"""

from __future__ import annotations

import base64
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

from .model import (
    CONTRACT_MANAGEMENT,
    ContractState,
    decode_stack,
    hash_to_hex,
    invoke_script_get_contract,
    normalize_hash,
)
from .rpc import RpcError

LIFECYCLE_EVENTS = ("Deploy", "Update", "Destroy")


@dataclass
class Version:
    """A contract version: the state that became live at `height`."""

    counter: int
    height: int
    kind: str  # "deploy", "update" or "genesis"
    state: ContractState | None = None
    time_ms: int | None = None
    txid: str | None = None  # None for native updates applied in OnPersist
    sender: str | None = None
    trigger: str | None = None  # "Application" or "OnPersist"
    hardfork: str | None = None
    steps: int = 1  # >1 if several updates landed in one block

    def as_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "state"}
        if self.state is not None:
            d["state"] = {
                "id": self.state.id,
                "updatecounter": self.state.updatecounter,
                "hash": self.state.hash,
                "nef": {k: v for k, v in self.state.nef.items() if k != "script"},
                "manifest": self.state.manifest,
            }
        return d


@dataclass
class History:
    contract: str
    name: str
    tip: int
    versions: list[Version] = field(default_factory=list)
    rpc_calls: int = 0


Progress = Callable[[str], None]


def _quiet(_msg: str) -> None:
    pass


def stderr_progress(msg: str) -> None:
    print(f"  .. {msg}", file=sys.stderr, flush=True)


def counters_at(rpc: Any, contract: str, heights: list[int]) -> dict[int, int | None]:
    """updatecounter of `contract` at each height (None = not deployed yet / destroyed)."""
    script = invoke_script_get_contract(contract, counter_only=True)
    res = rpc.batch([("invokescripthistoric", [h, script]) for h in heights])
    out: dict[int, int | None] = {}
    for h, r in zip(heights, res):
        if isinstance(r, RpcError):
            raise r
        if r.get("state") != "HALT":
            raise RpcError(f"historic call at {h} faulted: {r.get('exception')}")
        out[h] = decode_stack(r["stack"][0])
    return out


def state_at(rpc: Any, contract: str, height: int) -> ContractState | None:
    r = rpc.call("invokescripthistoric", [height, invoke_script_get_contract(contract)])
    if r.get("state") != "HALT":
        raise RpcError(f"historic getContract at {height} faulted: {r.get('exception')}")
    return ContractState.from_stack(r["stack"][0], height)


def states_at(rpc: Any, contract: str, heights: list[int]) -> dict[int, ContractState | None]:
    script = invoke_script_get_contract(contract)
    out = {}
    # full states can be tens of KB each; keep batches small
    for i in range(0, len(heights), 8):
        chunk = heights[i : i + 8]
        for h, r in zip(chunk, rpc.batch([("invokescripthistoric", [h, script]) for h in chunk])):
            if isinstance(r, RpcError):
                raise r
            out[h] = ContractState.from_stack(r["stack"][0], h)
    return out


_UNKNOWN = object()


def _safe_counters(rpc: Any, contract: str, heights: list[int]) -> dict[int, Any]:
    """Like counters_at, but a height the node cannot answer maps to _UNKNOWN instead of failing.

    (Seen on NeoGo 0.123: a historic call at mainnet block 7,299,999, the block
    before the Echidna hardfork, answers 502 for every contract.)
    """
    if not heights:
        return {}
    try:
        return dict(counters_at(rpc, contract, heights))
    except RpcError:
        out: dict[int, Any] = {}
        for h in heights:
            try:
                out.update(counters_at(rpc, contract, [h]))
            except RpcError:
                out[h] = _UNKNOWN
        return out


def find_changes(
    rpc: Any,
    contract: str,
    lo: int,
    hi: int,
    progress: Progress = _quiet,
    pinpoint: Callable[[int, int], int] | None = None,
) -> list[tuple[int, int | None, int | None]]:
    """Every height h in (lo, hi] where the counter differs from h-1, as (h, before, after).

    Batched bisection: each round queries the midpoints of all open intervals
    in one JSON-RPC batch, so the round count is ~log2(hi - lo). If the node
    cannot answer for any height inside an interval, `pinpoint(a, b)` decides
    which block in (a, b] holds the change (default: b).
    """
    c = _safe_counters(rpc, contract, sorted({lo, hi}))
    if c[lo] is _UNKNOWN or c[hi] is _UNKNOWN:
        raise RpcError(f"node cannot answer historic calls at {lo} or {hi}")
    work = [(lo, hi)] if c[lo] != c[hi] else []
    bad: set[int] = set()
    changes: list[tuple[int, int | None, int | None]] = []
    rounds = 0
    while work:
        rounds += 1
        open_, picks = [], []
        for a, b in work:
            mid = (a + b) // 2
            interior = sorted(range(a + 1, b), key=lambda x: abs(x - mid)) if b - a < 64 else [
                x for d in range(0, 32) for x in (mid - d, mid + d) if a < x < b
            ]
            m = next((x for x in interior if x not in bad), None)
            if m is None:  # b == a + 1, or nothing answerable in between
                h = b if b - a == 1 or pinpoint is None else pinpoint(a, b)
                changes.append((h, c[a], c[b]))
            else:
                open_.append((a, b))
                picks.append(m)
        if not open_:
            break
        c.update(_safe_counters(rpc, contract, sorted({m for m in picks if m not in c})))
        nxt = []
        for (a, b), m in zip(open_, picks):
            if c[m] is _UNKNOWN:
                bad.add(m)
                nxt.append((a, b))
                continue
            if c[a] != c[m]:
                nxt.append((a, m))
            if c[m] != c[b]:
                nxt.append((m, b))
        work = nxt
        progress(f"bisect round {rounds}: {len(work)} open interval(s), {len(changes)} change(s) pinned")
    return sorted(changes)


def _hardfork_at(hardforks: list[dict[str, Any]], height: int) -> str | None:
    for hf in hardforks:
        if hf.get("blockheight") == height:
            return hf.get("name")
    return None


def _lifecycle_hits(log: dict[str, Any], contract_le: bytes) -> list[tuple[str, str]]:
    """(eventname, trigger) for ContractManagement Deploy/Update/Destroy notifications about the contract."""
    hits = []
    for ex in log.get("executions", []):
        for n in ex.get("notifications", []):
            if n.get("contract") != CONTRACT_MANAGEMENT or n.get("eventname") not in LIFECYCLE_EVENTS:
                continue
            try:
                arg = n["state"]["value"][0]
                if base64.b64decode(arg["value"]) == contract_le:
                    hits.append((n["eventname"], ex.get("trigger", "")))
            except (KeyError, IndexError, TypeError, ValueError):
                continue
    return hits


def locate(rpc: Any, contract: str, height: int) -> dict[str, Any]:
    """Find the tx (or OnPersist step) in `height` that emitted the contract's Deploy/Update event."""
    contract_le = bytes.fromhex(contract[2:])[::-1]
    block = rpc.call("getblock", [height, 1])
    info: dict[str, Any] = {"time_ms": block.get("time")}
    txs = block.get("tx", [])
    # Transactions whose script mentions the contract hash are the likely ones; check them first.
    ordered = sorted(txs, key=lambda t: contract_le not in base64.b64decode(t.get("script", "")))
    for tx in ordered:
        try:
            log = rpc.call("getapplicationlog", [tx["hash"]])
        except RpcError:
            continue
        if _lifecycle_hits(log, contract_le):
            info.update(txid=tx["hash"], sender=tx.get("sender"), trigger="Application")
            return info
    try:
        blog = rpc.call("getapplicationlog", [block["hash"]])
    except RpcError:
        blog = {}
    hits = _lifecycle_hits(blog, contract_le)
    if hits:
        info.update(trigger=hits[0][1] or "OnPersist")
    return info


def build_history(
    rpc: Any,
    contract: str,
    tip: int | None = None,
    with_states: bool = True,
    progress: Progress = _quiet,
) -> History:
    """Full lineage of a contract: one Version per deploy/update, oldest first."""
    if tip is None:
        tip = rpc.call("getblockcount") - 1
    live = rpc.call("getcontractstate", [contract])
    contract = normalize_hash(live["hash"])
    hist = History(contract=contract, name=live["manifest"]["name"], tip=tip)
    hardforks = rpc.call("getversion").get("protocol", {}).get("hardforks", [])

    def pinpoint(a: int, b: int) -> int:
        for h in range(a + 1, b + 1):
            if locate(rpc, contract, h).get("trigger"):
                return h
        return b

    changes = find_changes(rpc, contract, 0, tip, progress, pinpoint)
    genesis = counters_at(rpc, contract, [0])[0]
    if genesis is not None:  # native contract present since genesis
        hist.versions.append(Version(counter=genesis, height=0, kind="genesis", trigger="OnPersist"))
    for h, before, after in changes:
        if after is None:
            kind = "destroy"
        elif before is None:
            kind = "deploy"
        else:
            kind = "update"
        steps = 1 if before is None or after is None else after - before
        hist.versions.append(
            Version(counter=after if after is not None else -1, height=h, kind=kind, steps=steps)
        )

    progress(f"{len(hist.versions)} version(s); locating transactions")
    for v in hist.versions:
        v.hardfork = _hardfork_at(hardforks, v.height)
        if v.kind == "genesis":
            continue
        info = locate(rpc, contract, v.height)
        v.time_ms = info.get("time_ms")
        v.txid = info.get("txid")
        v.sender = info.get("sender")
        v.trigger = info.get("trigger")

    if with_states:
        progress("reading the contract state of every version")
        heights = [v.height for v in hist.versions if v.kind != "destroy"]
        states = states_at(rpc, contract, heights)
        for v in hist.versions:
            v.state = states.get(v.height)
    hist.rpc_calls = getattr(rpc, "calls", 0)
    return hist


def resolve(rpc: Any, ref: str) -> str:
    """Script hash for a hash, a contract id or a native contract name."""
    try:
        return normalize_hash(ref)
    except ValueError:
        pass
    param: Any = int(ref) if ref.lstrip("-").isdigit() else ref
    return normalize_hash(rpc.call("getcontractstate", [param])["hash"])


__all__ = [
    "History",
    "Version",
    "build_history",
    "counters_at",
    "find_changes",
    "hash_to_hex",
    "locate",
    "resolve",
    "state_at",
    "states_at",
]
