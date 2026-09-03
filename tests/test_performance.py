from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from pure_module_loader import load_toolkit_module


performance = load_toolkit_module("performance")


class PerformanceTimingTests(unittest.TestCase):
    def test_repeated_categories_aggregate_totals_and_counts(self) -> None:
        timings = performance.PerformanceTimings()
        timings.add("parse", 0.1250004)
        timings.add("parse", 0.25)
        timings.add("load", 1.0)

        self.assertAlmostEqual(timings.seconds("parse"), 0.3750004)
        self.assertEqual(timings.seconds("missing"), 0.0)
        self.assertEqual(
            timings.payload(),
            {
                "schema_version": 1,
                "totals_seconds": {"load": 1.0, "parse": 0.375},
                "counts": {"load": 1, "parse": 2},
            },
        )
        self.assertEqual(json.loads(timings.to_json()), timings.payload())

    def test_active_context_collects_direct_and_decorated_timings(self) -> None:
        timings = performance.PerformanceTimings()
        token = performance.set_active_timings(timings)

        @performance.timed("decorated")
        def fixture() -> str:
            return "result"

        try:
            performance.record_timing("direct", 0.5)
            with patch.object(
                performance.time,
                "perf_counter",
                side_effect=(10.0, 10.25),
            ):
                self.assertEqual(fixture(), "result")
        finally:
            performance.reset_active_timings(token)

        self.assertEqual(timings.seconds("direct"), 0.5)
        self.assertEqual(timings.seconds("decorated"), 0.25)
        self.assertIsNone(performance.active_timings())


if __name__ == "__main__":
    unittest.main()
