"""Syntax and pure-logic checks for the dependency-free admin panel."""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
PANEL_JS = (
    ROOT / "custom_components" / "enocean_custom" / "www" / "enocean-custom-panel.js"
)
LOGIC_TEST = ROOT / "tests" / "panel_logic_test.cjs"
NODE = shutil.which("node")


class PanelSourceTests(unittest.TestCase):
    def test_panel_has_no_remote_assets_or_unsafe_data_html(self):
        source = PANEL_JS.read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", source)
        self.assertNotIn("outerHTML", source)
        self.assertNotIn("fetch(", source)
        self.assertNotIn("@import", source)
        # The W3C SVG namespace is an identifier, not a fetched asset.
        asset_source = source.replace("http://www.w3.org/2000/svg", "")
        self.assertNotIn("http://", asset_source)
        self.assertNotIn("https://", asset_source)
        self.assertIn("attachShadow", source)


@unittest.skipUnless(NODE, "Node.js not installed")
class PanelJavaScriptTests(unittest.TestCase):
    def test_javascript_syntax(self):
        self.assertIsNotNone(NODE)
        assert NODE is not None
        result = subprocess.run(
            [NODE, "--check", str(PANEL_JS)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_pure_panel_helpers(self):
        self.assertIsNotNone(NODE)
        assert NODE is not None
        result = subprocess.run(
            [NODE, str(LOGIC_TEST)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PANEL_LOGIC_OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
