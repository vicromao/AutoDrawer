"""Headless tests for the pacing model - no display, no mouse."""

import unittest

import pacing


def spiral_points(count=500):
    return [(float(i), float((i * 7) % 13)) for i in range(count)]


class PlanEmissionTest(unittest.TestCase):
    def setUp(self):
        self.paths = [spiral_points(),
                      [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)]]

    def test_exact_plan_paces_with_frame_margin(self):
        plan = pacing.plan_emission(self.paths, 60, 180.0, 1)
        self.assertTrue(plan.exact)
        self.assertTrue(plan.fits)
        self.assertAlmostEqual(plan.dwell, pacing.FRAME_MARGIN / 60)

    def test_estimate_counts_emit_and_pen_up_costs(self):
        plan = pacing.plan_emission(self.paths, 60, 180.0, 1)
        frame = pacing.FRAME_MARGIN / 60
        expected = (pacing.path_points(plan.paths) * (plan.dwell + pacing.POINT_EMIT)
                    + len(plan.paths) * (pacing.STROKE_OVERHEAD + frame))
        self.assertAlmostEqual(plan.seconds, expected)

    def test_tight_budget_forces_lossy_plan(self):
        plan = pacing.plan_emission(self.paths, 60, 0.05, 1)
        self.assertFalse(plan.exact)
        self.assertGreaterEqual(plan.dwell, pacing.MIN_DWELL)

    def test_simulation_keeps_stroke_parity(self):
        plan = pacing.plan_emission(self.paths, 60, 0.05, 1)
        sim = pacing.simulate_capture(plan.paths, plan.dwell, 60)
        self.assertEqual(len(sim), len(plan.paths))
        for captured in sim:
            self.assertGreaterEqual(len(captured), 2)

    def test_long_segments_are_capped(self):
        plan = pacing.plan_emission([[(0, 0), (5000, 0)]], 60, 600.0, 1)
        for start, end in zip(plan.paths[0], plan.paths[0][1:]):
            self.assertLessEqual(abs(end[0] - start[0]),
                                 pacing.MAX_SEGMENT + 1e-9)


class DecimateTest(unittest.TestCase):
    def test_endpoints_survive_and_point_count_drops(self):
        path = spiral_points()
        result = pacing.decimate([path], 0.5)[0]
        self.assertEqual(result[0], path[0])
        self.assertEqual(result[-1], path[-1])
        self.assertLess(len(result), len(path))

    def test_collinear_points_collapse_to_endpoints(self):
        path = [(float(i), 2.0 * i) for i in range(100)]
        self.assertEqual(pacing.decimate([path], 0.5)[0],
                         [path[0], path[-1]])


class DensifyTest(unittest.TestCase):
    def test_no_segment_longer_than_step(self):
        dense = pacing.densify([[(0, 0), (300, 0)]], 8)[0]
        for start, end in zip(dense, dense[1:]):
            self.assertLessEqual(end[0] - start[0], 8 + 1e-9)
        self.assertEqual(dense[0], (0, 0))
        self.assertEqual(dense[-1], (300, 0))


class FormatDurationTest(unittest.TestCase):
    def test_ranges(self):
        self.assertEqual(pacing.format_duration(0.4), "<1s")
        self.assertEqual(pacing.format_duration(45), "45s")
        self.assertEqual(pacing.format_duration(125), "2m05s")
        self.assertEqual(pacing.format_duration(3700), "1h01m")


if __name__ == "__main__":
    unittest.main()
