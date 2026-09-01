"""Image to stroke-path conversion.

Turns a binary image into a list of continuous polylines, so the drawer can emit
one stroke per path instead of one tiny drag per scanline run. Pure logic: no
GUI, no mouse automation, so it can be exercised headless.

Coordinate space is the output grid (``settings.size``); scaling by pixel size
happens at draw time. Ink convention matches the rest of the app: black is ink.
"""

from math import hypot

import cv2
import numpy as np
from PIL import Image

OUTLINE = "Outline"
OUTLINE_HATCH = "Outline + Hatch"
MODES = (OUTLINE, OUTLINE_HATCH)


def build_mask(image, size):
    """Resize an already-binarized image into a uint8 mask where 255 means ink.

    ``image`` is expected to be black-and-white already; resampling to ``size``
    reintroduces intermediate values, so the midpoint is the only meaningful cut
    here. Re-applying the user threshold would make the ink area depend on the
    threshold slider a second time, and on the output size.

    No morphological cleanup happens here: a 3x3 opening erases every stroke
    thinner than three pixels, which destroys line-art sources outright.
    Speckle is dropped by ``min_perimeter`` in extract_outline_paths instead.
    """

    im = image.resize(size, Image.Resampling.LANCZOS).convert("L")
    data = np.asarray(im)

    # The app draws the dark areas, so ink is everything below the midpoint.
    return np.where(data < 128, 255, 0).astype(np.uint8)


def _find_contours(mask):
    found = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    # OpenCV 3 returned (image, contours, hierarchy); 4+ returns (contours, hierarchy).
    return found[-2]


def _dedupe(points):
    out = []
    for pt in points:
        if not out or pt != out[-1]:
            out.append(pt)
    return out


def extract_outline_paths(mask, epsilon=1.0, min_perimeter=12.0):
    """Trace ink boundaries as closed polylines.

    ``min_perimeter`` drops contours too small to be worth a stroke, which is
    what keeps speckle from turning into dots. ``epsilon`` is the
    Ramer-Douglas-Peucker tolerance and controls vertex count.
    """

    paths = []
    for contour in _find_contours(mask):
        if cv2.arcLength(contour, True) < min_perimeter:
            continue

        if epsilon > 0:
            contour = cv2.approxPolyDP(contour, epsilon, True)

        points = _dedupe([(int(p[0][0]), int(p[0][1])) for p in contour])
        if len(points) < 2:
            continue

        points.append(points[0])  # close the loop
        paths.append(points)

    return paths


def _row_runs(row, min_run):
    """Start/end x of each ink run in a boolean row, ignoring runs below min_run."""

    padded = np.concatenate(([False], row, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    runs = []
    for start, end in zip(edges[::2], edges[1::2]):
        if end - start >= min_run:
            runs.append((int(start), int(end) - 1))
    return runs


def extract_hatch_paths(mask, spacing=4, min_run=2, connect_tol=None):
    """Fill ink areas with a serpentine hatch.

    Scanline runs are chained head-to-tail into long zig-zag strokes whenever the
    next run starts close enough, so a solid region costs a handful of strokes
    instead of one per row.
    """

    spacing = max(1, int(spacing))
    if connect_tol is None:
        connect_tol = spacing * 1.5

    height = mask.shape[0]
    strokes = []
    current = None

    for index, y in enumerate(range(0, height, spacing)):
        runs = _row_runs(mask[y] > 0, min_run)
        if index % 2:
            runs = [(x1, x0) for x0, x1 in reversed(runs)]

        for x0, x1 in runs:
            segment = [(x0, y), (x1, y)]
            if current is not None and hypot(segment[0][0] - current[-1][0],
                                             segment[0][1] - current[-1][1]) <= connect_tol:
                current.extend(segment)
            else:
                if current is not None:
                    strokes.append(current)
                current = segment

    if current is not None:
        strokes.append(current)

    return [_dedupe(s) for s in strokes if len(_dedupe(s)) >= 2]


def build_paths(image, size, mode=OUTLINE, epsilon=1.0,
                min_perimeter=12.0, spacing=4, min_run=2):
    """Full pipeline: rescale ``image`` to ``size`` and return the strokes to draw."""

    mask = build_mask(image, size)
    paths = extract_outline_paths(mask, epsilon, min_perimeter)

    if mode == OUTLINE_HATCH:
        paths.extend(extract_hatch_paths(mask, spacing, min_run))

    return paths


def path_stats(paths):
    """(stroke count, vertex count) for the given paths."""

    return len(paths), sum(len(p) for p in paths)


def legacy_stroke_count(mask):
    """Strokes the old per-run scanline algorithm would have emitted.

    Kept as the baseline the vector pipeline is measured against.
    """

    return sum(len(_row_runs(row > 0, 1)) for row in mask)
