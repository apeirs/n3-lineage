"""Shared test helpers: fixture paths and a synthetic RPC for bisection tests."""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Callable

from n3lineage.rpc import ReplayRpc, RpcError

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")
BUILDS = os.path.join(FIX, "builds")

ROUTER = "0xf970f4ccecd765b63732b821775dc38c25d74f23"  # FlamingoSwapRouter, mainnet
POLICY = "0xcc5e4edd9f5f8dba8bb65734541df7a1c081c67b"  # PolicyContract (native)
FLM = "0xf0151f528127558851b39c2cd8aa47da7418ab28"
TIP = 14829383  # block the fixtures were recorded at


def replay(name: str) -> ReplayRpc:
    return ReplayRpc.load(os.path.join(FIX, name))


def build(tag: str, ext: str) -> str:
    return os.path.join(BUILDS, f"router-{tag}", f"FlamingoSwapRouter.{ext}")


def load_json(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class CounterRpc:
    """Fake node: answers counter-only historic calls from a Python function of the height."""

    def __init__(self, counter: Callable[[int], int | None], broken: set[int] = frozenset()):
        self.counter = counter
        self.broken = set(broken)
        self.calls = 0
        self.queried: list[int] = []

    def _one(self, height: int) -> Any:
        self.queried.append(height)
        if height in self.broken:
            return RpcError("HTTP Error 502: Bad Gateway")
        c = self.counter(height)
        item = {"type": "Any"} if c is None else {"type": "Integer", "value": str(c)}
        return {"state": "HALT", "stack": [item]}

    def batch(self, requests):
        self.calls += 1
        out = [self._one(p[0]) for _m, p in requests]
        if any(isinstance(r, RpcError) for r in out) and len(out) > 1:
            raise RpcError("HTTP Error 502: Bad Gateway")
        return out

    def call(self, method, params=()):
        r = self.batch([(method, params)])[0]
        if isinstance(r, RpcError):
            raise r
        return r


def b64(s: bytes) -> str:
    return base64.b64encode(s).decode()
