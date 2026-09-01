"""Headless tests for the stroke pipeline - no display, no mouse."""

import unittest

import numpy as np
from PIL import Image

import vectorize


def circle_image(size=200, radius=60, speckle=False):
    data = np.full((size, size), 255, dtype=np.uint8)
    yy, xx = np.mgrid[0:size, 0:size]
    data[(yy - size // 2) ** 2 + (xx - size // 2) ** 2 < radius ** 2] = 0
    if speckle:
        data[5, 5] = 0
    return Image.fromarray(data)


class BuildMaskTest(unittest.TestCase):
    def test_dark_pixels_become_ink(self):
        mask = vectorize.build_mask(circle_image(), (200, 200))
        self.assertEqual(mask[100, 100], 255)
        self.assertEqual(mask[0, 0], 0)


class OutlineTest(unittest.TestCase):
    def test_circle_is_one_closed_stroke(self):
        paths = vectorize.build_paths(circle_image(), (200, 200),
                                      mode=vectorize.OUTLINE,
                                      epsilon=1.0, min_perimeter=12)
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0][0], paths[0][-1])

    def test_speckle_dropped_by_min_perimeter(self):
        paths = vectorize.build_paths(circle_image(speckle=True), (200, 200),
                                      mode=vectorize.OUTLINE,
                                      epsilon=1.0, min_perimeter=12)
        self.assertEqual(len(paths), 1)


class HatchTest(unittest.TestCase):
    def test_hatch_adds_fill_strokes(self):
        outline = vectorize.build_paths(circle_image(), (200, 200),
                                        mode=vectorize.OUTLINE,
                                        epsilon=1.0, min_perimeter=12)
        hatched = vectorize.build_paths(circle_image(), (200, 200),
                                        mode=vectorize.OUTLINE_HATCH,
                                        epsilon=1.0, min_perimeter=12,
                                        spacing=4)
        self.assertGreater(len(hatched), len(outline))

    def test_hatch_strokes_stay_on_sampled_rows(self):
        strokes = vectorize.extract_hatch_paths(
            vectorize.build_mask(circle_image(), (200, 200)), spacing=4)
        self.assertTrue(strokes)
        for stroke in strokes:
            for _x, y in stroke:
                self.assertEqual(y % 4, 0)
            self.assertGreaterEqual(len(stroke), 2)


if __name__ == "__main__":
    unittest.main()
