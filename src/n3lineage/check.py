"""Pre-upgrade checks: will ContractManagement.update accept this build, and what does it change?

Run it against the deployed contract before you sign the update transaction.
Errors are things the chain will reject (or that make the update impossible);
the diff shows what existing users of the contract will notice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .diff import Change, diff_states, upgrade_path
from .model import ContractState, script_bytes

MAX_MANIFEST_BYTES = 0xFFFF  # ContractManifest.MaxLength

# Required ABI of the token standards (name, params, return type, safe).
STANDARDS: dict[str, list[tuple[str, list[str], str, bool]]] = {
    "NEP-17": [
        ("symbol", [], "String", True),
        ("decimals", [], "Integer", True),
        ("totalSupply", [], "Integer", True),
        ("balanceOf", ["Hash160"], "Integer", True),
        ("transfer", ["Hash160", "Hash160", "Integer", "Any"], "Boolean", False),
    ],
    "NEP-11": [
        ("symbol", [], "String", True),
        ("decimals", [], "Integer", True),
        ("totalSupply", [], "Integer", True),
        ("tokensOf", ["Hash160"], "InteropInterface", True),
        ("ownerOf", ["ByteArray"], "Hash160", True),
    ],
}
STANDARD_EVENTS = {
    "NEP-17": ("Transfer", ["Hash160", "Hash160", "Integer"]),
    "NEP-11": ("Transfer", ["Hash160", "Hash160", "Integer", "ByteArray"]),
}


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _dupes(items: list[Any]) -> list[Any]:
    seen, out = set(), []
    for i in items:
        if i in seen and i not in out:
            out.append(i)
        seen.add(i)
    return out


def validate_manifest(m: dict[str, Any], manifest_bytes: int | None = None) -> list[str]:
    """Structural rules from ContractManifest.IsValid / ContractAbi that make deploy or update FAULT."""
    errs = []
    if not m.get("name"):
        errs.append("manifest name is empty")
    if manifest_bytes is not None and manifest_bytes > MAX_MANIFEST_BYTES:
        errs.append(f"manifest is {manifest_bytes} bytes; the limit is {MAX_MANIFEST_BYTES}")
    if m.get("features") not in ({}, None):
        errs.append("manifest 'features' must be empty ({})")
    methods = m.get("abi", {}).get("methods", [])
    for name, n in _dupes([(x["name"], len(x.get("parameters", []))) for x in methods]):
        errs.append(f"duplicate method {name}/{n}")
    for name in _dupes([e["name"] for e in m.get("abi", {}).get("events", [])]):
        errs.append(f"duplicate event {name}")
    for c in _dupes([p.get("contract") for p in m.get("permissions", [])]):
        errs.append(f"duplicate permission entry for {c}")
    if m.get("trusts") != "*":
        for t in _dupes(list(m.get("trusts", []))):
            errs.append(f"duplicate trust {t}")
    for s in _dupes(list(m.get("supportedstandards", []))):
        errs.append(f"duplicate supported standard {s}")
    for g in _dupes([g["pubkey"] for g in m.get("groups", [])]):
        errs.append(f"duplicate group {g}")
    for x in methods:
        names = [p["name"] for p in x.get("parameters", [])]
        for p in _dupes(names):
            errs.append(f"method {x['name']} has duplicate parameter {p}")
    if not methods:
        errs.append("the ABI has no methods")
    for s in m.get("supportedstandards", []):
        if not s:
            errs.append("empty string in supportedstandards")
    return errs


def standard_warnings(m: dict[str, Any]) -> list[str]:
    out = []
    for x in m.get("abi", {}).get("methods", []):
        if x["name"] == "_deploy":
            types = [p["type"] for p in x.get("parameters", [])]
            if types != ["Any", "Boolean"] or x.get("returntype") != "Void":
                out.append("_deploy is not _deploy(Any data, Boolean update) -> Void, so the node may skip it")
    methods = {(x["name"], len(x.get("parameters", []))): x for x in m.get("abi", {}).get("methods", [])}
    events = {e["name"]: e for e in m.get("abi", {}).get("events", [])}
    for std in m.get("supportedstandards", []):
        for name, params, ret, safe in STANDARDS.get(std, []):
            got = methods.get((name, len(params)))
            if got is None:
                out.append(f"declares {std} but has no {name}/{len(params)}")
                continue
            types = [p["type"] for p in got.get("parameters", [])]
            if types != params or got.get("returntype") != ret:
                out.append(f"{std}: {name} should take {params} and return {ret}")
            if got.get("safe") != safe:
                out.append(f"{std}: {name} should be {'safe' if safe else 'not safe'}")
        if std in STANDARD_EVENTS:
            ename, etypes = STANDARD_EVENTS[std]
            ev = events.get(ename)
            if ev is None or [p["type"] for p in ev.get("parameters", [])] != etypes:
                out.append(f"{std}: event {ename} should have parameters {etypes}")
    return out


def check_update(deployed: ContractState, new: ContractState) -> Report:
    r = Report()
    m = new.manifest
    r.errors += validate_manifest(m, new.extra.get("manifest_bytes"))
    if m.get("name") != deployed.manifest.get("name"):
        r.errors.append(
            f"name {m.get('name')!r} differs from the deployed {deployed.manifest.get('name')!r}: "
            "ContractManagement.update rejects a name change"
        )
    if not upgrade_path(deployed.nef):
        r.errors.append(
            "the deployed contract has no visible path to ContractManagement.update (no method token, no "
            "dynamic call): it probably cannot be upgraded at all"
        )
    if new.nef:
        if not new.nef.get("checksum_ok", True):
            r.errors.append("NEF checksum does not match its content (rebuild the .nef)")
        script_len = len(script_bytes(new.nef))
        if script_len == 0:
            r.errors.append("NEF script is empty")
        for x in m.get("abi", {}).get("methods", []):
            if not 0 <= x.get("offset", 0) < max(script_len, 1):
                r.errors.append(f"method {x['name']} offset {x.get('offset')} is outside the {script_len}-byte script")
        if not upgrade_path(new.nef):
            r.warnings.append("the new build has no path to ContractManagement.update: after this update the "
                              "contract can never be upgraded again")
        if new.nef.get("script") == deployed.nef.get("script") and new.manifest == deployed.manifest:
            r.warnings.append("the new build is identical to what is deployed")
    else:
        r.warnings.append("no .nef given: only the manifest was checked")
    r.warnings += standard_warnings(m)
    r.changes = diff_states(deployed, new) if new.nef else diff_states(
        ContractState(deployed.id, deployed.updatecounter, deployed.hash, {}, deployed.manifest), new
    )
    return r
