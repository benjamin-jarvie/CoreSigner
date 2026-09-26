"""The QR return channel: what the coordinator's scanner actually sees.

This suite exists because the review found D11 and D12 shipped untested.
A signed PSBT leaves Core Signer as pixels on a panel, so the properties that
matter are geometric, not logical: modules must stay square, the quiet zone
must survive, and a multi-frame animation must repeat at a steady rate.

Run: python3 tests/test_qr_out.py
"""
import base64
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "coresigner"))
from PIL import Image  # noqa: E402
import qrchannel  # noqa: E402
import screens  # noqa: E402
import hal  # noqa: E402
import main as coresigner_main  # noqa: E402

fails = []


def ok(m):
    print("ok  ", m)


def bad(m):
    print("FAIL", m)
    fails.append(m)


PANEL_W, PANEL_H = 320, 240


# --- D11: integer scaling, square modules, quiet zone ---------------------

def module_edges_are_integral(src, out, factor):
    """Every source pixel must map to an exact factor x factor block."""
    sx, sy = src.size
    for py in range(0, sy, max(1, sy // 8)):
        for px in range(0, sx, max(1, sx // 8)):
            want = src.getpixel((px, py))
            ox = (out.width - sx * factor) // 2 + px * factor
            oy = (out.height - sy * factor) // 2 + py * factor
            for dy in range(factor):
                for dx in range(factor):
                    if out.getpixel((ox + dx, oy + dy)) != want:
                        return False
    return True


src = Image.new("RGB", (60, 60), "white")
for x in range(0, 60, 2):          # a striped pattern stands in for modules
    for y in range(60):
        src.putpixel((x, y), (0, 0, 0))

# These four properties belonged to qrchannel.fit_to_panel, which scaled a
# QR by an integer factor and letterboxed it in white. It had one caller,
# the signing loop, and on 2026-09-08 that loop moved onto screens'
# gold-edged card so the screen a signed transaction leaves by stops
# looking like nothing else on the device (Ben, from the demo recording).
# fit_to_panel went with it. The properties did not: they belong to
# frames_to_images (which picks an integer box_size) and to _qr_card
# (which pastes at natural size and refuses what will not fit), and they
# are checked HERE against the pair rather than against the function that
# used to hold them.
out = screens.qr_frame(PANEL_W, PANEL_H, src)
if out.size != (PANEL_W, PANEL_H):
    bad(f"qr_frame returned {out.size}, not the panel size")
else:
    ok("qr_frame returns exactly the panel size")

# Every module is box_size pixels wide because box_size is an int, so a
# square QR stays square and no module is a fraction of a pixel. Checked
# on the real frames rather than on the striped stand-in, because the
# scaling now happens when the code is rendered and not afterwards.
_probe = qrchannel.frames_to_images(
    qrchannel.psbt_to_frames(base64.b64encode(b"psbt\xff" * 200).decode()),
    panel=(200, 200))
if any(i.width != i.height for i in _probe):
    bad(f"a frame is not square: {[i.size for i in _probe[:3]]}")
elif len({i.size for i in _probe}) != 1:
    bad(f"frames differ in size mid-animation: {sorted({i.size for i in _probe})}")
else:
    ok(f"every frame is square and the same size ({_probe[0].width}px), so "
       "modules stay square and the image does not resize mid-scan")

# The surround immediately around the code must be WHITE, or the quiet
# zone is swallowed. It is a card on an ink ground now rather than a white
# letterbox, so the check moved inward: sample just outside the code.
_c = screens.qr_frame(PANEL_W, PANEL_H, src)
_inset = (PANEL_W - src.width) // 2 - 3
if _c.getpixel((_inset, PANEL_H // 2)) != (255, 255, 255):
    bad(f"the pixel just outside the code is "
        f"{_c.getpixel((_inset, PANEL_H // 2))}, not white: the card is not "
        "supplying the quiet zone")
else:
    ok("the card supplies a white quiet zone around the code")

# --- I-1: an oversized QR must never be cropped ---------------------------
#
# Cropping leaves the panel showing something QR-shaped that no scanner can
# read, and nothing on the device says so. PIL's paste crops in silence, so
# the card refuses; the real guard is frames_to_images(panel=...), which
# sizes the modules so an oversized frame cannot be produced at all.

big = Image.new("RGB", (400, 400), "white")
try:
    screens.qr_frame(PANEL_W, PANEL_H, big)
    bad("the QR card pasted a 400x400 code instead of refusing it (I-1)")
except ValueError:
    ok("the QR card refuses an oversized code rather than cropping it (I-1)")

# The cliff measured before the fix: 336 characters renders a version-10 QR
# at 244px, which overflows a 240px panel. Sweep fragment lengths well past
# it, on BOTH panels, and require every frame to fit.
psbt_b64 = base64.b64encode(bytes(range(256)) * 16).decode()
for panel in ((320, 240), (240, 240)):          # SeedSigner+ hat, pocket hat
    for mfl in (100, 150, 200, 400):
        parts = qrchannel.psbt_to_frames(psbt_b64, max_fragment_len=mfl)
        longest = max(len(f) for f in parts)
        imgs = qrchannel.frames_to_images(parts, panel=panel)
        over = [i.size for i in imgs if i.width > panel[0] or i.height > panel[1]]
        if over:
            bad(f"panel {panel}, fragment {mfl} ({longest} chars): "
                f"frames overflow the panel: {set(over)}")
            continue
        # One size for the whole set: a set that changes size mid-animation
        # makes a scanner re-acquire on every frame.
        if len({i.size for i in imgs}) != 1:
            bad(f"panel {panel}, fragment {mfl}: the animation changes size "
                f"between frames: {sorted({i.size for i in imgs})}")
            continue
        # And each one must survive the real display path, which is the
        # card. This swept fit_to_panel until 2026-09-08; the card is what
        # the signing loop composes now, and it is the thing that can
        # refuse.
        try:
            fitted = {screens.qr_frame(*panel, i).size for i in imgs}
        except ValueError as exc:
            bad(f"panel {panel}, fragment {mfl}: a frame will not fit the "
                f"card: {exc}")
            continue
        if fitted != {panel}:
            bad(f"panel {panel}, fragment {mfl}: qr_frame gave {fitted}")
        else:
            ok(f"panel {panel[0]}x{panel[1]}, fragment {mfl} "
               f"({longest} chars): every frame fits at {imgs[0].width}px")

# box_size stays the CEILING. A frame that already fits must render exactly
# as it did before the fix, because changing what the coordinator sees is
# not provable without a scanner in front of the panel (audit D11/D12).
for mfl in (100, 150):
    parts = qrchannel.psbt_to_frames(psbt_b64, max_fragment_len=mfl)
    sized = qrchannel.frames_to_images(parts, panel=(PANEL_W, PANEL_H))[0]
    fixed = qrchannel.frames_to_images(parts)[0]
    fits = fixed.height <= PANEL_H
    if fits and sized.size != fixed.size:
        bad(f"fragment {mfl} already fitted at {fixed.size} and panel sizing "
            f"changed it to {sized.size}")
    elif not fits and sized.height > PANEL_H:
        bad(f"fragment {mfl} did not fit at {fixed.size} and panel sizing "
            f"left it at {sized.size}")
    else:
        ok(f"fragment {mfl}: {fixed.size[0]}px -> {sized.size[0]}px "
           f"({'unchanged, it already fitted' if fits else 'shrunk to fit'})")


# --- the signature must survive a QR that cannot be shown -----------------
#
# fit_to_panel and frames_to_images both raise now. state_sign runs them
# AFTER signing, so an uncaught raise would unwind past the result screen
# and throw a good signature away (the shape of audit D18).

class _Rpc:
    chain = "regtest"

    def call(self, *a, **k):
        return ""


class _Display:
    width, height = PANEL_W, PANEL_H

    def show(self, image, sensitive=False):
        pass


class _Buttons:
    def read(self):
        return "a"


boom = coresigner_main.Session(_Display(), _Buttons(), _Rpc())
boom.animate = False
real_frames = qrchannel.frames_to_images


def _raise(*a, **k):
    raise qrchannel.QrChannelError("frame does not fit the panel")


qrchannel.frames_to_images = _raise
try:
    import signer as _signer
    real_sign = _signer.sign_psbt
    _fake = base64.b64encode(b"psbt\xff" + bytes(range(256))).decode()
    _signer.sign_psbt = lambda rpc, psbt, **kw: {"complete": True,
                                             "added": True,
                                             "psbt": _fake}
    try:
        outcome = boom._sign_and_deliver(_fake, None, "coresigner")
    finally:
        _signer.sign_psbt = real_sign
except qrchannel.QrChannelError:
    bad("state_sign let QrChannelError unwind after a successful sign: "
        "the signature is lost and no screen says so")
    outcome = None
finally:
    qrchannel.frames_to_images = real_frames

if outcome == coresigner_main.TO_HOME:
    ok("a QR that cannot be shown reports the failure and keeps the session, "
       "rather than throwing the signature away")
elif outcome is not None:
    bad(f"state_sign returned {outcome!r} after an unshowable QR")


# --- D12: the animation repeats, is paced, and a key stops it -------------

class CountingDisplay:
    width, height = PANEL_W, PANEL_H

    def __init__(self):
        self.shown = []
        self.lock = threading.Lock()

    def show(self, image, sensitive=False):
        with self.lock:
            self.shown.append(image)


class BlockingButtons:
    """Holds until released, so the loop runs like it does on the device."""

    def __init__(self):
        self.release = threading.Event()

    def read(self):
        self.release.wait(timeout=5)
        return "c"


class FakeRpc:
    chain = "regtest"

    def call(self, *a, **k):
        return ""


frames = [f"ur:crypto-psbt/{i}-4/abcdefgh" for i in range(4)]
display = CountingDisplay()
buttons = BlockingButtons()
session = coresigner_main.Session(display, buttons, FakeRpc())
session.animate = True             # the path that ships to the device

worker = threading.Thread(target=session._show_qr_loop,
                          args=(frames,), kwargs={"delay": 0.02},
                          daemon=True)
worker.start()
time.sleep(0.45)                   # long enough for several full cycles
with display.lock:
    during = len(display.shown)
buttons.release.set()
worker.join(timeout=3)

if worker.is_alive():
    bad("_show_qr_loop did not stop when a key was pressed")
else:
    ok("_show_qr_loop stops on a key press")

if during <= len(frames):
    bad(f"_show_qr_loop showed {during} frames for a {len(frames)}-frame "
        "animation: it played once instead of repeating")
else:
    ok(f"_show_qr_loop repeats ({during} frames shown for {len(frames)} "
       "parts, so the coordinator can catch every one)")

# Pacing: with a 0.02s delay, ~0.45s of running cannot produce hundreds of
# frames. An unpaced loop would spin at the display's full rate.
if during > 200:
    bad(f"_show_qr_loop is not paced: {during} frames in 0.45s")
else:
    ok(f"_show_qr_loop is paced by its delay ({during} frames in 0.45s)")

# A single-frame PSBT is a static QR, and must wait rather than animate.
single_display = CountingDisplay()
single_buttons = hal.DevButtons("a")
single = coresigner_main.Session(single_display, single_buttons, FakeRpc())
single.animate = True
single._show_qr_loop([frames[0]])
if len(single_display.shown) != 1:
    bad(f"a one-frame PSBT painted {len(single_display.shown)} frames, not 1")
else:
    ok("a one-frame PSBT is shown once as a static QR and waits for a key")

# Every frame the loop paints must be panel-sized, not raw QR-sized.
sizes = {img.size for img in display.shown}
if sizes and sizes != {(PANEL_W, PANEL_H)}:
    bad(f"the loop painted frames that are not panel-sized: {sizes}")
else:
    ok("every animated frame is panel-sized and letterboxed")


# The busy spinner must be stopped BEFORE a screen that blocks.
# _tool_leak_check and _confirm_typed_key both call stop() in their
# except block AND in a finally. That looks like duplication and is not:
# finally runs after the handler, and _hold paints then waits on a
# button, so without the early call the animation repaints over the
# error every 150ms and the operator waits on a busy screen for ever.
# Deleting the "redundant" call on 2026-09-07 reproduced exactly that.
import screens                                          # noqa: E402

_painted = []


class _Disp:
    width, height = 320, 240

    def show(self, image, sensitive=False):
        _painted.append(image)


# Driven through the REAL _confirm_typed_key, not a copy of its shape. A
# first version rebuilt the try/except here and passed with the early
# stop() deleted from main.py, which pinned the rule and not the code
# (2026-09-07).
import signer                                           # noqa: E402


def _panel_after_core_refuses():
    """Make Core refuse, then read what the panel is left showing.

    _hold blocks on a button, and DevButtons answers at once, so the
    spinner needs a moment to paint over the error if it is going to.
    """
    real_opens = signer.opens_wallet
    real_read = hal.DevButtons.read
    signer.opens_wallet = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("Core says no"))

    def slow_read(self):
        time.sleep(0.35)        # the operator is reading the screen
        return real_read(self)

    hal.DevButtons.read = slow_read
    _painted.clear()
    sess = coresigner_main.Session(_Disp(), hal.DevButtons("aa"), rpc=object(),
                              animate=True)
    try:
        sess._confirm_typed_key("tprvWHATEVER", "coresigner-x", "73c5da0a")
    finally:
        signer.opens_wallet = real_opens
        hal.DevButtons.read = real_read
    return _painted[-1]


_last = _panel_after_core_refuses()
_busy_frames = {screens.busy(320, 240, "Bitcoin Core is reading what you "
                             "typed…", ph).tobytes() for ph in range(12)}
if _last.tobytes() in _busy_frames:
    bad("the spinner painted over the error: the panel is left showing a "
        "busy screen that will never finish")
else:
    ok("Core's refusal is what the panel is left showing, not the spinner")

# --- the export QR must never be wider than the panel it is pasted onto ---
#
# screens.qr_export paints the code with img.paste, and PIL's paste CROPS
# whatever falls outside the destination. A cropped QR still looks like a
# QR and no scanner will ever read it, which is the one failure the whole
# fit_to_panel/frames_to_images contract exists to refuse.
#
# What keeps it unreachable is arithmetic, not a check: a QR tops out at
# version 40, 177 modules, so with qrchannel's 2-module border the widest
# code is 181 across, and screens.QR_MAX_PX is 190. Nothing pinned that
# gap, so lowering QR_MAX_PX would have reopened the crop in silence
# (found reading qrchannel.py, 2026-09-08).
import qrcode                                              # noqa: E402

BORDER = 2                          # qrchannel.text_to_image's default
_v40 = qrcode.QRCode(box_size=1, border=BORDER,
                     error_correction=qrcode.constants.ERROR_CORRECT_M)
_v40.add_data("x" * 2300)          # the most ECC-M carries at version 40
_v40.make(fit=True)
_widest = _v40.modules_count + 2 * BORDER
if _v40.version != 40:
    bad(f"2300 characters no longer reaches QR version 40 ({_v40.version}), "
        "so this check is not measuring the widest code any more")
elif _widest > screens.QR_MAX_PX:
    bad(f"QR_MAX_PX is {screens.QR_MAX_PX} and the widest QR is {_widest} "
        "modules, so text_to_image can be handed a panel it cannot fit and "
        "qr_export would paste a cropped, unreadable code")
else:
    ok(f"the widest QR is {_widest} modules and QR_MAX_PX is "
       f"{screens.QR_MAX_PX}, so the export code always fits its card")

# ...and when it genuinely cannot fit, it refuses rather than clamping.
try:
    qrchannel.text_to_image("x" * 200, panel=(40, 40))
except qrchannel.QrChannelError:
    ok("a code too large for its panel is refused, not silently oversized")
except Exception as exc:
    bad(f"a code too large for its panel raised {type(exc).__name__}, which "
        f"Session.HANDLED does not catch: {exc}")
else:
    bad("a code too large for its panel came back anyway; qr_export would "
        "paste it cropped")

# ...and a text no QR can carry is a message, not a dead process.
try:
    qrchannel.text_to_image("x" * 2400, panel=(320, screens.QR_MAX_PX))
except qrchannel.QrChannelError:
    ok("a text past QR version 40 is refused with an error the panel shows")
except Exception as exc:
    bad(f"a text past QR version 40 raised {type(exc).__name__}, which "
        f"Session.HANDLED does not catch, so the process ends: {exc}")
else:
    bad("a text past QR version 40 rendered something anyway")

# --- the outbound frame must not shrink for the sake of the card -------
#
# The signed transaction leaves by this screen, a coordinator's camera has
# to read it, and M1's optics are the gate that is not passed. Putting the
# frames on the export's card (2026-09-08) was only acceptable because it
# cost nothing: sized against the card's budget an outbound frame is the
# same 212px the white letterbox gave it. If a later change to the pad,
# the stroke or MAX_FRAGMENT_LEN starts charging the code for the
# decoration, that is a readability regression wearing a style change.
_psbt = base64.b64encode(b"psbt\xff" + b"\x01\x02\x03" * 400).decode()
_frames = qrchannel.psbt_to_frames(_psbt)
for _W, _H in ((320, 240), (240, 240)):
    _bare = qrchannel.frames_to_images(_frames, panel=(_W, _H))[0].width
    _budget = min(_W, _H) - 2 * (screens.QR_CARD_PAD + screens.STROKE)
    _carded = qrchannel.frames_to_images(_frames,
                                         panel=(_budget, _budget))[0].width
    if _carded < _bare:
        bad(f"{_W}x{_H}: the card costs the outbound QR {_bare - _carded}px "
            f"({_bare} -> {_carded}). A coordinator's camera pays for that, "
            "and M1's optics are not proven.")
    else:
        ok(f"{_W}x{_H}: the outbound QR is {_carded}px on the card, the same "
           "as it was on the bare white panel")

# --- the export screen must offer EVERY mask, not one -------------------
#
# Driven through the real _export_qr, because the whole point is what
# main.py paints. tests/sparrow checks that the eight renders decode; this
# checks that the device shows them. Between them a regression to one
# static render cannot pass: rebuilding text_to_images as a single
# text_to_image leaves this at 1 painted frame.
#
# One descriptor in a hundred gets a mask Sparrow's zxing cannot read, the
# same mask every time, so that key's export never worked (ISSUES.md E-5).
# The worst descriptor of 120 had 7 of 8 masks readable, which is why
# showing all eight is the fix.
_painted.clear()
_sess = coresigner_main.Session(_Disp(), hal.DevButtons("a"), rpc=object(),
                           animate=False)
_DESC = ("wpkh([73c5da0a/84h/1h/0h]tpubDC3Amaq5HtW8qxGoTwyDBaD12LoZ6xXuHGKo3"
         "34cLm2yJAze7kU9fmKkfYgMy6jvCRUxQxQ9FruBfEytY5sQE5RBex8X4nEY8W26B9"
         "KYvzF/0/*)#85lfyknl")
_took = _sess._export_qr("coresigner", _DESC, "wpkh")
_distinct = {i.tobytes() for i in _painted}
if len(_painted) != len(qrchannel.QR_MASKS):
    bad(f"the export screen painted {len(_painted)} frame(s), not one per "
        f"mask ({len(qrchannel.QR_MASKS)}). A single render is unreadable "
        "for about one descriptor in a hundred and always the same one.")
elif len(_distinct) != len(_painted):
    bad(f"the export painted {len(_painted)} frames but only "
        f"{len(_distinct)} are different, so some masks are duplicates and "
        "the cycle covers less than it claims")
elif _took is not True:
    bad(f"the export returned {_took!r} after A, so the animated screen "
        "lost the key that accepts it")
else:
    ok(f"the export screen shows all {len(_painted)} masks, every one a "
       "different image, and still answers the button")

# --- DOWN holds one mask, so a scanner can be handed a STATIC code -----
#
# The cycling is the only thing on this device that changes a QR while a
# scanner is looking at it, and a scanner that reads a changing image as
# a multi-part code waits for a part that never arrives. BlueWallet hangs
# on this export (Ben, 2026-09-24). Holding a mask is what tells a tester
# whether the cycling is the cause, so it has to actually stop: a hold
# that still advances proves nothing either way.
#
# The keys go in as a script and the frames painted after the hold are
# read back. Rule 2: this counts DISTINCT images the device painted, and
# does not ask main.py what it thinks it is showing.
_painted.clear()
_sess = coresigner_main.Session(_Disp(), hal.DevButtons("dddda"), rpc=object(),
                                animate=False)
_sess._export_qr("coresigner", _DESC, "wpkh")
_after_hold = _painted[len(qrchannel.QR_MASKS):-0 or None][:4]
if len({i.tobytes() for i in _after_hold}) != 1:
    bad(f"DOWN four times painted {len({i.tobytes() for i in _after_hold})} "
        "different images; the hold does not hold, so a scanner is still "
        "being shown a moving target")
else:
    ok("DOWN holds one mask and keeps holding it")

# LEFT and RIGHT must reach every mask and come back, or a tester cannot
# say WHICH mask a coordinator choked on.
_painted.clear()
_steps = "d" + "r" * len(qrchannel.QR_MASKS)
_sess = coresigner_main.Session(_Disp(), hal.DevButtons(_steps + "a"),
                                rpc=object(), animate=False)
_sess._export_qr("coresigner", _DESC, "wpkh")
_stepped = _painted[len(qrchannel.QR_MASKS):]
_seen = {i.tobytes() for i in _stepped}
if len(_seen) != len(qrchannel.QR_MASKS):
    bad(f"stepping RIGHT through the set reached {len(_seen)} of "
        f"{len(qrchannel.QR_MASKS)} masks")
elif _stepped[0].tobytes() != _stepped[-1].tobytes():
    bad("stepping RIGHT once per mask did not return to the first one, so "
        "the step does not wrap")
else:
    ok(f"LEFT and RIGHT reach all {len(qrchannel.QR_MASKS)} masks and wrap")

# UP lets go, and the cycle must be the ORIGINAL frames again: a held
# render carries a line saying it is held, and shipping that to a
# coordinator would be a different image from the one Sparrow was tested
# against.
_painted.clear()
_sess = coresigner_main.Session(_Disp(), hal.DevButtons("dua"), rpc=object(),
                                animate=False)
_sess._export_qr("coresigner", _DESC, "wpkh")
if _painted[-1].tobytes() != _painted[0].tobytes():
    bad("UP did not return the screen to the first cycling frame")
else:
    ok("UP lets go and the plain cycling frame comes back")

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
