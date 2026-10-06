"""Clipboard history retention: age limit and size limit (no display needed)."""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class RetentionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["XDG_STATE_HOME"] = os.path.join(self.tmp.name, "state")
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.tmp.name, "config")
        from snapshot_kwin.clipboard import History
        self.History = History

    def tearDown(self):
        self.tmp.cleanup()

    def config(self, **kw):
        d = os.path.join(os.environ["XDG_CONFIG_HOME"], "snapshot-kwin")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "config.json"), "w") as f:
            json.dump(kw, f)

    def test_defaults_half_year_5gib(self):
        h = self.History(log=lambda *_: None)
        self.assertEqual(h.config["max_age_days"], 183)
        self.assertEqual(h.config["max_bytes"], 5 * 1024 ** 3)

    def test_age_limit(self):
        h = self.History(log=lambda *_: None)
        h._add("text", b"old", text="old")
        h.items[0]["ts"] = time.time() - 200 * 86400
        h._add("text", b"new", text="new")
        self.assertEqual([i["text"] for i in h.items], ["new"])

    def test_size_limit_drops_oldest_images_first(self):
        self.config(max_bytes=2500)
        h = self.History(log=lambda *_: None)
        for n in range(4):
            h._add("image", bytes([n]) * 1000)
            h.items[0]["ts"] = time.time() - (10 - n)
            h._save()
        h._add("text", b"keep me", text="keep me")
        images = [i for i in h.items if i["kind"] == "image"]
        self.assertLessEqual(sum(h._size(i) for i in images), 2500)
        self.assertEqual(len(images), 2)  # the two newest survive
        self.assertTrue(any(i["kind"] == "text" for i in h.items))
        files = os.listdir(h.dir)
        self.assertEqual(len([f for f in files if f.endswith(".png")]), 2)  # files really deleted


if __name__ == "__main__":
    unittest.main()
