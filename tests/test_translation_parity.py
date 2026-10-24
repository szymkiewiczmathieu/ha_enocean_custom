"""Every shipped language carries every key of strings.json, placeholders included.

``strings.json`` is the source of truth and ``translations/en.json`` must be
its byte-for-byte copy (the CI compares them). French, German and Dutch must
have exactly the same tree of keys, and every leaf must use exactly the same
``{placeholders}`` as its English original, or Home Assistant renders a raw
key or crashes on a missing format argument at runtime.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "enocean_custom"
LANGUAGES = ("en", "fr", "de", "nl")
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


def _leaves(node: object, prefix: str = "") -> dict[str, str]:
    if isinstance(node, dict):
        out: dict[str, str] = {}
        for key, value in node.items():
            out.update(_leaves(value, f"{prefix}/{key}"))
        return out
    return {prefix: str(node)}


class TranslationParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.strings = json.loads((COMPONENT / "strings.json").read_text("utf-8"))
        cls.english = _leaves(cls.strings)
        cls.translations = {
            lang: _leaves(
                json.loads(
                    (COMPONENT / "translations" / f"{lang}.json").read_text("utf-8")
                )
            )
            for lang in LANGUAGES
        }

    def test_english_runtime_translation_is_the_source_of_truth(self):
        self.assertEqual(
            (COMPONENT / "strings.json").read_bytes(),
            (COMPONENT / "translations" / "en.json").read_bytes(),
        )

    def test_every_language_has_exactly_the_english_keys(self):
        for lang, leaves in self.translations.items():
            with self.subTest(lang=lang):
                self.assertEqual(sorted(self.english), sorted(leaves))

    def test_every_leaf_keeps_its_placeholders(self):
        for lang, leaves in self.translations.items():
            for key, english in self.english.items():
                with self.subTest(lang=lang, key=key):
                    self.assertEqual(
                        set(_PLACEHOLDER.findall(english)),
                        set(_PLACEHOLDER.findall(leaves[key])),
                    )
                    self.assertTrue(leaves[key].strip(), "empty translation")

    def test_translations_are_actually_translated(self):
        # Proper nouns, units and words identical in both languages are
        # allowed; whole sections copied from English are not.
        for lang in ("fr", "de", "nl"):
            leaves = self.translations[lang]
            identical = sum(
                1
                for key, english in self.english.items()
                if leaves[key] == english and not english.startswith("[%key")
            )
            with self.subTest(lang=lang):
                self.assertLess(identical, len(self.english) * 0.05)

    def test_subentry_flow_strings_exist(self):
        device = self.strings["config_subentries"]["device"]
        self.assertEqual(set(device["initiate_flow"]), {"user", "reconfigure"})
        self.assertEqual(set(device["step"]), {"user", "details", "reconfigure"})
        self.assertIn("already_configured", device["abort"])
        self.assertIn("d2_01_refresh", self.strings["entity"]["button"])
        self.assertIn("actuators", self.strings["options"]["step"])


if __name__ == "__main__":
    unittest.main()
