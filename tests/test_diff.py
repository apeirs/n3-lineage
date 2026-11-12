import copy
import unittest

from helpers import build, load_json
from n3lineage.diff import diff_manifests, diff_nefs, diff_states, summarize, upgrade_path
from n3lineage.model import CONTRACT_MANAGEMENT, ContractState


def manifest(**kw):
    m = {
        "name": "Token",
        "groups": [],
        "features": {},
        "supportedstandards": ["NEP-17"],
        "abi": {
            "methods": [
                {"name": "symbol", "parameters": [], "returntype": "String", "offset": 0, "safe": True},
                {"name": "balanceOf", "parameters": [{"name": "account", "type": "Hash160"}],
                 "returntype": "Integer", "offset": 5, "safe": True},
                {"name": "mint", "parameters": [{"name": "to", "type": "Hash160"}, {"name": "amount", "type": "Integer"}],
                 "returntype": "Void", "offset": 9, "safe": False},
            ],
            "events": [{"name": "Transfer", "parameters": [{"name": "from", "type": "Hash160"},
                                                          {"name": "to", "type": "Hash160"},
                                                          {"name": "amount", "type": "Integer"}]}],
        },
        "permissions": [{"contract": "0xd2a4cff31913016155e38e474a2c06d08be276cf", "methods": ["transfer"]}],
        "trusts": [],
        "extra": {"Author": "a"},
    }
    m.update(kw)
    return m


def find(changes, sev, text):
    return [c for c in changes if c.severity == sev and text in c.message]


class ManifestDiffTests(unittest.TestCase):
    def test_identical(self):
        self.assertEqual(diff_manifests(manifest(), manifest()), [])

    def test_method_changes(self):
        new = manifest()
        ms = new["abi"]["methods"]
        ms[0]["safe"] = False  # symbol no longer safe
        ms[1]["returntype"] = "String"  # balanceOf return type changed
        ms[2]["parameters"][1]["name"] = "value"  # rename only
        ms.append({"name": "burn", "parameters": [], "returntype": "Void", "offset": 20, "safe": False})
        ch = diff_manifests(manifest(), new)
        self.assertTrue(find(ch, "breaking", "symbol/0 is no longer safe"))
        self.assertTrue(find(ch, "breaking", "signature balanceOf(Hash160 account) -> Integer"))
        self.assertTrue(find(ch, "info", "parameter names mint("))
        self.assertTrue(find(ch, "info", "added burn() -> Void"))

    def test_overload_change_is_reported_once(self):
        new = manifest()
        new["abi"]["methods"][2]["parameters"].append({"name": "data", "type": "Any"})
        ch = diff_manifests(manifest(), new)
        self.assertEqual(len([c for c in ch if "mint" in c.message]), 1)
        self.assertTrue(find(ch, "breaking", "signature mint(Hash160 to, Integer amount) -> Void  =>  mint("))

    def test_removed_method_and_event_and_standard(self):
        new = manifest(supportedstandards=[])
        new["abi"]["methods"] = new["abi"]["methods"][:2]
        new["abi"]["events"] = []
        ch = diff_manifests(manifest(), new)
        self.assertTrue(find(ch, "breaking", "removed mint"))
        self.assertTrue(find(ch, "breaking", "removed event Transfer"))
        self.assertTrue(find(ch, "breaking", "no longer declares NEP-17"))

    def test_event_param_types(self):
        new = manifest()
        new["abi"]["events"][0]["parameters"][2]["type"] = "ByteArray"
        self.assertTrue(find(diff_manifests(manifest(), new), "breaking", "event Transfer parameters"))

    def test_permission_widening(self):
        ch = diff_manifests(manifest(), manifest(permissions=[{"contract": "*", "methods": "*"}]))
        self.assertTrue(find(ch, "risk", "may now call ANY contract: any method"))
        self.assertTrue(find(ch, "info", "dropped permission for GasToken"))
        new = manifest(permissions=[{"contract": "0xd2a4cff31913016155e38e474a2c06d08be276cf",
                                     "methods": ["transfer", "balanceOf"]}])
        self.assertTrue(find(diff_manifests(manifest(), new), "risk", "GasToken: may now call balanceOf"))
        new = manifest(permissions=[{"contract": "0xd2a4cff31913016155e38e474a2c06d08be276cf", "methods": "*"}])
        self.assertTrue(find(diff_manifests(manifest(), new), "risk", "GasToken: methods widened to *"))

    def test_trusts_groups_extra(self):
        ch = diff_manifests(manifest(), manifest(trusts="*"))
        self.assertTrue(find(ch, "risk", "trusts widened to *"))
        ch = diff_manifests(manifest(), manifest(trusts=["0xef4073a0f2b305a38ec4050e4d3d28bc40ea63f5"]))
        self.assertTrue(find(ch, "risk", "now trusts NeoToken"))
        pk = "02" + "11" * 32
        ch = diff_manifests(manifest(), manifest(groups=[{"pubkey": pk, "signature": "AA=="}]))
        self.assertTrue(find(ch, "risk", f"joined group {pk}"))
        ch = diff_manifests(manifest(), manifest(extra={"Author": "b", "Version": "2"}))
        self.assertTrue(find(ch, "info", "extra fields changed: Author, Version"))


class NefDiffTests(unittest.TestCase):
    def setUp(self):
        self.v3 = ContractState.from_files(build("v3", "manifest.json"), build("v3", "nef"))
        self.v11 = ContractState.from_files(build("v11", "manifest.json"), build("v11", "nef"))

    def test_real_upgrade_v3_to_v11(self):
        ch = diff_states(self.v3, self.v11)
        s = summarize(ch)
        self.assertEqual(s["breaking"], 4)  # update() overload + addAmount/removeAmount/startUpdate removed
        self.assertTrue(find(ch, "risk", "new external call (method token): ContractManagement.getContract/1"))
        self.assertTrue(find(ch, "info", "compiler 'Neo.Compiler.CSharp 3.1.0' -> 'Neo.Compiler.CSharp 3.3.0'"))
        self.assertTrue(find(ch, "info", "script changed: 2567 -> 4415 bytes (+1848)"))
        # breaking first
        self.assertEqual(ch[0].severity, "breaking")

    def test_upgrade_path_detection(self):
        self.assertEqual(upgrade_path(self.v11.nef), "token")
        nef = copy.deepcopy(self.v11.nef)
        nef["tokens"] = [t for t in nef["tokens"] if t["method"] != "update"]
        self.assertIsNone(upgrade_path(nef))
        ch = diff_nefs(self.v11.nef, nef)
        self.assertTrue(find(ch, "risk", "looks immutable"))
        self.assertTrue(find(diff_nefs(nef, self.v11.nef), "risk", "gained a path"))
        self.assertIsNone(upgrade_path({}))

    def test_dynamic_upgrade_path(self):
        import base64
        nef = copy.deepcopy(self.v11.nef)
        nef["tokens"] = []
        nef["script"] = base64.b64encode(
            b"\x0c\x06update\x0c\x14" + bytes.fromhex(CONTRACT_MANAGEMENT[2:])[::-1]).decode()
        self.assertEqual(upgrade_path(nef), "dynamic")

    def test_files_without_nef_skip_code_diff(self):
        self.assertEqual(diff_nefs({}, self.v11.nef), [])


if __name__ == "__main__":
    unittest.main()
