"""Command line interface: n3-lineage history | diff | check | status."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

from . import __version__
from .check import check_update
from .diff import Change, diff_states, summarize, upgrade_path
from .lineage import History, build_history, counters_at, find_changes, resolve, state_at, stderr_progress
from .model import NATIVE_CONTRACTS, ContractState, script_sha256


def _upgradable(st: ContractState) -> str:
    if st.hash in NATIVE_CONTRACTS:
        return "native (hardforks)"
    return upgrade_path(st.nef) or "none"
from .rpc import RecordingRpc, ReplayRpc, Rpc, RpcError

EXIT_OK, EXIT_FINDINGS, EXIT_ERROR, EXIT_CHANGED = 0, 1, 2, 3


def _when(ms: int | None) -> str:
    if not ms:
        return "-"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ")


def _short(x: str | None, n: int = 10) -> str:
    if not x:
        return "-"
    return x if len(x) <= n + 4 else f"{x[:n]}..{x[-4:]}"


def _print_changes(changes: list[Change], indent: str = "      ") -> None:
    if not changes:
        print(f"{indent}(no manifest or code change)")
    for c in changes:
        print(f"{indent}{c.severity:<9} {c.area:<13} {c.message}")


class Ctx:
    """RPC per network, with optional recording/replay for reproducible runs."""

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.rpcs: dict[str, Any] = {}
        self.changes: dict[tuple[str, str], list[tuple[int, Any, Any]]] = {}  # (network, contract) -> bisection
        self.replay = ReplayRpc.load(args.replay) if getattr(args, "replay", None) else None

    def rpc(self, network: str | None = None) -> Any:
        network = network or self.args.network
        if self.replay is not None:
            return self.replay
        if network not in self.rpcs:
            urls = self.args.rpc if network == self.args.network else []
            inner = Rpc.for_network(network, urls, timeout=self.args.timeout)
            self.rpcs[network] = RecordingRpc(inner) if self.args.record else inner
        return self.rpcs[network]

    def save(self) -> None:
        if self.args.record:
            tape: dict[str, Any] = {}
            for r in self.rpcs.values():
                tape.update(r.tape)
            with open(self.args.record, "w", encoding="utf-8") as fh:
                json.dump(tape, fh, sort_keys=True, indent=0)
                fh.write("\n")


def _progress(args: argparse.Namespace):
    return (lambda _m: None) if args.quiet or args.json else stderr_progress


def _history(ctx: Ctx, ref: str, with_states: bool = True, network: str | None = None) -> History:
    rpc = ctx.rpc(network)
    contract = resolve(rpc, ref)
    return build_history(rpc, contract, ctx.args.until, with_states, _progress(ctx.args))


def cmd_history(ctx: Ctx) -> int:
    a = ctx.args
    hist = _history(ctx, a.contract, with_states=not a.no_diff)
    rows = []
    prev: ContractState | None = None
    for v in hist.versions:
        changes = diff_states(prev, v.state) if prev and v.state and not a.no_diff else []
        rows.append((v, changes))
        prev = v.state or prev
    if a.json:
        print(
            json.dumps(
                {
                    "contract": hist.contract,
                    "name": hist.name,
                    "network": a.network,
                    "tip": hist.tip,
                    "versions": [
                        dict(v.as_dict() if a.full else {k: x for k, x in v.as_dict().items() if k != "state"},
                             changes=[c.as_dict() for c in ch], summary=summarize(ch))
                        for v, ch in rows
                    ],
                },
                indent=2,
            )
        )
        return EXIT_OK
    updates = sum(v.steps for v in hist.versions if v.kind == "update")
    print(f"{hist.name}  {hist.contract}  ({a.network}, read at block {hist.tip:,})")
    first = hist.versions[0] if hist.versions else None
    origin = "genesis" if first and first.kind == "genesis" else (
        f"deploy at block {first.height:,} ({_when(first.time_ms)[:10]})" if first else "?")
    print(f"{updates} update(s) since {origin}\n")
    for v, changes in rows:
        who = (
            f"tx {_short(v.txid)}  by {v.sender}" if v.txid
            else f"{v.trigger or 'block'}" + (f" (hardfork {v.hardfork})" if v.hardfork else "")
        )
        tag = f"v{v.counter}" if v.kind != "destroy" else "--"
        jump = f" x{v.steps}" if v.steps > 1 else ""
        print(f"{tag:<5} {v.kind:<8}{jump} block {v.height:>11,}  {_when(v.time_ms)}  {who}")
        if v.state is not None and (a.verbose or v.kind in ("deploy", "genesis")):
            nef = v.state.nef
            print(f"      compiler {nef.get('compiler')!r}, script sha256 {script_sha256(nef)[:16]}.., "
                  f"{len(v.state.manifest['abi']['methods'])} methods, upgrade path: {_upgradable(v.state)}")
        if v.kind not in ("deploy", "genesis") and not a.no_diff:
            _print_changes(changes)
    if a.verbose:
        print(f"\n{hist.rpc_calls} RPC round trips", file=sys.stderr)
    return EXIT_OK


def _parse_spec(ctx: Ctx, spec: str) -> ContractState:
    """[network:]<contract>[@vN|@height] or a path to a .manifest.json (with a .nef next to it)."""
    if spec.endswith(".json") or os.path.exists(spec):
        nef = spec.replace(".manifest.json", ".nef")
        return ContractState.from_files(spec, nef if nef != spec and os.path.exists(nef) else None)
    network = None
    if ":" in spec and spec.split(":", 1)[0] in ("mainnet", "testnet"):
        network, spec = spec.split(":", 1)
    ref, _, at = spec.partition("@")
    rpc = ctx.rpc(network)
    contract = resolve(rpc, ref)
    if not at:
        return ContractState.from_rpc(rpc.call("getcontractstate", [contract]))
    if at.startswith("v"):
        # Only the block heights are needed here: bisect once per contract, skip the tx lookups.
        key = (network or ctx.args.network, contract)
        if key not in ctx.changes:
            tip = ctx.args.until if ctx.args.until is not None else rpc.call("getblockcount") - 1
            found = find_changes(rpc, contract, 0, tip, _progress(ctx.args))
            ctx.changes[key] = [(0, None, c) for c in [counters_at(rpc, contract, [0])[0]] if c is not None] + found
        want = int(at[1:])
        for h, _before, after in ctx.changes[key]:
            if after == want:
                st = state_at(rpc, contract, h)
                if st is None:
                    break
                return st
        raise SystemExit(f"{contract} has no version v{want}")
    st = state_at(rpc, contract, int(at.replace(",", "").replace("_", "")))
    if st is None:
        raise SystemExit(f"{contract} did not exist at block {at}")
    return st


def cmd_diff(ctx: Ctx) -> int:
    a = ctx.args
    if a.new is None:  # previous version vs current
        hist = _history(ctx, a.old)
        live = [v for v in hist.versions if v.state is not None]
        if len(live) < 2:
            print(f"{hist.name} {hist.contract} has never been updated")
            return EXIT_OK
        old, new = live[-2].state, live[-1].state
    else:
        old, new = _parse_spec(ctx, a.old), _parse_spec(ctx, a.new)
    changes = diff_states(old, new)
    if a.json:
        print(json.dumps({"changes": [c.as_dict() for c in changes], "summary": summarize(changes)}, indent=2))
    else:
        label = lambda s: s.extra.get("path") or f"{s.manifest['name']} v{s.updatecounter}" + (  # noqa: E731
            f" @{s.height:,}" if s.height is not None else " (current)")
        print(f"{label(old)}  ->  {label(new)}")
        _print_changes(changes, indent="  ")
        s = summarize(changes)
        print(f"\n{s['breaking']} breaking, {s['risk']} risk, {s['info']} info")
    return EXIT_FINDINGS if a.strict and any(c.severity != "info" for c in changes) else EXIT_OK


def cmd_check(ctx: Ctx) -> int:
    a = ctx.args
    rpc = ctx.rpc()
    contract = resolve(rpc, a.contract)
    deployed = ContractState.from_rpc(rpc.call("getcontractstate", [contract]))
    nef = a.nef
    if nef is None:
        guess = a.manifest.replace(".manifest.json", ".nef")
        nef = guess if guess != a.manifest and os.path.exists(guess) else None
    new = ContractState.from_files(a.manifest, nef)
    rep = check_update(deployed, new)
    if a.json:
        print(json.dumps({"ok": rep.ok, "errors": rep.errors, "warnings": rep.warnings,
                          "changes": [c.as_dict() for c in rep.changes], "summary": summarize(rep.changes)}, indent=2))
    else:
        print(f"update check: {deployed.manifest['name']} {deployed.hash} v{deployed.updatecounter} "
              f"({a.network})  <-  {a.manifest}" + (f" + {nef}" if nef else ""))
        for e in rep.errors:
            print(f"  ERROR    {e}")
        for w in rep.warnings:
            print(f"  warning  {w}")
        print("  changes:")
        _print_changes(rep.changes, indent="    ")
        s = summarize(rep.changes)
        verdict = "would be REJECTED or impossible" if rep.errors else "passes the on-chain checks"
        print(f"\n{verdict}; {s['breaking']} breaking, {s['risk']} risk, {s['info']} info change(s)")
    if rep.errors:
        return EXIT_FINDINGS
    if a.strict and any(c.severity != "info" for c in rep.changes):
        return EXIT_FINDINGS
    return EXIT_OK


def cmd_status(ctx: Ctx) -> int:
    a = ctx.args
    rpc = ctx.rpc()
    out, code = [], EXIT_OK
    for ref in a.contracts:
        contract = resolve(rpc, ref)
        st = ContractState.from_rpc(rpc.call("getcontractstate", [contract]))
        row = {
            "contract": st.hash,
            "name": st.manifest["name"],
            "updatecounter": st.updatecounter,
            "nef_checksum": st.nef.get("checksum"),
            "script_sha256": script_sha256(st.nef),
            "upgrade_path": _upgradable(st),
        }
        if a.expect is not None and st.updatecounter != a.expect:
            row["changed"] = True
            code = EXIT_CHANGED
        if a.expect_sha and not row["script_sha256"].startswith(a.expect_sha.lower()):
            row["changed"] = True
            code = EXIT_CHANGED
        out.append(row)
    if a.json:
        print(json.dumps(out, indent=2))
    else:
        for r in out:
            flag = "  CHANGED" if r.get("changed") else ""
            print(f"{r['name']:<24} {r['contract']}  v{r['updatecounter']}  sha256 {r['script_sha256'][:16]}..  "
                  f"upgrade path: {r['upgrade_path']}{flag}")
    return code


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="n3-lineage",
        description="Upgrade history and manifest/NEF diffs for Neo N3 contracts, from public RPC only.",
    )
    p.add_argument("--version", action="version", version=f"n3-lineage {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-n", "--network", default="mainnet", choices=["mainnet", "testnet"])
    common.add_argument("--rpc", action="append", default=[], metavar="URL",
                        help="RPC endpoint (repeatable; history needs a NeoGo node with historic calls)")
    common.add_argument("--timeout", type=float, default=30.0)
    common.add_argument("--json", action="store_true", help="machine-readable output")
    common.add_argument("-q", "--quiet", action="store_true", help="no progress on stderr")
    common.add_argument("-v", "--verbose", action="store_true")
    common.add_argument("--until", type=int, metavar="HEIGHT", help="read history up to this block (default: tip)")
    common.add_argument("--record", metavar="FILE", help=argparse.SUPPRESS)
    common.add_argument("--replay", metavar="FILE", help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("history", parents=[common], help="every deploy/update of a contract, with what changed")
    h.add_argument("contract", help="script hash, contract id or native name (e.g. PolicyContract)")
    h.add_argument("--no-diff", action="store_true", help="timeline only (faster)")
    h.add_argument("--full", action="store_true", help="with --json: include each version's manifest")
    h.set_defaults(func=cmd_history)

    d = sub.add_parser("diff", parents=[common], help="compare two versions (default: previous vs current)")
    d.add_argument("old", help="[mainnet:|testnet:]<contract>[@vN|@HEIGHT] or path/to/X.manifest.json")
    d.add_argument("new", nargs="?", help="same forms; omit to diff the last update")
    d.add_argument("--strict", action="store_true", help="exit 1 on any breaking or risk change")
    d.set_defaults(func=cmd_diff)

    c = sub.add_parser("check", parents=[common], help="lint a new build against the deployed contract")
    c.add_argument("contract")
    c.add_argument("manifest", help="path/to/Contract.manifest.json of the new build")
    c.add_argument("--nef", help="path/to/Contract.nef (default: next to the manifest)")
    c.add_argument("--strict", action="store_true", help="exit 1 on any breaking or risk change too")
    c.set_defaults(func=cmd_check)

    s = sub.add_parser("status", parents=[common], help="current version of contracts; guard for CI/cron")
    s.add_argument("contracts", nargs="+")
    s.add_argument("--expect", type=int, metavar="N", help="exit 3 unless every contract's updatecounter == N")
    s.add_argument("--expect-sha", metavar="HEX", help="exit 3 unless the script sha256 starts with HEX")
    s.set_defaults(func=cmd_status)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ctx = Ctx(args)
    try:
        return args.func(ctx)
    except RpcError as exc:
        print(f"n3-lineage: RPC error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except (OSError, ValueError) as exc:
        print(f"n3-lineage: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        ctx.save()


if __name__ == "__main__":
    sys.exit(main())
