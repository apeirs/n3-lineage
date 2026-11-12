import base64
import unittest

from helpers import FLM, ROUTER, build, load_json, replay
from n3lineage.lineage import states_at
from n3lineage.model import (
    ContractState,
    DecodeError,
    decode_stack,
    flags_name,
    hash_to_hex,
    hex_to_hash,
    invoke_script_get_contract,
    nef_checksum,
    parse_nef,
    serialize_nef,
)


class NefTests(unittest.TestCase):
    def test_parse_real_nef(self):
        with open(build("v11", "nef"), "rb") as fh:
            raw = fh.read()
        nef = parse_nef(raw)
        self.assertTrue(nef["checksum_ok"])
        self.assertEqual(nef["compiler"], "Neo.Compiler.CSharp 3.3.0")
        self.assertIn(
            ("0xfffdc93764dbaddd97c48f252a53ea4643faa3fd", "update"),
            [(t["hash"], t["method"]) for t in nef["tokens"]],
        )
        self.assertEqual(len(base64.b64decode(nef["script"])), 4415)

    def test_serialize_round_trip_is_byte_exact(self):
        for tag in ("v3", "v11"):
            with open(build(tag, "nef"), "rb") as fh:
                raw = fh.read()
            self.assertEqual(serialize_nef(parse_nef(raw)), raw)

    def test_bad_checksum_is_flagged(self):
        with open(build("v3", "nef"), "rb") as fh:
            raw = bytearray(fh.read())
        raw[-1] ^= 0xFF
        self.assertFalse(parse_nef(bytes(raw))["checksum_ok"])

    def test_bad_magic_and_truncation(self):
        with open(build("v3", "nef"), "rb") as fh:
            raw = fh.read()
        with self.assertRaises(DecodeError):
            parse_nef(b"XXXX" + raw[4:])
        with self.assertRaises(DecodeError):
            parse_nef(raw[:100])
        with self.assertRaises(DecodeError):
            parse_nef(raw + b"\0")

    def test_checksum_definition(self):
        # sha256(sha256(data))[:4], little-endian
        self.assertEqual(nef_checksum(b""), 0xE2E0F65D)


class HashAndFlagTests(unittest.TestCase):
    def test_hash_byte_order(self):
        le = hex_to_hash(FLM)
        self.assertEqual(le[:2], bytes.fromhex("28ab"))
        self.assertEqual(hash_to_hex(le), FLM)
        with self.assertRaises(ValueError):
            hex_to_hash("0x1234")

    def test_flags(self):
        self.assertEqual(flags_name(15), "All")
        self.assertEqual(flags_name(5), "ReadOnly")
        self.assertEqual(flags_name(9), "ReadStates, AllowNotify")
        self.assertEqual(flags_name(0), "None")

    def test_invoke_script_matches_what_the_node_echoes(self):
        # the node echoed this exact script back for getContract(FLM) on 2026-11-12
        self.assertEqual(
            invoke_script_get_contract(FLM),
            "DBQoqxh02keq2Cycs1GIVSeBUh8V8BHAHwwLZ2V0Q29udHJhY3QMFP2j+kNG6lMqJY/El92t22Q3yf3/QWJ9W1I=",
        )
        tail = base64.b64decode(invoke_script_get_contract(FLM, counter_only=True))[-7:]
        self.assertEqual(tail, bytes([0x4A, 0xD8, 0x24, 0x04, 0x11, 0xCE, 0x40]))


class StackTests(unittest.TestCase):
    def test_decode_types(self):
        item = {
            "type": "Array",
            "value": [
                {"type": "Integer", "value": "-7"},
                {"type": "Boolean", "value": True},
                {"type": "Any"},
                {"type": "ByteString", "value": "AQI="},
                {"type": "Map", "value": [{"key": {"type": "ByteString", "value": "YQ=="}, "value": {"type": "Integer", "value": "1"}}]},
            ],
        }
        self.assertEqual(decode_stack(item), [-7, True, None, b"\x01\x02", [(b"a", 1)]])
        with self.assertRaises(DecodeError):
            decode_stack({"type": "Weird", "value": 1})

    def test_historic_state_equals_getcontractstate(self):
        """The manifest/NEF decoded from a historic getContract equal what getcontractstate returns."""
        live = ContractState.from_rpc(replay("router-check.json").call("getcontractstate", [ROUTER]))
        hist = states_at(replay("router-history.json"), ROUTER, [2642845])[2642845]
        nef = dict(hist.nef)
        self.assertTrue(nef.pop("checksum_ok"))
        self.assertEqual(hist.manifest, live.manifest)
        self.assertEqual(nef, live.nef)
        self.assertEqual((hist.id, hist.updatecounter, hist.hash), (live.id, live.updatecounter, live.hash))

    def test_from_files(self):
        st = ContractState.from_files(build("v3", "manifest.json"), build("v3", "nef"))
        self.assertEqual(st.manifest["name"], "FlamingoSwapRouter")
        self.assertGreater(st.extra["manifest_bytes"], 1000)
        self.assertEqual(st.manifest, load_json(build("v3", "manifest.json")))


if __name__ == "__main__":
    unittest.main()
