"""Several keys, on the device: scripted dev-HAL sessions for map
e2e-before-testers tickets 03 and 10. Run: python3 tests/e2e_keys.py
(needs bitcoind)."""
import io
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "coresigner"))
import signer  # noqa: E402
import main as coresigner_main  # noqa: E402
import screens as scr  # noqa: E402
import qrchannel  # noqa: E402

XPRV_A = "tprv8ZgxMBicQKsPe5YMU9gHen4Ez3ApihUfykaqUorj9t6FDqy3nP6eoXiAo2ssvpAjoLroQxHqr3R5nE3a5dU3DHTjTgJDd7zrbniJr6nrCzd"


def _cell_route(cells, start, target):
    """Shortest presses from one grid cell to another, found by search.

    The ROUTE is searched for and never written down, which is what
    TESTING.md rule 2 is protecting: a helper that encodes a route
    agrees with whatever produced it. The MOVES come from the device,
    because what one press does is behaviour and not the strip's shape.
    This held its own copy of them until 2026-09-18 and then walked
    routes the device would not walk, through a wrap it had dropped.

    What keeps _cell_move honest is tests/test_ui_cost.py, which
    asserts its properties directly: every cell moves on every press
    but one, and every cell reaches every other.
    """
    def moves(cur):
        for k in "udlr":
            yield k, coresigner_main._cell_move(k, cells, cur)

    seen, queue = {start: ""}, [start]
    while queue:
        cur = queue.pop(0)
        if cur == target:
            return seen[cur]
        for key, nxt in moves(cur):
            if nxt not in seen:
                seen[nxt] = seen[cur] + key
                queue.append(nxt)
    raise AssertionError(f"no route to cell {target}")


def _mode_of(charset, ch):
    """Which mode holds a character, and where in its strip."""
    for m, (_label, run) in enumerate(scr.modes(charset)):
        if ch in run:
            return m, scr.mode_cells(run).index(ch)
    raise AssertionError(f"{ch!r} is in no mode of {charset}")


def grid_presses(charset, want, mode=0, cur=0):
    """Presses that type `want` on the grid, without the closing press.

    `mode` and `cur` carry the cursor in from an earlier run, so a
    sequence broken by something else, a message that has to be
    dismissed, can be written as two calls.
    """
    runs = scr.modes(charset)
    out = []
    for ch in want:
        want_mode, target = _mode_of(charset, ch)
        while mode != want_mode:              # C cycles, one press each
            # _same_cell and not a copy of it. Where the cursor lands
            # after a toggle is the device's rule, and a script holding
            # its own version of that rule is TESTING.md rule 11's two
            # lists: the toggle changed on 2026-09-18 and this walked
            # the grid from a cell the device had already left.
            here = scr.mode_cells(runs[mode][1])
            mode = (mode + 1) % len(runs)
            cur = coresigner_main._same_cell(
                here, cur, scr.mode_cells(runs[mode][1]))
            out.append("c")
        cells = scr.mode_cells(runs[mode][1])
        out.append(_cell_route(cells, cur, target) + "a")
        cur = target
    return "".join(out), mode, cur


def commit_presses(charset, mode, cur):
    """Presses that leave the grid for the buttons and take the action.

    The centre press used to commit from anywhere. It does not: centre is
    SELECT on every screen (Ben, 2026-09-18), so finishing means reaching
    the buttons, which is what DOWN off the bottom does.

    EXACT, because the d-pad loops: one press too many comes off the bar
    and back to the top of the grid.
    """
    cells = scr.mode_cells(scr.modes(charset)[mode][1])
    downs = 0
    while coresigner_main._cell_move("d", cells, cur) != cur:
        cur = coresigner_main._cell_move("d", cells, cur)
        downs += 1
    return "d" * (downs + 1) + "a"


def text_keys(charset, want, page_full=False):
    """Presses that type `want` and then take the bar's action.

    `page_full` when the screen KNOWS how long the page is and `want`
    fills it, which only the backup check does. There the bar takes the
    focus on the last character, so the walk down to it is already done
    and one press finishes. Everywhere else the screen cannot know when
    you have finished, so the presses go down to the bar as before.
    """
    presses, mode, cur = grid_presses(charset, want)
    if page_full:
        return presses + "a"
    return presses + commit_presses(charset, mode, cur)


def check_keys(want, typed=None):
    """Presses that type a key into the CHECK screen, and commit.

    That screen opens with the 16-character master prefix already in
    place, because every Core master key on a network starts with the
    same one (signer.MASTER_PREFIXES). `want` is the key the device
    holds, which decides how much is prefilled; `typed` is what the
    person types, when the point is to get something wrong. The bar
    takes the focus on the last character, so one press commits.
    """
    import signer as _signer
    pre = next((p for p in _signer.MASTER_PREFIXES if want.startswith(p)), "")
    return text_keys("xprv", (want if typed is None else typed)[len(pre):],
                     page_full=True)


def entry_keys(xprv):
    """Presses that type a key into the MASTER PRIVATE KEY grid.

    That grid opens holding the 16 characters every Core master key on
    this network starts with (Ben, 2026-09-19), so only what follows is
    typed. The walk down to DONE is still needed: that screen cannot
    know how long a key is, so nothing moves the focus for you.
    """
    import signer as _signer
    pre = next((p for p in _signer.MASTER_PREFIXES if xprv.startswith(p)), "")
    return text_keys("xprv", xprv[len(pre):])


def home_press(tile, start=0):
    """Presses that pick a home tile, computed from the real 2x2 grid.

    HOME_TILES is laid out two to a row, and state_home wraps both axes.
    Counting these by hand is TESTING.md rule 11's defect in its other
    form: the script agrees with the code and neither agrees with the
    screen.
    """
    names = [label for label, _icon in scr.HOME_TILES]
    target = names.index(tile)
    row, col = divmod(target, 2)
    start_row, start_col = divmod(start, 2)
    return ("d" * ((row - start_row) % 2)
            + "r" * ((col - start_col) % 2) + "a")


def tools_press(action, start=0):
    """Presses that pick an action on the TOOLS screen."""
    names = [label for label, _note in scr.TOOLS_OPTIONS]
    return "d" * (names.index(action) - start) + "a"


def key_menu_press(action, start=0):
    """Presses that pick an action on one key's menu, from `start`.

    UP as well as DOWN. It multiplied "d" by a delta that could be
    negative, which is the empty string, so a move upward silently picked
    whatever row the cursor was already on. Nothing noticed until Backup
    key moved to the top on 2026-09-11 and the walk below had to go back
    up the list.
    """
    names = [label for label, _note, _kind in scr.KEY_MENU_OPTIONS]
    delta = names.index(action) - start
    return ("d" if delta > 0 else "u") * abs(delta) + "a"


def keys_press(n_keys, action, start=0):
    """Presses that pick `action` on the KEYS screen, computed from the
    real menu rather than counted by hand.

    The screen lists the loaded keys, then screens.KEYS_ACTIONS. Every time
    that list or the key count changed, hand-written "dda" strings across
    eight sessions went quietly wrong, which is TESTING.md rule 2: the
    helper must not repeat the code's assumptions, it must derive them.
    """
    names = [label for label, _note in scr.KEYS_ACTIONS]
    target = n_keys + names.index(action)
    return "d" * (target - start) + "a"


def run_device(datadir, script, frames, qr_key=None, qr_psbt=None,
               stick=None, card=None):
    cmd = [sys.executable, str(ROOT / "coresigner" / "main.py"), "--dev",
           f"--datadir={datadir}", "--chain=regtest", f"--script={script}",
           f"--frames-dir={frames}"]
    if stick:
        cmd.append(f"--stick-dir={stick}")
    if card:
        cmd.append(f"--card-dir={card}")
    if qr_key:
        cmd.append(f"--qr-key={qr_key}")
    if qr_psbt:
        cmd.append(f"--qr-psbt={qr_psbt}")
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300)


def _render(fn, *a, **k):
    b = io.BytesIO()
    fn(320, 240, *a, **k).save(b, format="PNG")
    return b.getvalue()


def _shots(d):
    """Frames in paint order. Typing an xprv paints more than 999 frames,
    and the dev display numbers them with three digits, so a text sort
    puts frame-1000 before frame-999."""
    return sorted(Path(d).glob("frame-*.png"),
                  key=lambda p: int(p.stem.split("-")[1]))


def _has(d, png):
    return any(p.read_bytes() == png for p in _shots(d))


def fresh_xprv(rpc):
    rpc.call("createwallet", "donor")
    text = rpc.call("listdescriptors", True, wallet="donor")["descriptors"][0]["desc"]
    key = text[text.rindex("(") + 1:]
    for stop in "/)":
        if stop in key:
            key = key[: key.index(stop)]
    rpc.call("unloadwallet", "donor")
    shutil.rmtree(rpc.wallet_dir / "donor", ignore_errors=True)
    return key


def main():
    datadir = tempfile.mkdtemp(prefix="coresigner-keys-")
    (Path(datadir) / "bitcoin.conf").write_text(
        "regtest=1\n[regtest]\nrpcport=%d\n" % random.randint(20000, 60000))
    work = Path(tempfile.mkdtemp(prefix="coresigner-keys-work-"))
    daemon = subprocess.Popen(
        ["bitcoind", "-regtest", f"-datadir={datadir}", "-listen=0",
         "-fallbackfee=0.0001", "-server=1", "-nodebuglogfile"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rpc = signer.Rpc(datadir, chain="regtest")
    try:
        for _ in range(60):
            try:
                rpc.call("getblockcount"); break
            except RuntimeError:
                time.sleep(0.5)
        # Two keys. A is the suite's key; B is born in Core. Their
        # fingerprints come from Core, never from a literal.
        xprv_b = fresh_xprv(rpc)
        name = signer.open_session_xprv(rpc, XPRV_A)
        xfp_a = signer.master_fingerprint(rpc, wallet=name)
        signer.close_session(rpc)
        name = signer.open_session_xprv(rpc, xprv_b)
        xfp_b = signer.master_fingerprint(rpc, wallet=name)
        pubs_b = signer.public_descriptors(rpc)
        signer.close_session(rpc)
        # A coordinator for B, funded, with one transaction ready.
        rpc.call("createwallet", "watchB", True, True, "", False, True)
        rpc.call("importdescriptors",
                 [{"desc": d, "active": True, "timestamp": "now",
                   "range": [0, 200], "internal": "/1/*" in d} for d in pubs_b],
                 wallet="watchB")
        addr_b = rpc.call("getnewaddress", wallet="watchB")
        rpc.call("generatetoaddress", 101, addr_b)
        psbt_b = rpc.call("walletcreatefundedpsbt", [],
                          [{rpc.call("getnewaddress", wallet="watchB"): 1.0}],
                          0, {"fee_rate": 10}, True, wallet="watchB")["psbt"]
        key_a = work / "key_a.txt"; key_a.write_text(XPRV_A)
        frames_b = work / "frames_b.txt"
        frames_b.write_text("\n".join(qrchannel.psbt_to_frames(psbt_b)))

        # ---- Session K1: the same key twice is refused, once loaded ----
        # The KEYS screen no longer skips itself when nothing is loaded, so
        # Load a key is a row you choose rather than a screen you land on.
        script1 = ("ra"                                  # Keys tile
                   + keys_press(0, "Scan a key")         # nothing loaded yet
                   + "a"         # accept the warning: the key loads
                   + "b"         # back to KEYS, which now lists it
                   + keys_press(1, "Scan a key")         # one key loaded now
                   + "a"         # accept the warning: refused, same key
                   + "a"         # dismiss the refusal
                   + "b"         # KEYS -> home
                   + "draa")     # settings -> power off
        r = run_device(datadir, script1, work / "framesK1", qr_key=key_a)
        assert r.returncode == 0, f"K1 rc={r.returncode}\n{r.stderr[-900:]}"
        assert _has(work / "framesK1", _render(
            scr.result, ok=False, detail=f"key {xfp_a} is already loaded")), \
            "K1: the duplicate refusal was never shown"
        assert _has(work / "framesK1", _render(scr.home, 0, xfp=xfp_a)), \
            "K1: home never showed the loaded key's fingerprint"
        print("ok   K1: loading the same key twice is refused by fingerprint")

        # ---- Session K2: two keys, and the transaction picks its key ----
        # load A by QR; the QR transaction belongs to B, nobody owns it,
        # dismiss; load B by typing its xprv; the same transaction now finds
        # B among two keys: the key screen appears with B pre-selected,
        # confirm, review, sign, QR out, home, power off. The result screen
        # stopped offering POWER OFF on 2026-09-24, so the walk to
        # Settings is part of every script that ends there now.
        script = ("ra" + keys_press(0, "Scan a key") + "a"   # Keys -> Scan -> warning
                  + "b" + "b"                     # key menu -> Keys -> home
                  + "a" + "a"                     # Sign tile: nobody owns it, dismiss
                  + "ra" + keys_press(1, "Type private key")
                  + entry_keys(xprv_b)
                  + "b" + "b"                     # key menu -> Keys -> home
                  + "a"                           # Sign tile
                  # Two review pages, destination and change, since the
                  # screen went to one output a page: confirm B, advance
                  # to the change, then sign.
                  + "a" + "aa" + "ra"            # confirm B, sign, go home
                  + "draa")                       # Settings -> power off
        r = run_device(datadir, script, work / "framesK2",
                       qr_key=key_a, qr_psbt=frames_b)
        assert r.returncode == 0, f"K2 failed:\n{r.stderr[-1500:]}"
        assert _has(work / "framesK2", _render(
            scr.result, ok=False, detail=f"no loaded key owns it; wants {xfp_b}")), \
            "K2: a transaction nobody owns was not refused by name"
        assert _has(work / "framesK2", _render(
            scr.choose_key, [("coresigner", xfp_a), ("coresigner-2", xfp_b)], {xfp_b}, 1)), \
            "K2: the key screen with B pre-selected was never shown"
        # SHOWN, not last. The PSBT leaves as QR frames so there is no
        # file to name and detail is None, and the walk to Power off is
        # drawn after the result since 2026-09-24.
        assert any(_has(work / "framesK2",
                        _render(scr.result, ok=True, detail=None,
                                actions_sel=sel, label="SIGNED",
                                note="ready to send"))
                   for sel in (0, 1)), "K2: the signed result never showed"
        print("ok   K2: two keys loaded; the transaction's owner is found and signs")

        # ---- Session K3: every menu, walked (tickets 02, 05, 07) ----
        # Home is Scan, Keys, Tools, Settings. The Scan tile reads whatever
        # is in front of it, and here that is an xprv, so it loads the key
        # and lands on its menu with no detour. Then Receiving addresses,
        # Backup key on paper, Discard key. Then Tools, which holds the leak
        # check alone. Then Keys, New key, which is the first row there now.
        #
        # BY LABEL, not by counted presses. This walk was written as "da"
        # three times, which was right only while Export was row 0, and
        # rule 11 is in TESTING.md because a scripted session that counts
        # presses proves the code agrees with itself. `key_menu_press`
        # reads the real menu; the second argument is where the cursor is
        # left, because the menu reopens on the row it last ran.
        script = ("ra" + keys_press(0, "Scan a key") + "a"  # Keys -> Scan a key -> warning
                  + key_menu_press("Receiving addresses", 0)
                  + "dda" + "b"                   # page on, back
                  + key_menu_press("Backup key", 2)
                  # Two pages on, then LEFT to DONE and take it. CHECK
                  # IT is the pre-selected button now (Ben, 2026-09-19),
                  # so a bare A on the last page starts the verify.
                  + "aa" + "la"
                  + key_menu_press("Discard key", 0)
                  + "ra"                          # confirm: DISCARD
                  + "da" + "a" + "c"              # Tools -> Check for leaks -> C leaves
                  + "b"                           # Tools -> home
                  + "ra" + keys_press(0, "New key")   # Keys -> New key, done
                  + key_menu_press("Backup key", 0)
                  + "aa" + "la"                   # 3 pages, then DONE
                  + "b" + "b"                     # key menu -> keys -> home
                  + "draa")
        r = run_device(datadir, script, work / "framesK3", qr_key=key_a)
        assert r.returncode == 0, f"K3 failed:\n{r.stderr[-1500:]}"
        fr = work / "framesK3"
        assert _has(fr, _render(scr.home, 0)), "K3: home without a key"
        assert _has(fr, _render(scr.key_menu, xfp_a, 0)), \
            "K3: scanning an xprv did not land on that key's menu"
        assert _has(fr, _render(scr.key_menu, xfp_a, 2)), "K3: Backup key highlighted"
        assert _has(fr, _render(scr.key_menu, xfp_a, 2)), \
            "K3: Backup key was never the selected row"
        # The dev display blanks every sensitive frame (hal.DevDisplay), so
        # the three xprv pages are three blank frames in a row, and the
        # backup page itself is pinned by test_screen_fit.
        from PIL import Image
        blank = io.BytesIO()
        Image.new("RGB", (320, 240), "#1A1714").save(blank, format="PNG")
        blanks = sum(1 for f in _shots(fr) if f.read_bytes() == blank.getvalue())
        assert blanks >= 3, f"K3: expected 3 blanked backup pages, saw {blanks}"
        # Both frames matter and they are different: BACK is pre-selected,
        # and DISCARD only happens once the user moves to it.
        assert _has(fr, _render(scr.confirm_discard, xfp_a, 0)), \
            "K3: discard did not ask first, with BACK pre-selected"
        assert _has(fr, _render(scr.confirm_discard, xfp_a, 1)), \
            "K3: DISCARD was never the selected action"
        assert _has(fr, _render(scr.tools_menu, 0)), \
            "K3: the Tools menu, which now holds only the leak check"
        assert _has(fr, _render(scr.keys_menu, [], 0)), \
            "K3: the KEYS screen offers New key first with nothing loaded"
        assert _has(fr, _render(scr.busy, "checking every way off this board…")), \
            "K3: the leak check never ran"
        assert _has(fr, _render(scr.key_menu, xfp_a, 1)), \
            "K3: Receiving addresses highlighted"
        # The address itself cannot be asserted here: this session ends
        # with the key discarded, so by the time these checks run there is
        # no wallet to ask what its first address was. The version of this
        # that tried was guarded by `if "coresigner" in listwallets`, which is
        # never true two lines above an assertion that no slot is loaded,
        # so it never ran at all (two-axis review, 2026-09-05). What CAN
        # be said is that the screen was reached with no chooser first.
        assert _has(fr, _render(scr.key_menu, xfp_a, 1)), \
            "K3: Receiving addresses was never the selected row"
        assert _has(fr, _render(scr.home, 2)), "K3: Tools tile highlighted"
        left = [w for w in rpc.call("listwallets") if w in signer.SLOTS]
        assert not left, f"K3: a key survived the session: {left}"
        print("ok   K3: Scan, Key, Tools, Settings; load, backup, discard, new key")

        # ---- Session K4: a key from a crashed session never reaches this
        # one. bitcoind and the ramdisk both outlive a UI restart, and
        # coresigner.service has Restart=on-failure, so this is what the board
        # does after a crash. Load a key OUTSIDE the device, then start the
        # device: it must clear it, say so, and show a home screen with no
        # fingerprint on it.
        signer.open_session_xprv(rpc, XPRV_A)
        assert signer.loaded_keys(rpc), "K4: setup failed to load a key"
        r = run_device(datadir, "a" + "draa", work / "framesK4")
        assert r.returncode == 0, f"K4 failed:\n{r.stderr[-1500:]}"
        assert not [w for w in rpc.call("listwallets") if w in signer.SLOTS], \
            "K4: a key from an earlier session survived into this one"
        fr4 = work / "framesK4"
        # ok=True: clearing an inherited key is the device working, not a
        # fault. It drew FAILED over that sentence until 2026-09-18.
        assert _has(fr4, _render(scr.result, ok=True, label="DONE",
                                 detail="Cleared 1 key(s) from an earlier session")), \
            "K4: the device did not say it had cleared an inherited key"
        assert _has(fr4, _render(scr.home, 0)), \
            "K4: home still showed a fingerprint after the clear"
        assert not _has(fr4, _render(scr.home, 0, xfp=xfp_a)), \
            "K4: the inherited key was still named on the home screen"
        print("ok   K4: a key left by a crashed session is cleared at startup")

        # ---- Session K5: export the public key (ticket 12) ----
        # The QR's CONTENT is proven against Sparrow's own zxing in
        # tests/sparrow/test_export_interop.py (rule 8). Here the question
        # is the device: does the panel show that exact code, the same
        # descriptor as text, and Core's real addresses in full.
        signer.close_session(rpc)
        name5 = signer.open_session_xprv(rpc, XPRV_A)
        desc = signer.export_descriptor(rpc, name5, "wpkh")
        want_addrs = signer.receive_addresses(rpc, name5, "wpkh", 3)
        signer.close_session(rpc)
        stick5 = work / "stick5"; stick5.mkdir()
        # Script type, then HOW it leaves, then the key, then the addresses
        # to compare it against (map D2, revised on the board 2026-09-05).
        # This session takes the file route, because that is the one that
        # writes something a check can find. A completed export leaves the
        # flow rather than dropping back on the script type.
        script = ("ra" + "da" + "a"                   # Keys -> Scan a key -> warning
                  + key_menu_press("Export public key", 0)
                  + "a"                               # SCRIPT TYPE -> Native segwit
                  # DERIVED. "dda" meant Wallet file until a second QR
                  # row was added above it, and then it meant Text to
                  # type: the file was never written and the assertion
                  # below is what finally said so.
                  + "d" * [lbl for lbl, _n in scr.EXPORT_OPTIONS].index(
                      "Wallet file") + "a"            # EXPORT AS -> Wallet file
                  + "a"                               # WHICH CHANNEL
                  + "a"                               # written; dismiss
                  # The dismissal draws the first address, so the walk is
                  # two more presses and not three. Traced, 2026-10-16.
                  + "a" * 2                           # addresses 2 and 3
                  # A finished export leaves you one level deeper than a
                  # plain key menu, so the walk out is three backs:
                  # address pages, key menu, Keys, home. Then Settings
                  # and Power off, since the result screen stopped
                  # offering it on 2026-09-24.
                  + "b" + "b" + "b" + "draa")
        r = run_device(datadir, script, work / "framesK5",
                       qr_key=key_a, stick=stick5)
        assert r.returncode == 0, (f"K5 failed rc={r.returncode}\n"
                                   f"STDERR:{r.stderr[-1200:]}\n"
                                   f"STDOUT:{r.stdout[-600:]}")
        fr5 = work / "framesK5"
        # The QR carries its policy name in the letterbox, so four codes
        # that look identical can be told apart (map ticket T0). The
        # caption is part of the shipped frame, so it is part of the
        # golden one.
        # The QR is not shown in this session, which takes the file route.
        # What the panel drew for it is proved against Sparrow's own zxing
        # in tests/sparrow/test_export_interop.py, which is the decoder
        # that matters (TESTING.md rule 8).
        xfp5, path5 = signer.origin_of(desc)
        assert xfp5 == xfp_a and path5.startswith("m/84h/"), \
            f"K5: Core wrote an unexpected origin: {xfp5} {path5}"
        # Every key presents all four since D6, and the wallet this session
        # used is already closed, so the list is EXPORT_ORDER rather than a
        # live lookup.
        # The menu carries a Multisig row under the four policies, so
        # the expected render has to be given what the device gives it.
        assert _has(fr5, _render(
            scr.script_menu, signer.EXPORT_ORDER, 0, multisig=True)), \
            "K5: the script type was not asked before the QR"
        assert _has(fr5, _render(scr.export_options, 0)), \
            "K5: the export destination was never asked"
        # The addresses come AFTER a successful export, because they are
        # what you compare against the coordinator that just read it.
        # total=3 because the walk after an export is bounded, and the
        # screen draws a different bar for a list with a known end (D3).
        for i, addr in enumerate(want_addrs):
            assert _has(fr5, _render(scr.address_page, i, addr, "wpkh",
                                     total=3)), \
                f"K5: address {i} was not shown after the export"
        written = list(stick5.glob("coresigner-*-watch.dat"))
        assert len(written) == 1, f"K5: watch-only file not written: {written}"
        assert _has(fr5, _render(scr.result, ok=True, label="DONE",
                                 detail=f"{written[0].name} written")), \
            "K5: the device did not say where the file went, under DONE"
        assert not _has(fr5, _render(scr.result, ok=True,
                                     detail=f"{written[0].name} written")), \
            "K5: writing a wallet file still draws SIGNED"
        assert xfp_a in written[0].name, \
            f"K5: the file is not named by fingerprint: {written[0].name}"
        print(f"ok   K5: export -> script type, destination, "
              f"{written[0].name}, then the addresses to compare")

        # ---- Session K7: a bad file on the stick must not kill the app ----
        # ISSUES D18. coresigner.service has Restart=on-failure, so an exception
        # here is not one bad screen, it is a restart loop that lasts as
        # long as the file is on the stick. A tester will hit this in the
        # first hour.
        signer.close_session(rpc)
        stick7 = work / "stick7"; stick7.mkdir()
        (stick7 / "junk.psbt").write_bytes(b"this is not a transaction" * 8)
        script = ("ra" + keys_press(0, "Scan a key") + "a"  # Keys -> Scan -> warning
                  + "b" + "b"           # key menu -> Keys -> home
                  + "a"                 # Sign tile -> the stick
                  + "a"                 # dismiss whatever it says
                  + "draa")
        r = run_device(datadir, script, work / "framesK7",
                       qr_key=key_a, stick=stick7)
        assert r.returncode == 0, (f"K7: a bad PSBT file crashed the device "
                                   f"rc={r.returncode}\n{r.stderr[-900:]}")
        assert not (stick7 / "junk-signed.psbt").exists(), \
            "K7: junk was signed"
        signer.close_session(rpc)
        print("ok   K7: a bad file on the stick is reported, not fatal")

        # ---- Session K8: an empty file is refused by the file channel ----
        stick8 = work / "stick8"; stick8.mkdir()
        (stick8 / "empty.psbt").write_bytes(b"")
        # The empty file is refused by name, and C leaves. Without the
        # message the device would ask for a stick that is already in it.
        # The dev keypad has no "no key pressed" state: every poll consumes
        # a script key. The spare "a" is the tick on which the loop repaints
        # with the reason, and C then leaves.
        script8 = ("ra" + keys_press(0, "Scan a key") + "a"
                   + "b" + "b"          # key menu -> Keys -> home
                   + "a"                # Sign tile -> the stick
                   + "a" + "c" + "draa")
        r = run_device(datadir, script8, work / "framesK8",
                       qr_key=key_a, stick=stick8)
        assert r.returncode == 0, (f"K8: an empty PSBT file crashed the "
                                   f"device rc={r.returncode}\n{r.stderr[-900:]}")
        assert _has(work / "framesK8", _render(
            scr.busy, "empty.psbt: 0 bytes, refusing")), \
            "K8: the empty file was never named on screen"
        signer.close_session(rpc)
        print("ok   K8: an empty file on the stick is named on screen, "
              "not waited on for ever")

        # ---- Session K9: VERIFY types the paper backup back in (Ben) ----
        # The last backup page has always offered VERIFY and always just
        # gone back to the menu. This is the flow it promises: type each
        # page back, get told exactly which characters are wrong, fix them
        # in place, and have Bitcoin Core confirm the whole key at the end.
        #
        # The key is typed with a deliberate error, so the screen has to
        # mark that position and the cursor has to land on it. Rule 1:
        # the key is a real one Core opens and signs with, and every
        # press is computed from the navigation rules, never counted.
        #
        # PAST THE PREFIX. The first 16 characters are already on the
        # screen when it opens, so a mistake there cannot be made and a
        # check that puts one there is checking nothing.
        signer.close_session(rpc)
        pages9 = scr.text_pages(XPRV_A)
        assert len(pages9) == 3, f"K9: expected 3 backup pages, got {len(pages9)}"
        wrong_at = len(signer.TESTNET_PREFIX) + 4
        right = XPRV_A[wrong_at]
        typo = "2" if right != "2" else "3"
        assert typo in scr.BASE58, "K9: the substitute is not a base58 character"
        # NO VERDICT SCREEN when something is wrong (map typing, T4).
        #
        # A WRONG CHARACTER CANNOT PASS (map correction, C1). It goes
        # red where it sits, and the moment another character is added
        # the screen stops and shows both. That press is spent on the
        # message, one more dismisses it, the device puts the right
        # character in, and typing carries on. Three segments, because
        # that is three things a person does.
        #
        # THE WHOLE KEY IN ONE GO. It was checked one 48-character page
        # at a time and a page would not let you out until it was
        # perfect, so a mistake at character 6 meant characters 49 to
        # 111 could not be typed at all (Ben, on the board, three times,
        # ending 2026-09-19).
        pre9 = len(signer.TESTNET_PREFIX)            # already on screen
        typed9, m9, c9 = grid_presses(
            "xprv", XPRV_A[pre9:wrong_at] + typo)
        trip9, m9b, c9b = grid_presses(
            "xprv", XPRV_A[wrong_at + 1], m9, c9)    # stopped by the message
        rest9, m9c, c9c = grid_presses(
            "xprv", XPRV_A[wrong_at + 1:], m9b, c9b)
        script = ("ra" + keys_press(0, "Scan a key") + "a"   # Keys -> Scan
                  + key_menu_press("Backup key", 0)  # paper, no chooser
                  + "aa" + "a"                   # 3 pages, then CHECK IT
                  + typed9 + trip9               # up to the mistake, then on
                  + "a"                          # dismiss the message
                  + rest9                        # the rest of the key
                  + "a"                          # DONE, the bar has focus
                  + "la"                         # corrections: LEFT to EXIT
                  # A finished export leaves you one level deeper than a
                  # plain key menu, so the walk out is three backs:
                  # address pages, key menu, Keys, home. Then Settings
                  # and Power off, since the result screen stopped
                  # offering it on 2026-09-24.
                  + "b" + "b" + "b" + "draa")
        r = run_device(datadir, script, work / "framesK9", qr_key=key_a)
        assert r.returncode == 0, f"K9 failed:\n{r.stderr[-1500:]}"
        fr9 = work / "framesK9"
        # Every screen in the check shows key material, so hal.DevDisplay
        # blanks those frames: what the verdict SAID is asserted in
        # tests/test_backup_check.py, where the screens are rendered
        # directly. What this session proves is the part only a real
        # device and a real node can: the flow runs end to end on Core's
        # own key and Core agrees at the end.
        # ONE correction was made, so the flow does NOT end on "your
        # paper opens key X": that is no longer a thing the device knows
        # (map correction, C2). It ends on the count, the fingerprint
        # Core confirmed, and the warning.
        # The list carries what to WRITE and not only where to look
        # (Ben, 2026-09-23), so the correction is (box, character, the
        # character it should be).
        box9, char9 = wrong_at // 4 + 1, wrong_at % 4 + 1
        assert _has(fr9, _render(scr.corrections,
                                 [(box9, char9, XPRV_A[wrong_at])], xfp_a)), \
            "K9: the corrections screen never reached the panel"
        assert not _has(fr9, _render(
            scr.verified, f"Your paper opens\nkey {xfp_a.upper()}")), \
            ("K9: the panel claimed the paper opens the key after "
             "correcting a character of it, which it cannot know")
        # And the check can actually FAIL, or the assertion above proves
        # nothing. Audit A6 found the old comparison could not: the pages
        # had already matched character for character, so Core was asked
        # whether a string equalled itself. It now asks whether the typed
        # key derives the addresses this wallet hands out.
        name9 = signer.open_session_xprv(rpc, XPRV_A)
        other = signer.master_xprv(rpc, wallet=signer.generate_wallet(rpc))
        assert signer.opens_wallet(rpc, name9, XPRV_A), \
            "K9: the wallet's own key was judged not to open it"
        assert not signer.opens_wallet(rpc, name9, other), \
            "K9: a different key was judged to open this wallet"
        try:
            signer.opens_wallet(
                rpc, name9,
                XPRV_A[:wrong_at] + typo + XPRV_A[wrong_at + 1:])
            raise AssertionError("K9: Core accepted a mistyped key")
        except RuntimeError as exc:
            assert XPRV_A[:20] not in str(exc) and typo not in str(exc)[:8], \
                f"K9: the refusal leaked key material: {exc}"
        # K9b: a wallet with no recognised policy. opens_wallet took
        # available_kinds(...)[0] without guarding the empty tuple, and
        # IndexError is not in Session.HANDLED, so the backup-check
        # screen ended the process instead of saying it could not check
        # (found reading signer.py, 2026-09-08).
        bare = signer.open_session_descriptors(
            rpc, [f"pk({XPRV_A}/0/*)"])
        try:
            signer.opens_wallet(rpc, bare, XPRV_A)
        except RuntimeError as exc:
            assert "no addresses" in str(exc), \
                f"K9b: refused, but with a message the panel cannot use: {exc}"
            print("ok   K9b: a key with no recognised policy is refused "
                  "with a message, not an IndexError")
        except IndexError:
            raise AssertionError(
                "K9b: opens_wallet raised IndexError on a wallet with no "
                "recognised policy; HANDLED cannot catch it, so the "
                "process ends on the backup-check screen") from None
        else:
            raise AssertionError(
                "K9b: opens_wallet answered for a wallet that derives no "
                "addresses it knows how to check")
        signer._drop_wallet(rpc, bare)

        signer.close_session(rpc)
        print("ok   K9: VERIFY types the backup back, names the wrong "
              "character, and Core confirms the key")

        # ---- Session K10: a key survives being walked away from --------
        # Map ticket N3, in Ben's words: keys "need to persist unless
        # turned off essentially". tests/test_key_persistence.py proves
        # nothing drops one on a clock, and walks the menus against a
        # stub. What it could not do is the half the ticket actually asks
        # for: that the key which comes back is "still able to sign".
        #
        # So: load a key, go somewhere else entirely, come back, and sign
        # a real transaction that only that key owns. If anything expired
        # it in between, the signature does not complete.
        # watchB already holds A's public descriptors and mined the chain,
        # so it can both fund an address of A's and take the change back.
        signer.close_session(rpc)
        name10 = signer.open_session_xprv(rpc, XPRV_A)
        pubs10 = signer.public_descriptors(rpc, wallet=name10)
        rpc.call("createwallet", "watch10", True, True, "", False, True)
        rpc.call("importdescriptors",
                 [{"desc": d, "active": True, "timestamp": "now",
                   "range": [0, 200], "internal": "/1/*" in d}
                  for d in pubs10], wallet="watch10")
        # watchB is watch-only and cannot spend, so K10 mines its own
        # coins into a wallet that holds keys.
        rpc.call("createwallet", "miner10")
        mine10 = rpc.call("getnewaddress", wallet="miner10")
        rpc.call("generatetoaddress", 101, mine10, wallet="miner10")
        fund10 = rpc.call("getnewaddress", wallet="watch10")
        rpc.call("sendtoaddress", fund10, 1.0, wallet="miner10")
        rpc.call("generatetoaddress", 1, mine10, wallet="miner10")
        back10 = rpc.call("getnewaddress", wallet="miner10")
        psbt10 = rpc.call("walletcreatefundedpsbt", [],
                          [{back10: 0.4}], 0, {"fee_rate": 5}, True,
                          wallet="watch10")["psbt"]
        frames10 = work / "psbt10.txt"
        frames10.write_text("\n".join(qrchannel.psbt_to_frames(psbt10)))
        signer.close_session(rpc)

        script = ("ra" + keys_press(0, "Scan a key") + "a"   # Keys -> Scan
                  + "b" + "b"                     # key menu -> Keys -> home
                  # walk away: Tools, the leak check, out again
                  + "da" + "a" + "c" + "b"
                  # and Settings, About, out again
                  + "dra" + "da" + "a" + "b"
                  # back to the key, through its whole menu, and out
                  + "ra" + "a" + "b" + "b"
                  # now sign, with the key that has been sitting there:
                  # two review pages, destination and change, then home
                  # and out through Settings
                  + "a" + "aa" + "ra" + "draa")
        r = run_device(datadir, script, work / "framesK10",
                       qr_key=key_a, qr_psbt=frames10)
        assert r.returncode == 0, f"K10 failed:\n{r.stderr[-1500:]}"
        # SHOWN, not last: detail is None when the PSBT leaves as QR
        # frames, and the walk to Power off draws after the result.
        assert any(_has(work / "framesK10",
                        _render(scr.result, ok=True, detail=None,
                                actions_sel=sel, label="SIGNED",
                                note="ready to send"))
                   for sel in (0, 1)), \
            "K10: the key did not sign after being walked away from"
        left10 = [w for w in rpc.call("listwallets") if w in signer.SLOTS]
        assert not left10, f"K10: a key survived power off: {left10}"
        print("ok   K10: a key walked away from and returned to still signs, "
              "and is gone at power off")

        # ---- Session K11: Check an address, which had no test at all ----
        # Audit A5 measured it: the whole Tools feature was uncovered, 27
        # statements including the branch that says nobody owns it. It
        # answers the one question a coordinator cannot answer for you,
        # which is whether the address on that other screen belongs to a
        # key in your hand, so getting it wrong is how someone pays a
        # stranger.
        signer.close_session(rpc)
        name11 = signer.open_session_xprv(rpc, XPRV_A)
        mine11 = signer.receive_addresses(rpc, name11, "wpkh", 1)[0]
        not_mine11 = rpc.call("getnewaddress", wallet="watchB")
        signer.close_session(rpc)

        for label, addr, want in (
                ("an address the key owns", mine11, "owned"),
                ("an address it does not", not_mine11, "not owned")):
            # Two codes, in the order the camera sees them: the key to
            # load, then the address to check. One file with one code
            # could not do this, so the first version of K11 scanned the
            # ADDRESS as the key, loaded nothing, and never reached the
            # verdict screen (devil's advocate on A5, 2026-09-06).
            qr11 = work / f"addr-{want.replace(' ', '')}.txt"
            qr11.write_text(f"{XPRV_A}\n{addr}\n")
            script11 = ("ra" + keys_press(0, "Scan a key") + "a"  # load A
                        + "b" + "b"                  # key menu -> Keys -> home
                        + home_press("tools")
                        + tools_press("Check an address")
                        + "a"                        # dismiss the verdict
                        + "b" + "draa")
            r11 = run_device(datadir, script11, work / f"framesK11-{want}",
                             qr_key=qr11)
            assert r11.returncode == 0, \
                f"K11 ({label}) failed:\n{r11.stderr[-900:]}"
            fr11 = work / f"framesK11-{want}"
            owned = _has(fr11, _render(
                scr.verified, f"key {xfp_a.upper()}\nowns this address"))
            refused = _has(fr11, _render(
                scr.result, ok=False, label="FAILED",
                detail=f"Not in the first {coresigner_main.ADDRESS_CHECK_DEPTH} "
                       f"addresses of any loaded key"))
            if want == "owned":
                assert owned and not refused, \
                    f"K11: {label} was not recognised as owned"
            else:
                assert refused and not owned, (
                    f"K11: {label} was claimed as owned, which is how "
                    "somebody pays a stranger")
        signer.close_session(rpc)
        print("ok   K11: Check an address says which key owns one, and "
              "refuses one no key owns")

        # ---- Session K12: walk every policy on the address screen ----
        # Audit A5 measured _next_kind as never called by anything: the
        # LEFT/RIGHT walk that reaches a legacy or nested address is the
        # whole reason the chooser was taken off the front of this screen
        # (Ben, 2026-09-05), and no test had ever pressed either key. A
        # wrong step here shows the operator an address from a policy the
        # screen does not name, which is how a receive goes to a script
        # the coordinator is not watching.
        name12 = signer.open_session_xprv(rpc, XPRV_A)
        order12 = signer.available_kinds(rpc, name12)
        first12 = {k: signer.receive_addresses(rpc, name12, k, 1)[0]
                   for k in order12}
        signer.close_session(rpc)
        # RIGHT once per policy walks the whole ring and comes home; the
        # final LEFT proves the walk goes both ways.
        script12 = ("ra" + keys_press(0, "Scan a key") + "a"
                    # loading a key lands on that key's own menu
                    + key_menu_press("Receiving addresses")
                    + "r" * len(order12)        # all the way round
                    + "l"                       # and one step back
                    + "b" + "b" + "b" + "draa")
        r12 = run_device(datadir, script12, work / "framesK12", qr_key=key_a)
        assert r12.returncode == 0, f"K12 failed:\n{r12.stderr[-900:]}"
        # Every policy's own first address must have been painted, each
        # under its own title. Rule 11: assert what the screen says, not
        # the number of steps taken to get there.
        missing12 = [k for k in order12
                     if not _has(work / "framesK12",
                                 _render(scr.address_page, 0, first12[k], k,
                                         switchable=True))]
        assert not missing12, (
            f"K12: LEFT/RIGHT never showed these policies: {missing12}; "
            f"the walk covers {order12}")
        signer.close_session(rpc)
        print(f"ok   K12: LEFT/RIGHT walks all {len(order12)} policies on "
              "the address screen and returns to the first")

        # ---- Session K13: two media, and the file goes where you said ----
        # `run_device` has taken a `card` argument since it was written and
        # no session had ever passed one, so the chooser had only ever been
        # asked with a single row (audit A6, 2026-09-06). One row is the
        # case where the answer cannot be wrong. The screen "is what tells
        # you where the file went, and that is the decision" (ticket 04),
        # and nothing had checked that the file agrees with the screen.
        signer.close_session(rpc)
        stick13 = work / "stick13"; stick13.mkdir()
        card13 = work / "card13"; card13.mkdir()
        script13 = ("ra" + keys_press(0, "Scan a key") + "a"
                    + key_menu_press("Export public key")
                    + "a"                        # SCRIPT TYPE -> native segwit
                    # DERIVED, for the same reason K5's is: a second QR
                    # row moved this one down.
                    + "d" * [lbl for lbl, _n in scr.EXPORT_OPTIONS].index(
                        "Wallet file") + "a"     # EXPORT AS -> Wallet file
                    + "da"                       # CHANNEL -> the second row
                    + "a"                        # dismiss
                    + "a" * 3                    # the three addresses
                    + "b" + "b" + "b" + "draa")
        r13 = run_device(datadir, script13, work / "framesK13",
                         qr_key=key_a, stick=stick13, card=card13)
        assert r13.returncode == 0, f"K13 failed:\n{r13.stderr[-900:]}"
        # The chooser must have OFFERED both, named as the code names them.
        assert _has(work / "framesK13",
                    _render(scr.choose_channel, ["stick", "card"], 1)), \
            "K13: the chooser did not offer both media with the card second"
        on_card = list(card13.glob("*.dat"))
        on_stick = list(stick13.glob("*.dat"))
        assert on_card and not on_stick, (
            f"K13: the card row was chosen, but the file landed on "
            f"{'both' if on_card and on_stick else 'the stick'}: "
            f"card={[p.name for p in on_card]} "
            f"stick={[p.name for p in on_stick]}")
        signer.close_session(rpc)
        print(f"ok   K13: with a stick AND a card, the file lands on the one "
              f"chosen ({on_card[0].name} on the card)")
        print("ALL PASS")
    finally:
        try:
            rpc.call("stop")
        except Exception:
            pass
        daemon.wait(timeout=30)
        if os.environ.get("KEEP"):
            print("kept:", work)
        else:
            shutil.rmtree(datadir, ignore_errors=True)
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
