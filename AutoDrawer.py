import os
import tkinter as tk
from tkinter import filedialog, ttk
from PIL import ImageTk, Image, ImageOps
import pyautogui
import threading
import time

import pacing
import prefs
import ui
import vectorize

try:
    import keyboard
except Exception:  # the library needs root on Linux; the app still works without it
    keyboard = None

# Pause after pressing the mouse button, so the target app registers the press
# before the first movement arrives.
STROKE_SETTLE = 0.01

# Resolution the image is thresholded and vectorized at. Deliberately fixed:
# it must not follow the window size, or resizing would change the drawing.
WORK_SIZE = 350

PREVIEW_DEBOUNCE = 120
RENDER_DEBOUNCE = 60
PREFS_DEBOUNCE = 500
HOTKEY_POLL = 80


class Picture(object):
    def __init__(self):
        self.source = None
        self.work = None
        self.thresh = None


class Settings(object):
    def __init__(self):
        self.size = None
        self.aspect = 1.0
        self.armed = False
        self.threshold = 125
        self.interrupted = False
        self.drawing = False
        self.paths = []
        self.preview_paths = []
        self.plan = None


pic = Picture()
settings = Settings()

preview_job = None
render_job = None
prefs_job = None
sizing = False
draw_requested = threading.Event()
source_photo = None

saved = prefs.load()


# ------------------------------------------------------------ settings ----

def currentPrefs():
    return {
        "channel": channelBox.get(),
        "threshold": thresholdSlider.get(),
        "resolution": resolutionSlider.get(),
        "mode": modeBox.get(),
        "simplify": simplifySlider.get(),
        "min_detail": detailSlider.get(),
        "hatch_gap": hatchSlider.get(),
        "pixel_size": pixelSlider.get(),
        "target_hz": int(rateBox.get()),
        "time_budget": budgetSlider.get(),
        "lock_aspect": bool(lockAspect.get()),
        "ghost_fill": bool(ghostFill.get()),
        "geometry": "%dx%d" % (root.winfo_width(), root.winfo_height()),
        "last_dir": saved.get("last_dir", ""),
    }


def savePrefs():
    global prefs_job

    prefs_job = None
    prefs.save(currentPrefs())


def schedulePrefs(*_):
    # Every control writes through here, so the last configuration is always
    # the one on disk without hammering it on each slider tick.
    global prefs_job

    if prefs_job is not None:
        root.after_cancel(prefs_job)
    prefs_job = root.after(PREFS_DEBOUNCE, savePrefs)


def applyPrefs(values):
    global sizing

    sizing = True
    channelBox.set(values["channel"])
    modeBox.set(values["mode"])
    rateBox.set(str(values["target_hz"]))
    budgetSlider.set(values["time_budget"])
    thresholdSlider.set(values["threshold"])
    resolutionSlider.set(values["resolution"])
    simplifySlider.set(values["simplify"])
    detailSlider.set(values["min_detail"])
    hatchSlider.set(values["hatch_gap"])
    pixelSlider.set(values["pixel_size"])
    lockAspect.set(values["lock_aspect"])
    ghostFill.set(values["ghost_fill"])
    sizing = False

    overlay.set_translucent(bool(ghostFill.get()))


def resetSettings():
    applyPrefs(prefs.DEFAULTS)
    resetSize()

    # resetSize only schedules a refresh; consume it here so the confirmation
    # below is not overwritten when the debounce fires.
    flushPreview()
    savePrefs()
    setStatus("Settings reset to defaults")


def onClose():
    # Stop a drawing in flight first; the worker is a daemon, but there is no
    # reason to let it keep clicking while the window comes down.
    settings.interrupted = True
    savePrefs()
    overlay.hide()
    root.destroy()


# ---------------------------------------------------------------- image ----

def loadPic():
    filename = filedialog.askopenfilename(
        title="Select an image",
        initialdir=saved.get("last_dir") or None,
        filetypes=(("Images", "*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff"),
                   ("All files", "*.*")))

    if not filename:
        return

    try:
        pic.source = Image.open(filename).convert("RGB")
    except (OSError, ValueError):
        setStatus("Could not read that file as an image", warning=True)
        return

    saved["last_dir"] = os.path.dirname(filename)
    schedulePrefs()

    adj = max(pic.source.width, pic.source.height) / WORK_SIZE
    workW = max(1, int(pic.source.width / adj))
    workH = max(1, int(pic.source.height / adj))

    pic.work = pic.source.resize((workW, workH), Image.Resampling.BOX)
    settings.size = (workW, workH)
    settings.aspect = workW / workH

    loadButton.config(text="Replace Image")
    widthSlider.configure_range(8, workW * 8)
    heightSlider.configure_range(8, workH * 8)

    global sizing
    sizing = True
    widthSlider.set(workW)
    heightSlider.set(workH)
    sizing = False

    refreshPreview()


def thresholdImage():
    working = pic.work
    resolution = resolutionSlider.get()
    settings.threshold = thresholdSlider.get()

    # Adjust image resolution
    if resolution < 100:
        width, height = working.size
        smallWidth = max(1, int(width * (resolution / 100)))
        smallHeight = max(1, int(height * (resolution / 100)))

        lowRes = working.resize((smallWidth, smallHeight))
        working = lowRes.resize((width, height), Image.Resampling.BOX)

    channel = channelBox.get()
    if channel == "All":
        return working.convert("L").point(lambda p: p > settings.threshold and 255)

    index = {"Red": 0, "Green": 1, "Blue": 2}[channel]
    return working.split()[index].point(lambda p: p > settings.threshold and 255)


def invertPreview():
    if pic.work is None:
        return

    pic.work = ImageOps.invert(pic.work)
    thresholdSlider.set(abs(255 - thresholdSlider.get()))
    refreshPreview()


# -------------------------------------------------------------- preview ----

def schedulePreview(*_):
    # Dragging a slider fires continuously; only vectorize once it settles.
    global preview_job

    schedulePrefs()

    if preview_job is not None:
        root.after_cancel(preview_job)
    preview_job = root.after(PREVIEW_DEBOUNCE, refreshPreview)


def flushPreview():
    # Run a debounced refresh right now instead of waiting for its timer.
    global preview_job

    if preview_job is not None:
        root.after_cancel(preview_job)
        preview_job = None
        refreshPreview()


def refreshPreview():
    global preview_job

    preview_job = None

    if pic.work is None or settings.size is None:
        return

    pic.thresh = thresholdImage()
    paths = vectorize.build_paths(pic.thresh,
                                  settings.size,
                                  mode=modeBox.get(),
                                  epsilon=simplifySlider.get(),
                                  min_perimeter=detailSlider.get(),
                                  spacing=hatchSlider.get())

    # How fast the target can absorb this decides what will actually be drawn,
    # so it has to be settled before anything is shown.
    rate = int(rateBox.get())
    settings.plan = pacing.plan_emission(paths, rate, budgetSlider.get(),
                                        pixelSlider.get())
    settings.paths = settings.plan.paths

    # In an exact plan the target sees every vertex, so the preview is the path
    # set itself. Otherwise the preview must show the degraded result - it comes
    # from the same model the drawing is paced by, so it cannot disagree.
    if settings.plan.exact:
        settings.preview_paths = settings.paths
    else:
        settings.preview_paths = pacing.simulate_capture(
            settings.paths, settings.plan.dwell, rate)

    renderCanvas()
    updateStatus()

    if overlay.visible:
        overlay.show(settings.preview_paths, settings.size, pixelSlider.get())


def scheduleRender(*_):
    # Resizing only changes display geometry, never the paths, so it must not
    # trigger a re-vectorization.
    global render_job

    if render_job is not None:
        root.after_cancel(render_job)
    render_job = root.after(RENDER_DEBOUNCE, renderCanvas)


def renderCanvas():
    global render_job, source_photo

    render_job = None
    canvas.delete("all")

    if pic.work is None:
        canvas.create_text(canvas.winfo_width() / 2, canvas.winfo_height() / 2,
                           text="Load an image to begin",
                           fill=ui.PALETTE["muted"])
        return

    width = canvas.winfo_width()
    height = canvas.winfo_height()
    paneW = width / 2
    labelH = 22

    if paneW < 20 or height < labelH + 20:
        return

    box = (paneW - 2 * ui.PAD, height - labelH - ui.PAD)

    canvas.create_text(paneW / 2, labelH / 2, text="Source",
                       fill=ui.PALETTE["muted"])
    canvas.create_text(paneW + paneW / 2, labelH / 2, text="Strokes",
                       fill=ui.PALETTE["muted"])

    # Source thumbnail, scaled to whatever room the window currently gives it.
    scale, offsetX, offsetY = ui.fit_box(pic.work.size, box)
    if scale > 0:
        thumb = pic.work.resize((max(1, int(pic.work.width * scale)),
                                 max(1, int(pic.work.height * scale))),
                                Image.Resampling.BOX)
        source_photo = ImageTk.PhotoImage(thumb)
        canvas.create_image(ui.PAD + offsetX, labelH + offsetY,
                            anchor="nw", image=source_photo)

    # Stroke preview, drawn from the same paths the mouse will follow.
    scale, offsetX, offsetY = ui.fit_box(settings.size, box)
    if scale <= 0:
        return

    originX = paneW + ui.PAD + offsetX
    originY = labelH + offsetY

    canvas.create_rectangle(originX, originY,
                            originX + settings.size[0] * scale,
                            originY + settings.size[1] * scale,
                            fill=ui.PALETTE["surface"], outline=ui.PALETTE["border"])

    for path in settings.preview_paths:
        points = []
        for x, y in path:
            points.append(originX + x * scale)
            points.append(originY + y * scale)
        canvas.create_line(points, fill=ui.PALETTE["text"])


# ----------------------------------------------------------------- size ----

def onWidthChange(value):
    global sizing

    if pic.work is None or sizing:
        return

    if lockAspect.get():
        sizing = True
        heightSlider.set(max(1, round(value / settings.aspect)))
        sizing = False

    # Read back rather than reuse: the sliders clamp to their own range, and the
    # drawing must be built at the size the user can actually see.
    settings.size = (widthSlider.get(), heightSlider.get())
    schedulePreview()


def onHeightChange(value):
    global sizing

    if pic.work is None or sizing:
        return

    if lockAspect.get():
        sizing = True
        widthSlider.set(max(1, round(value * settings.aspect)))
        sizing = False

    settings.size = (widthSlider.get(), heightSlider.get())
    schedulePreview()


def resetSize():
    global sizing

    if pic.work is None:
        return

    width, height = pic.work.size
    settings.size = (width, height)

    sizing = True
    widthSlider.set(width)
    heightSlider.set(height)
    sizing = False

    schedulePreview()


# -------------------------------------------------------------- drawing ----

def onRateChange(*_):
    # The pace is derived from the target's sampling rate now, not from a
    # pyautogui pause that had no relation to what the target could see.
    schedulePreview()


def armDrawing():
    if not settings.armed and pic.work is None:
        # settings.size is only set by loadPic; arming first would hand None to
        # the overlay and take the poll loop down with it.
        setStatus("Load an image before arming", warning=True)
        return

    settings.armed = not settings.armed

    if settings.armed:
        armButton.config(text="Armed", style="Danger.TButton")
        overlay.show(settings.preview_paths, settings.size, pixelSlider.get())
    else:
        armButton.config(text="Arm", style="Accent.TButton")
        overlay.hide()

    updateStatus()


def onPixelChange(*_):
    # Pixel size changes the screen length of every segment, so the plan has to
    # be rebuilt, not just re-shown. schedulePreview also persists the setting
    # and refreshes the status once the debounce fires.
    schedulePreview()

    if overlay.visible:
        overlay.show(settings.preview_paths, settings.size, pixelSlider.get())


def onGhostToggle():
    overlay.set_translucent(bool(ghostFill.get()))
    schedulePrefs()


def appRect():
    """Screen rect of the app window, so the overlay can keep off it."""

    if not root.winfo_viewable():
        return None

    left, top = root.winfo_rootx(), root.winfo_rooty()
    return (left, top, left + root.winfo_width(), top + root.winfo_height())


def cursorOverApp():
    return ui.point_in_rect(pyautogui.position(), appRect())


def drawPaths(paths, origin, pixelSize, dwell=0.0, frame=0.0):
    originX, originY = origin

    def screen(point):
        return originX + point[0] * pixelSize, originY + point[1] * pixelSize

    held = False

    try:
        for path in paths:
            if settings.interrupted:
                break

            previous = screen(path[0])
            pyautogui.moveTo(previous[0], previous[1])
            pyautogui.mouseDown(button='left')
            held = True
            time.sleep(STROKE_SETTLE)

            for point in path[1:]:
                if settings.interrupted:
                    break

                target = screen(point)
                pyautogui.moveTo(target[0], target[1])
                if dwell:
                    # The only pacing in the app. One vertex per frame of the
                    # target is what makes the drawing match the preview;
                    # emitting faster just loses vertices. Long segments were
                    # already split by the plan, so every point costs one dwell
                    # and the estimate cannot drift from reality.
                    time.sleep(dwell)
                previous = target

            # Without this the last vertex can fall in the same frame as the
            # release and never be sampled, truncating every stroke.
            time.sleep(STROKE_SETTLE)
            pyautogui.mouseUp(button='left')
            held = False

            # The release has to land in a frame of its own before the cursor
            # travels to the next stroke. A target that samples pointer and
            # button once per frame can otherwise miss the whole up-move-down
            # flick and join the two strokes with a pen-up travel line - one
            # the preview never showed, since the pacing model treats strokes
            # as independent. Hatch fills feel this most: they have the most
            # transitions packed into the smallest area.
            time.sleep(max(STROKE_SETTLE, frame))
    finally:
        # Never leave the button held down after an interrupt or a failsafe.
        # The failsafe aborts by raising from inside moveTo while the cursor
        # sits in a screen corner, and mouseUp runs the same corner check
        # before acting - so it has to be suspended to get the button back up.
        if held:
            failsafe = pyautogui.FAILSAFE
            pyautogui.FAILSAFE = False
            try:
                pyautogui.mouseUp(button='left')
            finally:
                pyautogui.FAILSAFE = failsafe


draw_thread = None
draw_reason = None


def _drawWorker(paths, origin, pixelSize, dwell, frame):
    # Runs off the Tk thread so the UI - and with it the in-window Esc binding -
    # stays alive for the whole drawing. Touches only the mouse and a result
    # slot; every Tk call happens back in pollDrawing. Sliders moved mid-drawing
    # rebuild settings.plan, but this worker holds its own references and
    # finishes the drawing it started.
    global draw_reason

    try:
        drawPaths(paths, origin, pixelSize, dwell, frame)
    except pyautogui.FailSafeException:
        # Cursor slammed into a screen corner. A documented way to cancel, so it
        # is recorded here rather than thrown at the Tk callback handler.
        draw_reason = "corner failsafe"
    except Exception as exc:
        # A thread dies quietly; surface whatever killed it on the status bar.
        draw_reason = str(exc) or type(exc).__name__


def startDrawing(event=None):
    global draw_thread, draw_reason

    # The drawing guard also drops a second F9 pressed while one is running.
    if pic.work is None or not settings.armed or settings.drawing:
        return

    # Enter is a toplevel binding, so it fires no matter which control has
    # focus - including right after picking a value in the sidebar, when the
    # cursor is still there. Drawing then scribbles over Auto Drawer's own
    # window. This is also why the overlay shows nothing here: there is no
    # sensible drawing to preview on top of the app itself.
    if cursorOverApp():
        setStatus("Point at the target first - the cursor is over Auto Drawer",
                  warning=True)
        return

    settings.interrupted = False

    # The preview is debounced, so pending settings changes have to be applied
    # before drawing - otherwise Enter draws the previous settings' paths.
    flushPreview()

    if not settings.paths:
        setStatus("Nothing to draw - lower Min Detail or adjust the threshold",
                  warning=True)
        return

    # The overlay is a real window; leaving it up would swallow the clicks.
    overlay.hide()
    root.update_idletasks()

    # flushPreview can take a moment; re-check rather than trust a stale read,
    # and draw from the position the guard actually approved.
    origin = pyautogui.position()
    if ui.point_in_rect(origin, appRect()):
        setStatus("Point at the target first - the cursor is over Auto Drawer",
                  warning=True)
        return

    plan = settings.plan
    settings.drawing = True
    draw_reason = None
    draw_thread = threading.Thread(target=_drawWorker,
                                   args=(plan.paths, origin, pixelSlider.get(),
                                         plan.dwell,
                                         pacing.FRAME_MARGIN / plan.rate_hz),
                                   daemon=True)
    draw_thread.start()
    setStatus("Drawing - Esc cancels")
    root.after(HOTKEY_POLL, pollDrawing)


def pollDrawing():
    if draw_thread is not None and draw_thread.is_alive():
        root.after(HOTKEY_POLL, pollDrawing)
        return

    settings.drawing = False
    reason = draw_reason

    if reason is None and settings.interrupted:
        reason = "Esc"

    if reason is not None:
        # A cancelled drawing disarms: whatever made the user abort, the
        # app must not stay one keypress away from doing it again.
        if settings.armed:
            armDrawing()
        setStatus("Drawing cancelled (%s) - disarmed" % reason, warning=True)
    else:
        if settings.armed:
            # Same content as when armed: the simulated capture, not the raw
            # plan, so the ghost never promises more than was drawn.
            overlay.show(settings.preview_paths, settings.size,
                         pixelSlider.get())
        updateStatus()


def pumpHotkeys():
    # Hotkeys fire on the keyboard library's own thread and Tcl is not
    # thread-safe, so they only set a flag; this poll runs the real work on the
    # Tk thread.
    if draw_requested.is_set():
        # Consumed as soon as the drawing starts; a second F9 while one is
        # running is dropped by the settings.drawing guard in startDrawing
        # instead of queuing another full pass.
        startDrawing()
        draw_requested.clear()

    root.after(HOTKEY_POLL, pumpHotkeys)


def interrupt():
    # Called from the global hotkey thread: must only touch a plain flag.
    settings.interrupted = True


def onEscape(*_):
    # Esc means "stop": abort a running drawing, or disarm when idle. Without
    # the second half, an armed overlay could only be dismissed by mouse.
    if settings.drawing:
        settings.interrupted = True
        return

    if settings.armed:
        armDrawing()


# --------------------------------------------------------------- status ----

def setStatus(text, warning=False):
    statusLabel.config(text=text,
                       style="Warning.TLabel" if warning else "Status.TLabel")


_screen_cache = [0.0, (0, 0)]


def screenSize():
    # pyautogui.size() is an X round trip and updateStatus runs on every slider
    # tick, so the answer is cached for a second. Monitor hotplug mid-session
    # is rare enough that a stale second is fine.
    now = time.monotonic()
    if now - _screen_cache[0] > 1.0:
        _screen_cache[0] = now
        _screen_cache[1] = pyautogui.size()
    return _screen_cache[1]


def updateStatus():
    if pic.work is None:
        setStatus("Load an image to begin")
        return

    strokes, vertices = vectorize.path_stats(settings.paths)
    pixelSize = pixelSlider.get()
    outputW = settings.size[0] * pixelSize
    outputH = settings.size[1] * pixelSize

    plan = settings.plan
    summary = "{:,} strokes  ·  {:,} points  ·  {} × {} px".format(
        strokes, vertices, outputW, outputH)

    # Every warning is collected instead of returning at the first one, so a
    # reduced plan can no longer hide an oversize drawing or the armed hint.
    warnings = []

    if plan is not None:
        if plan.exact:
            summary += "  ·  Exact"
        elif plan.fits:
            summary += "  ·  Reduced to fit %ds" % budgetSlider.get()
            warnings.append("preview shows what will actually be drawn - raise "
                            "Hatch Gap, Simplify or Max draw time")
        else:
            summary += "  ·  Reduced, cannot fit %ds" % budgetSlider.get()
            warnings.append("too many strokes to fit the budget at any pace - "
                            "raise Max draw time or Hatch Gap")

        summary += "  ·  ~%s" % pacing.format_duration(plan.seconds)

    screenW, screenH = screenSize()
    if outputW > screenW or outputH > screenH:
        warnings.append("larger than the screen")

    if settings.armed and not warnings:
        summary += "  ·  ARMED: point at the target and press Enter  ·  Esc disarms"
    elif settings.armed:
        warnings.append("ARMED - Esc disarms")

    if warnings:
        setStatus(summary + "  ·  " + "  ·  ".join(warnings), warning=True)
    else:
        setStatus(summary)


# ------------------------------------------------------------------ ui -----

def section(parent, title):
    ttk.Label(parent, text=title, style="Heading.TLabel").pack(
        anchor="w", pady=(ui.PAD * 2, ui.PAD // 2))
    frame = ttk.Frame(parent)
    frame.pack(fill="x")
    return frame


def labeledBox(parent, label, values, command, row):
    ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
    box = ttk.Combobox(parent, values=values, state="readonly", width=16)
    box.current(0)
    box.grid(row=row, column=1, sticky="ew", pady=2)
    box.bind("<<ComboboxSelected>>", command)
    return box


root = tk.Tk()
root.title("Auto Drawer")
root.minsize(760, 520)
try:
    root.geometry(saved["geometry"])
except tk.TclError:
    pass  # never let a stale config stop the app from opening
root.columnconfigure(0, weight=1)
root.rowconfigure(0, weight=1)

ui.apply_theme(root)
# All pacing lives in drawPaths now, derived from the target's sampling rate.
pyautogui.PAUSE = 0

canvasFrame = ttk.Frame(root, padding=ui.PAD)
canvasFrame.grid(row=0, column=0, sticky="nsew")
canvasFrame.columnconfigure(0, weight=1)
canvasFrame.rowconfigure(0, weight=1)

canvas = tk.Canvas(canvasFrame, background=ui.PALETTE["bg"], highlightthickness=0)
canvas.grid(row=0, column=0, sticky="nsew")
canvas.bind("<Configure>", scheduleRender)

ttk.Separator(root, orient="vertical").grid(row=0, column=1, sticky="ns")

sidePanel = ttk.Frame(root)
sidePanel.grid(row=0, column=2, sticky="ns")
sidePanel.rowconfigure(0, weight=1)

scroller = ui.ScrollFrame(sidePanel, width=272)
scroller.grid(row=0, column=0, sticky="nsew")

# Everything scrollable lives in the body; Arm is pinned below so it is always
# reachable no matter how short the window gets.
sidebar = ttk.Frame(scroller.body, padding=(ui.PAD * 2, ui.PAD))
sidebar.pack(fill="both", expand=True)

loadButton = ttk.Button(sidebar, text="Load Image", style="Accent.TButton",
                        command=loadPic)
loadButton.pack(fill="x", pady=(ui.PAD, 0))

# -- Image -------------------------------------------------------------------
imageSection = section(sidebar, "Image")
imageSection.columnconfigure(1, weight=1)

channelBox = labeledBox(imageSection, "Channel", ["All", "Red", "Green", "Blue"],
                        schedulePreview, 0)
channelBox.set(saved["channel"])

thresholdSlider = ui.LabeledSlider(sidebar, "Threshold", 0, 255, 1, saved["threshold"],
                                   on_change=schedulePreview)
thresholdSlider.pack(fill="x", pady=(ui.PAD // 2, 0))

resolutionSlider = ui.LabeledSlider(sidebar, "Resolution %", 1, 100, 1, saved["resolution"],
                                    on_change=schedulePreview)
resolutionSlider.pack(fill="x", pady=(ui.PAD // 2, 0))

ttk.Button(sidebar, text="Invert Image", command=invertPreview).pack(
    fill="x", pady=(ui.PAD, 0))

# -- Strokes -----------------------------------------------------------------
strokeSection = section(sidebar, "Strokes")
strokeSection.columnconfigure(1, weight=1)

modeBox = labeledBox(strokeSection, "Mode", list(vectorize.MODES),
                     schedulePreview, 0)
modeBox.set(saved["mode"])

simplifySlider = ui.LabeledSlider(sidebar, "Simplify", 0, 5, 0.5, saved["simplify"],
                                  on_change=schedulePreview)
simplifySlider.pack(fill="x", pady=(ui.PAD // 2, 0))

detailSlider = ui.LabeledSlider(sidebar, "Min Detail", 0, 60, 1, saved["min_detail"],
                                on_change=schedulePreview)
detailSlider.pack(fill="x", pady=(ui.PAD // 2, 0))

hatchSlider = ui.LabeledSlider(sidebar, "Hatch Gap", 1, 20, 1, saved["hatch_gap"],
                               on_change=schedulePreview)
hatchSlider.pack(fill="x", pady=(ui.PAD // 2, 0))

# -- Drawing -----------------------------------------------------------------
drawSection = section(sidebar, "Drawing")
drawSection.columnconfigure(1, weight=1)

rateBox = labeledBox(drawSection, "Target Hz", ["30", "60", "120", "144"],
                     onRateChange, 0)
rateBox.set(str(saved["target_hz"]))

budgetSlider = ui.LabeledSlider(sidebar, "Max draw time (s)", 5, 600, 5,
                                saved["time_budget"], on_change=schedulePreview,
                                width=6)

budgetSlider.pack(fill="x", pady=(ui.PAD // 2, 0))

widthSlider = ui.LabeledSlider(sidebar, "Width", 8, 4000, 1, 250,
                               on_change=onWidthChange, width=6)
widthSlider.pack(fill="x", pady=(ui.PAD // 2, 0))

heightSlider = ui.LabeledSlider(sidebar, "Height", 8, 4000, 1, 250,
                                on_change=onHeightChange, width=6)
heightSlider.pack(fill="x", pady=(ui.PAD // 2, 0))

sizeRow = ttk.Frame(sidebar)
sizeRow.pack(fill="x", pady=(ui.PAD // 2, 0))
lockAspect = tk.BooleanVar(value=saved["lock_aspect"])
ttk.Checkbutton(sizeRow, text="Lock aspect ratio", variable=lockAspect,
                command=schedulePrefs).pack(side="left")
ttk.Button(sizeRow, text="Native size", command=resetSize).pack(side="right")

pixelSlider = ui.LabeledSlider(sidebar, "Pixel Size", 1, 20, 1, saved["pixel_size"],
                               on_change=onPixelChange)
pixelSlider.pack(fill="x", pady=(ui.PAD // 2, 0))

ghostFill = tk.BooleanVar(value=saved["ghost_fill"])
ttk.Checkbutton(sidebar, text="Ghost strokes in overlay", variable=ghostFill,
                command=onGhostToggle).pack(anchor="w", pady=(ui.PAD // 2, ui.PAD))

armBar = ttk.Frame(sidePanel, padding=(ui.PAD * 2, ui.PAD))
armBar.grid(row=1, column=0, sticky="ew")
ttk.Button(armBar, text="Reset Settings", command=resetSettings).pack(
    fill="x", pady=(0, ui.PAD // 2))
armButton = ttk.Button(armBar, text="Arm", style="Accent.TButton", command=armDrawing)
armButton.pack(fill="x")

# -- Status ------------------------------------------------------------------
scroller.bind_wheel()

statusLabel = ttk.Label(root, text="", style="Status.TLabel", anchor="w")
statusLabel.grid(row=1, column=0, columnspan=3, sticky="ew")

overlay = ui.PlacementOverlay(root, pyautogui.position, pyautogui.size, appRect)

root.bind("<Return>", startDrawing)
root.bind("<Escape>", onEscape)
root.protocol("WM_DELETE_WINDOW", onClose)
root.focus_force()

if keyboard is not None:
    try:
        # Hotkeys fire on the keyboard library's own thread. Tcl calls are not
        # thread-safe, so this may only set a flag - disarming is left to the
        # in-window Escape binding.
        keyboard.add_hotkey("esc", interrupt)
        keyboard.add_hotkey("f9", draw_requested.set)
    except Exception:
        keyboard = None

overlay.set_translucent(bool(ghostFill.get()))

if keyboard is not None:
    pumpHotkeys()

updateStatus()
startup_notes = []
if keyboard is None:
    startup_notes.append("global hotkeys unavailable "
                         "(run as root for Esc/F9 outside this window)")
# PyAutoGUI drives the pointer through X11; on a Wayland session the drawing
# only reaches XWayland clients, and pyautogui may fail outright.
if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
    startup_notes.append("Wayland session - mouse automation needs X11/XWayland")
if startup_notes:
    setStatus("Load an image to begin  ·  " + "  ·  ".join(startup_notes))

root.mainloop()
