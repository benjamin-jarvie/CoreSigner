"""Every string on every screen must land inside the panel.

The device has 320x240 pixels and no scrollbar: a string drawn past the edge
is simply not there, and the user is asked to write down a backup whose last
third never rendered. This suite instruments ImageDraw.text, renders every
screen at both v1 resolutions with worst-case content, and fails on any
bounding box outside the canvas.

Run: python3 tests/test_screen_fit.py
"""
import ast
import base64
import inspect
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "coresigner"))
from PIL import Image, ImageColor, ImageDraw  # noqa: E402
import main as coresigner_main  # noqa: E402
import screens  # noqa: E402
import qrchannel  # noqa: E402

fails = []


def ok(m):
    print("ok  ", m)


def bad(m):
    fails.append(m)
    print("FAIL", m)


_orig_text = ImageDraw.ImageDraw.text
_ctx = {"w": 0, "h": 0, "name": "", "over": [], "drawn": []}


def _measured_text(self, xy, text, *a, **kw):
    box = self.textbbox(xy, text, font=kw.get("font"),
                        anchor=kw.get("anchor", "la"))
    w, h = _ctx["w"], _ctx["h"]
    if box[0] < 0 or box[1] < 0 or box[2] > w or box[3] > h:
        _ctx["over"].append((text, [int(v) for v in box]))
    _ctx["drawn"].append((str(text), tuple(int(v) for v in box)))
    return _orig_text(self, xy, text, *a, **kw)


def collisions(drawn):
    """Pairs of DIFFERENT strings whose boxes intersect.

    Escaping the panel was checked; two strings painted on top of each
    other inside it was not. The review screen drew its address and its
    amount with a bare d.text, neither bounded by the other, and on the
    240x240 pocket panel they overlapped from about 100,000 BTC: two
    unreadable strings on the screen the user signs from (found reading
    screens.py, 2026-09-08).

    Two exclusions, both real rather than convenient:

      the same string twice is `_fit(bold=True)`, which draws a word a
      second time one pixel right to thicken its stems;

      two SINGLE characters are `_tracked`, which draws letter by letter
      and lets adjacent glyph boxes share a pixel of bearing.

    A single character against a longer string is still a collision, so
    neither exclusion opens a hole.
    """
    def hits(a, b):
        return not (a[2] <= b[0] or b[2] <= a[0]
                    or a[3] <= b[1] or b[3] <= a[1])

    return [(x, y) for i, x in enumerate(drawn) for y in drawn[i + 1:]
            if x[0] != y[0] and len(x[0]) + len(y[0]) > 2 and hits(x[1], y[1])]


ImageDraw.ImageDraw.text = _measured_text

# Worst case in every field the flows can actually produce.
XPRV = ("xprv9s21ZrQH143K3QTDL4LXw2F7HEK3wJUD2nW2nRk4stbPy6cq3jPPqjiChkVvv"
        "NKmPGJxWUtg6LnF5kejMRNNU3TGtRBeJgk33yuGBxrMPHi")          # 111 chars
ADDR = "bc1p5cyxnuxmeuwuvkwfem96lqzszd02n6xdcjrs20cac6yqjjwudpxqkedrcr"
OUTPUTS = [(ADDR, 0.03444556), ("bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu",
                                21.21212121), (ADDR, 0.1), (ADDR, 0.2)]

COSIGNERS = [("a1b2c3d4", "m/48h/0h/0h/2h"), ("e5f6a7b8", "m/48h/0h/0h/2h"),
             ("09c1d2e3", "m/48h/0h/0h/2h")]
# A blinded path, which is what M4 proved Core signs on: three hardened
# levels of 31 bits each, so the longest path this device can be told.
BLINDED = [(f"{i:08x}", "m/607137099h/1711870460h/1965312408h")
           for i in (0x4513369e, 0x73c5da0a, 0xbeb4fc29)]
# A quorum too wide to name key by key. P2WSH allows up to 20 pubkeys in
# a CHECKMULTISIG, and 20 eight-character fingerprints do not fit on 240
# pixels at any size a person can read.
WIDE = [(f"{i:08x}", "m/48h/0h/0h/2h") for i in range(0x10000000, 0x10000014)]

CASES = {
    "splash": lambda w, h: screens.splash(w, h),
    "home": lambda w, h: screens.home(w, h, 1),
    "review-1page": lambda w, h: screens.review(w, h, OUTPUTS[:2], 0.0000851,
                                                input_total_btc=21.3),
    "review-paged": lambda w, h: screens.review(w, h, OUTPUTS, 0.0000851,
                                                input_total_btc=21.3, page=1),
    "review-refused": lambda w, h: screens.review(w, h, OUTPUTS, 0.0000851,
                                                  input_total_btc=21.3,
                                                  unseen_pages=True),
    # The widest string the format can produce. 21,000,000.00000000 is
    # every bitcoin there will ever be, so no real transaction is wider,
    # and the pocket panel had the address and the amount overlapping from
    # about a twentieth of that (2026-09-08). The fixture above tops out
    # at 21.21212121, which is why nothing saw it.
    "review-whole-supply": lambda w, h: screens.review(
        w, h, [(ADDR, Decimal("20999999.99999999")),
               (ADDR, Decimal("21000000"))],
        Decimal("20999999.99999999"),
        input_total_btc=Decimal("21000000")),
    # The multisig review (map M9). Three eight-character fingerprints
    # and a path on the screen you sign from is the most crowded this
    # screen gets, and the worst case is all of it at once: the widest
    # amount, a paged transaction, unseen pages, and a blinded path,
    # which is longer than any BIP48 one and is the case Core Signer can sign
    # that most vendors cannot.
    "review-quorum": lambda w, h: screens.review(
        w, h, OUTPUTS[:2], Decimal("20999999.99999999"),
        input_total_btc=Decimal("21000000"),
        quorum=(2, 3), cosigners=COSIGNERS, ours=COSIGNERS[1][0]),
    "review-quorum-paged": lambda w, h: screens.review(
        w, h, OUTPUTS, 0.0000851, input_total_btc=21.3, page=1,
        unseen_pages=True, quorum=(2, 3), cosigners=BLINDED,
        ours=BLINDED[0][0]),
    "review-quorum-wide": lambda w, h: screens.review(
        w, h, OUTPUTS[:2], 0.0000851, input_total_btc=21.3,
        quorum=(11, 15), cosigners=WIDE, ours=WIDE[7][0]),
    "review-mixed": lambda w, h: screens.review(
        w, h, OUTPUTS[:2], 0.0000851, input_total_btc=21.3,
        quorum="mixed", cosigners=COSIGNERS, ours=None),
    "review-solo-path": lambda w, h: screens.review(
        w, h, OUTPUTS[:2], 0.0000851, input_total_btc=21.3,
        cosigners=[("a1b2c3d4", "m/84h/0h/0h")], ours="a1b2c3d4"),
    # A decaying quorum (map M6). Core types a miniscript witness script
    # as nonstandard, so there is no threshold to show and the TIER goes
    # on that line instead. The worst case is the longest wording with
    # the longest path under it.
    "review-decay": lambda w, h: screens.review(
        w, h, OUTPUTS[:2], 0.0000851, input_total_btc=21.3,
        cosigners=BLINDED, ours=BLINDED[0][0],
        timelocks=(10, 20), spend_lock=20),
    "review-timelocked": lambda w, h: screens.review(
        w, h, OUTPUTS[:2], 0.0000851, input_total_btc=21.3,
        cosigners=WIDE, ours=WIDE[3][0], timelocks=(4194303,)),
    # The two outcomes of signing. Both are signed and only one of them
    # can move the money, which is the whole point of the note line.
    "result-share": lambda w, h: screens.result(
        w, h, ok=True, label="SHARE", note="needs another signature",
        detail="coresigner-73c5da0a-signed.psbt written", actions_sel=0),
    "result-complete": lambda w, h: screens.result(
        w, h, ok=True, label="SIGNED", note="ready to send",
        detail="shown as 12 QR frames", actions_sel=1),
    # Six screens had no case at all until 2026-09-08, so neither the fit
    # check nor the collision check had ever rendered them. The guard
    # below fails if a seventh appears.
    "about": lambda w, h: screens.about(w, h),
    # These two were in MENUS below, which checks menu GEOMETRY and does
    # not run the fit or collision checks. Rendered for one purpose and
    # never for the other, which reads as covered and is not.
    "settings-menu": lambda w, h: screens.settings_menu(w, h, 1),
    "channel-menu": lambda w, h: screens.channel_menu(w, h, 1),
    "scanning": lambda w, h: screens.scanning(
        w, h, None, "hold the QR in view", 0.42),
    "scanning-advisory": lambda w, h: screens.scanning(
        w, h, None, "large frames: set Sparrow to Low density", 0.0),
    # The outbound PSBT frame, on the same card as the export since
    # 2026-09-08. Sized against the card's budget, which is what the
    # device does, so this case fails if the card ever starts costing the
    # code its size.
    "qr-frame": lambda w, h: screens.qr_frame(
        w, h, qrchannel.frames_to_images(
            qrchannel.psbt_to_frames(
                base64.b64encode(b"psbt\xff" + b"\x01\x02\x03" * 400).decode()),
            panel=(min(w, h) - 2 * (screens.QR_CARD_PAD + screens.STROKE),) * 2)[0]),
    "qr-export": lambda w, h: screens.qr_export(
        w, h, qrchannel.text_to_image(
            "wpkh([73c5da0a/84h/0h/0h]" + "x" * 90 + "/0/*)#kwx0dvhr",
            panel=(w, min(h, screens.QR_MAX_PX))),
        "73c5da0a", "wpkh", "m/84h/0h/0h"),
    "text-entry": lambda w, h: screens.text_entry(
        w, h, "BIP32  EXTENDED  PRIVATE  KEY", "tprv8ZgxMBicQKsPe", 7,
        secret=True, caret=17),
    # The typing screen, on the box model (map typing, T3). Worst case
    # is a page with mistakes marked, the caret on one of them, and the
    # widest mode drawn under it.
    "typing": lambda w, h: screens.text_entry(
        w, h, "KEY  73C5DA0A  ·  TYPE  1/3", "xprv9s21ZrQH143K3QTDL",
        cursor=0, charset="xprv", mode=0, caret=20,
        hint="A types  ·  B deletes  ·  C for ABC",
        actions=("ABORT", "CHECK"), wrong={3, 11}, want_len=48),
    "typing-caps": lambda w, h: screens.text_entry(
        w, h, "KEY  73C5DA0A  ·  TYPE  1/3", "xprv9s21ZrQH143K3QTDL",
        cursor=25, charset="xprv", mode=1, caret=21,
        hint="A types  ·  B deletes  ·  C for abc",
        actions=("ABORT", "CHECK"), want_len=48),
    "typing-descriptor": lambda w, h: screens.text_entry(
        w, h, "DESCRIPTOR", "wpkh([73c5da0a/84h", cursor=37,
        charset="descriptor", mode=0, caret=18,
        hint="A types  ·  B deletes  ·  C for ABC", want_len=40),
    "verified": lambda w, h: screens.verified(
        w, h, "key 73C5DA0A\nowns this address"),
    "result-ok": lambda w, h: screens.result(w, h),
    "result-fail": lambda w, h: screens.result(
        w, h, ok=False, detail="PSBT lacks input data; fee unknown"),

    "keys-menu": lambda w, h: screens.keys_menu(
        w, h, [("coresigner", "d2b7e45c"), ("coresigner-2", "668b2262"),
               ("coresigner-3", "1df2e0b2"), ("coresigner-4", "73c5da0a"),
               ("coresigner-5", "ba4c8bd5")], 5),
    "keys-menu-empty": lambda w, h: screens.keys_menu(w, h, [], 0),
    "key-menu": lambda w, h: screens.key_menu(w, h, "d2b7e45c", 3),
    "tools-menu": lambda w, h: screens.tools_menu(w, h, 1),
    "leak-clear": lambda w, h: screens.leak_report(
        w, h, [("Wi-Fi driver", "not loaded", "normal")] * 24, 0),
    "leak-failures": lambda w, h: screens.leak_report(
        w, h, [("Wi-Fi overlay", "not set", "leak"),
               ("Bluetooth driver", "loaded", "leak"),
               ("Swap", "ON, key pages reach the card", "leak"),
               ("Serial console", "on the GPIO header", "leak"),
               ("USB device mode", "active, can be a disk", "leak"),
               ("Core networking", "off", "normal")], 3),
    "export-text": lambda w, h: screens.export_text(
        w, h, "wpkh([73c5da0a/84h/1h/0h]tpubDDRDHYNXyuoRVQwotDQHr", 1, 3),
    "address-taproot": lambda w, h: screens.address_page(w, h, 2, "bcrt1p5cyxnuxmeuwuvkwfem96lqzszd02n6xdcjrs20cac6yqjjwudpxqkedrcr", "tr"),
    "address-segwit": lambda w, h: screens.address_page(
        w, h, 0, "bc1q635yhaml2afumm27jxsjmqayczf5nf0xmm9zh0", "wpkh"),
    # Map correction, C1 and C2. The interruption's worst case is two
    # wide characters and a two-digit box; the summary's is a two-digit
    # count, which widens every sentence on it, and a list long enough
    # to need the bar.
    "wrong-character": lambda w, h: screens.wrong_character(w, h, 28, 4,
                                                            "W", "M"),
    "corrections-claim": lambda w, h: screens.corrections(
        w, h, [(b, c, "W") for b, c in
               ((3, 1), (7, 3), (9, 2), (14, 1), (18, 4), (22, 4),
                (25, 2), (27, 4), (28, 1), (11, 2), (12, 3), (13, 4))],
        "73c5da0a"),
    "corrections-one": lambda w, h: screens.corrections(
        w, h, [(7, 3, "C")], "73c5da0a"),
    "corrections-list": lambda w, h: screens.corrections(
        w, h, [(b, c, "W") for b, c in
               ((3, 1), (7, 3), (9, 2), (14, 1), (18, 4), (22, 4))],
        "73c5da0a", page=1, first=2),
    # The recheck: boxes that do not run on, numbered as the paper
    # numbers them, with the done ones filled (Ben, 2026-09-23).
    "recheck-boxes": lambda w, h: screens.text_entry(
        w, h, "RECHECK  3  BOXES  ·  5/12", "en4En", 0, "xprv", 0,
        caret=5, want_len=12, box_numbers=(7, 14, 22),
        actions=("ABORT", "DONE")),
    "choose-channel": lambda w, h: screens.choose_channel(
        w, h, ["stick", "card"], 1),
    "confirm-discard": lambda w, h: screens.confirm_discard(w, h, "d2b7e45c", 1),
    "export-options": lambda w, h: screens.export_options(w, h, 0),
    # The cosigner rows and the two routes out (map M11). The path on the
    # row is the longest the named rows can produce, which is mainnet's
    # m/48'/0'/0'/2' at the same width as regtest's.
    "cosigner-options": lambda w, h: screens.cosigner_options(w, h, 1),
    "multisig-menu": lambda w, h: screens.multisig_menu(
        w, h, screens.multisig_rows("m/48'/0'/0'/2'", "m/48'/0'/0'/1'", 9), 3),
    "account-menu": lambda w, h: screens.account_menu(w, h, 9),
    # The worst path a person can type: three hardened levels of 31 bits,
    # which is the shape buidl's secure_secret_path builds and the only
    # thing this row exists for.
    "path-echo": lambda w, h: screens.path_echo(
        w, h, "m/607137099'/1711870460'/1965312408'", "7asmw9jj", 1),
    "script-menu-cosigner": lambda w, h: screens.script_menu(
        w, h, ("wpkh", "tr", "sh", "pkh"), 4, multisig=True),
    "script-menu": lambda w, h: screens.script_menu(
        w, h, ("wpkh", "tr", "sh", "pkh"), 0),
    "choose-key": lambda w, h: screens.choose_key(
        w, h, [("coresigner", "d2b7e45c"), ("coresigner-2", "668b2262"),
               ("coresigner-3", "1df2e0b2"), ("coresigner-4", "73c5da0a"),
               ("coresigner-5", "ba4c8bd5")], {"1df2e0b2"}, 2),
    "busy": lambda w, h: screens.busy(w, h, "Bitcoin Core is generating your key…"),
    "keymaterial-warning": lambda w, h: screens.keymaterial_warning(w, h,
                                                                    "descriptor"),
}

# ---- every menu starts in the same place, and below its own divider ----
# Found on the board 2026-09-05: the selection box on a four-row menu was
# drawn through the title and the line under it, and two menus had their own
# geometry so each one began at a different height. This looks for the
# highlight colour above the divider, which is what that bug looks like in
# pixels, and pins that the first row lands identically on every menu.
MENUS = {
    "keys": lambda w, h: screens.keys_menu(w, h, [("coresigner", "d2b7e45c")], 0),
    "key": lambda w, h: screens.key_menu(w, h, "d2b7e45c", 0),
    "tools": lambda w, h: screens.tools_menu(w, h, 0),
    "settings": lambda w, h: screens.settings_menu(w, h, 0),
    "channel": lambda w, h: screens.channel_menu(w, h, 0),
    "export options": lambda w, h: screens.export_options(w, h, 0),
    "script type": lambda w, h: screens.script_menu(
        w, h, ("wpkh", "tr", "sh", "pkh"), 0),
    "cosigner options": lambda w, h: screens.cosigner_options(w, h, 0),
    "multisig": lambda w, h: screens.multisig_menu(
        w, h, screens.multisig_rows("m/48'/0'/0'/2'", "m/48'/0'/0'/1'", 0), 0),
    "account number": lambda w, h: screens.account_menu(w, h, 0),
    "script type with multisig": lambda w, h: screens.script_menu(
        w, h, ("wpkh", "tr", "sh", "pkh"), 0, multisig=True),
}


def _ochre_rows(img):
    """Rows carrying the selection box's top or bottom edge.

    It used to be any row carrying the colour, which stopped working when
    the title turned gold too (Ben, 2026-09-05). Looking at both edges of
    the row was not enough either: a long title fitted to 92% of a 240px
    panel spans the same x range the box does.

    What separates them is that the box's edge is a SOLID horizontal run
    and text is not, however wide the text gets. So a row counts when its
    longest unbroken run of gold covers more than 60% of the width.
    """
    want = tuple(int(screens.OCHRE[i:i + 2], 16) for i in (1, 3, 5))
    px = img.load()
    rows = set()
    for y in range(img.height):
        run = best = 0
        for x in range(img.width):
            run = run + 1 if px[x, y] == want else 0
            best = max(best, run)
        if best > img.width * 0.6:
            rows.add(y)
    return rows


for w, h in [(320, 240), (240, 240)]:
    # The divider is gone; what the first row must clear is the title,
    # which sits centred in the space above MENU_TOP.
    title_bottom = int(h * screens.MENU_TOP / 2) + int(h * 0.035)
    tops = {}
    for name, render in MENUS.items():
        rows = _ochre_rows(render(w, h))
        if not rows:
            bad(f"{w}x{h} {name}: nothing is highlighted at all")
            continue
        top = min(rows)
        tops[name] = top
        if top <= title_bottom:
            bad(f"{w}x{h} {name}: the selection box reaches y={top}, "
                f"through the title, which ends near y={title_bottom}")
    if len(set(tops.values())) == 1:
        ok(f"{w}x{h}: all {len(tops)} menus start their first row at "
           f"y={next(iter(tops.values()))}, clear of the title")
    else:
        bad(f"{w}x{h}: menus start at different heights: {tops}")


# EVERY screen must have a case, or the two checks above are reporting on
# whatever somebody remembered to add. Six had none until 2026-09-08:
# about, scanning, qr_export, text_entry and verified,
# which between them are the export card, the viewfinder, the keyboard a
# key is typed into and the screen that says a paper backup is good.
#
# A screen is a public function in screens.py whose first two parameters
# are w and h. That leaves out scrollbar (which draws onto a canvas it is
# handed), and modes, mode_cells and text_pages, which return
# data rather than a frame.
_tree = ast.parse((ROOT / "coresigner" / "screens.py").read_text())
_screens = [n.name for n in _tree.body
            if isinstance(n, ast.FunctionDef)
            and not n.name.startswith("_")
            and [a.arg for a in n.args.args][:2] == ["w", "h"]]
#: Screens checked by a loop of their own further down rather than by a
#: CASES entry, and why. Named here rather than found by searching the
#: whole file, because MENUS renders two screens for a geometry check that
#: runs neither of the checks above: a mention is not a check.
CHECKED_ELSEWHERE = {
    "backup_page": "paginated per page in the text_pages loop below",
}
_source_of_cases = "".join(
    inspect.getsource(fn) for fn in CASES.values())
_uncovered = [n for n in _screens
              if f"screens.{n}(" not in _source_of_cases
              and n not in CHECKED_ELSEWHERE]
if _uncovered:
    bad(f"{len(_uncovered)} screen(s) that no case renders, so nothing "
        f"checks whether they fit or overlap: {_uncovered}")
else:
    ok(f"all {len(_screens)} screens have a case in this file")

for w, h in [(320, 240), (240, 240)]:
    for name, render in CASES.items():
        _ctx.update(w=w, h=h, name=name, over=[], drawn=[])
        render(w, h)
        clashes = collisions(_ctx["drawn"])
        if _ctx["over"]:
            for text, box in _ctx["over"]:
                bad(f"{w}x{h} {name}: {text[:40]!r} at {box} escapes {w}x{h}")
        elif clashes:
            for x, y in clashes:
                bad(f"{w}x{h} {name}: {x[0][:28]!r} at {x[1]} is painted "
                    f"over {y[0][:28]!r} at {y[1]}")
        else:
            ok(f"{w}x{h} {name} fits, and nothing is painted over "
               f"anything else")

# Branding must survive a one-bit or inverted panel without depending on
# colour. Capture the requested drawing colours rather than antialiased pixel
# blends, which naturally contain intermediate RGB values.
used_splash_colours = []
draw_methods = {name: getattr(ImageDraw.ImageDraw, name)
                for name in ("text", "line", "polygon", "rectangle")}
try:
    for name, method in draw_methods.items():
        def capture(self, *args, _method=method, **kwargs):
            used_splash_colours.extend(
                colour for colour in (kwargs.get("fill"), kwargs.get("outline"))
                if colour is not None)
            return _method(self, *args, **kwargs)
        setattr(ImageDraw.ImageDraw, name, capture)
    screens.splash(320, 240)
finally:
    for name, method in draw_methods.items():
        setattr(ImageDraw.ImageDraw, name, method)

# Ben (2026-09-01): the splash names the brand, "Bitcoin Butlers" in gold.
# It is no longer monochrome; it must still use only brand tokens, never a
# stray colour.
allowed = {screens.CREAM, screens.OCHRE, screens.GREY}
unexpected = set(used_splash_colours) - allowed
if unexpected:
    bad(f"splash requests non-brand foreground colours: {unexpected}")
else:
    ok("splash requests only brand tokens (cream, gold, grey)")

# The backup screens carry strings the flows really produce: a 127-character
# A-22: only Core's 111-character master xprv now; the codex32 string
# went to the lab with the module that made it.
# Neither fits one page, so the screen must paginate rather than overrun.
for w, h in [(320, 240), (240, 240)]:
    for label, payload in (("xprv", XPRV),):
        pages = screens.text_pages(payload)
        if len(pages) < 2:
            bad(f"{label}: text_pages did not split a {len(payload)}-char string")
            continue
        if "".join(pages) != payload:
            bad(f"{label}: text_pages loses or reorders characters")
            continue
        for i, page in enumerate(pages):
            _ctx.update(w=w, h=h, name=f"backup-{label}-{i}", over=[],
                        drawn=[])
            screens.backup_page(w, h, page, "KEY  D2B7E45C",
                                page=i, pages=len(pages))
            for text, box in _ctx["over"]:
                bad(f"{w}x{h} backup {label} page {i}: {text[:32]!r} at {box}")
            # This loop checked the panel edge and not the overlap, which
            # is how backup_page reached 2026-09-08 half-checked while
            # reading as covered. It carries the key a user writes down.
            for x, y in collisions(_ctx["drawn"]):
                bad(f"{w}x{h} backup {label} page {i}: {x[0][:20]!r} at "
                    f"{x[1]} is painted over {y[0][:20]!r} at {y[1]}")
        ok(f"{w}x{h} {label} paginates into {len(pages)} pages that fit")

# --- what the review screen must SAY, not just where it fits ----------
#
# Both of these shipped broken and every suite stayed green, because
# fitting and overlapping were checked and PRESENCE was not.


def _strings(**kw):
    """Every string one review render draws."""
    outs = [(ADDR, Decimal("0.1"))] * 4
    _ctx.update(w=240, h=240, name="probe", over=[], drawn=[])
    screens.review(240, 240, outs[:kw.pop("outputs", 4)],
                   Decimal("0.0001"), input_total_btc=Decimal("1"), **kw)
    return [t for t, _ in _ctx["drawn"]]


SOLO = [("a1b2c3d4", "m/84h/0h/0h")]
QUORUM = [("a1b2c3d4", "m/48h/0h/0h/2h"), ("e5f6a7b8", "m/48h/0h/0h/2h")]
HINT = "UP/DOWN · more outputs"
_GOLD = tuple(int(screens.OCHRE[i:i + 2], 16) for i in (1, 3, 5))


def _has_bar(**kw):
    """True when a review render paints a scrollbar thumb."""
    outs = [(ADDR, Decimal("0.1"))] * 4
    img = screens.review(240, 240, outs[:kw.pop("outputs", 4)],
                         Decimal("0.0001"), input_total_btc=Decimal("1"),
                         **kw)
    px = img.load()
    # The bar lives in the right-hand margin, clear of anything else.
    return any(px[x, y][:3] == _GOLD
               for x in range(img.width - 8, img.width)
               for y in range(int(img.height * 0.16),
                              int(img.height * 0.80)))


# 1. THE PAGING BAR. A line of gold type said "UP/DOWN · more outputs"
#    until 2026-09-24, and it was drawn only when no quorum line was:
#    `main` passes cosigners for any PSBT carrying derivations, so it
#    vanished from every paged review including single-sig ones and
#    nothing failed. Ben then cut the line itself, on the same rule he
#    gave for the corrections screen ("just show the scroll bar if we
#    scroll"), and one output a page needs that room for the address.
#
#    So the GUARANTEE is unchanged and the thing carrying it moved: a
#    paged review must still tell you there is more, and the bar is what
#    tells you. Checked in pixels, because the bar draws no string and a
#    string test would pass on a screen with no bar at all.
for label, kw in (("single-sig", {"cosigners": SOLO, "ours": "a1b2c3d4"}),
                  ("a quorum", {"quorum": (2, 3), "cosigners": QUORUM,
                                "ours": "a1b2c3d4"}),
                  ("no derivations at all", {})):
    if _has_bar(**kw):
        ok(f"a paged review draws its scrollbar, with {label}")
    else:
        bad(f"a paged review with {label} draws no scrollbar")
    if HINT in _strings(**kw):
        bad(f"the review still says {HINT!r}; the bar replaced it")
if not _has_bar(outputs=1, cosigners=QUORUM, ours="a1b2c3d4"):
    ok("a one-page review draws no scrollbar")
else:
    bad("a single page still draws a scrollbar")

# 2. THE PATH MUST BE OURS. It fell back to the first cosigner's when
#    ours was not among them, which printed a stranger's derivation as
#    the wallet being signed for. `_key_for` lets a person choose any
#    loaded key, so the case is reachable.
if any("m/48h/0h/0h/2h" in t for t in
       _strings(quorum=(2, 3), cosigners=QUORUM, ours="e5f6a7b8")):
    ok("the review shows OUR derivation path")
else:
    bad("the review does not show our path at all")
if not any("m/48h" in t for t in
           _strings(quorum=(2, 3), cosigners=QUORUM, ours="cccccccc")):
    ok("and shows NO path when this key is not one of the cosigners")
else:
    bad("the review shows a stranger's path as the wallet being signed for")

# --- The receive address screen, after E-7 -----------------------------
# Five things Ben read wrong on the board on 2026-09-18, exporting a key
# for the first time. Each is asserted by what the screen SAYS, per
# TESTING.md rule 11, so a layout change cannot quietly undo one.

ADDR42 = "bcrt1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"


def _drawn(render):
    """Every string one render draws, with the box it drew it in."""
    _ctx.update(w=320, h=240, name="address", over=[], drawn=[])
    render(320, 240)
    return list(_ctx["drawn"])


_export = _drawn(lambda w, h: screens.address_page(w, h, 0, ADDR42, "wpkh",
                                                   total=3))
_browse = _drawn(lambda w, h: screens.address_page(w, h, 10, ADDR42, "wpkh",
                                                   switchable=True))
_said = [t for t, _ in _export]

# 1. WHICH address, in the words a person uses out loud.
if any(t.startswith("1ST  RECEIVE") for t in _said):
    ok("the address screen says WHICH address it is, as 1ST")
else:
    bad(f"the address screen never says 1ST: {_said[:2]}")
if any("11TH  RECEIVE" in t for t, _ in _browse):
    ok("and counts on, in ordinals, past the teens")
else:
    bad("index 10 is not drawn as 11TH")

# 2. NOT the policy, on the walk after an export, where it was chosen
#    two screens ago. It stays on the endless browse, where LEFT and
#    RIGHT change it and nothing else would show that they had.
if not any("SEGWIT" in t for t in _said):
    ok("the export walk does not repeat the policy you already chose")
else:
    bad("the policy is still on the export walk's address screen")
if any("NATIVE SEGWIT" in t for t, _ in _browse):
    ok("the browse, where LEFT and RIGHT switch it, still names it")
else:
    bad("the browse screen no longer says which policy it is showing")

# 3. No footer.
if not any("compare" in t for t in _said + [t for t, _ in _browse]):
    ok("the compare-every-group footer is gone")
else:
    bad("the compare-every-group footer is still drawn")

# 4. BIGGER. Measured against the layout Ben complained about, which
#    fixed four groups to a row and shrank from h*0.075 to fit the
#    width. Not a re-derivation of the new rule: it is the old one.
_probe = ImageDraw.Draw(Image.new("RGB", (320, 240)))
_was = int(240 * 0.075)
while _was > int(240 * 0.03):
    if _probe.textlength("W" * 19, font=screens._font(_was)) <= int(320 * .92):
        break
    _was -= 1
_old_h = _probe.textbbox((0, 0), "8z30", font=screens._font(_was))[3]
_new_h = max(b[3] - b[1] for t, b in _export if len(t) == 4)
if _new_h >= _old_h * 1.25:
    ok(f"the address type grew from {_old_h}px to {_new_h}px tall")
else:
    bad(f"the address type is {_new_h}px tall against {_old_h}px before; "
        "E-7 item 5 asked for the room the footer freed")

# 5. The hardened mark, in the notation of the wallet beside you.
_ctx.update(w=320, h=240, name="qr-export", over=[], drawn=[])
screens.qr_export(320, 240, Image.new("RGB", (100, 100)), "73c5da0a",
                  "wpkh", "m/84h/0h/0h")
_caption = [t for t, _ in _ctx["drawn"] if "84" in t]
if _caption and "m/84'/0'/0'" in _caption[0]:
    ok("the export caption writes hardened steps the way Sparrow does")
else:
    bad(f"the export caption still draws Core's h: {_caption}")

# --- The typing screen's boxes -----------------------------------------
# Ben, on the board, 2026-09-18: "entering a private key, the box should
# be larger, it should be gold around the active one with the same size
# number to its left."

_TYPED = "tprv8ZgxMBicQKQx"
_WANT = "tprv8ZgxMBicQKsPe5YMU9gHen4Ez3ApihUfykaqUorj9t6"


def _entry(caret):
    _ctx.update(w=320, h=240, name="text_entry", over=[], drawn=[])
    img = screens.text_entry(320, 240, "KEY 73C5DA0A  ·  TYPE 1/3", _TYPED,
                             5, "xprv", 0, caret=caret, wrong={14, 15},
                             want_len=len(_WANT), actions=("ABORT", "CHECK"),
                             hint="C changes case")
    return img, list(_ctx["drawn"])


_img0, _boxes0 = _entry(0)
_img8, _ = _entry(8)

# 1. BIGGER. The layout that shipped before drew the characters at
#    h*0.048 and their numbers at h*0.030.
_probe = ImageDraw.Draw(Image.new("RGB", (320, 240)))


def _size_of(glyph, size):
    """How wide and tall one glyph renders at one type size."""
    x1, y1, x2, y2 = _probe.textbbox((0, 0), glyph, font=screens._font(size))
    return x2 - x1, y2 - y1


# Box glyphs only: the same letters appear on the keyboard grid below,
# and the grid is not what Ben asked to grow. One glyph, measured on
# both, because a descender makes a box taller without making the type
# bigger.
_chars = [b for t, b in _boxes0
          if len(t) == 1 and t in _TYPED and b[3] < 240 * 0.5]
_eight = next(b for t, b in _boxes0
              if t == "8" and b[3] < 240 * 0.5)
_now = (_eight[2] - _eight[0], _eight[3] - _eight[1])
_was = _size_of("8", int(240 * 0.048))          # the size that shipped
if _now[0] >= _was[0] * 1.4 and _now[1] >= _was[1] * 1.3:
    ok(f"the typed characters grew from {_was[0]}x{_was[1]}px to "
       f"{_now[0]}x{_now[1]}px")
else:
    bad(f"the typed characters are {_now[0]}x{_now[1]}px against "
        f"{_was[0]}x{_was[1]}px before; Ben asked for a larger box")

# 2. THE NUMBER, to the left of its box and the size of the characters.
_num = next((b for t, b in _boxes0 if t == "1"), None)
_first = min((b for b in _chars), key=lambda b: b[0])
if _num is None:
    bad("the box number is not drawn at all")
elif _num[2] > _first[0]:
    bad(f"the box number is not left of its box: {_num} against {_first}")
elif (_num[3] - _num[1]) < _size_of("1", int(240 * 0.072))[1] * 0.9:
    bad(f"the box number is {_num[3] - _num[1]}px tall, smaller than the "
        "characters beside it")
else:
    ok("the box number is left of its box, at the size of the characters")


# 3. GOLD ROUND THE ACTIVE BOX, and it moves with the caret.
_GOLD = ImageColor.getrgb(screens.OCHRE)


def _gold(img, region):
    return sum(1 for p in img.crop(region).getdata() if p == _GOLD)


_BOX1 = (0, int(240 * 0.14), 160, int(240 * 0.25))
if _gold(_img0, _BOX1) > 100 and _gold(_img8, _BOX1) < 20:
    ok("the active box is outlined in gold, and the gold follows the caret")
else:
    bad(f"gold in box 1: {_gold(_img0, _BOX1)} with the caret in it, "
        f"{_gold(_img8, _BOX1)} with the caret in box 3. The active box "
        "is not marked, or the mark does not move")

# 4. AN EMPTY BOX AHEAD, while a key is being typed. A box appeared
#    when its first character was typed, so box 2 did not exist until
#    you had already committed to it (Ben, 2026-09-18).
_HINT = coresigner_main.Session._type_hint(screens.modes("xprv"), 0)


def _boxes_drawn(text, caret):
    _ctx.update(w=320, h=240, name="text_entry", over=[], drawn=[])
    screens.text_entry(320, 240, "MASTER  PRIVATE  KEY", text, 0, "xprv",
                       0, caret=caret, hint=_HINT)
    return {t for t, b in _ctx["drawn"] if b[3] < 240 * 0.5 and t.isdigit()}


if "2" in _boxes_drawn("tprv", 4):
    ok("box 2 is there the moment box 1 is full, empty and waiting")
else:
    bad("box 2 does not appear until its first character is typed")
if _boxes_drawn("tpr", 3) == {"1"}:
    ok("and no box appears before there is anywhere to put it")
else:
    bad(f"a part-typed box drew {_boxes_drawn('tpr', 3)}, not just box 1")

# 5. The one on the grid is a ONE. Base58 has no capital I, no lowercase
#    l, no capital O and no zero, because at this size they are each
#    other. The prose face drew the one as a bare stroke and put the
#    confusion back, so the screen carried the sentence "1 is a one":
#    Ben read the first cell as a capital I (2026-09-18).
#
#    The key face draws it with a flag and a foot, so the sentence went
#    on 2026-09-24 and "A types" got its place back. What replaces the
#    sentence is a MEASUREMENT: the 1 and the characters it was mistaken
#    for must not render as the same pixels. That is the property the
#    sentence was standing in for, and unlike the sentence it cannot be
#    true while the screen is wrong.
_UPPER = screens.modes("xprv")[1][1]
_LOWER = screens.modes("xprv")[0][1]
_absent = [c for c in "IlO0" if c in _UPPER + _LOWER]
if _absent:
    bad(f"the key charset is not base58: it holds {_absent}")
else:
    ok("the key charset is base58")
if "1 is a one" in _HINT:
    bad(f"the key screen still spends a line on the one: {_HINT!r}")
else:
    ok("the key screen no longer has to explain the one")
_grid_face = screens._mono(max(screens.MONO_FLOOR, int(240 * 0.05)))
_one = bytes(_grid_face.getmask("1"))
for _other in "lLiI":
    if bytes(_grid_face.getmask(_other)) == _one:
        bad(f"the grid draws 1 and {_other} as the same pixels")
        break
else:
    ok("the grid draws 1 apart from l, L, i and I")

# 6. THE CARET SITS ON THE BORDER, in ink. A gold bar inside the box
#    took interior height the bigger type wants, and two marks stacked
#    in one box read as crammed (Ben, 2026-09-18).
_INK = ImageColor.getrgb(screens.INK)


def _row_colours(img, y):
    return [img.getpixel((x, y)) for x in range(320)]


_caret = screens.text_entry(320, 240, "MASTER  PRIVATE  KEY", "tprv", 0,
                            "xprv", 0, caret=2, hint=_HINT)
_plain = screens.text_entry(320, 240, "MASTER  PRIVATE  KEY", "tprv", 0,
                            "xprv", 0, caret=None, hint=_HINT)
_moved = [(x, y) for y in range(240) for x in range(320)
          if _caret.getpixel((x, y)) != _plain.getpixel((x, y))]
if not _moved:
    bad("the caret is not drawn at all")
elif any(_caret.getpixel(p) != _INK for p in _moved):
    bad("the caret paints something other than ink over the box")
else:
    _ys = {y for _x, y in _moved}
    ok(f"the caret cuts an ink notch in the border, on {len(_ys)} rows")

# 7. DOWN TO THE BAR HAS TO LOOK LIKE SOMETHING. CHECK was drawn gold
#    while the cursor was still in the character grid, so pressing DOWN
#    changed nothing on the panel and LEFT to ABORT was a move nobody
#    would try (Ben, 2026-09-18).


def _entry_bar(sel):
    return screens.text_entry(320, 240, "MASTER  PRIVATE  KEY", "tprv", 14,
                              "xprv", 0, caret=4, hint=_HINT,
                              actions_sel=sel)


_on_grid, _on_check, _on_abort = _entry_bar(None), _entry_bar(1), _entry_bar(0)
_BAR = (0, int(240 * 0.87), 320, 240)
_GRIDBAND = (0, int(240 * 0.60), 320, int(240 * 0.85))

if _on_grid.crop(_BAR).tobytes() == _on_check.crop(_BAR).tobytes():
    bad("the action bar looks the same whether it has focus or not, so "
        "pressing DOWN into it shows nothing")
elif _gold(_on_grid, _BAR) > 10:
    bad(f"{_gold(_on_grid, _BAR)} gold pixels mark a button while the "
        "grid still has focus")
elif not _gold(_on_check, _BAR) > 10:
    bad("no button is marked when the bar DOES have focus")
else:
    ok("no button is marked until the bar has focus, and then one is")

if _gold(_on_grid, _GRIDBAND) > _gold(_on_check, _GRIDBAND) * 2:
    ok("and the grid cursor goes from filled to outlined as focus leaves")
else:
    bad("the grid cursor looks the same with and without focus, so two "
       f"things claim it: {_gold(_on_grid, _GRIDBAND)} gold pixels on "
       f"the grid against {_gold(_on_check, _GRIDBAND)}")

if _on_check.crop(_BAR).tobytes() != _on_abort.crop(_BAR).tobytes():
    ok("and LEFT moves the mark from CHECK to ABORT")
else:
    bad("LEFT does not change which button is marked")

# 8. EVERY SENTENCE THE DEVICE DRAWS STARTS WITH A CAPITAL. Ben's rule
#    (2026-09-19, map correction C5). The device's copy was lower case
#    on purpose once: "write this down. it opens the wallet".
#
#    Checked on the SOURCE, not on the render, and that matters. A
#    rendered line can be the middle of a wrapped sentence, so a render
#    check flags "them, you may not be able to recover" and is useless.
#    The source knows which string STARTS a piece of copy: the argument
#    _fit is given, or the first line of the list _fit_block is given.
#    Titles, button labels and the character grid are not sentences and
#    are not passed to either.

COPY_ARG = {"_fit": 2, "_fit_block": 1, "_hold": 1, "_row": 4}


def _opening_text(node):
    """The literal a piece of copy starts with, when it is literal."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values:
        head = node.values[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            return head.value
        return None          # opens with a substitution: nothing to judge
    if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
        return _opening_text(node.elts[0])
    return None


lower = []
for _f in ("coresigner/screens.py", "coresigner/main.py"):
    _tree = ast.parse((ROOT / _f).read_text())
    # A SENTENCE A HELPER RETURNS is copy too. `_core_says` returned
    # "that is not a valid key, check what you typed" and `_hold` drew
    # it, and this check saw only the literal AT the `_hold` call site,
    # which was `self._hold(self._core_says(exc))`: nothing to judge.
    # It shipped in the same diff that capitalised fourteen others
    # (two-axis review, 2026-09-23). Every returned literal in these two
    # modules is copy or nothing, so judging them all costs no false
    # alarms; measured, not assumed.
    for _node in ast.walk(_tree):
        if isinstance(_node, ast.Return) and _node.value is not None:
            _t = _opening_text(_node.value)
            if _t and " " in _t and _t[:1].isascii() and _t[:1].islower():
                lower.append(f"{_f}:{_node.lineno} {_t!r} (returned)")
    for _node in ast.walk(_tree):
        if not isinstance(_node, ast.Call):
            continue
        _name = (_node.func.attr if isinstance(_node.func, ast.Attribute)
                 else getattr(_node.func, "id", ""))
        if _name not in COPY_ARG:
            continue
        # self._hold(detail) puts the copy one argument earlier.
        _i = 0 if (_name == "_hold"
                   and isinstance(_node.func, ast.Attribute)) else COPY_ARG[_name]
        if len(_node.args) <= _i:
            continue
        _t = _opening_text(_node.args[_i])
        if _t and " " in _t and _t[:1].isascii() and _t[:1].islower():
            lower.append(f"{_f}:{_node.lineno} {_t!r}")

if lower:
    bad(f"{len(lower)} sentence(s) on screen start lower case: "
        + "; ".join(lower[:3]) + ("; ..." if len(lower) > 3 else ""))
else:
    ok("every sentence the device draws starts with a capital letter")

# 9. THE PAPER IS NUMBERED, 1 to 28 across its three parts. It is the
#    only lever the device has on a pen error: a skipped character
#    leaves a box with three in it, seen while writing rather than days
#    later, and "box 7, character 3" becomes a glance instead of
#    counting seven groups along your own handwriting (map correction,
#    C7). Nothing tested it until a mutation run deleted the numbers on
#    2026-09-23 and every suite stayed green.

_KEY111 = ("tprv8ZgxMBicQKsPe5YMU9gHen4Ez3ApihUfykaqUorj9t6FDqy3nP6eoXiAo2ss"
           "vpAjoLroQxHqr3R5nE3a5dU3DHTjTgJDd7zrbniJr6nrCzd")
_paper_numbers = []
for _i, _page in enumerate(screens.text_pages(_KEY111)):
    _ctx.update(w=320, h=240, name=f"backup-{_i}", over=[], drawn=[])
    screens.backup_page(320, 240, _page, "KEY 73C5DA0A", _i, 3)
    _paper_numbers += [int(t) for t, _b in _ctx["drawn"] if t.isdigit()]

_want_boxes = len(screens._groups(_KEY111))
if _paper_numbers == list(range(1, _want_boxes + 1)):
    ok(f"the paper numbers its {_want_boxes} boxes 1 to {_want_boxes}, "
       "once each, across all three parts")
else:
    bad(f"the paper draws box numbers {_paper_numbers[:6]}..., not 1 to "
        f"{_want_boxes}. A dropped character is invisible without them, "
        "and every message the device sends about a box is addressed to "
        "a numbering nobody can see.")

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
