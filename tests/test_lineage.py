import unittest

from helpers import POLICY, ROUTER, TIP, CounterRpc, replay
from n3lineage.lineage import build_history, find_changes, resolve
from n3lineage.rpc import RpcError


class BisectionTests(unittest.TestCase):
    def test_finds_every_change(self):
        updates = [1000, 1001, 50_000, 999_999]
        rpc = CounterRpc(lambda h: None if h < 500 else sum(1 for u in updates if h >= u))
        ch = find_changes(rpc, ROUTER, 0, 1_000_000)
        self.assertEqual(ch, [(500, None, 0), (1000, 0, 1), (1001, 1, 2), (50_000, 2, 3), (999_999, 3, 4)])
        # batched: rounds ~ log2(range), not updates * log2(range)
        self.assertLess(rpc.calls, 30)

    def test_two_updates_in_one_block(self):
        rpc = CounterRpc(lambda h: 0 if h < 77 else 2)
        self.assertEqual(find_changes(rpc, ROUTER, 0, 1000), [(77, 0, 2)])

    def test_no_change(self):
        rpc = CounterRpc(lambda h: 3)
        self.assertEqual(find_changes(rpc, ROUTER, 0, 10**7), [])
        self.assertEqual(rpc.calls, 1)

    def test_destroy(self):
        rpc = CounterRpc(lambda h: 0 if h < 900 else None)
        self.assertEqual(find_changes(rpc, ROUTER, 0, 1000), [(900, 0, None)])
        # bisection only looks inside intervals whose ends differ: a deploy+destroy that both
        # happen inside the range is invisible (and getcontractstate cannot find such a contract)
        rpc = CounterRpc(lambda h: None if h < 10 or h >= 900 else 0)
        self.assertEqual(find_changes(rpc, ROUTER, 0, 1000), [])

    def test_routes_around_heights_the_node_cannot_answer(self):
        # like NeoGo 0.123 at mainnet block 7,299,999
        rpc = CounterRpc(lambda h: 0 if h < 7_300_000 else 1, broken={7_299_999})
        self.assertEqual(find_changes(rpc, ROUTER, 0, 8_000_000), [(7_300_000, 0, 1)])
        # change hidden behind an unanswerable block: the pinpoint callback decides
        rpc = CounterRpc(lambda h: 0 if h < 7_299_999 else 1, broken={7_299_999})
        self.assertEqual(find_changes(rpc, ROUTER, 0, 8_000_000, pinpoint=lambda a, b: a + 1),
                         [(7_299_999, 0, 1)])

    def test_endpoints_must_answer(self):
        with self.assertRaises(RpcError):
            find_changes(CounterRpc(lambda h: 0, broken={0}), ROUTER, 0, 100)


class RecordedHistoryTests(unittest.TestCase):
    """Replays of real mainnet answers recorded on 2026-11-12 at block 14,829,383."""

    @classmethod
    def setUpClass(cls):
        cls.router = build_history(replay("router-history.json"), ROUTER, TIP)
        cls.policy = build_history(replay("policy-history.json"), POLICY, TIP)

    def test_router_timeline(self):
        v = self.router.versions
        self.assertEqual(self.router.name, "FlamingoSwapRouter")
        self.assertEqual(len(v), 12)
        self.assertEqual([x.kind for x in v], ["deploy"] + ["update"] * 11)
        self.assertEqual([x.counter for x in v], list(range(12)))
        self.assertEqual(v[0].height, 596838)
        self.assertEqual(v[0].txid[:10], "0x044ddb63")
        self.assertEqual(v[0].sender, "NXpYrXFRFc9P1gB2jY43erQDUSKd2cV4jr")
        self.assertEqual(v[-1].height, 2642845)
        self.assertTrue(all(x.trigger == "Application" and x.txid for x in v))
        self.assertTrue(all(x.state is not None and x.state.updatecounter == x.counter for x in v))
        times = [x.time_ms for x in v]
        self.assertEqual(times, sorted(times))

    def test_policy_native_updates_at_hardforks(self):
        v = self.policy.versions
        self.assertEqual([(x.kind, x.height, x.hardfork) for x in v],
                         [("genesis", 0, None), ("update", 7300000, "Echidna"), ("update", 8800000, "Faun")])
        self.assertTrue(all(x.txid is None and x.trigger == "OnPersist" for x in v))
        faun = {m["name"] for m in v[2].state.manifest["abi"]["methods"]}
        echidna = {m["name"] for m in v[1].state.manifest["abi"]["methods"]}
        # the methods Dora's PolicyContract page is missing (CityOfZion/dora#706)
        self.assertEqual({"getBlockedAccounts", "getExecPicoFeeFactor", "getWhitelistFeeContracts"} - echidna,
                         {"getBlockedAccounts", "getExecPicoFeeFactor", "getWhitelistFeeContracts"})
        self.assertLessEqual({"getBlockedAccounts", "getExecPicoFeeFactor", "getWhitelistFeeContracts"}, faun)

    def test_resolve(self):
        self.assertEqual(resolve(None, ROUTER.upper().replace("0X", "0x")), ROUTER)
        self.assertEqual(resolve(replay("policy-history.json"), "0x" + POLICY[2:]), POLICY)


if __name__ == "__main__":
    unittest.main()
