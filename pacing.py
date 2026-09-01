"""How fast the target application can actually absorb a drawing.

A freehand tool records the pointer once per frame: it stores the pointerdown
position, whatever it sees on each sampling tick, and the pointerup position.
Anything emitted between two ticks is lost, and the surviving points get joined
by straight lines. Emitting faster than the target samples therefore does not
draw the same picture faster - it draws a different picture.

Everything here is pure so the whole model can be exercised without a display,
which is how the original defect was found and how the fix is proved.
"""

from collections import namedtuple
from math import hypot

import numpy as np

# mouseDown + settle + settle + mouseUp, per stroke.
STROKE_OVERHEAD = 0.03

# Floor on the per-point pause, so a hopeless budget cannot ask for an
# infinitely fast emission that no target could see at all.
MIN_DWELL = 0.0005

# Cost of one pyautogui.moveTo with PAUSE=0: the X11 round trip is not free.
# Counted in the estimate and the simulation so the plan budgets for it instead
# of discovering the overhead mid-drawing.
POINT_EMIT = 0.001

# Emitting exactly at the frame rate leaves no room for sleep jitter: one short
# sleep and two vertices land in the same frame, and one is silently lost.
# Emitting slightly slower can only skip frames - never vertices - so the exact
# plan paces itself against a slightly longer frame.
FRAME_MARGIN = 1.05

# Ramer-Douglas-Peucker tolerance used to shed points that cost a frame each and
# move the line by less than half a pixel.
DECIMATE_EPS = 0.5

# Spacing used when interpolating extra points for a lossy plan.
DENSIFY_STEP = 8

# Simplified paths can jump a long way between vertices, and some drawing apps
# ignore very large pointer deltas. Segments longer than this many *screen*
# pixels get intermediate points. Applied here rather than at emission time so
# the extra points are counted by the estimate and reproduced by the simulation.
MAX_SEGMENT = 200

Plan = namedtuple("Plan", "paths dwell exact fits seconds points strokes rate_hz")


def path_points(paths):
    return sum(len(path) for path in paths)


def _rdp(points, epsilon):
    """Ramer-Douglas-Peucker with a strict perpendicular-distance bound.

    Written out rather than delegated to ``cv2.approxPolyDP``: measured on a
    1844-point contour, OpenCV leaves points 5.0px off the result for
    ``epsilon=0.5`` when the polyline is open, and 1.0px when closed. Its
    epsilon is not the guarantee claimed here, and the whole point of this step
    is that the deviation is provably invisible.

    Iterative so an 8000-point contour cannot blow the recursion limit.
    """

    count = len(points)
    if count < 3:
        return list(points)

    pts = np.asarray(points, dtype=float)
    keep = np.zeros(count, dtype=bool)
    keep[0] = keep[-1] = True

    stack = [(0, count - 1)]
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue

        start, end = pts[first], pts[last]
        middle = pts[first + 1:last]
        edge = end - start
        length = hypot(edge[0], edge[1])

        if length < 1e-12:
            # Closed contours start and end on the same point; fall back to the
            # distance from that point instead of from a degenerate line.
            distance = np.hypot(*(middle - start).T)
        else:
            offset = middle - start
            # 2-D cross product, written out: numpy 2 dropped the 2-D case.
            area = edge[0] * offset[:, 1] - edge[1] * offset[:, 0]
            distance = np.abs(area) / length

        worst = int(np.argmax(distance))
        if distance[worst] > epsilon:
            split = first + 1 + worst
            keep[split] = True
            stack.append((first, split))
            stack.append((split, last))

    return [points[index] for index in np.flatnonzero(keep)]


def decimate(paths, epsilon=DECIMATE_EPS):
    """Drop points that move the line by less than ``epsilon``.

    No point of the original sits further than ``epsilon`` from the result. At
    half a pixel that is invisible, which is what makes this safe to apply even
    when the user asked for no simplification at all: it is not a change of
    shape, only of encoding, and it buys back the frames those points cost.
    """

    if epsilon <= 0:
        return paths

    return [_rdp(path, epsilon) if len(path) >= 3 else path for path in paths]


def densify(paths, max_step=DENSIFY_STEP):
    """Insert points so no segment is longer than ``max_step``.

    Only worth doing for a lossy plan: if the target misses a sample, the ones it
    does catch still land on the intended line instead of cutting the corner
    between two distant vertices. Original points are never moved.
    """

    if max_step <= 0:
        return paths

    out = []
    for path in paths:
        if len(path) < 2:
            out.append(path)
            continue

        dense = [path[0]]
        for start, end in zip(path, path[1:]):
            distance = hypot(end[0] - start[0], end[1] - start[1])
            steps = int(distance // max_step)

            for step in range(1, steps + 1):
                fraction = step * max_step / distance
                if fraction < 1.0:
                    dense.append((start[0] + (end[0] - start[0]) * fraction,
                                  start[1] + (end[1] - start[1]) * fraction))
            dense.append(end)

        out.append(dense)

    return out


def estimate(paths, dwell, stroke_overhead=STROKE_OVERHEAD, point_emit=POINT_EMIT):
    return path_points(paths) * (dwell + point_emit) + len(paths) * stroke_overhead


def plan_emission(paths, rate_hz=60, budget_s=180.0, pixel_size=1,
                  decimate_eps=DECIMATE_EPS, densify_step=DENSIFY_STEP,
                  stroke_overhead=STROKE_OVERHEAD, max_segment=MAX_SEGMENT,
                  point_emit=POINT_EMIT, frame_margin=FRAME_MARGIN):
    """Decide how to emit ``paths`` so the result matches the preview.

    Exact means one vertex per frame of the target (paced with a small margin,
    see FRAME_MARGIN), which is the only pace that guarantees every vertex is
    seen. When that does not fit the budget, points are first shed losslessly,
    and only if it still does not fit is sampling fidelity given up.

    The returned ``paths`` are exactly what will be emitted - the long-segment
    cap is applied here, not at emission time, so the estimate, the simulation
    and the drawing all describe the same point sequence.
    """

    frame = frame_margin / max(1, rate_hz)
    cap = max_segment / max(1, pixel_size)

    # Pen-up travel between strokes gets a frame of its own (see drawPaths):
    # the release must be sampled before the cursor moves on, or the target
    # joins the two strokes with a line the preview never showed.
    overhead = stroke_overhead + frame

    def finish(points_paths, dwell, exact):
        seconds = estimate(points_paths, dwell, overhead, point_emit)
        return Plan(points_paths, dwell, exact, seconds <= budget_s + 1e-9,
                    seconds, path_points(points_paths), len(points_paths),
                    rate_hz)

    capped = densify(paths, cap)
    plan = finish(capped, frame, True)
    if plan.fits:
        return plan

    # Decimating lengthens segments, so the cap has to be re-applied after it.
    lean = densify(decimate(capped, decimate_eps), cap)
    plan = finish(lean, frame, True)
    if plan.fits:
        return plan

    dense = densify(lean, min(densify_step, cap))
    points = path_points(dense)
    available = max(budget_s - len(dense) * overhead - points * point_emit, 0.0)
    dwell = max(MIN_DWELL, available / max(points, 1))

    return finish(dense, dwell, False)


def simulate_capture(paths, dwell, rate_hz, stroke_overhead=STROKE_OVERHEAD,
                     point_emit=POINT_EMIT):
    """The strokes the target application actually ends up with.

    Feeding the preview from this is what keeps the preview honest: it is the
    same model the pacing decision is made with.

    Sampling is modelled relative to the last captured point. A real target
    ticks on an absolute clock, so treat the result as a close approximation,
    not a bound.
    """

    frame = 1.0 / max(1, rate_hz)
    clock = 0.0
    out = []

    for path in paths:
        if len(path) < 2:
            continue

        position = path[0]
        captured = [position]

        clock += stroke_overhead / 2      # mouseDown and its settle
        nextSample = clock + frame

        for point in path[1:]:
            clock += dwell + point_emit
            position = point

            if clock >= nextSample:
                if captured[-1] != position:
                    captured.append(position)
                nextSample = clock + frame

        # The pointerup carries a position of its own, so the final vertex of a
        # stroke always survives.
        if captured[-1] != position:
            captured.append(position)

        clock += stroke_overhead / 2

        # A stroke whose whole emission lands inside one frame still reaches the
        # target as a pointerdown/pointerup pair - a dot. Dropping it here would
        # make the preview show less than what is actually drawn.
        if len(captured) == 1:
            captured = [captured[0], captured[0]]

        out.append(captured)

    return out


def format_duration(seconds):
    if seconds < 1:
        return "<1s"
    if round(seconds) < 60:
        return "%ds" % round(seconds)

    minutes, rest = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return "%dm%02ds" % (minutes, rest)

    hours, minutes = divmod(minutes, 60)
    return "%dh%02dm" % (hours, minutes)
