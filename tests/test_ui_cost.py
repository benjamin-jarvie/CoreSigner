"""Device usability, measured rather than asserted.

Two things this suite holds:

1. Error recovery. A failure the user cannot read is a failure the user
   cannot act on. Every failing path must park on its message until a key is
   pressed, and the sign button must say why it is refusing.
2. Entry cost. The number of button presses per task is a design budget. The
   figures below are the audit's measurements; the test fails if entry gets
   more expensive, so a regression is visible before it reaches a panel.

Run: python3 tests/test_ui_cost.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "coresigner"))
import main as coresigner_main  # noqa: E402
import qrsource  # noqa: E402
import screens  # noqa: E402

fails = []


def ok(m):
    print("ok  ", m)


def bad(m):
    fails.append(m)
    print("FAIL", m)


class RecordingDisplay:
    """Records which screen was painted, in order."""

    width, height = 320, 240

    def __init__(self):
        self.painted = []

    def show(self, image, sensitive=False):
        self.painted.append(image)


class ScriptedButtons:
    def __init__(self, script):
        self.script = list(script)
        self.reads = 0

    def read(self):
        self.reads += 1
        if not self.script:
            raise AssertionError("session read past the end of its script")
        return self.script.pop(0)


def named_screens(display):
    """Paint names instead of images, so a session's screen order is
    assertable without rendering."""
    for name in ("home", "keys_menu", "result", "busy", "review",
                 "settings_menu"):
        setattr(screens, name,
                (lambda n: lambda *a, **k: n)(name))
    return display


class FakeRpc:
    wallet_dir = Path("/nonexistent")  # A-22: close_session reads it
    chain = "regtest"

    def call(self, method, *a, **k):
        # home now asks for the master fingerprint on every repaint, and it
        # subscripts what comes back. A double that returns "" for every
        # method is not a wallet-shaped answer.
        if method == "listdescriptors":
            return {"descriptors": []}
        return ""


# --- boot splash: the signing unit must activate the ordered unit ---------
service = (ROOT / "image" / "coresigner.service").read_text()
if "Wants=coresigner-splash.service" not in service:
    bad("coresigner.service orders after the splash but does not activate it")
else:
    ok("enabling coresigner.service also activates the brand splash")


# --- boot splash: the dedicated entrypoint paints exactly one frame -------
painted = []


class SplashDisplay:
    width, height = 320, 240

    def show(self, image, sensitive=False):
        painted.append(image)


import splash as coresigner_splash  # noqa: E402  (dedicated boot entrypoint)

real_argv = sys.argv
real_dev_display = coresigner_splash.hal.DevDisplay
real_splash = screens.splash
try:
    sys.argv = ["splash.py", "--dev", "--frames-dir", "unused"]
    coresigner_splash.hal.DevDisplay = lambda _path: SplashDisplay()
    # build and dev are what the frame now says about the card
    # (2026-09-23), and a stub that ignores them would let the
    # entrypoint stop passing them without anything noticing.
    screens.splash = lambda w, h, build=None, dev=(): (
        "splash", w, h, build, tuple(dev))
    coresigner_splash.main()
finally:
    sys.argv = real_argv
    coresigner_splash.hal.DevDisplay = real_dev_display
    screens.splash = real_splash

# The dev reasons come from the real filesystem this suite runs on, so
# they are whatever the developer's machine is; the check is that the
# entrypoint passes BOTH arguments and paints one frame.
if len(painted) != 1 or painted[0][:3] != ("splash", 320, 240):
    bad(f"the splash entrypoint painted unexpected frames: {painted}")
elif len(painted[0]) != 5:
    bad("the splash entrypoint no longer says what the card is")
else:
    ok("the splash entrypoint paints one branded frame and exits")


# --- backup strings paginate in order and C aborts immediately ------------
backup_calls = []
real_backup_screen = screens.backup_page
try:
    screens.backup_page = (
        lambda _w, _h, page_text, _label, page, pages, actions_sel=0:
        backup_calls.append((page_text, page, pages)) or "backup")
    buttons = ScriptedButtons("aac")   # page, page, abort
    session = coresigner_main.Session(RecordingDisplay(), buttons, FakeRpc())
    completed = session._show_backup("x" * 97, "KEY  D2B7E45C")
    abort_calls = list(backup_calls)

    backup_calls.clear()
    success_buttons = ScriptedButtons("aa")
    success_session = coresigner_main.Session(
        RecordingDisplay(), success_buttons, FakeRpc())
    succeeded = success_session._show_backup("y" * 49, "KEY  D2B7E45C")
    success_calls = list(backup_calls)

    # The last page's bar is live: CHECK IT is pre-selected and DONE is
    # one press LEFT of it (Ben, 2026-09-19: the safe choice belongs
    # under the thumb, and leaving without checking is the risky one).
    # Both must reach the caller.
    backup_calls.clear()
    check_session = coresigner_main.Session(
        RecordingDisplay(), ScriptedButtons("ala"), FakeRpc())
    chose_done = check_session._show_backup("z" * 49, "KEY  D2B7E45C")

    # DOWN turns the page everywhere else, so it turns the page here too
    # (057d910). On the LAST page there is nowhere to turn to, and it must
    # not fall through into DONE: the same gesture would then finish the
    # backup, and the user meant "next page". The `continue` that stops
    # that had no assertion behind it until 2026-09-08.
    backup_calls.clear()
    down_session = coresigner_main.Session(
        RecordingDisplay(), ScriptedButtons("dda"), FakeRpc())
    down_result = down_session._show_backup("w" * 49, "KEY  D2B7E45C")
    down_calls = list(backup_calls)
finally:
    screens.backup_page = real_backup_screen

expected_abort = [("x" * 48, 0, 3), ("x" * 48, 1, 3), ("x", 2, 3)]
expected_success = [("y" * 48, 0, 2), ("y", 1, 2)]
if (completed is not None or abort_calls != expected_abort
        or buttons.reads != 3
        or succeeded != "check" or success_calls != expected_success
        or success_buttons.reads != 2):
    bad("backup pagination/abort drifted: "
        f"abort={abort_calls}, success={success_calls}")
else:
    ok("backup strings paginate in order, complete, and abort immediately")

if succeeded != "check":
    bad("A on the last backup page did not choose CHECK IT. The safe "
        "choice has to be the one already under the thumb.")
elif chose_done != "done":
    bad(f"LEFT then A on the last backup page returned {chose_done!r}, "
        "so DONE is not reachable beside CHECK IT")
else:
    ok("CHECK IT is the default, and LEFT reaches DONE without checking")

# "d" turns page 0 -> 1, then "d" on the last page redraws it, then "a"
# finishes. Four paints for three presses: pages 0, 1, and 1 again.
expected_down = [("w" * 48, 0, 2), ("w", 1, 2), ("w", 1, 2)]
if down_result != "check":
    bad(f"DOWN then A on the last backup page returned {down_result!r}")
elif down_calls != expected_down:
    bad(f"DOWN on the LAST backup page did not simply redraw it: "
        f"{down_calls}")
else:
    ok("DOWN turns the page, and on the last page redraws it rather than "
       "finishing the backup")


# --- D6: a failing seed mode must hold its message ------------------------
_real = {n: getattr(screens, n) for n in
         ("home", "keys_menu", "result", "busy", "review",
          "settings_menu")}
display = named_screens(RecordingDisplay())
# R,A opens the Keys tile. The list is flat now (Ben, 2026-09-05), so D
# moves from New key to Scan a key, A picks it, A accepts the warning, and
# the scan fails because this machine has no camera. ONE key dismisses the
# error. C leaves Keys for home, then D,R,A,A goes to settings and powers
# off. If the error is not held, the dismissing A falls through and the
# script runs out.
buttons = ScriptedButtons(["r", "a"] + ["d", "a"] + ["a"] + ["a"] +
                          ["c"] + ["d", "r", "a", "a"])
session = coresigner_main.Session(display, buttons, FakeRpc())
session.qr = qrsource.CameraQrSource()
raised = None
try:
    session.state_home()
except Exception as exc:
    raised = exc
for n, f in _real.items():
    setattr(screens, n, f)

painted = display.painted
if raised is not None:
    bad(f"the error was not held; the session ran off its script: {raised}")
# The failure is SHOWN, and the screen after it is the one level UP from
# the menu that failed, not the menu itself. Before 2026-09-05 that was
# home, because Load a key was reached straight from the tile; now Keys
# sits between them, so Keys is where a failed load returns you.
elif not (painted[0] == "home"
          and "result" in painted
          and painted[painted.index("result") + 1] == "keys_menu"
          and painted.count("keys_menu") >= 2):
    bad(f"unexpected screen order after a failing key mode: {painted}")
else:
    ok("a failing key mode holds its error, then returns to Keys")

# --- the typed key screen must not be a dead end --------------------------
# On the board 2026-09-05 the back button did nothing on an empty box,
# because B deletes a character and there was nothing to delete. It was
# found on the passphrase screen, which went with the encrypted backup
# (A-24); the same rule binds the one typed screen that is left, where a
# private key goes in.
class Recorder:
    width, height = 320, 240

    def __init__(self):
        self.painted = []

    def show(self, image, sensitive=False):
        self.painted.append(image)


def entry(keys):
    sess = coresigner_main.Session(Recorder(), ScriptedButtons(keys), FakeRpc())
    return sess._text_entry("MASTER  PRIVATE  KEY", "xprv", secret=True)


if entry(["b"]) is None:
    ok("B on an empty typed key goes back instead of doing nothing")
else:
    bad("B on an empty typed key did not go back")

if entry(["a", "b", "b"]) is None:
    ok("B deletes what is there, then goes back when there is nothing")
else:
    bad("B did not fall through to back once the buffer was empty")

# --- D9: the sign button explains its refusal -----------------------------
img_quiet = _real["review"](320, 240, [("bc1q", 1.0)], 0.0001)
img_loud = _real["review"](320, 240, [("bc1q", 1.0)], 0.0001,
                           unseen_pages=True)
if img_quiet.tobytes() == img_loud.tobytes():
    bad("review() renders the same frame whether or not SIGN was refused")
else:
    ok("review() says why it will not sign yet")

# --- entry cost budgets ---------------------------------------------------
# A-22: the word and codex32 entry budgets went with their grids. What is
# left is the base58 grid, which types a private key in and types one back
# to check a paper backup. TESTING.md rule 6: these are measurements, taken
# by replaying the real navigation rules, and they fail when the cost
# drifts.
#
# Up and down cross a page boundary (main._grid_move). Before they did, a
# page turn meant walking to the end of the 32-cell strip first, and base58
# is 58 characters over two pages. That one rule is most of the difference
# below, and without it Ben's paper check would have cost 1,444 presses,
# which is not a check anyone performs.
sys.path.insert(0, str(ROOT / "tests"))
from e2e_keys import check_keys, entry_keys      # noqa: E402

KEY = ("tprv8ZgxMBicQKsPe5YMU9gHen4Ez3ApihUfykaqUorj9t6FDqy3nP6eoXiAo2ss"
       "vpAjoLroQxHqr3R5nE3a5dU3DHTjTgJDd7zrbniJr6nrCzd")

# Both flows as a person meets them: the 16-character master prefix is
# already on the screen, and the check is one pass over the whole key
# rather than three pages each of which had to be perfect before the
# next would open (both 2026-09-19).
typing_in = len(entry_keys(KEY))
checking = len(check_keys(KEY))

for label, got, budget in (
        ("typing a 111-character private key in", typing_in, 640),
        ("checking a written backup, all 111 characters", checking, 650)):
    if got <= budget:
        ok(f"{label}: {got} presses (budget {budget})")
    else:
        bad(f"{label}: {got} presses, over the {budget} budget")

# The grid is one MODE at a time now and never a paged alphabet (map
# typing, T2), so there is no page boundary left to cross. What has to
# hold instead is that the d-pad reaches every cell from every cell, and
# that DOWN from the bottom row is the ONE move that does not move,
# because that is how the loop leaves the grid for ABORT and CHECK.
_cells = screens.mode_cells(screens.modes("xprv")[0][1])
_cols = screens.GRID_COLS
_last = len(_cells) - 1
_bottom = (_last // _cols) * _cols

_stuck = [i for i in range(len(_cells))
          for k in "udlr"
          if coresigner_main._cell_move(k, _cells, i) == i
          and not (k == "d" and i >= _bottom)]
if _stuck:
    bad(f"cells {sorted(set(_stuck))} have a d-pad press that does "
        "nothing, and a press that does nothing reads as off the grid")
elif coresigner_main._cell_move("d", _cells, _last) != _last:
    bad("DOWN from the last cell moved instead of leaving for the bar")
elif coresigner_main._cell_move("l", _cells, _bottom) != _bottom - 1:
    bad("LEFT from the start of the last row does not reach the "
        "character before it")
elif coresigner_main._cell_move("d", _cells, _bottom - 1) != _last:
    bad("DOWN from the long row above a ragged one does not land on "
        "the end of it")
elif coresigner_main._cell_move("u", _cells, 0) != _bottom:
    bad("UP from the top row does not come round to the bottom")
else:
    ok("every cell moves on every press, except DOWN off the bottom")

# Every cell is REACHABLE from every other, which is the property the
# four checks above are each one example of.
def _reach(start):
    seen, queue = {start}, [start]
    while queue:
        at = queue.pop(0)
        for k in "udlr":
            nxt = coresigner_main._cell_move(k, _cells, at)
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


if all(len(_reach(i)) == len(_cells) for i in range(len(_cells))):
    ok(f"and all {len(_cells)} cells reach all {len(_cells)} others")
else:
    bad("some cell cannot reach the whole grid with the d-pad")



# --- I-6: the two decision screens, branch by branch ----------------------
# Both were reached only through end-to-end sessions, so their individual
# outcomes were never asserted. TESTING.md rule 3: exercise the branches.

class Rec:
    """Records the actions_sel / selected each screen was drawn with."""

    width, height = 320, 240

    def __init__(self):
        self.sel = []

    def show(self, image, sensitive=False):
        pass


def signed_outcome(keys):
    d = Rec()
    sess = coresigner_main.Session(d, ScriptedButtons(list(keys)), FakeRpc())
    return sess._state_signed("x.psbt written")


# The second choice was POWER OFF until 2026-09-24, and so was C. Ben
# cut both: "it's not intuitive and they can just pull the cable if they
# want to turn it off or do it from home". Powering down lives in
# Settings, where a person goes looking for it. C now does nothing here,
# which is what every other screen's unused button does, so it is
# checked by the ScriptedButtons script running out rather than by a
# return value.
cases = [("a", coresigner_main.SIGN_AGAIN, "A on the default choice signs another"),
         ("ra", coresigner_main.TO_HOME, "R then A goes home"),
         ("rla", coresigner_main.SIGN_AGAIN, "R then L returns to sign another"),
         ("ba", coresigner_main.TO_HOME, "B leaves the screen, as it does everywhere")]
for keys, want, why in cases:
    got = signed_outcome(keys)
    if got != want:
        bad(f"_state_signed({keys!r}) returned {got!r}, expected {want!r}")
    else:
        ok(f"_state_signed: {why}")

try:
    signed_outcome("c")
except AssertionError:
    ok("_state_signed: C no longer halts the device from the result screen")
else:
    bad("C still leaves the result screen; POWER OFF was cut from it")




# A-22: the passphrase entry checks lived here. The prompt was a BIP39
# concept and went with it, and this scaffolding computed key sequences
# for assertions that were already gone.


print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
