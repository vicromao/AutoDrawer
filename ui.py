"""Reusable interface pieces: theme, numeric control, placement overlay.

Kept apart from AutoDrawer.py so the geometry and value math can be exercised
without a display. The overlay takes the cursor and screen size as callables so
this module never has to import pyautogui.
"""

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

PALETTE = {
    "bg": "#f4f5f7",
    "surface": "#ffffff",
    "border": "#d7dbe0",
    "text": "#1f2430",
    "muted": "#6b7280",
    "accent": "#2563eb",
    "danger": "#dc2626",
    "ghost": "#e8eefc",
}

PAD = 8


def apply_theme(root):
    """Install the clam-based theme and return the style object."""

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass  # clam ships with every Tk build we support, but never hard-fail

    base = tkfont.nametofont("TkDefaultFont")
    base.configure(size=10)
    heading = base.copy()
    heading.configure(size=10, weight="bold")
    small = base.copy()
    small.configure(size=9)

    root.configure(background=PALETTE["bg"])
    root.option_add("*Font", base)

    style.configure(".", background=PALETTE["bg"], foreground=PALETTE["text"],
                    bordercolor=PALETTE["border"], focuscolor=PALETTE["accent"])
    style.configure("TFrame", background=PALETTE["bg"])
    style.configure("Surface.TFrame", background=PALETTE["surface"])
    style.configure("TLabel", background=PALETTE["bg"], foreground=PALETTE["text"])
    style.configure("Heading.TLabel", font=heading, foreground=PALETTE["text"])
    style.configure("Muted.TLabel", font=small, foreground=PALETTE["muted"])
    style.configure("Status.TLabel", font=small, foreground=PALETTE["muted"],
                    background=PALETTE["surface"], padding=(PAD, PAD // 2))
    style.configure("Warning.TLabel", font=small, foreground=PALETTE["danger"],
                    background=PALETTE["surface"], padding=(PAD, PAD // 2))

    style.configure("TButton", padding=(PAD + 2, PAD // 2), relief="flat",
                    background=PALETTE["surface"], bordercolor=PALETTE["border"])
    style.map("TButton",
              background=[("active", PALETTE["ghost"]), ("pressed", PALETTE["ghost"])],
              bordercolor=[("focus", PALETTE["accent"])])
    style.configure("Accent.TButton", background=PALETTE["accent"], foreground="#ffffff")
    style.map("Accent.TButton", background=[("active", "#1d4ed8"), ("pressed", "#1d4ed8")])
    style.configure("Danger.TButton", background=PALETTE["danger"], foreground="#ffffff")
    style.map("Danger.TButton", background=[("active", "#b91c1c"), ("pressed", "#b91c1c")])

    style.configure("TSeparator", background=PALETTE["border"])
    style.configure("TCheckbutton", background=PALETTE["bg"])
    style.configure("TScale", background=PALETTE["bg"], troughcolor=PALETTE["border"])
    style.configure("TSpinbox", fieldbackground=PALETTE["surface"],
                    bordercolor=PALETTE["border"], arrowsize=11, padding=(4, 2))
    style.configure("TCombobox", fieldbackground=PALETTE["surface"],
                    bordercolor=PALETTE["border"], padding=(4, 2))

    return style


def fit_box(content, box):
    """Scale and centering offsets to fit ``content`` inside ``box``.

    Returns ``(scale, offset_x, offset_y)``. Aspect ratio is preserved, so the
    unused axis is letterboxed. A zero-sized input yields a zero scale, which
    callers use to skip rendering before the window has been laid out.
    """

    contentW, contentH = content
    boxW, boxH = box

    if contentW <= 0 or contentH <= 0 or boxW <= 0 or boxH <= 0:
        return 0.0, 0.0, 0.0

    scale = min(boxW / contentW, boxH / contentH)
    return scale, (boxW - contentW * scale) / 2, (boxH - contentH * scale) / 2


def quantize(value, minimum, maximum, step):
    """Clamp ``value`` to the range and snap it onto the ``step`` grid."""

    value = max(minimum, min(maximum, value))

    if step:
        value = minimum + round((value - minimum) / step) * step
        value = max(minimum, min(maximum, value))

    return round(value, 6)


def point_in_rect(point, rect):
    """True when ``point`` falls inside ``rect`` given as (l, t, r, b)."""

    if rect is None:
        return False

    left, top, right, bottom = rect
    return left <= point[0] <= right and top <= point[1] <= bottom


def overlay_geometry(cursor, size, pixelSize, screen):
    """Where the drawing will land, and whether it fits on screen.

    Drawing starts at the cursor and grows right and down, matching how
    ``drawPaths`` offsets every point from ``pyautogui.position()``.
    """

    width = max(1, int(size[0] * pixelSize))
    height = max(1, int(size[1] * pixelSize))
    x, y = int(cursor[0]), int(cursor[1])

    fits = x >= 0 and y >= 0 and x + width <= screen[0] and y + height <= screen[1]

    return width, height, x, y, fits


class LabeledSlider(ttk.Frame):
    """A slider and a spinbox over one value.

    The slider is for coarse exploration, the spinbox for hitting an exact
    number - a 0-255 range on a 100px slider is about 2.5 units per pixel, which
    no amount of careful dragging can resolve. The spinbox also validates, so
    callers never have to defend against a half-typed field.
    """

    def __init__(self, parent, label, minimum, maximum, step=1, value=None,
                 on_change=None, width=5):
        super().__init__(parent)

        self.minimum = minimum
        self.maximum = maximum
        self.step = step
        self.integer = float(step).is_integer() and float(minimum).is_integer()
        self.on_change = on_change

        self._syncing = False
        self._value = quantize(minimum if value is None else value,
                               minimum, maximum, step)

        self._text = tk.StringVar(value=self._format(self._value))
        self._slider = tk.DoubleVar(value=self._value)

        ttk.Label(self, text=label).grid(row=0, column=0, sticky="w")

        self.spin = ttk.Spinbox(self, from_=minimum, to=maximum, increment=step,
                                textvariable=self._text, width=width,
                                command=self._commit_text, justify="right")
        self.spin.grid(row=0, column=1, sticky="e")

        self.scale = ttk.Scale(self, from_=minimum, to=maximum, orient="horizontal",
                               variable=self._slider, command=self._on_scale)
        self.scale.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 0))

        self.columnconfigure(0, weight=1)

        for widget in (self.spin, self.scale):
            widget.bind("<MouseWheel>", self._on_wheel)
            widget.bind("<Button-4>", self._on_wheel)
            widget.bind("<Button-5>", self._on_wheel)

        # Return commits the value and stops there. Without the "break" it
        # reaches the toplevel binding and fires a real drawing; the focus move
        # is what keeps it from being a dead key, so a second Return - now that
        # the spinbox no longer holds focus - draws as expected.
        self.spin.bind("<Return>", lambda e: self._commit_and_release())
        self.spin.bind("<FocusOut>", lambda e: self._commit_text())
        self.spin.bind("<Up>", lambda e: self._nudge(1) or "break")
        self.spin.bind("<Down>", lambda e: self._nudge(-1) or "break")

    # -- value access -----------------------------------------------------

    def get(self):
        return int(self._value) if self.integer else self._value

    def set(self, value, notify=False):
        value = quantize(value, self.minimum, self.maximum, self.step)
        changed = value != self._value
        self._value = value

        self._syncing = True
        self._text.set(self._format(value))
        self._slider.set(value)
        self._syncing = False

        if changed and notify and self.on_change:
            self.on_change(self.get())

    def configure_range(self, minimum, maximum):
        self.minimum, self.maximum = minimum, maximum
        self.spin.configure(from_=minimum, to=maximum)
        self.scale.configure(from_=minimum, to=maximum)
        self.set(self._value)

    # -- internals --------------------------------------------------------

    def _format(self, value):
        return str(int(value)) if self.integer else ("%g" % value)

    def _apply(self, value):
        value = quantize(value, self.minimum, self.maximum, self.step)

        if value == self._value:
            # Still resync the widgets: the text may be stale ("abc", "007").
            self._syncing = True
            self._text.set(self._format(value))
            self._slider.set(value)
            self._syncing = False
            return

        self._value = value
        self._syncing = True
        self._text.set(self._format(value))
        self._slider.set(value)
        self._syncing = False

        if self.on_change:
            self.on_change(self.get())

    def _on_scale(self, raw):
        if self._syncing:
            return

        value = quantize(float(raw), self.minimum, self.maximum, self.step)
        if value == self._value:
            return

        self._value = value
        self._syncing = True
        self._text.set(self._format(value))
        self._syncing = False

        if self.on_change:
            self.on_change(self.get())

    def _commit_and_release(self):
        self._commit_text()
        self.winfo_toplevel().focus_set()
        return "break"

    def _commit_text(self):
        if self._syncing:
            return

        try:
            value = float(self._text.get().strip().replace(",", "."))
        except ValueError:
            value = self._value  # empty or garbage: snap back to the last good one

        self._apply(value)

    def _nudge(self, direction):
        self._apply(self._value + direction * self.step)

    def _on_wheel(self, event):
        direction = 1 if getattr(event, "delta", 0) > 0 or event.num == 4 else -1
        self._nudge(direction)
        return "break"


class ScrollFrame(ttk.Frame):
    """Vertically scrollable container. Put widgets into ``.body``.

    The sidebar holds more controls than a short window can show, and a control
    the user cannot scroll to is a control that does not exist. The scrollbar
    hides itself whenever everything already fits.
    """

    def __init__(self, parent, width=260):
        super().__init__(parent)

        self.canvas = tk.Canvas(self, highlightthickness=0, width=width,
                                background=PALETTE["bg"])
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = ttk.Frame(self.canvas)

        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=self._on_scroll)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.bar.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.body.bind("<Configure>", self._on_body)
        self.canvas.bind("<Configure>", self._on_canvas)

        for widget in (self.canvas, self.body):
            widget.bind("<MouseWheel>", self._on_wheel)
            widget.bind("<Button-4>", self._on_wheel)
            widget.bind("<Button-5>", self._on_wheel)

    def bind_wheel(self, widget=None):
        """Attach wheel scrolling to every descendant.

        Tk dispatches an event to the widget under the pointer and its class,
        never to the parent widget, so without this the wheel does nothing over
        the labels and buttons that cover most of the panel. Widgets that
        already consume the wheel (the sliders) are left alone.
        """

        widget = self.body if widget is None else widget

        for child in widget.winfo_children():
            if not child.bind("<Button-4>"):
                child.bind("<MouseWheel>", self._on_wheel)
                child.bind("<Button-4>", self._on_wheel)
                child.bind("<Button-5>", self._on_wheel)
            self.bind_wheel(child)

    def _on_body(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event):
        self.canvas.itemconfigure(self._window, width=event.width)

    def _on_scroll(self, first, last):
        # Only take up room when there is something to scroll.
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.bar.grid_remove()
        else:
            self.bar.grid()
        self.bar.set(first, last)

    def _on_wheel(self, event):
        first, last = self.canvas.yview()
        if first <= 0.0 and last >= 1.0:
            return "break"

        direction = -1 if getattr(event, "delta", 0) > 0 or event.num == 4 else 1
        self.canvas.yview_scroll(direction, "units")
        return "break"


class PlacementOverlay:
    """Borderless always-on-top window showing where the drawing will land.

    ``cursor_fn`` and ``screen_fn`` are injected so this stays free of any
    automation dependency and can be driven from a test.
    """

    POLL_MS = 30

    def __init__(self, root, cursor_fn, screen_fn, avoid_fn=None):
        self.root = root
        self._cursor = cursor_fn
        self._screen = screen_fn

        # Rect the overlay must never cover. The window sits directly under the
        # pointer and is always on top, so without this it swallows every click
        # - including the one that would disarm it.
        self._avoid = avoid_fn or (lambda: None)

        self.window = None
        self.canvas = None
        self.translucent = True

        self._job = None
        self._paths = []
        self._size = (0, 0)
        self._pixel = 1
        self._last = None
        self._shown = False

    @property
    def visible(self):
        return self.window is not None

    def show(self, paths, size, pixelSize):
        self._paths = paths
        self._size = size
        self._pixel = max(1, pixelSize)

        if self.window is None:
            self._build()

        # show() is called again on every settings change while visible; without
        # cancelling first, each call would start another self-rescheduling
        # chain and the poll rate would double per interaction.
        self._cancel()
        self._last = None
        self._tick()

    _inside = staticmethod(point_in_rect)

    def _cancel(self):
        if self._job is not None:
            self.root.after_cancel(self._job)
            self._job = None

    def hide(self):
        self._cancel()

        if self.window is not None:
            self.window.destroy()
            self.window = None
            self.canvas = None

        self._shown = False

    def set_translucent(self, translucent):
        """Ghost fill needs a compositor; without one, fall back to an outline."""

        self.translucent = translucent

        if self.window is not None:
            self._apply_alpha()
            self._last = None
            self._render()

    # -- internals --------------------------------------------------------

    def _build(self):
        self.window = tk.Toplevel(self.root)
        self.window.overrideredirect(True)

        for attribute, value in (("-topmost", True), ("-type", "splash")):
            try:
                self.window.wm_attributes(attribute, value)
            except tk.TclError:
                pass  # -type is X11 only; -topmost is honoured almost everywhere

        self._apply_alpha()

        self.canvas = tk.Canvas(self.window, highlightthickness=0,
                                background=PALETTE["ghost"])
        self.canvas.pack(fill="both", expand=True)
        self._shown = True

    def _apply_alpha(self):
        try:
            self.window.wm_attributes("-alpha", 0.35 if self.translucent else 0.9)
        except tk.TclError:
            pass  # no compositor: the window is simply opaque

    def _tick(self):
        self._job = None
        self._render()
        self._job = self.root.after(self.POLL_MS, self._tick)

    def _render(self):
        if self.window is None:
            return

        cursor = self._cursor()

        # Step aside while the pointer is over the app, so its own controls
        # stay clickable without having to disarm first.
        if self._inside(cursor, self._avoid()):
            if self._shown:
                self.window.withdraw()
                self._shown = False
                self._last = None
            return

        width, height, x, y, fits = overlay_geometry(
            cursor, self._size, self._pixel, self._screen())

        if not self._shown:
            self.window.deiconify()
            self.window.lift()
            self._shown = True
            self._last = None

        state = (width, height, x, y, fits)
        if state == self._last:
            return
        previous, self._last = self._last, state

        self.window.geometry("%dx%d+%d+%d" % (width, height, x, y))

        if (previous is not None and previous[0] == width
                and previous[1] == height and previous[4] == fits):
            # Only the position changed: moving the window carries the canvas
            # with it, so the stroke items - potentially thousands of points -
            # are left alone instead of being rebuilt on every mouse step.
            return

        self.canvas.configure(
            background=PALETTE["ghost"] if self.translucent else PALETTE["surface"])
        self.canvas.delete("all")

        outline = PALETTE["danger"] if not fits else PALETTE["accent"]
        self.canvas.create_rectangle(1, 1, width - 1, height - 1,
                                     outline=outline, width=2)

        if not self.translucent:
            return

        for path in self._paths:
            points = []
            for pathX, pathY in path:
                points.append(pathX * self._pixel)
                points.append(pathY * self._pixel)
            self.canvas.create_line(points, fill=PALETTE["text"])
