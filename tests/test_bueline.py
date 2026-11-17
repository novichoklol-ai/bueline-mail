#!/usr/bin/env python3
"""End-to-end tests for BueLine Mail: real server thread + real HTTP client."""
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "client"))

os.environ["MAILRELAY_TOKEN_ALICE"] = "tok-alice-aaa"
os.environ["MAILRELAY_TOKEN_BOB"] = "tok-bob-bbb"
os.environ["MAILRELAY_TOKEN_CAROL"] = "tok-carol-ccc"

import bueline_mail  # noqa: E402
from bueline import Client  # noqa: E402


class RelayFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bueline-test-")
        cls.db = os.path.join(cls.tmp, "test.db")
        bueline_mail.log.disabled = True
        cls.server = bueline_mail.ThreadingHTTPServer(("127.0.0.1", 0), bueline_mail.Handler)
        cls.server.store = bueline_mail.Store(cls.db)
        cls.server.tokens = bueline_mail.load_tokens()
        cls.server.stale_minutes = 45
        cls.server.max_body = 65536
        cls.server.daemon_threads = True
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.alice = Client("http://127.0.0.1:%d" % cls.port, token="tok-alice-aaa")
        cls.bob = Client("http://127.0.0.1:%d" % cls.port, token="tok-bob-bbb")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_01_health_no_auth(self):
        r = self.alice.health()
        self.assertTrue(r["ok"])
        self.assertEqual(r["service"], "bueline-mail")

    def test_02_unauthorized(self):
        bad = Client("http://127.0.0.1:%d" % self.port, token="wrong")
        try:
            bad.inbox()
            self.fail("expected 401")
        except Exception as e:
            self.assertIn("401", str(e))

    def test_03_boxes_listing(self):
        self.assertEqual(self.alice.boxes(), ["alice", "bob", "carol"])

    def test_04_send_and_inbox_auto_delivered(self):
        mid = self.alice.send("bob", subject="hello", body="privet", ref="t-1")
        self.assertIsInstance(mid, int)
        msgs = self.bob.inbox()
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["id"], mid)
        self.assertEqual(msgs[0]["state"], "delivered")
        self.assertEqual(msgs[0]["body"], "privet")
        # inbox is drained: second fetch is empty
        self.assertEqual(self.bob.inbox(), [])

    def test_05_ack_monotonic(self):
        mid = self.alice.send("bob", subject="s", body="b")
        self.bob.inbox()
        r = self.bob.ack([mid], "read")
        self.assertEqual(r["updated"], [mid])
        # cannot go backwards
        r = self.bob.ack([mid], "delivered")
        self.assertEqual(r["skipped"], [mid])
        # forward still works
        r = self.bob.ack([mid], "done")
        self.assertEqual(r["updated"], [mid])

    def test_06_ack_foreign_box_skipped(self):
        mid = self.alice.send("bob", subject="s", body="b")
        r = self.alice.ack([mid], "read")  # alice acks bob's mail -> skipped
        self.assertEqual(r["skipped"], [mid])

    def test_07_bad_recipient(self):
        import urllib.request, json as _json
        req = urllib.request.Request(self.alice.base_url + "/v1/send",
                                     data=_json.dumps({"to": "nobody", "body": "x"}).encode(),
                                     method="POST")
        req.add_header("Authorization", "Bearer tok-alice-aaa")
        try:
            urllib.request.urlopen(req)
            self.fail("expected 400")
        except Exception as e:
            body = _json.loads(e.read().decode())
            self.assertEqual(body["error"], "bad_recipient")
            self.assertIn("carol", body["boxes"])

    def test_08_too_large(self):
        # slightly over max_body -> 413 (parsed, rejected by size check)
        try:
            self.alice.send("bob", subject="s", body="x" * 66000)
            self.fail("expected 413")
        except Exception as e:
            self.assertIn("413", str(e))
        # way over max_body + slack -> 400 (rejected before parsing)
        try:
            self.alice.send("bob", subject="s", body="x" * 200000)
            self.fail("expected 400")
        except Exception as e:
            self.assertIn("400", str(e))

    def test_09_status_counts_and_stale(self):
        before = self.bob.status()["counts"]["new"]
        mid = self.alice.send("bob", subject="stale-me", body="b")
        st = self.bob.status()
        self.assertEqual(st["counts"]["new"], before + 1)
        # force the message to look old
        with self.server.store._lock:
            self.server.store.db.execute(
                "UPDATE messages SET state_ts='2020-01-01T00:00:00Z' WHERE id=?", (mid,))
            self.server.store.db.commit()
        st = self.bob.status()
        self.assertTrue(any(m["id"] == mid for m in st["stale"]))

    def test_10_unicode_roundtrip(self):
        self.alice.send("carol", subject="тема", body="привет, 你好")
        carol = Client("http://127.0.0.1:%d" % self.port, token="tok-carol-ccc")
        msgs = carol.inbox()
        self.assertEqual(msgs[-1]["body"], "привет, 你好")
        self.assertEqual(msgs[-1]["subject"], "тема")


if __name__ == "__main__":
    unittest.main(verbosity=2)
