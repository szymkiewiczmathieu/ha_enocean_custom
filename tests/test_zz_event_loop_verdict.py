"""Last module of the discovery order: the suite-wide event-loop verdict.

``unittest`` discovery sorts test files by name, so this module runs after
every other one. The guard installed by ``tests/test_event_loop_guard.py``
raises where a blocking call happens, but a broad ``except Exception`` on the
way up (Home Assistant wraps entry set-up and flow steps in one) can swallow
that. Anything recorded during the run is reported here.

Run on its own, the module has nothing to report and passes.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from tests.event_loop_guard import SUITE_GUARD


class EventLoopVerdictTests(unittest.TestCase):
    def test_no_integration_code_blocked_the_event_loop_during_the_suite(self):
        self.assertEqual(
            SUITE_GUARD.violations,
            [],
            "blocking calls ran inside the event loop during this test run:\n"
            + SUITE_GUARD.describe(),
        )


if __name__ == "__main__":
    unittest.main()
