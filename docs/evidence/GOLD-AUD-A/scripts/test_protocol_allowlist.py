"""Offline unit tests for protocol_allowlist.py. No network. Run from this directory: python3 -B test_protocol_allowlist.py"""
import json
import unittest

import protocol_allowlist as pa


def L(t, argv, **kw):
    e = {"conn": "c1", "utc": "2026-10-16T09:00:%06.3fZ" % t, "argv": argv, "reply_kind": "x", "reply_size": 10}
    e.update(kw)
    return json.dumps(e)


K = "hermes:candles:XAU_USD:H1:latest:v1"


class T(unittest.TestCase):
    def test_clean_sequence_passes(self):
        lines = [L(0.0, ["PING"]), L(1.0, ["INFO", "server"]), L(2.0, ["DBSIZE"]),
                 L(3.0, ["SCAN", "0", "MATCH", "hermes:*XAU_USD*", "COUNT", "200"]),
                 L(4.5, ["SCAN", "17", "MATCH", "hermes:*XAU_USD*", "COUNT", "200"]),
                 L(6.0, ["TYPE", K], reply_value="string"), L(6.2, ["STRLEN", K], reply_value=900),
                 L(6.4, ["GET", K], reply_size=900), L(6.6, ["PTTL", K])]
        self.assertTrue(pa.check_log(lines)["pass"])

    def test_forbidden_and_unlisted(self):
        for argv in (["KEYS", "*"], ["SET", K, "1"], ["CONFIG", "GET", "*"], ["SUBSCRIBE", "x"], ["FLUSHALL"],
                     ["XLEN", K], ["PUBSUB", "CHANNELS"]):
            self.assertTrue(pa.check_command(argv, {}), argv)

    def test_scan_shapes(self):
        ok = ["SCAN", "0", "MATCH", "hermes:*XAU_USD*", "COUNT", "200"]
        self.assertEqual(pa.check_command(ok, {}), [])
        for bad in (["SCAN", "0"], ["SCAN", "0", "COUNT", "200"], ["SCAN", "0", "MATCH", "*", "COUNT", "200"],
                    ["SCAN", "0", "MATCH", "hermes:*XAU_USD*", "COUNT", "201"],
                    ["SCAN", "0", "MATCH", "hermes:*XAU_USD*", "COUNT", "200", "TYPE", "string"]):
            self.assertTrue(pa.check_command(bad, {}), bad)

    def test_get_needs_prechecks(self):
        self.assertTrue(pa.check_command(["GET", K], {}))
        st = {"type": {K: "string"}, "strlen": {K: 70000}}
        self.assertTrue(pa.check_command(["GET", K], st))
        st["strlen"][K] = 100
        self.assertEqual(pa.check_command(["GET", K], st), [])
        st["type"][K] = "hash"
        self.assertTrue(pa.check_command(["GET", K], st))

    def test_hgetall_zrange_smembers(self):
        h = "hermes:signals:latest:XAU_USD"
        self.assertTrue(pa.check_command(["HGETALL", h], {}))
        lp = ("hermes:",)
        self.assertEqual(pa.check_command(["HGETALL", h], {"hlen": {h: 60}}, legacy_prefixes=lp), [])
        self.assertTrue(pa.check_command(["HGETALL", h], {"hlen": {h: 201}}, legacy_prefixes=lp))
        z = "hermes:candles:XAU_USD:D1:history:v1:index"
        self.assertEqual(pa.check_command(["ZRANGE", z, "0", "99"], {"zcard": {z: 35}}), [])
        self.assertTrue(pa.check_command(["ZRANGE", z, "0", "100"], {"zcard": {z: 35}}))
        self.assertTrue(pa.check_command(["ZRANGE", z, "-5", "-1"], {"zcard": {z: 35}}))
        self.assertTrue(pa.check_command(["ZRANGE", z, "0", "9"], {"zcard": {z: 5000}}))
        self.assertTrue(pa.check_command(["ZRANGE", z, "0", "9", "WITHSCORES"], {"zcard": {z: 5}}))
        self.assertTrue(pa.check_command(["SMEMBERS", z], {}))

    def test_info_sections(self):
        for s in ("server", "clients", "persistence"):
            self.assertEqual(pa.check_command(["INFO", s], {}), [])
        for bad in (["INFO"], ["INFO", "memory"], ["INFO", "keyspace"], ["INFO", "all"], ["INFO", "stats"]):
            self.assertTrue(pa.check_command(bad, {}), bad)

    def test_scan_pattern_exact_allow_list(self):
        def sc(p):
            return pa.check_command(["SCAN", "0", "MATCH", p, "COUNT", "200"], {})
        for bad in ("**", "?*", "[a-z]*", "other:*", "*", "", "hermes:*", "hermes:**", "billing:*XAU_USD*",
                    "*XAU_USD*", "*XAUUSD*", "hermes:*XAU_USD*x"):
            self.assertTrue(sc(bad), bad)
        self.assertEqual(sc("hermes:*XAU_USD*"), [])
        self.assertEqual(sc("hermes:*XAUUSD*"), [])
        self.assertEqual(pa.check_command(["SCAN", "0", "MATCH", "*XAU_USD*", "COUNT", "200"], {},
                                          allow_unprefixed_scan=True), [])
        self.assertEqual(pa.check_command(["SCAN", "0", "MATCH", "legacy:*XAU_USD*", "COUNT", "200"], {},
                                          extra_scan=("legacy:*XAU_USD*",)), [])
        self.assertTrue(pa.check_command(["SCAN", "0", "MATCH", "other:*", "COUNT", "200"], {},
                                         allow_unprefixed_scan=True))

    def test_key_fence(self):
        for key in ("other:system:key", "billing:XAU_USD", "hermes:candles:EUR_USD:H1:latest:v1",
                    "xhermes:health:v1", "hermes:health:v1x", "billing:hermes:health:v1"):
            self.assertTrue(pa.check_command(["TYPE", key], {}), key)
        self.assertEqual(pa.check_command(["TYPE", "hermes:health:v1"], {}), [])
        # content reads only on governed family patterns
        st = {"type": {"hermes:candles:XAUUSD:H1:latest:v1": "string"}, "strlen": {"hermes:candles:XAUUSD:H1:latest:v1": 5}}
        self.assertTrue(pa.check_command(["GET", "hermes:candles:XAUUSD:H1:latest:v1"], st))      # alias: metadata only
        self.assertEqual(pa.check_command(["PTTL", "hermes:candles:XAUUSD:H1:latest:v1"], {}), [])
        self.assertTrue(pa.check_command(["GET", "hermes:candles:XAU_USD:M30:latest:v1"], {}))
        self.assertTrue(pa.check_command(["HGETALL", "hermes:signals:latest:XAU_USD"], {"hlen": {"hermes:signals:latest:XAU_USD": 5}}))
        self.assertEqual(pa.check_command(["HGETALL", "hermes:signals:latest:XAU_USD"], {"hlen": {"hermes:signals:latest:XAU_USD": 5}},
                                          legacy_prefixes=("hermes:",)), [])
        self.assertTrue(pa.check_command(["HGETALL", "billing:signals:latest:XAU_USD"], {"hlen": {"billing:signals:latest:XAU_USD": 5}},
                                         legacy_prefixes=("hermes:",)))

    def test_rate_limit_and_scan_gap_and_payload(self):
        burst = [L(0.0 + i * 0.05, ["PING"]) for i in range(12)]
        self.assertFalse(pa.check_log(burst)["pass"])
        scans = [L(0.0, ["SCAN", "0", "MATCH", "hermes:*XAU_USD*", "COUNT", "200"]),
                 L(0.4, ["SCAN", "9", "MATCH", "hermes:*XAU_USD*", "COUNT", "200"])]
        self.assertFalse(pa.check_log(scans)["pass"])
        big = [L(0.0, ["PING"], reply_size=70000)]
        self.assertFalse(pa.check_log(big)["pass"])

    def test_control_characters_and_trailing_newline(self):
        for suffix in ("\n", "\r", "\x00", "\t", " ", "\r\n"):
            for key in ("hermes:health:v1", "hermes:candles:XAU_USD:H1:latest:v1", "hermes:XAU_USD"):
                for cmd in ("TYPE", "PTTL"):
                    self.assertTrue(pa.check_command([cmd, key + suffix], {}), (cmd, key, suffix))
        st = {"type": {}, "strlen": {}}
        self.assertTrue(pa.check_command(["GET", "hermes:health:v1\n"], st))
        self.assertTrue(pa.check_command(["GET", "hermes:candles:XAU_USD:H1:latest:v1\n"], st))
        self.assertTrue(pa.check_command(["TYPE", "hermes:candles:XAU_USD: H1:latest:v1"], {}))
        self.assertTrue(pa.check_command(["SCAN", "0", "MATCH", "hermes:*XAU_USD*\n", "COUNT", "200"], {}))
        self.assertTrue(pa.check_command(["SCAN", "0\n", "MATCH", "hermes:*XAU_USD*", "COUNT", "200"], {}))
        self.assertTrue(pa.check_command(["SCAN", "0", "MATCH", "hermes:*XAU_USD*", "COUNT", "200 "], {}))

    def test_robustness_and_limits(self):
        # out-of-order timestamps are a recorded violation, not a crash
        r = pa.check_log([L(5.0, ["PING"]), L(1.0, ["PING"])])
        self.assertFalse(r["pass"])
        # non-numeric reply_size is a recorded violation, not a crash
        r = pa.check_log([L(0.0, ["PING"], reply_size="abc")])
        self.assertFalse(r["pass"])
        r = pa.check_log([L(0.0, ["PING"], reply_size=None)])
        self.assertTrue(r["pass"])
        # more than three scan passes
        lines = [L(i * 2.0, ["SCAN", "0", "MATCH", "hermes:*XAU_USD*", "COUNT", "200"]) for i in range(4)]
        r = pa.check_log(lines)
        self.assertFalse(r["pass"])
        self.assertEqual(r["scan_passes"], 4)
        # six-hour time-box
        e1 = json.loads(L(0.0, ["PING"]))
        e2 = json.loads(L(1.0, ["PING"]))
        e2["utc"] = "2026-10-16T16:00:05.000Z"
        self.assertFalse(pa.check_log([json.dumps(e1), json.dumps(e2)])["pass"])
        # malformed argv and unparseable lines do not crash
        self.assertFalse(pa.check_log(["{not json}"])["pass"])
        bad = json.loads(L(0.0, ["PING"]))
        bad["argv"] = "PING"
        self.assertFalse(pa.check_log([json.dumps(bad)])["pass"])
        # linear time: 20000 well-spaced commands finish quickly
        import time
        t0 = time.time()
        many = []
        for i in range(20000):
            e = json.loads(L(0.0, ["PING"]))
            e["utc"] = "2026-10-16T%02d:%02d:%02d.000Z" % ((i // 3600) % 6 + 3, (i // 60) % 60, i % 60)
            many.append(json.dumps(e))
        pa.check_log(many)
        self.assertLess(time.time() - t0, 5.0)

    def test_single_connection(self):
        a = json.loads(L(0.0, ["PING"]))
        b = json.loads(L(2.0, ["PING"]))
        b["conn"] = "c2"
        self.assertFalse(pa.check_log([json.dumps(a), json.dumps(b)])["pass"])


if __name__ == "__main__":
    unittest.main()
