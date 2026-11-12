import copy
import json
import os
import tempfile
import unittest

from helpers import ROUTER, build, load_json, replay
from n3lineage.check import check_update, standard_warnings, validate_manifest
from n3lineage.model import ContractState, parse_nef, serialize_nef


def deployed():
    return ContractState.from_rpc(replay("router-check.json").call("getcontractstate", [ROUTER]))


def local(manifest, nef_bytes=None):
    d = tempfile.mkdtemp()
    mp = os.path.join(d, "X.manifest.json")
    with open(mp, "w") as fh:
        json.dump(manifest, fh)
    np = None
    if nef_bytes is not None:
        np = os.path.join(d, "X.nef")
        with open(np, "wb") as fh:
            fh.write(nef_bytes)
    return ContractState.from_files(mp, np)


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.m = load_json(build("v11", "manifest.json"))
        with open(build("v11", "nef"), "rb") as fh:
            self.nef = fh.read()

    def test_identical_build(self):
        r = check_update(deployed(), local(self.m, self.nef))
        self.assertTrue(r.ok)
        self.assertIn("identical", " ".join(r.warnings))
        self.assertEqual(r.changes, [])

    def test_old_build_against_current(self):
        r = check_update(deployed(), ContractState.from_files(build("v3", "manifest.json"), build("v3", "nef")))
        self.assertTrue(r.ok)
        self.assertEqual(sum(c.severity == "breaking" for c in r.changes), 6)

    def test_name_change_is_rejected(self):
        m = copy.deepcopy(self.m)
        m["name"] = "FlamingoSwapRouterV2"
        r = check_update(deployed(), local(m, self.nef))
        self.assertFalse(r.ok)
        self.assertIn("rejects a name change", r.errors[0])

    def test_offsets_checksum_and_immutability(self):
        m = copy.deepcopy(self.m)
        m["abi"]["methods"][0]["offset"] = 10**6
        nef = parse_nef(self.nef)
        nef["tokens"] = [t for t in nef["tokens"] if t["method"] != "update"]
        raw = bytearray(serialize_nef(nef))
        raw[-1] ^= 1
        r = check_update(deployed(), local(m, bytes(raw)))
        text = " ".join(r.errors)
        self.assertIn("outside the", text)
        self.assertIn("checksum", text)
        self.assertIn("can never be upgraded again", " ".join(r.warnings))
        self.assertTrue(any("looks immutable" in c.message for c in r.changes))

    def test_manifest_only(self):
        r = check_update(deployed(), local(self.m))
        self.assertTrue(r.ok)
        self.assertIn("no .nef given", " ".join(r.warnings))

    def test_deployed_without_upgrade_path(self):
        dep = deployed()
        dep.nef = dict(dep.nef, tokens=[t for t in dep.nef["tokens"] if t["method"] != "update"])
        r = check_update(dep, local(self.m, self.nef))
        self.assertIn("cannot be upgraded at all", " ".join(r.errors))


class ManifestRuleTests(unittest.TestCase):
    def test_structural_rules(self):
        m = load_json(build("v11", "manifest.json"))
        self.assertEqual(validate_manifest(m), [])
        m["abi"]["methods"].append(copy.deepcopy(m["abi"]["methods"][0]))
        m["permissions"].append(copy.deepcopy(m["permissions"][0]))
        m["features"] = {"storage": True}
        m["supportedstandards"] = ["NEP-17", "NEP-17", ""]
        errs = " | ".join(validate_manifest(m, manifest_bytes=70000))
        for text in ("duplicate method", "duplicate permission", "features", "duplicate supported standard",
                     "empty string", "70000 bytes"):
            self.assertIn(text, errs)
        self.assertIn("no methods", " ".join(validate_manifest({"name": "x", "abi": {"methods": []}})))

    def test_nep17_shape(self):
        m = {"supportedstandards": ["NEP-17"], "abi": {"methods": [
            {"name": "symbol", "parameters": [], "returntype": "String", "safe": True},
            {"name": "decimals", "parameters": [], "returntype": "Integer", "safe": False},
            {"name": "_deploy", "parameters": [], "returntype": "Void", "safe": False},
        ], "events": []}}
        w = " | ".join(standard_warnings(m))
        self.assertIn("decimals should be safe", w)
        self.assertIn("no totalSupply/0", w)
        self.assertIn("no transfer/4", w)
        self.assertIn("event Transfer", w)
        self.assertIn("_deploy is not", w)


if __name__ == "__main__":
    unittest.main()
