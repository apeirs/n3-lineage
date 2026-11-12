"""Decoding of Neo N3 contract state: RPC stack items, NEF files and manifests.

`ContractManagement.getContract(hash)` returns the contract state as a VM
stack item: Array[id, updatecounter, hash, nef bytes, manifest Struct]. This
module turns that into the same JSON shape that the `getcontractstate` RPC
method returns, so states read at any historical height can be compared with
each other and with local build artifacts.
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
from dataclasses import dataclass, field
from typing import Any

CONTRACT_MANAGEMENT = "0xfffdc93764dbaddd97c48f252a53ea4643faa3fd"

NATIVE_CONTRACTS = {
    "0xfffdc93764dbaddd97c48f252a53ea4643faa3fd": "ContractManagement",
    "0xacce6fd80d44e1796aa0c2c625e9e4e0ce39efc0": "StdLib",
    "0x726cb6e0cd8628a1350a611384688911ab75f51b": "CryptoLib",
    "0xda65b600f7124ce6c79950c1772a36403104f2be": "LedgerContract",
    "0xef4073a0f2b305a38ec4050e4d3d28bc40ea63f5": "NeoToken",
    "0xd2a4cff31913016155e38e474a2c06d08be276cf": "GasToken",
    "0xcc5e4edd9f5f8dba8bb65734541df7a1c081c67b": "PolicyContract",
    "0x49cf4e5378ffcd4dec034fd98a174c5491e395e2": "RoleManagement",
    "0xfe924b7cfe89ddd271abaf7210a80a7e11178758": "OracleContract",
    "0xc1e14f19c3e60d0b9244d06dd7ba9b113135ec3b": "Notary",
    "0x156326f25b1b5d839a4d326aeaa75383c9563ac1": "Treasury",
}

PARAM_TYPES = {
    0x00: "Any",
    0x10: "Boolean",
    0x11: "Integer",
    0x12: "ByteArray",
    0x13: "String",
    0x14: "Hash160",
    0x15: "Hash256",
    0x16: "PublicKey",
    0x17: "Signature",
    0x20: "Array",
    0x22: "Map",
    0x30: "InteropInterface",
    0xFF: "Void",
}

CALL_FLAGS = {0: "None", 1: "ReadStates", 2: "WriteStates", 4: "AllowCall", 8: "AllowNotify"}
CALL_FLAG_NAMES = {3: "States", 5: "ReadOnly", 15: "All"}

NEF_MAGIC = 0x3346454E  # "NEF3"


class DecodeError(ValueError):
    pass


def hash_to_hex(le: bytes) -> str:
    """Little-endian UInt160/UInt256 bytes -> 0x-prefixed big-endian hex, as RPC prints it."""
    return "0x" + le[::-1].hex()


def hex_to_hash(h: str) -> bytes:
    """0x-prefixed big-endian hex -> little-endian bytes as stored in the VM."""
    h = h.lower().removeprefix("0x")
    if len(h) != 40:
        raise ValueError(f"not a script hash: {h!r}")
    return bytes.fromhex(h)[::-1]


def normalize_hash(h: str) -> str:
    return hash_to_hex(hex_to_hash(h))


def flags_name(v: int) -> str:
    if v in CALL_FLAG_NAMES:
        return CALL_FLAG_NAMES[v]
    if v == 0:
        return "None"
    return ", ".join(name for bit, name in CALL_FLAGS.items() if bit and v & bit)


# -- stack items --------------------------------------------------------------
def decode_stack(item: dict[str, Any]) -> Any:
    """RPC JSON stack item -> Python value (bytes, int, bool, None, list, list of pairs)."""
    t = item.get("type")
    v = item.get("value")
    if t == "Any":
        return None
    if t in ("ByteString", "Buffer"):
        return base64.b64decode(v or "")
    if t == "Integer":
        return int(v)
    if t == "Boolean":
        return bool(v)
    if t in ("Array", "Struct"):
        return [decode_stack(x) for x in v]
    if t == "Map":
        return [(decode_stack(e["key"]), decode_stack(e["value"])) for e in v]
    if t == "Pointer":
        return int(v)
    raise DecodeError(f"unsupported stack item type {t!r}")


# -- NEF ----------------------------------------------------------------------
class _Reader:
    def __init__(self, data: bytes):
        self.d = data
        self.p = 0

    def take(self, n: int) -> bytes:
        if self.p + n > len(self.d):
            raise DecodeError("NEF truncated")
        out = self.d[self.p : self.p + n]
        self.p += n
        return out

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return struct.unpack("<H", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def varint(self) -> int:
        b = self.u8()
        if b < 0xFD:
            return b
        if b == 0xFD:
            return self.u16()
        if b == 0xFE:
            return self.u32()
        return struct.unpack("<Q", self.take(8))[0]

    def varbytes(self, limit: int) -> bytes:
        n = self.varint()
        if n > limit:
            raise DecodeError(f"NEF field longer than {limit} bytes")
        return self.take(n)


def nef_checksum(data_without_checksum: bytes) -> int:
    return struct.unpack("<I", hashlib.sha256(hashlib.sha256(data_without_checksum).digest()).digest()[:4])[0]


def parse_nef(data: bytes) -> dict[str, Any]:
    """Parse NEF3 bytes into the JSON shape of `getcontractstate().nef`, plus derived fields."""
    r = _Reader(data)
    magic = r.u32()
    if magic != NEF_MAGIC:
        raise DecodeError(f"bad NEF magic 0x{magic:08x}")
    compiler = r.take(64).rstrip(b"\0").decode("utf-8", "replace")
    source = r.varbytes(256).decode("utf-8", "replace")
    if r.u8() != 0:
        raise DecodeError("NEF reserved byte is not zero")
    tokens = []
    for _ in range(r.varint()):
        h = r.take(20)
        method = r.varbytes(32).decode("utf-8", "replace")
        tokens.append(
            {
                "hash": hash_to_hex(h),
                "method": method,
                "paramcount": r.u16(),
                "hasreturnvalue": bool(r.u8()),
                "callflags": flags_name(r.u8()),
            }
        )
    if r.u16() != 0:
        raise DecodeError("NEF reserved u16 is not zero")
    script = r.varbytes(512 * 1024)
    checksum = r.u32()
    if r.p != len(data):
        raise DecodeError("trailing bytes after NEF checksum")
    return {
        "magic": magic,
        "compiler": compiler,
        "source": source,
        "tokens": tokens,
        "script": base64.b64encode(script).decode(),
        "checksum": checksum,
        "checksum_ok": checksum == nef_checksum(data[:-4]),
    }


def _varint(n: int) -> bytes:
    if n < 0xFD:
        return bytes([n])
    if n <= 0xFFFF:
        return b"\xfd" + struct.pack("<H", n)
    if n <= 0xFFFFFFFF:
        return b"\xfe" + struct.pack("<I", n)
    return b"\xff" + struct.pack("<Q", n)


def serialize_nef(nef: dict[str, Any]) -> bytes:
    """Inverse of parse_nef (checksum recomputed). Handy for writing a .nef from chain data."""
    flags = {v: k for k, v in {**CALL_FLAGS, **CALL_FLAG_NAMES}.items()}

    def flag_value(name: str) -> int:
        if name in flags:
            return flags[name]
        return sum(flags[p.strip()] for p in name.split(","))

    out = bytearray(struct.pack("<I", NEF_MAGIC))
    out += nef["compiler"].encode().ljust(64, b"\0")
    src = nef.get("source", "").encode()
    out += _varint(len(src)) + src + b"\0"
    out += _varint(len(nef.get("tokens", [])))
    for t in nef.get("tokens", []):
        m = t["method"].encode()
        out += hex_to_hash(t["hash"]) + _varint(len(m)) + m
        out += struct.pack("<HBB", t["paramcount"], int(t["hasreturnvalue"]), flag_value(t["callflags"]))
    out += b"\0\0"
    script = base64.b64decode(nef["script"])
    out += _varint(len(script)) + script
    out += struct.pack("<I", nef_checksum(bytes(out)))
    return bytes(out)


def script_bytes(nef: dict[str, Any]) -> bytes:
    return base64.b64decode(nef["script"])


def script_sha256(nef: dict[str, Any]) -> str:
    return hashlib.sha256(script_bytes(nef)).hexdigest()


# -- manifest -----------------------------------------------------------------
def _descriptor(v: bytes | None) -> str:
    if v is None:
        return "*"
    if len(v) == 20:
        return hash_to_hex(v)
    if len(v) == 33:
        return v.hex()
    raise DecodeError(f"bad permission descriptor of {len(v)} bytes")


def _param(p: list[Any]) -> dict[str, Any]:
    return {"name": p[0].decode(), "type": PARAM_TYPES.get(p[1], f"0x{p[1]:02x}")}


def manifest_from_stack(s: list[Any]) -> dict[str, Any]:
    """Manifest Struct (decoded) -> the manifest JSON that getcontractstate returns."""
    name, groups, features, standards, abi, perms, trusts, extra = s
    methods, events = abi
    return {
        "name": name.decode(),
        "groups": [{"pubkey": g[0].hex(), "signature": base64.b64encode(g[1]).decode()} for g in groups],
        "features": {k.decode(): v for k, v in features},
        "supportedstandards": [x.decode() for x in standards],
        "abi": {
            "methods": [
                {
                    "name": m[0].decode(),
                    "parameters": [_param(p) for p in m[1]],
                    "returntype": PARAM_TYPES.get(m[2], f"0x{m[2]:02x}"),
                    "offset": m[3],
                    "safe": m[4],
                }
                for m in methods
            ],
            "events": [{"name": e[0].decode(), "parameters": [_param(p) for p in e[1]]} for e in events],
        },
        "permissions": [
            {"contract": _descriptor(p[0]), "methods": "*" if p[1] is None else [m.decode() for m in p[1]]}
            for p in perms
        ],
        "trusts": "*" if trusts is None else [_descriptor(t) for t in trusts],
        "extra": None if extra is None else json.loads(extra.decode() or "null"),
    }


@dataclass
class ContractState:
    """One version of a contract, as stored by ContractManagement."""

    id: int
    updatecounter: int
    hash: str
    nef: dict[str, Any]
    manifest: dict[str, Any]
    height: int | None = None  # block height the state was read at, if historical
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_stack(cls, item: dict[str, Any], height: int | None = None) -> "ContractState | None":
        v = decode_stack(item)
        if v is None:
            return None
        cid, counter, h, nef, manifest = v
        return cls(cid, counter, hash_to_hex(h), parse_nef(nef), manifest_from_stack(manifest), height)

    @classmethod
    def from_rpc(cls, res: dict[str, Any]) -> "ContractState":
        """From the JSON of the getcontractstate RPC method."""
        return cls(res["id"], res["updatecounter"], res["hash"], res["nef"], res["manifest"])

    @classmethod
    def from_files(cls, manifest_path: str, nef_path: str | None = None) -> "ContractState":
        """From local build artifacts (Contract.manifest.json and, optionally, Contract.nef)."""
        with open(manifest_path, "rb") as fh:
            raw = fh.read()
        manifest = json.loads(raw)
        nef: dict[str, Any] = {}
        if nef_path:
            with open(nef_path, "rb") as fh:
                nef = parse_nef(fh.read())
        st = cls(0, -1, "local", nef, manifest)
        st.extra["manifest_bytes"] = len(json.dumps(manifest, separators=(",", ":")).encode())
        st.extra["path"] = manifest_path
        return st


def invoke_script_get_contract(contract: str, counter_only: bool = False) -> str:
    """Base64 NeoVM script calling ContractManagement.getContract(contract).

    With counter_only the script returns just the updatecounter (or Null when
    the contract does not exist at that height), which keeps batched bisection
    responses tiny.
    """
    s = bytearray()
    s += b"\x0c\x14" + hex_to_hash(contract)  # PUSHDATA1 20 <hash>
    s += b"\x11\xc0"  # PUSH1, PACK -> [hash]
    s += b"\x1f"  # PUSH15 = CallFlags.All
    s += b"\x0c\x0bgetContract"  # PUSHDATA1 "getContract"
    s += b"\x0c\x14" + hex_to_hash(CONTRACT_MANAGEMENT)
    s += b"\x41" + bytes.fromhex("627d5b52")  # SYSCALL System.Contract.Call
    if counter_only:
        # DUP ISNULL JMPIF +4 -> RET ; else PUSH1 PICKITEM ; RET
        s += bytes([0x4A, 0xD8, 0x24, 0x04, 0x11, 0xCE, 0x40])
    return base64.b64encode(bytes(s)).decode()
