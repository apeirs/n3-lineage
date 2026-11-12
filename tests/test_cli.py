import contextlib
import io
import json
import os
import unittest

from helpers import FIX, POLICY, ROUTER, TIP, build
from n3lineage.cli import main


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def fx(name):
    return os.path.join(FIX, name)


class CliTests(unittest.TestCase):
    def test_history_text(self):
        code, out, _ = run("history", ROUTER, "--until", str(TIP), "-q", "--replay", fx("router-history.json"))
        self.assertEqual(code, 0)
        self.assertIn("11 update(s) since deploy at block 596,838 (2021-11-18)", out)
        self.assertIn("v4    update   block   1,492,580  2022-05-04 13:55Z  tx 0xa4fe7574..cf4f", out)
        self.assertIn("breaking  methods       signature update(ByteArray nefFile, String manifest, Any data)", out)
        self.assertIn("(no manifest or code change)", out)  # v10 re-sent the same code

    def test_history_json(self):
        code, out, _ = run("history", POLICY, "--until", str(TIP), "--json", "--replay", fx("policy-history.json"))
        self.assertEqual(code, 0)
        doc = json.loads(out)
        self.assertEqual(doc["name"], "PolicyContract")
        self.assertEqual([v["hardfork"] for v in doc["versions"]], [None, "Echidna", "Faun"])
        self.assertEqual(doc["versions"][2]["summary"], {"breaking": 0, "risk": 0, "info": 9})
        self.assertNotIn("state", doc["versions"][0])

    def test_diff_versions_and_files(self):
        code, out, _ = run("diff", f"{ROUTER}@v4", f"{ROUTER}@v5", "--until", str(TIP), "-q",
                           "--replay", fx("router-history.json"))
        self.assertEqual(code, 0)
        self.assertIn("FlamingoSwapRouter v4 @1,492,580  ->  FlamingoSwapRouter v5 @1,495,220", out)
        self.assertIn("removed startUpdate(Integer blockHeight) -> Boolean", out)
        self.assertIn("3 breaking, 0 risk, 3 info", out)
        code, out, _ = run("diff", build("v3", "manifest.json"), build("v11", "manifest.json"), "--strict")
        self.assertEqual(code, 1)
        self.assertIn("4 breaking, 1 risk", out)

    def test_diff_last_update(self):
        code, out, _ = run("diff", ROUTER, "--until", str(TIP), "-q", "--replay", fx("router-history.json"))
        self.assertEqual(code, 0)
        self.assertIn("v10 @2,642,827  ->  FlamingoSwapRouter v11 @2,642,845", out)

    def test_check(self):
        code, out, _ = run("check", ROUTER, build("v3", "manifest.json"), "--replay", fx("router-check.json"))
        self.assertEqual(code, 0)
        self.assertIn("passes the on-chain checks; 6 breaking, 1 risk, 6 info change(s)", out)
        code, _, _ = run("check", ROUTER, build("v3", "manifest.json"), "--strict", "--replay",
                         fx("router-check.json"))
        self.assertEqual(code, 1)
        code, out, _ = run("check", ROUTER, build("v11", "manifest.json"), "--json", "--replay",
                           fx("router-check.json"))
        self.assertEqual((code, json.loads(out)["ok"]), (0, True))

    def test_status_guard(self):
        args = ["status", ROUTER, "--replay", fx("status.json")]
        self.assertEqual(run(*args, "--expect", "11")[0], 0)
        code, out, _ = run(*args, "--expect", "10")
        self.assertEqual(code, 3)
        self.assertIn("CHANGED", out)
        code, out, _ = run(*args, "--expect-sha", "bbaf8ef2", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)[0]["upgrade_path"], "token")
        code, out, _ = run("status", POLICY, "--replay", fx("status.json"))
        self.assertIn("native (hardforks)", out)

    def test_rpc_errors_exit_2(self):
        code, _, err = run("status", "0x" + "00" * 20, "--replay", fx("status-missing.json"))
        self.assertEqual(code, 2)
        self.assertIn("Unknown contract", err)


if __name__ == "__main__":
    unittest.main()
