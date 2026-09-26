"""Typing a paper backup back in, and being told exactly what is wrong.

The last backup page offered VERIFY for months and then just returned to
the menu (Ben, on the board, 2026-09-05). This is the flow it now runs.

These checks are in-process rather than in an end-to-end session because
every screen in the flow shows key material, so hal.DevDisplay blanks the
frames a scripted session writes: the session can prove the flow COMPLETES
but cannot see what it said. Here the screens are rendered directly and the
loop is driven with a scripted keypad, so the wrong character is visible.

Run: python3 tests/test_backup_check.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "coresigner"))
sys.path.insert(0, str(ROOT / "tests"))
import hal                              # noqa: E402
import main as coresigner_main               # noqa: E402
import screens                          # noqa: E402
from PIL import ImageDraw               # noqa: E402
import signer                           # noqa: E402
from e2e_keys import (check_keys, commit_presses,  # noqa: E402
                      grid_presses, text_keys)

# A real regtest master private key, the one every other suite signs with.
KEY = ("tprv8ZgxMBicQKsPe5YMU9gHen4Ez3ApihUfykaqUorj9t6FDqy3nP6eoXiAo2ss"
       "vpAjoLroQxHqr3R5nE3a5dU3DHTjTgJDd7zrbniJr6nrCzd")
LABEL = "KEY  73C5DA0A"

fails = []


def ok(m):
    print("ok  ", m)


def bad(m):
    print("FAIL", m)
    fails.append(m)


class Frames:
    """Keeps every frame, unblanked, so a check can look at what was said."""

    width, height = 320, 240

    def __init__(self):
        self.shown = []

    def show(self, image, sensitive=False):
        self.shown.append(image)


class NoRpc:
    """A node that is never called, but knows which network it is on.

    The typing screens ask, because the 16 characters every master key
    starts with are different on mainnet and everywhere else.
    """

    chain = "regtest"


def session(script):
    return coresigner_main.Session(Frames(), hal.DevButtons(script), NoRpc(),
                              animate=False, on_device=False)


def run_page(script, want):
    """Drives the check and returns (session, typed) or the exhaustion.

    `_check_typed` hands back (typed, corrections); the corrections are
    checked where they matter and most cases here only care whether the
    key was accepted.
    """
    sess = session(script)
    try:
        return sess, sess._check_typed(LABEL, want)[0]
    except hal.ScriptExhausted:
        return sess, "ran out of presses"


def run_full(script, want):
    """The same, keeping the corrections the device made."""
    sess = session(script)
    try:
        return sess, sess._check_typed(LABEL, want)
    except hal.ScriptExhausted:
        return sess, ("ran out of presses", [])


def drew(sess, image):
    want = image.tobytes()
    return any(f.tobytes() == want for f in sess.display.shown)


PAGES = screens.text_pages(KEY)


# --- 1. a page typed correctly is accepted ------------------------------
# No trailing "a": text_keys walks to the bar and takes CHECK itself, and
# a correct key returns from _check_typed with no verdict in between.

sess, got = run_page(check_keys(PAGES[0]), PAGES[0])
if got != PAGES[0]:
    bad(f"a correctly typed page was not accepted: {got!r}")
else:
    ok("a page typed correctly is accepted, with no screen in the way")

# --- 2. a wrong character goes red where it sits ------------------------
# Map correction C1. It goes red immediately and the caret moves on, so
# at most one can ever be outstanding.

# PAST THE PREFIX. The first 16 characters are prefilled, so they can
# no longer be got wrong, and a check that puts the mistake there is
# checking nothing.
AT = len(signer.TESTNET_PREFIX) + 4
BAD_PAGE = PAGES[0][:AT] + ("2" if PAGES[0][AT] != "2" else "3") + PAGES[0][AT + 1:]

# Typed only as far as the mistake, and NOT committed: the key is not
# finished, so nothing moves the focus to the bar and a trailing press
# would simply type another character.
_upto = BAD_PAGE[:AT + 1]
_presses, _mode, _cur = grid_presses(
    "xprv", _upto[len(signer.TESTNET_PREFIX):])
sess, got = run_page(_presses, PAGES[0])
if got != "ran out of presses":
    bad(f"the key was accepted with a wrong character in it: {got!r}")
elif not drew(sess, screens.text_entry(
        320, 240, f"{LABEL}  ·  {AT + 1}/{len(PAGES[0])}",
        _upto, _cur, "xprv", _mode,
        actions_sel=None, caret=AT + 1, actions=("ABORT", "DONE"),
        wrong={AT}, want_len=len(PAGES[0]),
        hint=coresigner_main.Session._type_hint(screens.modes("xprv"), 0))):
    bad(f"the screen was not redrawn with position {AT} marked")
else:
    ok("a wrong character goes red where it sits, and the caret moves on")

# A marked page and a clean one must not render the same, or the red
# outline is decoration.
# Both renders carry the SAME caret, so the only difference is the
# outline. Without it the window follows the caret to the end of the
# page and position 5 is not on screen to be marked at all.
if (screens.text_entry(320, 240, "T", PAGES[0], 0, "xprv", 0, caret=AT,
                       wrong={AT}, want_len=48).tobytes()
        == screens.text_entry(320, 240, "T", PAGES[0], 0, "xprv", 0,
                              caret=AT, wrong=set(), want_len=48).tobytes()):
    bad("a page with a mistake renders identically to a clean one")
else:
    ok("the red outline actually changes what is drawn")

# --- 3. you cannot add a character with one still red ------------------
# The whole of map correction C1. Ben, 2026-09-19: "if you type
# something incorrect, the box goes red and you can't move forward."

_one_more = BAD_PAGE[:AT + 2]          # the wrong one, then one more
sess, (got, made) = run_full(check_keys(PAGES[0], _one_more), PAGES[0])
_box, _char = AT // 4 + 1, AT % 4 + 1
if not drew(sess, screens.wrong_character(320, 240, _box, _char,
                                          BAD_PAGE[AT], PAGES[0][AT])):
    bad(f"typing on past a wrong character did not stop with the "
        f"message naming box {_box}, character {_char}")
else:
    ok(f"adding a character with one still red stops, and names box "
       f"{_box}, character {_char}")

# AND THE DEVICE PUTS THE RIGHT CHARACTER IN, so the person carries on.
# Driven on a short key, because the presses have to be written out: the
# press that triggers the message is spent on it, so the character is
# typed, dismissed, and typed again. That is what a person does.

SHORT_WANT = "abcdefgh"
_p1, _m1, _c1 = grid_presses("xprv", "abcx")      # x is wrong at 3
_p2, _m2, _c2 = grid_presses("xprv", "e", _m1, _c1)   # this one is stopped
_p3, _m3, _c3 = grid_presses("xprv", "efgh", _m2, _c2)  # dismissed, retyped
# No walk down to the bar: the last character finishes the key, so the
# focus is already there and one press takes DONE.
sess3 = session(_p1 + _p2 + "a" + _p3 + "a")
got3, made3 = sess3._check_entry(LABEL, SHORT_WANT, "")
if got3 != SHORT_WANT:
    bad(f"the key was not repaired by the correction: {got3!r}")
elif made3 != [(1, 4, SHORT_WANT[3])]:
    bad(f"the corrections recorded are {made3}, not [(1, 4, 'd')]")
else:
    ok("the device puts the right character in, and records where")

# --- 4. a short page is not accepted, and is not accused either --------
# Red marks what you typed wrong. Every position ahead of the caret was
# in that set until 2026-09-18, so a page eight characters in drew forty
# red boxes: the screen said "forty mistakes" to a person who had made
# none. Whether the page is FINISHED is a question about its length.

short = PAGES[0][:20]
if coresigner_main._wrong_at(short, PAGES[0]):
    bad("a page still being typed is marked wrong where it is not typed")
else:
    ok("a page still being typed carries no red marks ahead of the caret")

# Typed short, then WALK DOWN TO THE BAR and press DONE. The walk is the
# point: `check_keys` commits with one press because a finished key moves
# the focus itself, and a short one does not, so the old version of this
# check typed two more characters and never reached a button at all. It
# ran out of presses whatever DONE did, which a mutation run found on
# 2026-09-23 by making DONE accept a short key and watching this pass.
_short_presses, _short_mode, _short_cur = grid_presses(
    "xprv", short[len(signer.TESTNET_PREFIX):])
_sess, got = run_page(
    _short_presses + commit_presses("xprv", _short_mode, _short_cur),
    PAGES[0])
if got != "ran out of presses":
    bad(f"a key that stops {len(PAGES[0]) - len(short)} characters early "
        f"was accepted: {got!r}")
else:
    ok("DONE on a key that is not finished puts you back in the grid")

# --- 5. the caret keys ON the grid move the caret --------------------
# SeedSigner puts cursor-left, cursor-right and backspace on the keyboard
# itself (map typing, T1), so there is no mode to find. Ours needs two,
# because B already deletes. Ben could not move the caret at all before:
# it lived behind a C nothing mentioned.

_CELLS = screens.mode_cells(screens.modes("xprv")[0][1])
_LEFT = _CELLS.index(screens.CARET_LEFT)


def _walk(cur, target):
    """Presses that move the grid cursor from one cell to another."""
    seen, queue = {cur: ""}, [cur]
    while queue:
        at = queue.pop(0)
        if at == target:
            return seen[at]
        for k in ("u", "d", "l", "r"):
            nxt = coresigner_main._cell_move(k, _CELLS, at)
            if nxt not in seen:                 # a move that does not move
                seen[nxt] = seen[at] + k        # is never put in the route
                queue.append(nxt)
    raise AssertionError(f"no route to cell {target}")


def _to_bar(cur):
    """Presses that take the cursor off the bottom of the grid, to the bar."""
    at, downs = cur, 1
    while coresigner_main._cell_move("d", _CELLS, at) != at:
        at = coresigner_main._cell_move("d", _CELLS, at)
        downs += 1
    return "d" * downs


# STARTED FROM A CORRECT STRING, because the device cannot produce any
# other kind now: a wrong character is corrected the moment somebody
# tries to move past it, so five wrong characters is a state that never
# exists. Two lefts and a delete take the third of five away.
# NOT COMMITTED, and read off the screen rather than the return value:
# a delete in the middle leaves a character that no longer matches, so
# pressing DONE would fire the correction message, which is the next
# check's business and not this one's.
_FIVE = PAGES[0][:5]
_drawn = []
_real_te = screens.text_entry


def _tap(*a, **kw):
    _drawn.append(a[3])                  # the typed string, as drawn
    return _real_te(*a, **kw)


screens.text_entry = _tap
try:
    sess = session(_walk(0, _LEFT) + "aa" + "b")
    try:
        sess._check_entry(LABEL, PAGES[0], _FIVE)
    except hal.ScriptExhausted:
        pass
finally:
    screens.text_entry = _real_te

if _drawn[-1] != _FIVE[:2] + _FIVE[3:]:
    bad(f"the caret keys did not move the caret: the screen shows "
        f"{_drawn[-1]!r}, expected {_FIVE[:2] + _FIVE[3:]!r} after two "
        "lefts and a delete")
else:
    ok("the caret keys walk the typed text, and B deletes at the caret")

# --- 6. ABORT is reachable by going DOWN, and the d-pad loops ----------
# Ben, on the board (2026-09-11): "If you can't get to abort, remove the
# button or make sure you can go down and left and actually get to the
# button." DOWN off the bottom row now lands on the bar, and DOWN again
# returns to the top of the grid.

sess = session(_to_bar(0) + "la")
typed, _made = sess._check_entry(LABEL, PAGES[0], "")
if typed is None:
    ok(f"DOWN x{len(_to_bar(0))} reaches the bar, then L and A abort")
else:
    bad(f"DOWN then L then A returned {typed!r}; the buttons under the "
        "grid are not reachable by going down")

sess = session(_to_bar(0) + "d" + _to_bar(0) + "la")
typed, _made = sess._check_entry(LABEL, PAGES[0], "")
if typed is None:
    ok("DOWN from the bar loops to the top of the grid, and round again")
else:
    bad(f"the d-pad did not loop through the bar; got {typed!r}")

sess = session("b")
typed, _made = sess._check_entry(LABEL, PAGES[0], "")
if typed is not None:
    bad("B with nothing typed did not leave the check entry")
else:
    ok("B with nothing typed leaves, the way B leaves everywhere else")

# --- 6b. the case toggle keeps the letter you were on ------------------
# Ben, on the board, 2026-09-18: "Should the numbers be after the
# characters so when going to and fro the capitals and normal letters,
# we are closer to where we were on the alphabet?" They moved, and the
# cursor lands on the same letter rather than near it.

_UP = screens.mode_cells(screens.modes("xprv")[1][1])
for _ch in "apz":
    _at = _CELLS.index(_ch)
    _to = _UP[coresigner_main._same_cell(_CELLS, _at, _UP)]
    if _to != _ch.upper():
        bad(f"C from {_ch!r} landed on {_to!r}, not {_ch.upper()!r}")
        break
else:
    ok("C carries the cursor to the same letter in the other case")

for _ch in (screens.CARET_LEFT, screens.CARET_RIGHT):
    _at = _CELLS.index(_ch)
    if _UP[coresigner_main._same_cell(_CELLS, _at, _UP)] != _ch:
        bad(f"C moved the cursor off the {_ch!r} key")
        break
else:
    ok("and leaves the caret keys where they are, which are in both")

_five = _CELLS.index("5")
if coresigner_main._same_cell(_CELLS, _five, _UP) == 0:
    ok("a digit has no other case, so C starts at the first capital")
else:
    bad("C from a digit lands somewhere with no relation to the digit")

# --- 6c. Core's refusal, in words a person can act on ------------------
# What Ben read on the board (2026-09-18), cut at sixty characters with
# the verdict still to come:
#   "getdescriptorinfo: error code: -5 error message: pkh(): key"

CORE_SAID = ("getdescriptorinfo: error code: -5\n\nerror message:\n"
             "pkh(): key 'tprv8ZgxMBicQKsPe5YM' is not valid")
said = coresigner_main.Session._core_says(RuntimeError(CORE_SAID))
if "not a valid key" in said and "pkh" not in said and len(said) < 60:
    ok(f"a refused key reads as {said!r}")
else:
    bad(f"a refused key still reads as Core's own jargon: {said!r}")

other = coresigner_main.Session._core_says(
    RuntimeError("loadwallet: error code: -4\n\nerror message:\n"
                 "Wallet file verification failed."))
if other == "Wallet file verification failed.":
    ok("and any other refusal keeps Core's own last line")
else:
    bad(f"an unrelated Core error was mangled: {other!r}")

# --- 6d. a refused key reopens the grid holding what was typed ---------
# Core refused Ben's key on the board (2026-09-18) and the screen threw
# all 111 characters away, so one wrong character cost the whole key
# again. The proof is that the SECOND attempt carries the same string
# with no typing presses between the two.

TRIES = []


def _refuse_once(_rpc, text):
    TRIES.append(text)
    if len(TRIES) == 1:
        raise RuntimeError("getdescriptorinfo: error code: -5\n\n"
                           "error message:\npkh(): key 'x' is not valid")
    return signer.WALLET


# The grid opens holding the prefix, so the person types what follows.
TAIL = "e5YMU9gH"
TYPED = signer.TESTNET_PREFIX + TAIL
_real_open = signer.open_session_xprv
signer.open_session_xprv = _refuse_once
try:
    sess = session(text_keys("xprv", TAIL)       # type it, then DONE
                   + "a"                         # dismiss FAILED
                   + commit_presses("xprv", 0, 0))   # DONE again, as-is
    sess._key_xprv_typed()
finally:
    signer.open_session_xprv = _real_open

if TRIES == [TYPED, TYPED]:
    ok("a refused key comes back typed, and DONE alone sends it again")
else:
    bad(f"the second attempt sent {TRIES[1:]!r}, not the key already "
        "typed. A refusal costs all 111 characters again")

# --- 6e. every box of the key is reachable by typing -------------------
# THE CHECK THAT SHOULD HAVE BEEN WRITTEN FIRST. Ben reported this three
# times: "I can not even re-enter all of the words" (2026-09-18), then
# twice on 2026-09-19. Two real defects were found and fixed on the way
# and neither was this one, because each was found by reading the loop
# and reasoning, and this is only visible by typing a key the way a
# person types one: with a mistake in it.
#
# The check took one 48-character page at a time and would not leave a
# page until every character matched, so one capital typed in lower case
# pinned a person on boxes 1 to 12 for ever. This suite typed every page
# PERFECTLY, so it never saw the wall.

KEY_BOXES = screens._groups(KEY)
boxes_seen = set()

# READ OFF THE SCREEN, not recomputed. The first version of this asked
# `text_entry` for its arguments and worked out how many boxes those
# OUGHT to mean, which is the code's own arithmetic run twice: it agreed
# with anything the screen did. A mutation that capped the width at 48,
# the exact defect Ben reported three times, left every suite green
# (2026-09-23). What the box numbers are is a question only the pixels
# can answer, so this counts the numbers the screen actually paints.
_real_text = ImageDraw.ImageDraw.text


def _catch_numbers(self, xy, text, *a, **kw):
    if str(text).isdigit():
        boxes_seen.add(int(text))
    return _real_text(self, xy, text, *a, **kw)


# ONE CAPITAL TYPED IN LOWER CASE, exactly what Ben did, and then the
# whole rest of the key. In three segments, because that is three things
# a person does: type up to the mistake and one past it, read the
# message and dismiss it, type on. The press that raises the message is
# spent on it.
_PRE = len(signer.TESTNET_PREFIX)
WRONG_AT = next(n for n, c in enumerate(KEY)
                if c.isupper() and n >= _PRE)
MISTYPED = KEY[:WRONG_AT] + KEY[WRONG_AT].lower() + KEY[WRONG_AT + 1:]

_up_to, _m1, _c1 = grid_presses("xprv", MISTYPED[_PRE:WRONG_AT + 1])
_trip, _m2, _c2 = grid_presses("xprv", KEY[WRONG_AT + 1], _m1, _c1)
_on, _m3, _c3 = grid_presses("xprv", KEY[WRONG_AT + 1:], _m2, _c2)

ImageDraw.ImageDraw.text = _catch_numbers
try:
    sess = session(_up_to + _trip + "a" + _on + "a")
    typed_back, made_back = sess._check_typed(LABEL, KEY)
finally:
    ImageDraw.ImageDraw.text = _real_text

if typed_back != KEY:
    bad(f"the key did not come back right through a correction: "
        f"{typed_back!r}")
elif made_back != [(WRONG_AT // 4 + 1, WRONG_AT % 4 + 1,
                    KEY[WRONG_AT])]:
    bad(f"the corrections recorded are {made_back}, not the one made")
else:
    ok("a key with one capital in lower case is corrected and accepted")

want_boxes = set(range(1, len(KEY_BOXES) + 1))
if boxes_seen == want_boxes:
    ok(f"a key typed with one capital in lower case still reaches all "
       f"{len(KEY_BOXES)} boxes")
elif boxes_seen:
    bad(f"typing reached boxes {min(boxes_seen)} to {max(boxes_seen)} of "
        f"{len(KEY_BOXES)}. One wrong character early stops the rest of "
        "the key being entered at all.")
else:
    bad("no boxes were drawn at all")

# And box N holds the Nth four characters of the key, which is what a
# person is matching against their paper.
for n in (1, 13, len(KEY_BOXES)):
    if KEY_BOXES[n - 1] != KEY[(n - 1) * 4:n * 4]:
        bad(f"box {n} does not hold characters {(n-1)*4+1} to {n*4}")
        break
else:
    ok("and box N holds the Nth four characters of the key")

# --- 7. every screen that can show a key is marked sensitive ------------
# hal.DevDisplay blanks a frame shown with sensitive=True, which is what
# stops key material landing as a PNG on a developer's disk. That property
# rests on call sites in main.py, and until audit A2 only one was covered,
# incidentally: session K3 counts blank frames and would have noticed the
# backup pages going unmarked.
#
# The first version of this check was VACUOUS for two of its four cases. A
# script of twenty presses never leaves the entry screen, so the verdict
# was never painted and the same 21 entry frames were counted twice; a
# devil's advocate dropped a flag and watched it still pass (2026-09-06).
# Each case below now drives the screen it names, with the arguments the
# real caller uses, and says how many frames it saw so a vacuous run shows
# up as a suspiciously small number.

class FlagWatcher:
    """Records the sensitive flag for every frame, and what was painted."""

    width, height = 320, 240

    def __init__(self):
        self.flags = []
        self.kinds = []

    def show(self, image, sensitive=False):
        self.flags.append(sensitive)
        self.kinds.append(image.size)


def flags_for(what, script, run, want_at_least=1):
    w = FlagWatcher()
    sess = coresigner_main.Session(w, hal.DevButtons(script), None,
                              animate=False, on_device=False)
    try:
        run(sess)
    except hal.ScriptExhausted:
        pass
    if len(w.flags) < want_at_least:
        bad(f"{what}: painted {len(w.flags)} frames, expected at least "
            f"{want_at_least}. The check did not reach the screen it names.")
    elif not all(w.flags):
        bad(f"{what}: {w.flags.count(False)} of {len(w.flags)} frames were "
            "NOT marked sensitive, so DevDisplay wrote key material to disk")
    else:
        ok(f"{what}: all {len(w.flags)} frames marked sensitive")


# The real caller passes no secret=, so neither does this.
flags_for("typing a key on the grid", "a" * 30,
          lambda s: s._text_entry("MASTER  PRIVATE  KEY", "xprv"), 20)

flags_for("the paper backup pages", "aaa",
          lambda s: s._show_backup(KEY, LABEL), 3)

# A full page typed, then committed, so every frame of a real page is
# counted. Without the commit the script never leaves the entry screen.
_a_page = check_keys(PAGES[0])
flags_for("the check entry", _a_page,
          lambda s: s._check_typed(LABEL, PAGES[0]),
          len(_a_page) - 5)


# The camera viewfinder is a screen that can show a key too, because a key
# scan points a lens at one. It was NOT marked until audit A2.
#
# Only the VIEWFINDER frames are checked, not every frame the flow paints.
# The warning screen and the "importing into Core" screen carry no key and
# are correctly unmarked; demanding otherwise was this check's first
# mistake. screens.scanning is tagged so its frames can be told apart.

class OneFrameQr:
    last_image = "a photograph of a key QR"

    def strings(self):
        yield None
        yield "tprv8ZgxMBicQKsPe5YMU9gHen4Ez3ApihUfykaqUorj9t6FDqy"


class ViewfinderWatcher:
    width, height = 320, 240

    def __init__(self):
        self.viewfinder_flags = []

    def show(self, image, sensitive=False):
        if getattr(image, "_is_viewfinder", False):
            self.viewfinder_flags.append(sensitive)


watcher = ViewfinderWatcher()
sess = coresigner_main.Session(watcher, hal.DevButtons("a" * 6), None,
                          animate=False, on_device=False)
sess.qr = OneFrameQr()
real_scanning = screens.scanning


def _tagged_scanning(w, h, *a, **k):
    img = real_scanning(w, h, None, "x", 0.0)
    img._is_viewfinder = True
    return img


screens.scanning = _tagged_scanning
try:
    sess._keymaterial("key")          # the REAL caller, not _scan_until
except Exception:                                  # noqa: BLE001
    pass
finally:
    screens.scanning = real_scanning

if not watcher.viewfinder_flags:
    bad("the key viewfinder: painted nothing, so this proves nothing")
elif not all(watcher.viewfinder_flags):
    bad(f"the key viewfinder: {watcher.viewfinder_flags.count(False)} of "
        f"{len(watcher.viewfinder_flags)} frames unmarked. Scanning a key "
        "writes a photograph of it to disk.")
else:
    ok(f"the key viewfinder: all {len(watcher.viewfinder_flags)} frames "
       "marked sensitive")

# ---- Core's verdict reaches the screen ---------------------------------
# The pages already matched character for character by the time this runs,
# so the UI cannot produce a disagreement and no end-to-end session can
# reach the refusal. The wiring is still worth pinning: a guard whose
# false branch is unreachable through the UI is exactly the kind that
# rots. Audit A6 (2026-09-06) deleted the whole comparison and every suite
# stayed green.
#
# signer.opens_wallet has its own real check against a real node in
# session K9. What is under test HERE is only that its answer changes what
# the panel says.
real_opens = signer.opens_wallet
# The note is part of the claim (map correction C2, two-axis review
# 2026-09-23): 16 characters are prefilled, so boxes 1 to 4 are never
# compared against the paper and the screen says so.
for verdict, want_text, why in (
        (True, "Your paper opens\nkey 73C5DA0A", "agrees"),
        (False, None, "refuses")):
    sess = session("a")
    signer.opens_wallet = lambda *a, _v=verdict, **k: _v
    try:
        got = sess._confirm_typed_key(KEY, "coresigner-73c5da0a", "73c5da0a")
    except hal.ScriptExhausted:
        got = "ran out of presses"
    finally:
        signer.opens_wallet = real_opens
    if got is not (verdict is True):
        bad(f"Core {why}, but _confirm_typed_key returned {got!r}")
    elif verdict and not drew(sess, screens.verified(
            320, 240, want_text,
            note="Boxes 1 to 4 start every key, so\n"
                 "nobody types them. Check those by eye.")):
        bad("Core agreed and the panel never said the paper opens the key")
    elif not verdict and not drew(sess, screens.result(
            320, 240, ok=False, detail="That key does not open this wallet")):
        bad("Core refused and the panel did not say so")
    else:
        ok(f"Core {why} and the panel says so")

# --- 6f. corrections replace the "your paper opens" screen -------------
# Map correction C2. With something corrected, that sentence is no
# longer a thing the device knows: the paper only opens the wallet if
# the person went back and wrote the corrections down, which nothing on
# the device can see.

# (box, character, what it should be). The list says what to WRITE,
# not only where to look (Ben, 2026-09-23).
MADE = [(7, 3, "C"), (14, 1, "8"), (22, 4, "w")]
signer.opens_wallet = lambda *a, **k: True
try:
    # LEFT then A: EXIT. RECHECK is the pre-selected button, for the
    # reason CHECK IT is on the backup page (Ben, 2026-09-23), so a
    # bare A starts typing the corrected boxes back.
    sess = session("la")
    got = sess._confirm_typed_key(KEY, "coresigner-73c5da0a", "73c5da0a",
                                  MADE)
finally:
    signer.opens_wallet = real_opens

if not got:
    bad("a key Core agreed with was refused because it had corrections")
elif drew(sess, screens.verified(320, 240,
                                 "Your paper opens\nkey 73C5DA0A")):
    bad("the panel claimed the paper opens the key after correcting 3 "
        "characters of it, which it cannot know")
elif not drew(sess, screens.corrections(320, 240, MADE, "73c5da0a")):
    bad("the corrections screen was never drawn")
else:
    ok("3 corrections replace the claim with the count and the warning")

# --- 6g. RECHECK asks for the corrected BOXES, off the paper -----------
# Ben, 2026-09-23, reading the summary: "It needs to be a recheck button
# and exit button, not just done." And on scope: the boxes, not the whole
# key and not the bare characters. Three corrections is three boxes and
# twelve characters, against 111, and the box is what the message named
# and what a person reads off their paper.

RECHECK_MADE = [(2, 1, KEY[4]), (5, 2, KEY[17])]
asked = []
_real_entry_fn = coresigner_main.Session._check_entry


def _watch_entry(self, label, want, typed, box_numbers=None, ask=None):
    asked.append((label, typed, box_numbers, sorted(ask or ())))
    return want, []                      # typed back clean


coresigner_main.Session._check_entry = _watch_entry
signer.opens_wallet = lambda *a, **k: True
try:
    sess = session("a" + "a")            # RECHECK, then dismiss the verdict
    got = sess._confirm_typed_key(KEY, "coresigner-73c5da0a", "73c5da0a",
                                  RECHECK_MADE)
finally:
    coresigner_main.Session._check_entry = _real_entry_fn
    signer.opens_wallet = real_opens

# ONE screen, both boxes on it numbered as the paper numbers them (Ben,
# 2026-09-23), and only the WRONG characters blank, everything else
# already filled and out of reach (Ben, 2026-09-24).
_r_want = KEY[4:8] + KEY[16:20]
_r_ask = [0, 5]                          # box 2 char 1, box 5 char 2
_r_typed = "".join(" " if n in _r_ask else c for n, c in enumerate(_r_want))
want_asked = [("RECHECK  2  CHARACTERS", _r_typed, [2, 5], _r_ask)]
if asked != want_asked:
    bad(f"RECHECK asked for {asked}, not {want_asked}: the two boxes on "
        "one screen with only the wrong characters blank")
elif not got:
    bad("a recheck that came back clean did not accept the paper")
elif not drew(sess, screens.verified(
        320, 240, "Your paper opens\nkey 73C5DA0A",
        note="Rechecked, and boxes 1 to 4 start every\n"
             "key so nobody types them.")):
    bad("a clean recheck did not say the paper opens the key")
else:
    ok("RECHECK shows every corrected box on one screen with only the "
       "wrong characters blank, and a clean pass accepts it")

# --- 6h. the recheck cannot type over a character already right --------
# Ben, 2026-09-24: "no writing over values they already entered
# correctly". The caret walks the asked-for slots and nothing else, so a
# person cannot land on one of the three characters that were right all
# along and lose it.

_RW = "abcdefgh"
_RASK = {2, 5}
_RTYPED = "".join(" " if n in _RASK else c for n, c in enumerate(_RW))

_walked, _at = [], min(_RASK)
for _ in range(6):
    _walked.append(_at)
    _at = coresigner_main._step(_at, 1, sorted(_RASK), len(_RW))
_back, _at = [], max(_RASK)
for _ in range(6):
    _back.append(_at)
    _at = coresigner_main._step(_at, -1, sorted(_RASK), len(_RW))

if set(_walked) | set(_back) != _RASK:
    bad(f"the caret reached {sorted(set(_walked) | set(_back))}, not only "
        f"the asked-for slots {sorted(_RASK)}")
else:
    ok("the caret walks only the slots being asked for, both ways")

# B blanks the slot rather than closing the gap, because every other
# character in the string is one this person already got right.
sess = session("b")
_seen = []
_real_te2 = screens.text_entry
screens.text_entry = lambda *a, **k: (_seen.append(a[3])
                                      or _real_te2(*a, **k))
try:
    sess._check_entry(LABEL, _RW, _RW, ask={2})
except hal.ScriptExhausted:
    pass
finally:
    screens.text_entry = _real_te2

if _seen[-1] != "ab defgh":
    bad(f"B on an asked-for slot gave {_seen[-1]!r}, not 'ab defgh': it "
        "closed the gap and moved every character after it")
else:
    ok("B blanks the slot it is on and leaves the rest where they are")

# --- 6i. typing past the end of the key ---------------------------------
# Ben's note of 2026-09-25 read "1 correction Box 32, character 1" on a
# key that is 28 boxes. The caret rests one past the end so B can delete
# the last character (two-axis review, 2026-09-23), and a press there
# APPENDED: `typed` grew past `want` without limit, `_wrong_at` flagged
# the overflow, and `_fix_one` indexed `want` out of range. IndexError is
# not in Session.HANDLED, so that took the process down, on the screen
# where a key is loaded.

_OVER = "abcdefgh"
_over_presses, _om, _oc = grid_presses("xprv", "abcdefgx")
_more, _om2, _oc2 = grid_presses("xprv", "zzzzzzzzzzzz", _om, _oc)
_over_drawn = []
_real_te3 = screens.text_entry
screens.text_entry = lambda *a, **k: (_over_drawn.append(a[3])
                                      or _real_te3(*a, **k))
try:
    sess = session(_over_presses + _more)
    sess._check_entry(LABEL, _OVER, "")
except hal.ScriptExhausted:
    pass
except IndexError as exc:
    bad(f"typing past the end of the key crashed the device: {exc!r}. "
        "IndexError is not in Session.HANDLED, so this ends the session "
        "with a key loaded.")
finally:
    screens.text_entry = _real_te3

if _over_drawn and len(_over_drawn[-1]) != len(_OVER):
    bad(f"typing past the end grew the key to "
        f"{len(_over_drawn[-1])} characters, so a message could name a "
        f"box that does not exist")
elif _over_drawn:
    ok("typing past the end of the key is refused, not appended")

# And the set of wrong positions can never point outside the key, which
# is the trap _fix_one walked into.
if coresigner_main._wrong_at("abcdefghZZZ", _OVER) - set(range(len(_OVER))):
    bad("_wrong_at returns positions past the end of the key, which "
        "_fix_one then indexes")
else:
    ok("_wrong_at never points past the end of the key")

print()
print("FAILED %d" % len(fails) if fails else "ALL PASS")
sys.exit(1 if fails else 0)
