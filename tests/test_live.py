"""Live checks against public mainnet RPC. Opt in with N3_LIVE=1 (CI runs them on a schedule)."""

import os
import unittest

from helpers import FLM, POLICY, ROUTER
from n3lineage.check import check_update
from n3lineage.lineage import counters_at, find_changes, state_at
from n3lineage.model import NATIVE_CONTRACTS, ContractState
from n3lineage.rpc import Rpc

LIVE = os.environ.get("N3_LIVE") == "1"


@unittest.skipUnless(LIVE, "set N3_LIVE=1 to run live mainnet tests")
class LiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rpc = Rpc.for_network("mainnet")
        cls.tip = cls.rpc.call("getblockcount") - 1

    def test_native_contract_table_is_current(self):
        live = {c["hash"]: c["manifest"]["name"] for c in self.rpc.call("getnativecontracts")}
        self.assertEqual(live, {h: n for h, n in NATIVE_CONTRACTS.items() if h in live})
        self.assertLessEqual(set(live), set(NATIVE_CONTRACTS))

    def test_decoded_state_matches_getcontractstate_at_tip(self):
        for h in (FLM, POLICY, ROUTER):
            live = ContractState.from_rpc(self.rpc.call("getcontractstate", [h]))
            hist = state_at(self.rpc, h, self.tip)
            nef = dict(hist.nef)
            self.assertTrue(nef.pop("checksum_ok"))
            self.assertEqual(hist.manifest, live.manifest, h)
            self.assertEqual(nef, live.nef, h)

    def test_flm_history_is_stable(self):
        ch = find_changes(self.rpc, FLM, 0, self.tip)
        self.assertEqual(ch[:3], [(596711, None, 0), (663915, 0, 1), (7032215, 1, 2)])

    def test_counters_before_genesis_of_contract(self):
        self.assertEqual(counters_at(self.rpc, FLM, [100])[100], None)

    def test_current_router_build_passes_check(self):
        live = ContractState.from_rpc(self.rpc.call("getcontractstate", [ROUTER]))
        self.assertTrue(check_update(live, live).ok)


if __name__ == "__main__":
    unittest.main()
