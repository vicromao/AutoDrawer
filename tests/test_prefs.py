"""Headless tests for settings persistence."""

import json
import os
import tempfile
import unittest

import prefs


class SanitizeTest(unittest.TestCase):
    def test_unknown_keys_and_bad_types_fall_back(self):
        values = prefs.sanitize({"threshold": "abc", "nope": 1,
                                 "channel": "Ultraviolet"})
        self.assertEqual(values["threshold"], prefs.DEFAULTS["threshold"])
        self.assertEqual(values["channel"], prefs.DEFAULTS["channel"])
        self.assertNotIn("nope", values)

    def test_out_of_range_numbers_fall_back(self):
        self.assertEqual(prefs.sanitize({"threshold": 999})["threshold"],
                         prefs.DEFAULTS["threshold"])
        self.assertEqual(prefs.sanitize({"pixel_size": 0})["pixel_size"],
                         prefs.DEFAULTS["pixel_size"])

    def test_bool_default_rejects_string(self):
        self.assertTrue(prefs.sanitize({"lock_aspect": "yes"})["lock_aspect"])


class RoundTripTest(unittest.TestCase):
    def test_save_then_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.json")
            payload = dict(prefs.DEFAULTS, threshold=42, mode="Outline + Hatch")
            self.assertTrue(prefs.save(payload, path))
            loaded = prefs.load(path)
            self.assertEqual(loaded["threshold"], 42)
            self.assertEqual(loaded["mode"], "Outline + Hatch")

    def test_truncated_file_returns_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write('{"threshold": ')
            self.assertEqual(prefs.load(path), prefs.DEFAULTS)

    def test_save_drops_unknown_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.json")
            prefs.save(dict(prefs.DEFAULTS, malicious="key"), path)
            with open(path, "r", encoding="utf-8") as handle:
                self.assertNotIn("malicious", json.load(handle))


if __name__ == "__main__":
    unittest.main()
