"""Compare two versions of a Neo N3 contract (manifest + NEF) and grade each change.

Severities:
  breaking  existing callers, indexers or wallets can stop working
  risk      the contract gained power (new call permissions, trusts, external calls)
            or lost a safety property; worth a human look
  info      everything else that changed
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .model import CONTRACT_MANAGEMENT, NATIVE_CONTRACTS, ContractState, hex_to_hash, script_bytes, script_sha256

SEVERITY_ORDER = {"breaking": 0, "risk": 1, "info": 2}


@dataclass(frozen=True)
class Change:
    severity: str
    area: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"severity": self.severity, "area": self.area, "message": self.message}


def _sig(m: dict[str, Any]) -> str:
    params = ", ".join(f"{p['type']} {p['name']}" for p in m.get("parameters", []))
    return f"{m['name']}({params}) -> {m.get('returntype', 'Void')}"


def _label(h: str) -> str:
    return NATIVE_CONTRACTS.get(h, h)


def _perm_map(manifest: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for p in manifest.get("permissions", []):
        methods = p.get("methods", [])
        out[p.get("contract", "*")] = "*" if methods == "*" else set(methods)
    return out


def upgrade_path(nef: dict[str, Any]) -> str | None:
    """How the code can reach ContractManagement.update, if at all.

    "token"   a CALLT method token for ContractManagement.update (what the C#
              and NeoGo compilers emit)
    "dynamic" no token, but the script embeds the ContractManagement hash and
              the string "update" (a System.Contract.Call)
    None      no visible path: the contract cannot upgrade itself
    """
    if not nef or "script" not in nef:
        return None
    for t in nef.get("tokens", []):
        if t["hash"] == CONTRACT_MANAGEMENT and t["method"] == "update":
            return "token"
    script = script_bytes(nef)
    if hex_to_hash(CONTRACT_MANAGEMENT) in script and b"update" in script:
        return "dynamic"
    return None


def diff_manifests(old: dict[str, Any], new: dict[str, Any]) -> list[Change]:
    ch: list[Change] = []
    if old.get("name") != new.get("name"):
        ch.append(Change("info", "name", f"name {old.get('name')!r} -> {new.get('name')!r}"))

    # standards
    o_std, n_std = set(old.get("supportedstandards", [])), set(new.get("supportedstandards", []))
    for s in sorted(o_std - n_std):
        ch.append(Change("breaking", "standards", f"no longer declares {s}"))
    for s in sorted(n_std - o_std):
        ch.append(Change("info", "standards", f"now declares {s}"))

    # methods, keyed by (name, parameter count) like the VM does
    o_m = {(m["name"], len(m.get("parameters", []))): m for m in old.get("abi", {}).get("methods", [])}
    n_m = {(m["name"], len(m.get("parameters", []))): m for m in new.get("abi", {}).get("methods", [])}
    removed, added = sorted(o_m.keys() - n_m.keys()), sorted(n_m.keys() - o_m.keys())
    # A method whose parameter count changed shows up as one removal + one addition: report it once.
    renamed = {k[0] for k in removed} & {k[0] for k in added}
    for name in sorted(renamed):
        olds = [_sig(o_m[k]) for k in removed if k[0] == name]
        news = [_sig(n_m[k]) for k in added if k[0] == name]
        sev = "info" if name.startswith("_") else "breaking"
        ch.append(Change(sev, "methods", f"signature {' | '.join(olds)}  =>  {' | '.join(news)}"))
    for k in removed:
        if k[0] not in renamed:
            sev = "info" if k[0].startswith("_") else "breaking"
            ch.append(Change(sev, "methods", f"removed {_sig(o_m[k])}"))
    for k in added:
        if k[0] not in renamed:
            safe = " [safe]" if n_m[k].get("safe") else ""
            ch.append(Change("info", "methods", f"added {_sig(n_m[k])}{safe}"))
    for k in sorted(o_m.keys() & n_m.keys()):
        a, b = o_m[k], n_m[k]
        a_types = [p["type"] for p in a.get("parameters", [])]
        b_types = [p["type"] for p in b.get("parameters", [])]
        if a_types != b_types or a.get("returntype") != b.get("returntype"):
            ch.append(Change("breaking", "methods", f"signature {_sig(a)}  =>  {_sig(b)}"))
        elif [p["name"] for p in a.get("parameters", [])] != [p["name"] for p in b.get("parameters", [])]:
            ch.append(Change("info", "methods", f"parameter names {_sig(a)}  =>  {_sig(b)}"))
        if a.get("safe") and not b.get("safe"):
            ch.append(
                Change("breaking", "methods", f"{k[0]}/{k[1]} is no longer safe (read-only callers will FAULT)")
            )
        elif b.get("safe") and not a.get("safe"):
            ch.append(Change("info", "methods", f"{k[0]}/{k[1]} is now safe"))

    # events, keyed by name
    o_e = {e["name"]: e for e in old.get("abi", {}).get("events", [])}
    n_e = {e["name"]: e for e in new.get("abi", {}).get("events", [])}
    for k in sorted(o_e.keys() - n_e.keys()):
        ch.append(Change("breaking", "events", f"removed event {k}"))
    for k in sorted(n_e.keys() - o_e.keys()):
        ch.append(Change("info", "events", f"added event {_sig(n_e[k]).rsplit(' -> ', 1)[0]}"))
    for k in sorted(o_e.keys() & n_e.keys()):
        a = [(p["name"], p["type"]) for p in o_e[k].get("parameters", [])]
        b = [(p["name"], p["type"]) for p in n_e[k].get("parameters", [])]
        if [t for _, t in a] != [t for _, t in b]:
            ch.append(Change("breaking", "events", f"event {k} parameters {a} -> {b}"))
        elif a != b:
            ch.append(Change("info", "events", f"event {k} parameter names {a} -> {b}"))

    # permissions: what this contract may call
    o_p, n_p = _perm_map(old), _perm_map(new)
    for c in sorted(n_p.keys() - o_p.keys()):
        what = "any method" if n_p[c] == "*" else ", ".join(sorted(n_p[c]))
        who = "ANY contract" if c == "*" else _label(c)
        ch.append(Change("risk", "permissions", f"may now call {who}: {what}"))
    for c in sorted(o_p.keys() - n_p.keys()):
        ch.append(Change("info", "permissions", f"dropped permission for {'*' if c == '*' else _label(c)}"))
    for c in sorted(o_p.keys() & n_p.keys()):
        a, b = o_p[c], n_p[c]
        if a == b:
            continue
        who = "ANY contract" if c == "*" else _label(c)
        if b == "*":
            ch.append(Change("risk", "permissions", f"{who}: methods widened to *"))
        elif a == "*":
            ch.append(Change("info", "permissions", f"{who}: methods narrowed from * to {sorted(b)}"))
        else:
            if b - a:
                ch.append(Change("risk", "permissions", f"{who}: may now call {', '.join(sorted(b - a))}"))
            if a - b:
                ch.append(Change("info", "permissions", f"{who}: no longer calls {', '.join(sorted(a - b))}"))

    # trusts: who may call this contract with the caller's witness scope
    o_t, n_t = old.get("trusts", []), new.get("trusts", [])
    if n_t == "*" and o_t != "*":
        ch.append(Change("risk", "trusts", "trusts widened to * (any contract)"))
    elif o_t == "*" and n_t != "*":
        ch.append(Change("info", "trusts", f"trusts narrowed from * to {n_t}"))
    elif o_t != "*" and n_t != "*":
        for t in sorted(set(n_t) - set(o_t)):
            ch.append(Change("risk", "trusts", f"now trusts {_label(t)}"))
        for t in sorted(set(o_t) - set(n_t)):
            ch.append(Change("info", "trusts", f"no longer trusts {_label(t)}"))

    # groups
    o_g = {g["pubkey"] for g in old.get("groups", [])}
    n_g = {g["pubkey"] for g in new.get("groups", [])}
    for g in sorted(n_g - o_g):
        ch.append(Change("risk", "groups", f"joined group {g}"))
    for g in sorted(o_g - n_g):
        ch.append(Change("risk", "groups", f"left group {g}"))

    # extra
    o_x, n_x = old.get("extra") or {}, new.get("extra") or {}
    if isinstance(o_x, dict) and isinstance(n_x, dict):
        keys = sorted(k for k in o_x.keys() | n_x.keys() if o_x.get(k) != n_x.get(k))
        if keys:
            ch.append(Change("info", "extra", f"extra fields changed: {', '.join(keys)}"))
    elif o_x != n_x:
        ch.append(Change("info", "extra", "extra changed"))
    return ch


def diff_nefs(old: dict[str, Any], new: dict[str, Any]) -> list[Change]:
    if not old or not new or "script" not in old or "script" not in new:
        return []
    ch: list[Change] = []
    if old.get("compiler") != new.get("compiler"):
        ch.append(Change("info", "nef", f"compiler {old.get('compiler')!r} -> {new.get('compiler')!r}"))
    if old["script"] != new["script"]:
        a, b = len(script_bytes(old)), len(script_bytes(new))
        ch.append(
            Change(
                "info",
                "nef",
                f"script changed: {a} -> {b} bytes ({b - a:+d}), sha256 {script_sha256(old)[:12]}.. -> "
                f"{script_sha256(new)[:12]}..",
            )
        )
    tok = lambda n: {(t["hash"], t["method"], t["paramcount"]) for t in n.get("tokens", [])}  # noqa: E731
    o_t, n_t = tok(old), tok(new)
    for h, m, pc in sorted(n_t - o_t):
        ch.append(Change("risk", "nef", f"new external call (method token): {_label(h)}.{m}/{pc}"))
    for h, m, pc in sorted(o_t - n_t):
        ch.append(Change("info", "nef", f"dropped method token {_label(h)}.{m}/{pc}"))
    o_up, n_up = upgrade_path(old), upgrade_path(new)
    if o_up and not n_up:
        ch.append(Change("risk", "upgradability", "no visible path to ContractManagement.update any more: "
                         "after this version the contract looks immutable"))
    elif n_up and not o_up:
        ch.append(Change("risk", "upgradability", f"gained a path to ContractManagement.update ({n_up})"))
    return ch


def diff_states(old: ContractState, new: ContractState) -> list[Change]:
    changes = diff_manifests(old.manifest, new.manifest) + diff_nefs(old.nef, new.nef)
    return sorted(changes, key=lambda c: SEVERITY_ORDER[c.severity])


def summarize(changes: list[Change]) -> dict[str, int]:
    out = {"breaking": 0, "risk": 0, "info": 0}
    for c in changes:
        out[c.severity] += 1
    return out
