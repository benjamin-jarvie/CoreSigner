"""Core Signer's screens as pure PIL renders.

Resolution-independent: every screen takes (width, height) and lays out from
proportions, so the same code drives the primary 2.8" ST7789 (320x240) and the
1.3" ST7789 (240x240). On-device, frames go to the vendored drivers'
show_image(); on a dev machine they save as PNGs for review (see
tools/render_screens.py).

Palette follows the Core Signer/Kawanatanga artefact palette: ink ground, cream
text, Te Peeke red for the one number that matters on each screen.
"""

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

INK = "#1A1714"
CREAM = "#F5EFE0"
#: Brighter since 2026-09-19. The old #9E2B25 is a deep brick that
#: reads as brown beside CREAM on this panel, and a wrong character
#: outlined in it is barely visible at arm's length (Ben, on the
#: board). Every red on the device is this one, so the FAILED ring
#: moved with it.
RED = "#FF4B3E"
GREY = "#B8B2A6"
#: One step down from CREAM, for every OTHER group of four in a long
#: string of characters. Ben, from the board notes: a subtle change of
#: colour every four characters. It has to stay well clear of GREY, which
#: on this device means "not this one" and would read as a dead group.
CREAM_DIM = "#D8D1C0"
# The one gold. Ben set it to #FBDC7B on 2026-09-05: "which will be the
# universal color where we currently have gold". It was #C8912F, which
# read as brown beside the cream on a lit panel. Every gold on the device
# comes from here, and tests/test_screen_fit.py reads this constant rather
# than a copy of the hex, so changing it changes everything at once.
OCHRE = "#FBDC7B"


@lru_cache(maxsize=None)
def _font(size):
    return ImageFont.load_default(size=size)


_ICON_TTF = (Path(__file__).resolve().parent.parent
             / "hw" / "vendor" / "fonts" / "fa-solid-subset.ttf")


@lru_cache(maxsize=None)
def _iconfont(size):
    return ImageFont.truetype(str(_ICON_TTF), size)


_MONO_TTF = (Path(__file__).resolve().parent.parent
             / "hw" / "vendor" / "fonts" / "iosevka-mono-subset.ttf")

#: Smallest a KEY CHARACTER may be drawn. Below this the loop on the
#: lowercase k fills in and the letter goes back to being a small K.
#: Measured, not chosen: tests/test_mono_font.py fails if it moves.
MONO_FLOOR = 13


@lru_cache(maxsize=None)
def _mono(size):
    """The face for characters a person copies or compares.

    Iosevka, cut to the characters this device paints, with the looped
    `k` swapped into the k slot (tools/make_mono_font.py). Two properties
    earn it a place beside the prose face:

    * the lowercase k closes into a loop, so it stops being a short K.
      Ben could not separate the pair on the board, and at 17px the prose
      face gives nothing to separate them by.
    * it is MONOSPACED, so every character occupies one width. A key is
      read one character at a time against paper, and a fixed pitch means
      box 7 character 3 sits where box 7 character 3 sat on the last
      screen.

    Prose keeps the prose face. This is for keys, addresses, fingerprints,
    descriptors and paths.
    """
    return ImageFont.truetype(str(_MONO_TTF), size)


def _face(size, mono):
    return _mono(size) if mono else _font(size)


# Font Awesome Free Solid codepoints (see hw/vendor/fonts/NOTICE.md).
ICON = {"load": "\uf019", "key": "\uf084", "tools": "\uf7d9",
        "gear": "\uf013", "power": "\uf011", "about": "\uf05a",
        "signature": "\uf5b7"}


#: Everything rounded is drawn this many times larger and scaled back
#: down. A radius drawn straight at panel size aliases into stair-steps,
#: which is what Ben saw on the export card's corners (2026-09-05). It is
#: the same trick _status_circle already used for its ring.
SS = 4


def _round_rect(img, box, radius, fill):
    """A FILLED rounded rectangle with smooth corners.

    PIL draws a radius at whatever resolution you give it, and 8 pixels of
    radius on a 320x240 panel is four or five steps you can count, which
    is what Ben saw on the export card. Drawing into a 4x mask and
    downsampling with LANCZOS spreads those steps along the edge.

    Fills only, deliberately. The same treatment on a ONE-PIXEL outline
    has no solid pixel to carry: every pixel of the line comes back
    part-blended, so a crisp gold rule turns into a faint smear, which is
    worse on a panel than the stair-steps were. Thin outlines stay on
    PIL's own drawing, where a straight edge lands on whole pixels and
    only the corners step.
    """
    x0, y0, x1, y1 = (int(v) for v in box)
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    mask = Image.new("L", (w * SS, h * SS), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, w * SS - 1, h * SS - 1], radius=radius * SS, fill=255)
    img.paste(fill, (x0, y0), mask.resize((w, h), Image.LANCZOS))


def _icon(d, cx, cy, size, name, col):
    d.text((cx, cy), ICON[name], font=_iconfont(size), fill=col, anchor="mm")


def _tracked(d, xy, text, font, fill, anchor, tracking, weight):
    """Draw `text` with `tracking` pixels between characters.

    Pillow draws a string in one call and has no letter-spacing, so the
    characters are placed one at a time and the gap is added by hand.
    Only the home tiles ask for this; every other screen takes the plain
    path above, which is left exactly as it was.
    """
    widths = [font.getlength(ch) for ch in text]
    total = sum(widths) + tracking * max(len(text) - 1, 0) + weight
    horizontal, vertical = anchor[0], anchor[1]
    x = xy[0]
    if horizontal == "m":
        x -= total / 2
    elif horizontal == "r":
        x -= total
    for ch, adv in zip(text, widths, strict=True):
        for dx in range(weight + 1):
            d.text((x + dx, xy[1]), ch, font=font, fill=fill,
                   anchor="l" + vertical)
        x += adv + tracking
    return total


def _tracked_width(font, text, tracking, weight):
    return (sum(font.getlength(ch) for ch in text)
            + tracking * max(len(text) - 1, 0) + weight)


def _fit(d, xy, text, size, fill, anchor, maxw, bold=False, tracking=0.0,
         mono=False):
    """Draw text at `size`, shrinking until it fits `maxw`.

    `mono` draws it in the key face instead of the prose face: see _mono.
    It also raises the floor to MONO_FLOOR, because a key character that
    shrank past the floor would lose the loop on its k and start lying
    about which case it is. A mono string that will not fit is cut to an
    ellipsis rather than shrunk into a lie.

    `bold` draws the word a second time one pixel to the right, which
    thickens the stems and leaves the letterforms alone. The prose face is
    Pillow's built-in and has no bold cut. The obvious
    alternative, `stroke_width=1`, outlines every glyph on all four sides:
    at 12px that closed the counters and "SETTINGS" came out a smear
    (looked at, 2026-09-06). The extra pixel is measured as well as drawn,
    so a bold word keeps the same width guarantee.

    The panel has no scrollbar: a string wider than the canvas is simply not
    there. Every screen that renders content it did not choose itself (an
    address, an error string, a wrapped warning) goes through here, so a
    longer string degrades in size instead of vanishing off the edge. If the
    floor size still overflows, the tail is cut to a visible ellipsis; backup
    key material never reaches that path (text_pages sizes each page, and
    test_screen_fit pins every string inside the canvas).
    """
    weight = 1 if bold else 0
    floor = MONO_FLOOR if mono else 6

    def width(s_, f_):
        if tracking:
            return _tracked_width(f_, s_, tracking, weight)
        box = d.textbbox(xy, s_, font=f_, anchor=anchor)
        return box[2] - box[0] + weight

    while True:
        font = _face(size, mono)
        if width(text, font) <= maxw or size <= floor:
            break
        size -= 1
    if width(text, font) > maxw:
        while len(text) > 1:
            text = text[:-1]
            if width(text + "…", font) <= maxw:
                break
        text += "…"
    if tracking:
        _tracked(d, xy, text, font, fill, anchor, tracking, weight)
        return
    for dx in range(weight + 1):
        d.text((xy[0] + dx, xy[1]), text, font=font, fill=fill,
               anchor=anchor)


def _fit_block(d, lines, xs, size, fill, anchor, maxw, mono=False):
    """Draw several lines of body copy at ONE shared size.

    _fit shrinks each string on its own, which is right for a single string
    the device did not choose and wrong for a paragraph: the long line comes
    out smaller than the short one and the block reads ragged. Found on the
    board, 2026-09-04, on the generate-a-seed warning.

    Picks the largest size at or below `size` where EVERY line fits, then
    draws them all at it. Falls back to _fit per line only at the floor, so
    a pathological string still degrades rather than running off the edge.
    """
    floor = MONO_FLOOR if mono else 6
    while size > floor:
        if all(_width(d, text, size, anchor, xs[0], mono) <= maxw
               for text in lines):
            break
        size -= 1
    font = _face(size, mono)
    for text, xy in zip(lines, xs, strict=True):
        if not text:
            # A blank line is a SPACER. Painting it drops a zero-width
            # mark at that point, which the collision check reads as a
            # string sitting on whatever else is drawn there: the
            # corrections screen paints its fingerprint into exactly
            # such a gap (test_screen_fit, 2026-09-24).
            continue
        if _width(d, text, size, anchor, xy, mono) > maxw:
            _fit(d, xy, text, size, fill, anchor, maxw, mono=mono)  # floor
        else:
            d.text(xy, text, font=font, fill=fill, anchor=anchor)
    return size


def _width(d, text, size, anchor, xy, mono=False):
    box = d.textbbox(xy, text, font=_face(size, mono), anchor=anchor)
    return box[2] - box[0]


def _row(d, left, right, y, lhs, rhs, lhs_size, rhs_size, lhs_fill,
         rhs_fill, gap, lhs_mono=False, rhs_mono=False):
    """Two strings on one line, one left-anchored and one right-anchored,
    that cannot collide.

    The review screen drew both with a bare `d.text`, so neither was
    bounded by the other. On the 240x240 pocket panel the address and the
    amount overlapped from about 100,000 BTC, and FEE and the fee did the
    same: two strings painted on top of each other on the screen the user
    signs from (measured 2026-09-08). Everything else that renders content
    the device did not choose goes through `_fit`; these two did not.

    THE RIGHT-HAND STRING WINS. It shrinks only to keep off the left edge,
    and the left-hand string then fits whatever is left. On a signing
    screen the amount is the thing that must stay readable; an address is
    already shortened to thirteen characters and shortens further without
    losing its ends.
    """
    span = right - left
    floor = MONO_FLOOR if rhs_mono else 6
    while (rhs_size > floor
           and _width(d, rhs, rhs_size, "rm", (right, y), rhs_mono)
           > span - gap):
        rhs_size -= 1
    face = _face(rhs_size, rhs_mono)
    d.text((right, y), rhs, font=face, fill=rhs_fill, anchor="rm")
    rhs_left = d.textbbox((right, y), rhs, font=face, anchor="rm")[0]
    _fit(d, (left, y), lhs, lhs_size, lhs_fill, "lm",
         max(1, rhs_left - left - gap), mono=lhs_mono)


def _actions(d, w, h, labels, selected=1):
    """The bottom action bar (Ben, 2026-09-01): actions are visible,
    d-pad-toggleable options in one place, never key legends in corners.
    The gold box marks the active option; A activates it.

    `selected=None` means the FOCUS IS SOMEWHERE ELSE on this screen,
    so no option is marked. The typing screen drew CHECK gold while the
    cursor was still up in the character grid, so pressing DOWN to
    reach the bar changed nothing on the panel: it already looked like
    the bar had focus (Ben, on the board, 2026-09-18). A screen that
    shows no change for a press has told a person the press did
    nothing, and then LEFT to ABORT is a move nobody tries."""
    size = int(h * 0.05)
    box_h = int(h * 0.085)
    gap = int(w * 0.03)
    widths = [d.textbbox((0, 0), t, font=_font(size))[2] + int(w * 0.06)
              for t in labels]
    x = (w - sum(widths) - gap * (len(labels) - 1)) // 2
    cy = int(h * 0.93)
    for i, (t, bw) in enumerate(zip(labels, widths, strict=True)):
        active = selected is not None and i == selected
        d.rounded_rectangle([x, cy - box_h // 2, x + bw, cy + box_h // 2],
                            radius=4, outline=OCHRE if active else GREY)
        d.text((x + bw // 2, cy), t, font=_font(size),
               fill=CREAM if active else GREY, anchor="mm")
        x += bw + gap


def _status_circle(img, d, w, h, label, colour):
    """A smooth status ring, drawn 4x and downsampled: a 3px ellipse at
    panel size aliases into stair-steps. Sized so the label breathes."""
    r = int(h * 0.23)
    cx, cy = w // 2, int(h * 0.32)
    ss = 4
    ring = Image.new("L", (2 * r * ss, 2 * r * ss), 0)
    ImageDraw.Draw(ring).ellipse(
        [2 * ss, 2 * ss, 2 * r * ss - 2 * ss, 2 * r * ss - 2 * ss],
        outline=255, width=3 * ss)
    img.paste(colour, (cx - r, cy - r), ring.resize((2 * r, 2 * r),
                                                    Image.LANCZOS))
    _fit(d, (cx, cy), label, int(h * 0.075), colour, "mm", int(r * 1.6))


# Menu geometry, defined here because _frame centres the title in the
# space above the first row and every list screen lays out from it.
MENU_TOP = 0.16          # top edge of the first row's box, on every menu
MENU_BOTTOM = 0.95       # bottom edge the last row may not pass
MENU_PITCH = 0.135       # one row's height, fixed, so two rows sit together
MENU_ROWS = 6            # rows on screen at once; a longer list scrolls


def _frame(w, h, title=None):
    """The ground every screen is drawn on, and its title.

    The title is gold and there is no rule under it (Ben, 2026-09-05). The
    divider was doing the job the empty space now does, and doing it with
    a line meant the title sat squashed against the top of the panel.

    It is centred in the space ABOVE the first menu row, so the same
    heading lands in the same place whether the screen below it is a menu,
    a page of a key, or an address. One number decides it, MENU_TOP, which
    is the same number the rows are laid out from.
    """
    img = Image.new("RGB", (w, h), INK)
    d = ImageDraw.Draw(img)
    if title:
        _fit(d, (w // 2, int(h * MENU_TOP / 2)), title, int(h * 0.07),
             OCHRE, "mm", int(w * 0.92))
    return img, d


# SeedSigner's four: Scan, Seeds, Tools, Settings (Ben, 2026-09-04, map
# e2e-before-testers ticket 02). Key generation lives under Tools, where
# SeedSigner keeps "New seed".
# Tiles are jobs, not devices (Ben, 2026-09-05). The camera is a means:
# Sign uses it for a transaction, Keys for a key, Tools to check an
# address. Naming the first tile after the camera made it the place
# everything happened, and then no word fitted it.
#: Pixels of air between the letters of a tile title (Ben, 2026-09-06).
#: Bolder letters need it: at zero the stems of SETTINGS run together.
#: Looked at against 1.0, 1.5 and 2.0 at the shipped size; past 1.5 a
#: short word like SIGN stops reading as one word. Pixels and not ems
#: because both panels this build supports are 240 high, so the title is
#: 12px on either; a taller panel would want this scaled with the size.
TILE_TRACKING = 1.25

#: The four home tiles, as (name, icon). The names are identifiers, so
#: they stay lower case here and the screen upper-cases them when it
#: draws; tests/e2e_keys.py's home_press() looks a tile up by this name.
HOME_TILES = [("sign", "signature"), ("keys", "key"),
              ("tools", "tools"), ("settings", "gear")]


def home(w, h, selected=0, xfp=None):
    """SeedSigner-style 2x2 home: four tiles, each a Font Awesome icon and a
    title. Scan, Key, Tools, Settings (which holds power off). No CORESIGNER
    text. It is a KEY, not a wallet.

    `xfp` is the loaded wallet's master fingerprint, shown at the top in
    ochre (Ben, 2026-09-04). It is the one fact that tells you WHICH key is
    open, and a signer that cannot answer that invites signing with the
    wrong one. Absent when no key is loaded, so the header doubles as the
    "is anything open" indicator.
    """
    img = Image.new("RGB", (w, h), INK)
    d = ImageDraw.Draw(img)
    mx, my, gap = int(w * 0.06), int(h * 0.09), int(w * 0.04)
    if xfp:
        _fit(d, (w // 2, int(h * 0.055)), xfp.upper(), int(h * 0.062),
             OCHRE, "mm", int(w * 0.9), mono=True)
        my = int(h * 0.125)
    bw = (w - 2 * mx - gap) // 2
    # Bottom margin stays at the original 0.09 so the tiles keep their
    # footing; only the top grows to make room for the fingerprint.
    bh = (h - my - int(h * 0.09) - gap) // 2
    for i, (label, icon) in enumerate(HOME_TILES):
        r, c = divmod(i, 2)
        x = mx + c * (bw + gap)
        y = my + r * (bh + gap)
        active = i == selected
        # The active tile is a gold block with black on it; the others are
        # gold on the ground (Ben, 2026-09-05). Selection reads at a
        # glance from across a desk, which an outline does not.
        if active:
            _round_rect(img, [x, y, x + bw, y + bh], TILE_RADIUS, fill=OCHRE)
        else:
            d.rounded_rectangle([x, y, x + bw, y + bh], radius=TILE_RADIUS,
                                outline="#3A352E")
        # The SYMBOL is gold when the tile is not chosen; the WORD is the
        # same light cream as body text everywhere else, so the icon leads
        # the eye and the label reads as a label (Ben, 2026-09-06). The
        # chosen tile inverts wholesale: gold block, both in ink.
        mark = INK if active else OCHRE
        word = INK if active else CREAM
        _icon(d, x + bw // 2, y + int(bh * 0.36), int(bh * 0.44), icon, mark)
        # Upper case, like the fingerprint above them and every other
        # title on the device (Ben, 2026-09-06). Drawn through _fit so a
        # longer word than "SETTINGS" can never run out of its tile.
        _fit(d, (x + bw // 2, y + int(bh * 0.80)), label.upper(),
             int(h * 0.05), word, "mm", int(bw * 0.88), bold=True,
             tracking=TILE_TRACKING)
    return img


def settings_menu(w, h, selected=0):
    """Settings holds power off, and grows over time. No legend."""
    return _menu(w, h, "SETTINGS",
                 [(label, "", "normal") for label in SETTINGS_OPTIONS],
                 selected, icons=["power", "about"])


def about(w, h):
    img, d = _frame(w, h, "ABOUT")
    d.text((w // 2, int(h * 0.34)), "CORESIGNER", font=_font(int(h * 0.11)),
           fill=CREAM, anchor="mm")
    _fit_block(d, ["Core's keys, nothing kept",
                   "wallet brain: Bitcoin Core 31.1"],
               [(w // 2, int(h * 0.52)), (w // 2, int(h * 0.66))],
               int(h * 0.05), GREY, "mm", int(w * 0.9))
    _actions(d, w, h, ["BACK"], 0)
    return img


SETTINGS_OPTIONS = ["Power off", "About"]


def _quorum_text(quorum, cosigners, ours, timelocks=(),
                 spend_lock=None) -> str:
    """The one line that says what is being signed for, and for whom.

    `"2 of 3 · m/48h/1h/0h/2h"` for a quorum, the path alone for a
    single-sig spend, and `"MIXED QUORUMS · …"` when the inputs do not
    agree, which `signer.describe_psbt` reports rather than guessing a
    threshold from the first input.

    Empty when the transaction names no path at all, so a PSBT carrying
    no derivations draws today's screen and not a blank line.
    """
    # ONLY THIS DEVICE'S PATH. This used to fall back to the first
    # cosigner's when `ours` was not among them, which printed a
    # stranger's derivation under "whose wallet you are approving for"
    # (M1 decision 2) with nothing to say it was not yours. `_key_for`
    # lets a person pick any loaded key, so that case is reachable: the
    # signing then adds nothing and is refused, but the screen had
    # already said something untrue (two-axis review, 2026-09-10).
    path = next((p for xfp, p in cosigners if xfp == ours), "")
    if not ours and len({p for _, p in cosigners}) == 1:
        # No key chosen yet and every input derives from one place, so
        # there is only one path it could be.
        path = cosigners[0][1]
    if quorum == "mixed":
        head = "MIXED QUORUMS"
    elif isinstance(quorum, tuple):
        head = f"{quorum[0]} of {quorum[1]}"
    elif spend_lock is not None:
        # M6: the coordinator picks the tier with nSequence, and a
        # spend at a tier nobody asked for looks exactly like a normal one
        # without this line. The person cannot change it and can refuse
        # it, so this is the only place it can matter.
        head = f"AFTER {spend_lock} BLOCKS"
    elif timelocks:
        head = "TIMELOCKED"
    else:
        head = ""
    if head and path:
        return f"{head} · {path}"
    return head


def _cosigner_row(d, w, y, cosigners, ours, size, maxw):
    """Every fingerprint on the transaction, this device's in cream.

    M3 decision 3. The marking is COLOUR and not a prefix character,
    because a prefix costs width on a line that already carries three
    eight-character fingerprints, and width is the scarce thing on the
    240x240 panel. Drawn segment by segment so one of them can differ,
    with a real gap between segments: `test_screen_fit.collisions`
    counts a separator touching a fingerprint as two strings painted on
    each other, which on this screen it would be.

    Returns nothing. Draws nothing when there is one cosigner, because a
    single-sig spend has no quorum to name and the path line above
    already says whose wallet it is.
    """
    if len(cosigners) < 2:
        return
    marks = [x for x, _ in cosigners]
    gap = max(3, int(w * 0.018))
    while True:
        font = _mono(size)
        widths = [d.textbbox((0, 0), m, font=font)[2] for m in marks]
        sep_w = d.textbbox((0, 0), "·", font=font)[2]
        total = sum(widths) + (len(marks) - 1) * (sep_w + gap * 2)
        if total <= maxw or size <= MONO_FLOOR:
            break
        size -= 1
    if total > maxw:
        # A big quorum does not fit at any legible size, and drawing it
        # anyway put eight of a 15-key quorum off both edges of the panel
        # where they are simply not there (test_screen_fit, 2026-09-10).
        # A count is true and readable; a row that runs off the screen is
        # neither. `_fit` shortens the line further if even this is wide.
        rest = len(marks) - 1 if ours in marks else len(marks)
        said = f"{ours} · {rest} more keys" if ours in marks \
            else f"{rest} cosigners"
        _fit(d, (w // 2, y), said, size, GREY, "mm", maxw)
        return
    x = (w - total) // 2
    for i, (mark, mw) in enumerate(zip(marks, widths, strict=True)):
        d.text((x, y), mark, font=font, anchor="lm",
               fill=CREAM if ours and mark == ours else GREY)
        x += mw
        if i < len(marks) - 1:
            d.text((x + gap, y), "·", font=font, anchor="lm", fill=GREY)
            x += gap * 2 + sep_w


def review(w, h, outputs, fee_btc, input_total_btc=None,
           page=0, unseen_pages=False, actions_sel=1,
           quorum=None, cosigners=(), ours=None,
           timelocks=(), spend_lock=None):
    """The screen that matters. outputs: [(address, amount_btc), ...]
    Two outputs per page (Ben, 2026-09-01): less going on per frame.

    `fee_btc` must not be None. A PSBT whose fee Core could not compute is
    one this device refuses, and `main.state_review` refuses it before
    reaching here; passing None raises a TypeError out of the format
    string, which Session.HANDLED does not catch. That is deliberate and
    it is the safe direction: a fee this screen cannot state must never
    become a screen with a SIGN button on it. The coupling lived in
    main.py alone until 2026-09-08, the way find_unsigned's symlink
    argument lived in one file before audit A1."""
    # ONE output a page. Ben went from more to two on 2026-09-01 for
    # "less going on per frame", and asked on 2026-09-24 for the whole
    # address instead of `first 8 … last 4`. Those two pull against each
    # other: measured at the 13px floor, 40 characters fit across the
    # band, so a 42-character segwit address takes two rows and a
    # 62-character taproot one takes two. Two outputs is then six rows in
    # 91 pixels, with nothing between them. One output a page buys the
    # room, and it is the same direction the 2026-09-01 call was already
    # going.
    pages = max(1, len(outputs))
    title = ("REVIEW  TRANSACTION" if pages == 1
             else f"REVIEW  ·  OUTPUT {page + 1}/{pages}")
    # This is the screen you sign from, and it was the one scrollable
    # screen with no bar on it (two-axis review, 2026-09-05).
    img, d = _frame(w, h, title)
    left, right, gap = int(w * 0.06), int(w * 0.94), int(w * 0.03)
    for addr, amt in outputs[page:page + 1]:
        # The amount first and on its own line, because it is the number
        # a person checks before they read anything else.
        _row(d, left, right, int(h * 0.22), "TO", f"{amt:.8f} BTC",
             int(h * 0.05), int(h * 0.065), GREY, CREAM, gap,
             rhs_mono=True)
        # Then the address WHOLE, in the key face, grouped in fours and
        # coloured by the same rule the receive screen uses. Truncation
        # is what address-replacement malware is built to survive, so
        # this screen stopped doing it (Ben, board notes 2026-09-24).
        #
        # It is NOT centred in a fixed band. A 42-character segwit
        # address takes two rows and a 62-character taproot one takes
        # three on a 240-wide panel, so everything under it is placed
        # from the bottom the block reports rather than from a fraction
        # that is only right for one of them.
        bottom = _address_block(d, w, addr, int(h * 0.27), int(h * 0.52),
                                int(w * 0.88), centre=False)
    # M1 decision 2 and M3 decision 2, in the band between the outputs
    # and the fee. With arbitrary paths allowed, the path is the only
    # thing telling a wallet you set up from one you did not, and the
    # threshold is what says this signature finishes nothing.
    # Three things want this band: the quorum and path, the cosigners,
    # and the paging hint. M3 ruled on the collision before it happened:
    # "If they do not fit, the fingerprints are the line to move, not the
    # fee." The hint is what a person needs to finish reading the
    # transaction, so it keeps its place and the rows above it compress.
    #
    # The hint used to be dropped whenever this line drew, which deleted
    # it from EVERY paged review rather than only a multisig one, because
    # `main` passes cosigners for any PSBT carrying derivations
    # (two-axis review, 2026-09-10).
    line = _quorum_text(quorum, cosigners, ours, timelocks, spend_lock)
    if line:
        _fit(d, (w // 2, bottom + int(h * 0.045)), line, int(h * 0.042),
             CREAM if quorum != "mixed" else OCHRE, "mm", int(w * 0.92))
        _cosigner_row(d, w, bottom + int(h * 0.09), cosigners, ours,
                      int(h * 0.040), int(w * 0.92))
    # NO HINT LINE. "UP/DOWN · more outputs" was a second way of saying
    # what the scrollbar says, and Ben ruled on exactly this shape on the
    # corrections screen (2026-09-23: "just show the scroll bar if we
    # scroll"). One output a page needs the room for the address.
    #
    # The rule sits under whatever the band above actually used. M3:
    # "If they do not fit, the fingerprints are the line to move, not
    # the fee", and the fee still holds its place at the bottom.
    ky = max(int(h * 0.58),
             int(bottom + h * (0.125 if line else 0.045)))
    d.line([(left, ky), (right, ky)], fill=GREY, width=1)
    # The fee WAS h * 0.075, the largest type anywhere on the screen, so
    # the eye landed on the miner's cut before the amount being sent
    # (Ben, board notes 2026-09-24). It now matches the amount above it.
    _row(d, left, right, ky + int(h * 0.075), "FEE", f"{fee_btc:.8f} BTC",
         int(h * 0.05), int(h * 0.055), GREY, RED, gap, rhs_mono=True)
    if input_total_btc is not None:
        _fit(d, (right, ky + int(h * 0.17)),
             f"Inputs {input_total_btc:.8f} BTC",
             int(h * 0.045), GREY, "rm", right - left)
    if unseen_pages:
        _fit(d, (w // 2, int(h * 0.84)), "See every output before you sign",
             int(h * 0.042), OCHRE, "mm", int(w * 0.92))
    if pages > 1:
        scrollbar(d, w, int(h * 0.16), int(h * 0.62), page, pages)
    _actions(d, w, h, ["REJECT", "SIGN"], actions_sel)
    return img


def result(w, h, ok=True, detail="tx-a4f2-signed.psbt written",
           actions_sel=None, label=None, note=None):
    """The end of a signing run, and every other message the device parks.

    `label` names what happened. It defaulted to SIGNED for anything that
    went well, so writing a watch-only wallet file drew a large SIGNED
    over "coresigner-7b6e6f0e-watch.dat written" and nothing had been signed
    (Ben, on the board, 2026-09-05). Signing passes SIGNED; everything
    else says DONE.

    When actions_sel is given, the screen offers SIGN ANOTHER / HOME
    instead of ending the session. It said POWER OFF until 2026-09-24.

    `detail` may be empty: signing to a QR has no filename to name, and
    the frame count that used to fill the line said something about the
    rendering rather than about the transaction (Ben, board notes).
    """
    if label is None:
        label = "SIGNED" if ok else "FAILED"
    img, d = _frame(w, h)
    _status_circle(img, d, w, h, label, OCHRE if ok else RED)
    if detail:
        _fit(d, (w // 2, int(h * 0.68)), detail, int(h * 0.055), CREAM,
             "mm", int(w * 0.92))
    if note:
        # What the signature MEANS, under where it went. M3 traded
        # SIGNED-over-a-partial against the review screen stating the
        # threshold, and M6 found a whole class of wallet whose screen
        # cannot state one: Core types a decaying policy's witness script as
        # nonstandard. So the outcome is said here instead, where it is
        # true for every wallet shape.
        _fit(d, (w // 2, int(h * 0.79)), note, int(h * 0.045), OCHRE, "mm",
             int(w * 0.92))
    if actions_sel is not None:
        _actions(d, w, h, ["SIGN ANOTHER", "HOME"], actions_sel)
    return img


def busy(w, h, message="working…", phase=0):
    """The wait frame: the brand mark, turning while Core works. phase
    advances the rotation; the dev harness renders phase 0 only, the
    device animates from a thread (main._busy)."""
    img, d = _frame(w, h)
    r, ss = int(h * 0.16), 4
    cx, cy = w // 2, int(h * 0.34)
    arc = Image.new("L", (2 * r * ss, 2 * r * ss), 0)
    start = (phase * 45) % 360
    ImageDraw.Draw(arc).arc([2 * ss, 2 * ss, 2 * r * ss - 2 * ss,
                             2 * r * ss - 2 * ss],
                            start, start + 270, fill=255, width=3 * ss)
    img.paste(OCHRE, (cx - r, cy - r),
              arc.resize((2 * r, 2 * r), Image.LANCZOS))
    _fit(d, (w // 2, int(h * 0.72)), message, int(h * 0.055), CREAM, "mm",
         int(w * 0.92))
    return img


# PLAN A-22: the pure signer accepts only what Core itself understands. The
# codex32 and seed-word modes moved to the lab branch with the code that
# transformed them; nothing here converts anything.


CHANNEL_OPTIONS = [("Scan QR", "camera"), ("USB stick", "/mnt/usb")]


def channel_menu(w, h, selected=0):
    """Pick the channel a PSBT arrives on (Ben, 2026-09-04).

    It used to poll both at once behind one vague line, "insert stick or
    show QR". Neither channel then got a screen of its own, so the camera
    ran while you were fetching a stick and the scan had nowhere to show
    what it could see.
    """
    return _menu(w, h, "LOAD  TRANSACTION",
                 [(label, note, "normal") for label, note in CHANNEL_OPTIONS],
                 selected)


# The camera sits at 90 degrees to the panel on this build, and Ben chose to
# fill the screen rather than keep the edges of the view (hw/HARDWARE.md).
VIEWFINDER_ROTATE = 90
VIEWFINDER_FILL = True


def scanning(w, h, frame, message, progress=0.0):
    """Live camera view with a caption. THE viewfinder.

    Measured on the board, 2026-09-04: aiming with no view on screen got 1
    read in 120s; the same target with a viewfinder got 53 in 90s, and time
    to first decode fell from 35.3s to 8.3s. A scan screen that shows
    nothing is asking the operator to aim a lens they cannot see through.
    """
    if frame is None:
        return busy(w, h, message)
    img = Image.fromarray(frame, mode="L").convert("RGB")
    if VIEWFINDER_ROTATE:
        img = img.rotate(VIEWFINDER_ROTATE, expand=True)
    if VIEWFINDER_FILL:
        scale = max(w / img.width, h / img.height)
        img = img.resize((round(img.width * scale), round(img.height * scale)),
                         Image.NEAREST)
        left, top = (img.width - w) // 2, (img.height - h) // 2
        img = img.crop((left, top, left + w, top + h))
    else:
        img.thumbnail((w, h), Image.NEAREST)
        canvas = Image.new("RGB", (w, h), INK)
        canvas.paste(img, ((w - img.width) // 2, (h - img.height) // 2))
        img = canvas
    d = ImageDraw.Draw(img)
    bar = int(h * 0.13)
    d.rectangle([0, h - bar, w, h], fill=INK)
    _fit(d, (w // 2, h - bar // 2), message, int(h * 0.05), CREAM, "mm",
         int(w * 0.94))
    if progress > 0:
        # PsbtScan.progress is the UR decoder's own estimate, 0 to 1. A bar
        # the operator can act on: it says keep going, this is working.
        pct = max(0.0, min(progress, 1.0))
        d.rectangle([int(w * 0.05), 2, int(w * 0.95), int(h * 0.035)],
                    outline=GREY)
        d.rectangle([int(w * 0.05), 2,
                     int(w * 0.05) + int(w * 0.9 * pct), int(h * 0.035)],
                    fill=OCHRE)
        _fit(d, (w // 2, int(h * 0.075)), f"{pct * 100:.0f}%",
             int(h * 0.042), OCHRE, "mm", int(w * 0.5))
    return img


# Every list screen puts the top of its first row here, whatever it holds
# (Ben, 2026-09-05, from the board). The divider under the title sits at
# 0.11, and the old geometry put a four-row selection box at 0.063, so the
# highlight drew straight through the title and the line under it. Two
# menus had their own geometry as well and started at 0.34 and 0.36, so
# every menu began at a different height.


def scrollbar(d, w, top, track_h, position, total, visible=1):
    """Where you are in something longer than the screen, on the right edge.

    One shape in one place, because a rule that lives in four call sites
    drifts (Ben, 2026-09-05: "we need the scroll bar if you can scroll for
    any screen you can scroll"). `_menu` drew this and nothing else did, so
    the addresses screen gave no sign that DOWN showed more.

    `total` None means an endless list, which browsing receiving addresses
    is: the thumb is a fixed height, it moves, and it never reaches the
    bottom, because a bar that pretends to know the length of an endless
    list is a lie told in pixels.
    """
    d.rectangle([w - 4, top, w - 3, top + track_h], fill="#3A352E")
    # `not total` and not `total is None`: 0 divides by zero two lines
    # below, and a list of no items has no position to show anyway. No
    # caller passes 0 today, and this is a shared drawing helper, so the
    # question is what it does when one eventually does.
    if not total:
        bar_h = max(int(track_h * 0.18), 6)
        # Asymptotic: fills the top nine tenths of the track and stops.
        span = track_h - bar_h
        bar_y = top + int(span * (1 - 1 / (1 + position / 12)) * 0.9)
    else:
        bar_h = max(int(track_h * visible / total), 6)
        bar_y = top + int(track_h * position / total)
    d.rectangle([w - 5, bar_y, w - 2, min(bar_y + bar_h, top + track_h)],
                fill=OCHRE)


def _menu(w, h, title, rows, selected, icons=None):
    """One list screen for every menu: rows of (label, note, tone), tone
    "normal" or "red". `icons` optionally names one glyph per row.

    Rows have a FIXED height and start at MENU_TOP, so a two-row menu sits
    together at the top instead of being flung to the corners of the panel
    (Ben, on the board, 2026-09-05: "new key sits nicely below but then
    check for leaks is a long way below"). A list longer than MENU_ROWS
    scrolls with the cursor, and a bar on the right says where you are.
    """
    img, d = _frame(w, h, title)
    n = max(len(rows), 1)
    # The rows ON SCREEN set the pitch, not the length of the whole list.
    # Using n crammed a 34-row report into the top 70 pixels.
    pitch = min(MENU_PITCH, (MENU_BOTTOM - MENU_TOP) / min(n, MENU_ROWS))
    first_top = int(h * MENU_TOP)
    box_h = int(pitch * 0.82 * h)
    # The window follows the cursor and never moves further than it must.
    start = 0
    if n > MENU_ROWS:
        start = max(0, min(selected - MENU_ROWS + 2, n - MENU_ROWS))
        start = max(0, min(start, selected)) if selected >= 0 else 0
    visible = rows[start:start + MENU_ROWS]
    for i, (label, note, tone) in enumerate(visible):
        box_top = first_top + int(i * pitch * h)
        y = box_top + box_h // 2
        active = (start + i) == selected
        if active:
            d.rounded_rectangle([int(w * 0.04), box_top,
                                 int(w * 0.96), box_top + box_h],
                                radius=4, outline=OCHRE)
        # Three tones. "red" paints the LABEL, for an action that destroys
        # something. "leak" paints the STATE on the right, because there the
        # thing is innocent and what it is doing is the alarm (Ben,
        # 2026-09-05). Both columns are the same size: the right one was
        # smaller and unreadable on the panel.
        # The selected row's label is gold, like the selected tile and the
        # export card's edge, so one colour means "this one" everywhere
        # (Ben, 2026-09-05).
        colour = OCHRE if active else GREY
        note_colour = CREAM if active else GREY
        if tone == "red":
            colour = RED if not active else "#D9433B"
        elif tone == "leak":
            note_colour = "#D9433B" if active else RED
        x = int(w * 0.08)
        if icons:
            _icon(d, int(w * 0.11), y, int(h * 0.055), icons[start + i], colour)
            x = int(w * 0.20)
        size = int(h * 0.052)
        _fit(d, (x, y), label, size, colour, "lm",
             int(w * 0.52) - (x - int(w * 0.08)))
        _fit(d, (int(w * 0.92), y), note, size, note_colour, "rm",
             int(w * 0.36))
    if n > MENU_ROWS:
        scrollbar(d, w, first_top, int(MENU_ROWS * pitch * h),
                  start, n, MENU_ROWS)
    return img


# SeedSigner's per-seed menu, in Core's words (ticket 07).
# Four things that belong to a key. Sign transaction left on 2026-09-05:
# the Sign tile does it, from the camera or a stick, and a second door to
# the same room only made you choose which door.
#: BACKUP IS FIRST, so it is also what the cursor lands on when the menu
#: opens (Ben, 2026-09-11). A key Core has just made exists in one place,
#: a ramdisk, and the 111 characters on paper are the only other copy. A
#: menu that led with "Export public key" invited a person to hand a
#: coordinator an address, fund it, and only then find out what a backup
#: is. The most consequential row is the first row.
#:
#: Each row carries what it MEANS, because `main.state_key_menu` used to
#: dispatch on `selected == 0`, which is TESTING.md rule 11's two lists
#: with nothing joining them: this reorder would have moved Export onto
#: the backup handler.
KEY_MENU_OPTIONS = [
    ("Backup key", "on paper", "backup"),
    ("Export public key", "for a coordinator", "export"),
    ("Receiving addresses", "Core derives", "addresses"),
    ("Discard key", "Core forgets it", "discard"),
]

#: The cosigner rows on the export menu, under the four policies. M1
#: decision 3: named rows at the top, the typed path one level down
#: inside Advanced, which is Coldcard's structure with the capability
#: they do not have. Nobody types m/48'/0'/0'/2', so nobody mistypes it.
#: Multisig is its own place, not a fifth policy with a jargon name
#: (Ben, 2026-09-11). "Cosigner (P2WSH)" sat in the list beside Native
#: segwit and Taproot and read as a different KIND of thing, which it is:
#: those four are for a wallet you sign alone, and this is for a wallet
#: where you are one of several. Behind this row the words are the same
#: ones again, Native segwit and Nested segwit, because inside multisig
#: the question really is the script type.
MULTISIG_KIND = "multisig"
MULTISIG_ROW = "Multisig…"

#: What one cosigner export can be. The first two are BIP48's script
#: step, 2h for P2WSH and 1h for P2SH-P2WSH, named in the words the rest
#: of the device uses rather than in the numbers.
MS_WSH, MS_SHWSH, MS_ACCOUNT, MS_TYPED = "wsh", "sh-wsh", "account", "typed"


#: OFF FOR THE PILOT (Ben, 2026-09-16). A typed path reaches anywhere,
#: including a blinded one, and that is the thing Core Signer can do that
#: most hardware wallets cannot. It is also the one way a tester loses
#: coins through OUR design rather than their mistake: the 111-character
#: paper backup recovers the standard paths by convention and cannot
#: recover a path nobody can guess. The coordinator holds that record,
#: and a tester who loses it has nothing left.
#:
#: The map wrote this down and never answered it: "blinding breaks the
#: property that the paper key alone recovers the wallet ... needs its
#: own thinking about what Core Signer then owes the user." M11 shipped
#: the row anyway. This closes it for the pilot rather than pretending
#: the question was settled.
#:
#: The code stays whole. `_export_typed_path` and its checksum echo are
#: reached by nothing while this is False, and turning it back on is one
#: word plus the warning that question still owes.
TYPED_PATH = False


def multisig_rows(wsh_path, shwsh_path, account):
    """The rows MULTISIG draws, with the thing each one means."""
    rows = [("Native segwit", wsh_path, MS_WSH),
            ("Nested segwit", shwsh_path, MS_SHWSH),
            ("Account number", str(account), MS_ACCOUNT)]
    if TYPED_PATH:
        rows.append(("Type a path…", "", MS_TYPED))
    return rows


def multisig_menu(w, h, rows, selected=0):
    return _menu(w, h, "MULTISIG",
                 [(label, note, "normal") for label, note, _k in rows],
                 selected)


def script_rows(kinds, multisig=False):
    """The rows SCRIPT TYPE draws, with the thing each one MEANS.

    ONE list. `script_menu` draws it and `main._export` dispatches on it,
    so the two cannot drift, which is TESTING.md rule 11: "a menu is two
    lists, and nothing joins them". That rule exists because the BACKUP
    menu drew "On paper" and ran the file backup. This screen had the
    same shape for a day: `main` built its own row list and `script_menu`
    appended the cosigner rows under a condition of its own.
    """
    rows = [(SCRIPT_LABELS[k], "", k) for k in kinds]
    if multisig:
        rows.append((MULTISIG_ROW, "one key of several", MULTISIG_KIND))
    return rows


def script_menu(w, h, kinds, selected=0, multisig=False):
    """Which script policy to export. Chosen FIRST, before the QR.

    Ben, 2026-09-05: "if exporting, you should have chosen this first."
    SeedSigner asks the same question in the same place; what it also asks,
    and we do not, is which coordinator, because all five read the same
    plain descriptor (map R3).

    `kinds` is what this key HAS, which is all four for a key Core made or
    a key imported since D6.
    """
    rows = [(label, note, "normal")
            for label, note, _kind in script_rows(kinds, multisig)]
    return _menu(w, h, "SCRIPT  TYPE", rows, selected)


#: How many accounts the chooser offers. A person with more than this
#: many separate quorums on one key types the path instead.
ACCOUNTS = 10


def account_menu(w, h, selected=0):
    """Which account the named rows derive at. `m/48'/coin'/ACCOUNT'/...`"""
    return _menu(w, h, "ACCOUNT  NUMBER",
                 [(str(i), "", "normal") for i in range(ACCOUNTS)], selected)


def path_echo(w, h, path, checksum, selected=0):
    """What Core made of a typed path, before anything leaves.

    M2 decided this echo and why it is a CHECKSUM and not a test address:
    a cosigner branch derives a single-sig address, not the quorum's, so
    an address here would be a number a coordinator never shows. Core's
    8-character descriptor checksum changes if the path or the
    fingerprint changes, and a corrupt xpub is refused outright because
    base58 carries its own.

    Blinding is exactly where a typo is unrecoverable: get it wrong and
    the coordinator watches a wallet nobody can spend from.
    """
    img, d = _frame(w, h, "CHECK  THE  PATH")
    _fit(d, (w // 2, int(h * 0.30)), path, int(h * 0.065), CREAM, "mm",
         int(w * 0.92), mono=True)
    _fit(d, (w // 2, int(h * 0.48)), checksum.upper(), int(h * 0.10),
         OCHRE, "mm", int(w * 0.9), mono=True)
    _fit_block(d, ["Core made this checksum.",
                   "It changes if the path does."],
               [(w // 2, int(h * 0.64)), (w // 2, int(h * 0.73))],
               int(h * 0.045), GREY, "mm", int(w * 0.92))
    _actions(d, w, h, ["BACK", "EXPORT"], selected)
    return img


#: How a cosigner record leaves. Two rows, not the three the single-sig
#: export offers: M2 ruled out typing it, and Core reads no file and no
#: QR anyway, so its half of this is a person copying a string.
COSIGNER_OPTIONS = [
    ("QR code", "scan it in"),
    ("File", "stick or card"),
]


def cosigner_options(w, h, selected=0):
    """QR or file, and nothing else."""
    return _menu(w, h, "HOW  IT  LEAVES",
                 [(label, note, "normal") for label, note in COSIGNER_OPTIONS],
                 selected)

# HOW the key leaves, asked after the script type and before anything is
# shown (Ben, 2026-09-05: "choose usb or QR to export after choosing
# type"). This is not the coordinator chooser that was cut: that one asked
# a question with the same answer four times out of five, and this one
# picks between photons, your fingers, and a file.
#: TWO QR rows, and they carry different text. Coordinators do not agree
#: on one payload, and pretending they do is what E-4 got wrong: measured
#: on 2026-09-29, BlueWallet reads one of our four descriptors and Bitcoin
#: Keeper's thirteen vendor adapters read none of them. Three of Keeper's
#: adapters read the account key, so does Sparrow, and so does the one
#: BlueWallet path that works.
#:
#: The rows name the FORMAT and not the wallet. Naming wallets is a claim
#: about software we do not control and cannot keep true, which is the
#: shape of the E-4 mistake. A format is a fact about the bytes we send.
EXPORT_OPTIONS = [
    ("QR, descriptor", "says which addresses"),
    ("QR, account key", "the wallet decides"),
    ("Text to type", ""),
    ("Wallet file", "for Bitcoin Core"),
]


def export_options(w, h, selected=0):
    return _menu(w, h, "EXPORT  AS",
                 [(label, note, "normal") for label, note in EXPORT_OPTIONS],
                 selected)


TOOLS_OPTIONS = [("Check for leaks", ""),
                 ("Check an address", "")]


def leak_report(w, h, rows, cursor=0):
    """What image/leak-check.sh found, one thing and its state per row.

    The device may be the only place this can be read: a hardened board has
    no SSH. Leaks come first, because they are what you opened this for.
    It is the same list screen as every other menu, so the d-pad scrolls it
    and A, B or C leaves, and there is no action bar inventing a second way
    to do the same thing (Ben, 2026-09-05).
    """
    leaks = sum(1 for _label, _state, tone in rows if tone == "leak")
    title = "LEAK  CHECK"
    if rows:
        title = (f"LEAK  CHECK  ·  {leaks} OF {len(rows)}" if leaks
                 else f"LEAK  CHECK  ·  ALL {len(rows)} CLEAR")
    return _menu(w, h, title, rows, cursor)


# What the KEYS screen offers below the loaded keys, flat (Ben,
# 2026-09-05: "so we don't have to nest"). New key first, then every way
# to bring an existing one in. The LOAD A KEY screen it replaces held four
# rows, and one of them was always the one you wanted.
# No notes. Each row is a plain thing you can do, and a three-word note
# beside it either repeated the row or tried to explain a screen that
# explains itself properly a press later (Ben, 2026-09-05).
#
# Type descriptor is gone: a descriptor lives as a printed QR, not as
# words on steel, so Scan a key covers it, and typing 111 characters on a
# five-way pad to avoid holding up a card is not a trade anyone makes.
# Typing a private key stays because it is the only way back from paper.
#
# The rows say "private key", not "xprv" (Ben, 2026-09-05: "I want to use
# correct terms instead of xprv because people won't know what that is").
# They also do NOT say "seed", which he asked about and which would be
# wrong here: a seed in Bitcoin means the 12 or 24 words, or the bytes
# they expand into, and Core Signer cannot take words at all. Calling this a
# seed would send a reader looking for words to write down, and would
# suggest another wallet could restore it from words. It cannot. What
# Core Signer holds is the master private key itself, which in a worded wallet
# is what the seed PRODUCES. "Private key" is both true and the phrase
# people already know. The exact token, xprv, belongs in the README.
KEYS_ACTIONS = [
    ("New key", ""),
    ("Scan a key", ""),
    ("Type private key", ""),
]


def keys_menu(w, h, keys, selected=0):
    """The loaded keys by fingerprint, then what you can do about keys.

    One screen, one title, whether or not a key is loaded. It used to jump
    straight past this into a differently titled LOAD A KEY when the device
    held nothing, so the same button gave you two different screens.
    `keys` are (wallet name, fingerprint) pairs in slot order.
    """
    rows = [((xfp or "????????").upper(), "loaded", "normal")
            for _n, xfp in keys]
    rows += [(label, note, "normal") for label, note in KEYS_ACTIONS]
    return _menu(w, h, "KEYS", rows, selected)


def key_menu(w, h, xfp, selected=0):
    """What one key can do. The title names it by fingerprint."""
    rows = [(label, note, "red" if kind == "discard" else "normal")
            for label, note, kind in KEY_MENU_OPTIONS]
    return _menu(w, h, f"KEY  {(xfp or '').upper()}", rows, selected)


def tools_menu(w, h, selected=0):
    return _menu(w, h, "TOOLS",
                 [(label, note, "normal") for label, note in TOOLS_OPTIONS],
                 selected)


# Core's four policies in the words their wallets use. "Legacy" and
# "Nested segwit" are what Sparrow, BlueWallet and Core's own GUI call
# them, so the panel uses them too rather than saying pkh and sh(wpkh).
SCRIPT_LABELS = {"wpkh": "Native segwit", "tr": "Taproot",
                 "sh": "Nested segwit", "pkh": "Legacy",
                 # Not a policy this key HAS, so `script_menu` never
                 # draws it: `available_kinds` returns the four above and
                 # the cosigner row is appended separately. It is here so
                 # the export QR can label itself with the same line the
                 # single-sig one uses.
                 MULTISIG_KIND: "Cosigner"}


def shown_path(path):
    """A derivation path in the notation the wallet beside you uses.

    BIP32 wrote hardened steps with an apostrophe. Core emits `h`,
    because an apostrophe is painful in a shell, and accepts either on
    input. Both are correct, and Sparrow draws `m/84'/0'/0'` while we
    drew `m/84h/0h/0h`, which left the person comparing the two screens
    to do the translation (Ben, on the board, 2026-09-18).

    CAPTIONS ONLY. A descriptor carries a checksum computed over its
    exact characters, so reshaping one invalidates it: `export_text`
    draws what Core said, unchanged, because a person types that string
    back in. This is for the line of identity beside a code, which
    nothing parses.
    """
    return path.replace("h", "'")


def _ordinal(n):
    """`1` as `1st`, the way a person says it out loud."""
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def _groups(text):
    return [text[i:i + 4] for i in range(0, len(text), 4)]


def _group_fill(index, total):
    """What colour the `index`th group of four is drawn in.

    The ends are gold, because they are where a person starts and stops
    when comparing against a coordinator. Every other group in between
    steps down to CREAM_DIM, which gives the eye a rhythm to count along
    without adding a rule or a gap. Both screens that paint a long string
    of characters call this, so neither can invent its own scheme.

    The ends being marked never means the ends are enough: address
    replacement malware matches exactly those. docs/TESTER-PACK.md says
    so where a tester reads it.
    """
    if index in (0, total - 1):
        return OCHRE
    return CREAM_DIM if index % 2 else CREAM


def _address_block(d, w, text, top, floor, maxw, start=None, centre=True):
    """Draw `text` WHOLE, in groups of four, centred between top and floor.

    Two screens show an address a person compares against a coordinator,
    and both must show every character: truncation is the one thing
    address-replacement malware is built to survive. So the shape is
    chosen rather than fixed. Every row split is tried, and the one that
    buys the largest type inside the band wins. A 42-character segwit
    address and a 62-character taproot one then both fill the band they
    are given instead of one of them rattling around in it.

    `centre` centres the rows in the band. A review screen sets it
    False and reads the returned bottom, because what follows the
    address there has to start under however many rows this took.

    Returns the y the block ENDS at, so a caller can flow the next thing
    beneath it instead of assuming a height.
    """
    groups = _groups(text)
    best = None
    for per_row in range(1, len(groups) + 1):
        rows = [groups[i:i + per_row]
                for i in range(0, len(groups), per_row)]
        widest = max(len(" ".join(r)) for r in rows)
        # WIDTH IS THE HARD ONE. A row wider than the band is painted off
        # the side of the panel, where it is simply not there; too many
        # rows only crowds what follows. So a split that cannot fit the
        # width at the floor is not a candidate at all, and the height is
        # what gives (test_screen_fit, 98 strings off a 240x240 panel).
        if d.textlength("W" * widest, font=_mono(MONO_FLOOR)) > maxw:
            continue
        size = start or int(floor - top)
        while size > MONO_FLOOR:
            if (d.textlength("W" * widest, font=_mono(size)) <= maxw
                    and len(rows) * size * LINE_SPACING <= floor - top):
                break
            size -= 1
        if best is None or (size, -len(rows)) > (best[0], -len(best[1])):
            best = (size, rows, per_row)
    size, rows, per_row = best
    font = _mono(size)
    step = size * LINE_SPACING
    space = d.textlength(" ", font=font)
    y0 = (top + ((floor - top) - len(rows) * step) / 2 + step / 2
          if centre else top + step / 2)
    # One row is measured whole, then drawn group by group at that
    # spacing, so each group carries its colour IN PLACE. Colouring
    # anywhere else would not help the eye track the comparison.
    for r, row in enumerate(rows):
        y = y0 + r * step
        x = (w - d.textlength(" ".join(row), font=font)) / 2
        for g, group in enumerate(row):
            d.text((x, y), group, font=font,
                   fill=_group_fill(r * per_row + g, len(groups)),
                   anchor="lm")
            x += d.textlength(group, font=font) + space
    return y0 + (len(rows) - 1) * step + size / 2


#: Row pitch as a multiple of the type size, on the address screen. The
#: font's own line height is tighter than this and runs the rows of a
#: taproot address together; 1.45 keeps them apart at every size the
#: chooser below picks.
LINE_SPACING = 1.45

#: How many corrections the list screen shows at once. `main` scrolls by
#: this, so the bar and the loop cannot disagree about how far there is
#: to go.
CORRECTIONS_SHOWN = 4


#: The QR is rendered no taller than this, so the light card and the line
#: of identity underneath both have room on a 240-pixel panel.
#:
#: Module scaling is integer, so this is a step rather than a slider. At
#: 190 every policy lands one step down: the 57-module codes render 159px
#: and nested segwit's 61 modules render 171px, leaving a card of about
#: 180px and forty-odd pixels for the line. At 212 the codes are a quarter
#: larger and there is no room for anything.
#:
#: The cost is real and worth stating: a smaller code on the panel means
#: holding the phone closer. Sparrow's own zxing reads all four at this
#: size, but a lens is not a decoder.
QR_MAX_PX = 190

#: The card's corner radius and the width of the gold stroke around it.
CARD_RADIUS = 8
STROKE = 2

#: The home tiles' corner radius.
TILE_RADIUS = 6


#: The white margin a code gets on top of the two modules `qrchannel`
#: renders. A QR wants four empty modules and the card supplies the rest.
#: Ten pixels is what a 212px animated frame needs to reach 4.7 modules,
#: which is the size the outbound PSBT frames are drawn at.
QR_CARD_PAD = 10


def _qr_card(w, h, code, reserve=0):
    """Draw one QR on the device's ground and return (img, d, bottom).

    **The light card is not decoration.** A QR needs a quiet zone of four
    empty modules, and while the panel was white the letterbox WAS the
    quiet zone and it was effectively infinite. On a dark ground that
    stops being true: `qrchannel` renders only two modules of border, so
    the card has to make up the rest or the code becomes harder to read
    rather than prettier.

    `reserve` is height kept clear at the bottom for a caption. Pass 0
    and the card centres in the whole panel.

    `code` is the rendered QR, already scaled. It is not resized here,
    because resizing a QR by anything but an integer factor turns square
    modules into soft ones.
    """
    img, d = _frame(w, h, None)
    pad = max(QR_CARD_PAD, int(code.width * 0.06))
    card_w, card_h = code.width + 2 * pad, code.height + 2 * pad
    # REFUSE, because `img.paste` below crops in silence and a cropped QR
    # still looks like a QR. This guard came from qrchannel.fit_to_panel,
    # which the signing path used until this card replaced it (I-1, and
    # again on text_to_image 2026-09-08). Nothing may lose it in the move.
    if card_w + 2 * STROKE > w or card_h + 2 * STROKE > h:
        raise ValueError(
            f"a {code.width}x{code.height} QR needs a "
            f"{card_w + 2 * STROKE}x{card_h + 2 * STROKE} card and the "
            f"panel is {w}x{h}; size the code against the card's budget")
    top = max(STROKE, (h - card_h - reserve) // 2)
    x0 = (w - card_w) // 2
    # The gold sits OUTSIDE the card: a larger rounded rectangle behind a
    # smaller white one, rather than an outline drawn on the card's own
    # edge, which would eat pixels the quiet zone is holding.
    _round_rect(img, [x0 - STROKE, top - STROKE,
                      x0 + card_w + STROKE, top + card_h + STROKE],
                CARD_RADIUS + STROKE, fill=OCHRE)
    _round_rect(img, [x0, top, x0 + card_w, top + card_h],
                CARD_RADIUS, fill="white")
    img.paste(code, (x0 + pad, top + pad))
    return img, d, top + card_h + STROKE


def qr_export(w, h, code, xfp, kind, path, mask=None, masks=None):
    """The export QR, with its identity underneath (Ben, 2026-09-05).

    The fingerprint, the script type and the derivation path run in one
    line below the card, so a coordinator can be checked rather than
    trusted.

    `mask` says the cycling is STOPPED and this one render is being held.
    The screen has to say so, because a frozen code and a cycling one
    look identical in a photograph and the whole point of freezing is to
    find out which of the two a scanner is choking on. It names the mask
    so a reader that works on one and not another can be reported as
    "mask 5 of 8" rather than as "sometimes".
    """
    img, d, below = _qr_card(w, h, code, reserve=int(h * 0.11))
    line = (f"{xfp.upper()}  ·  {SCRIPT_LABELS[kind].upper()}  ·  "
            f"{shown_path(path)}")
    # Centred between the outside of the stroke and the bottom of the
    # screen, which is what Ben asked for and what stops the line looking
    # tacked onto the card.
    if mask is None:
        _fit(d, (w // 2, (below + h) // 2), line,
             int(h * 0.045), CREAM, "mm", int(w * 0.94))
    else:
        _fit(d, (w // 2, below + int(h * 0.035)), line,
             int(h * 0.040), CREAM, "mm", int(w * 0.94))
        _fit(d, (w // 2, below + int(h * 0.082)),
             f"HELD  ·  MASK {mask + 1}/{masks}  ·  LEFT RIGHT to step",
             int(h * 0.040), OCHRE, "mm", int(w * 0.94))
    return img


def qr_frame(w, h, code):
    """One frame of the outbound PSBT animation, on the same card.

    It was a bare QR letterboxed on a WHITE panel until 2026-09-08, so
    the screen a signed transaction leaves by looked like nothing else on
    the device, and the one screen that DID have the card was the export
    (Ben spotted it in the demo recording).

    The size does not change. Sized against the card's own budget, an
    outbound frame is 212px on both panels, which is what the white
    letterbox gave it, and the pad brings the quiet zone to 4.7 modules
    against the four the spec asks for. So this is the dark ground and
    the gold edge for nothing: M1's optics are the gate that is not
    passed, and a prettier screen must not cost a coordinator's camera a
    single module.

    No caption. A caption would need height, and height is the thing the
    code is using.
    """
    return _qr_card(w, h, code)[0]


def address_page(w, h, index, address, kind, total=None, switchable=False):
    """One receive address, in full, for comparison against a coordinator.

    Ben's rule: never truncate, group in fours, colour the first and last
    group differently from the middle.

    The title says WHICH address, in the words a person says out loud:
    "1st receive address", then 2nd, then 3rd. It read
    `RECEIVE  0  ·  NATIVE SEGWIT`, which counted from zero and repeated
    the policy chosen two screens before (Ben, on the board, 2026-09-18).

    The footer said "compare every group" and it is gone at Ben's word.
    It was there because matching only the two coloured ends is the
    shortcut address-replacement malware relies on, so that reasoning
    moves to docs/TESTER-PACK.md, where a tester reads it once and can
    act on it. The colouring stays: it marks the ends, and never says
    the ends are enough.

    `switchable` says LEFT and RIGHT change the policy on this screen,
    which only the endless browse does. The name stays there, because it
    is the only thing that shows what those two presses did.
    """
    img, d = _frame(w, h, f"{_ordinal(index + 1).upper()}  RECEIVE  ADDRESS")
    # The height to give it came from dropping the footer (E-7 item 5).
    # _address_block picks the row split that buys the largest type in
    # that band, so a 42-character segwit address and a 62-character
    # taproot one each fill it.
    _address_block(d, w, address, h * 0.24,
                   h * (0.86 if switchable else 0.95), w * 0.92,
                   start=int(h * 0.12))
    if switchable:
        _fit(d, (w // 2, int(h * 0.92)), SCRIPT_LABELS[kind].upper(),
             int(h * 0.05), GREY, "mm", int(w * 0.7))
    # Two kinds of bar, which is D3's whole point. Browsing receiving
    # addresses has no end, so the thumb moves and never arrives. The
    # three at the end of an export DO have an end, and drawing them the
    # endless way told the user the list went on when it did not (found
    # by the two-axis review, 2026-09-05).
    scrollbar(d, w, int(h * 0.16), int(h * 0.62), index, total)
    return img


def export_text(w, h, chunk, page=0, pages=1, title="PUBLIC  KEY"):
    """One screenful of the descriptor, in four-character groups, for
    someone typing it into a coordinator by hand. Public: no blanking."""
    head = title if pages == 1 else f"{title}  ·  PART  {page + 1}/{pages}"
    img, d = _frame(w, h, head)
    _fit(d, (w // 2, int(h * 0.855)),
         "Your public key and where it sits. No private key here",
         int(h * 0.042), GREY, "mm", int(w * 0.94))
    groups = _groups(chunk)
    for row_start in range(0, len(groups), GROUPS_PER_ROW):
        y = int(h * (0.26 + (row_start // GROUPS_PER_ROW) * 0.13))
        _fit(d, (w // 2, y),
             "  ".join(groups[row_start:row_start + GROUPS_PER_ROW]),
             int(h * 0.075), CREAM, "mm", int(w * 0.92), mono=True)
    if pages > 1:
        scrollbar(d, w, int(h * 0.16), int(h * 0.62), page, pages)
    _actions(d, w, h, ["BACK", "NEXT" if page + 1 < pages else "DONE"], 1)
    return img


# CONTEXT.md calls these channels: how bytes cross the air gap. QR is the
# third; only the two file channels can carry a file.
FILE_CHANNELS = {"stick": ("USB stick", "/mnt/usb"),
                 "card": ("boot card", "/boot/firmware")}


def choose_channel(w, h, channels, selected=0):
    """Which file channel a file goes to. Asked every time (ticket 04)."""
    return _menu(w, h, "WRITE  IT  WHERE",
                 [(FILE_CHANNELS[c][0], FILE_CHANNELS[c][1], "normal")
                  for c in channels], selected)


def confirm_discard(w, h, xfp, selected=0):
    """Discard asks first. BACK is pre-selected; DISCARD must be chosen."""
    img, d = _frame(w, h, "DISCARD  KEY")
    _fit(d, (w // 2, int(h * 0.32)), (xfp or "").upper(), int(h * 0.11),
         CREAM, "mm", int(w * 0.9), mono=True)
    _fit_block(d, ["Core forgets this key now.",
                   "Your backup is the only copy."],
               [(w // 2, int(h * 0.55)), (w // 2, int(h * 0.65))],
               int(h * 0.05), GREY, "mm", int(w * 0.92))
    _actions(d, w, h, ["BACK", "DISCARD"], selected)
    return img


def choose_key(w, h, keys, owners, selected=0):
    """Which key signs (map e2e-before-testers, ticket 03). Shown only when
    more than one key is loaded. `keys` are (wallet name, fingerprint)
    pairs in slot order; `owners` are the fingerprints Core found on the
    transaction's inputs. The owner is pre-selected by the caller; a key
    that owns nothing is greyed, because Core will not complete it."""
    img, d = _frame(w, h, "WHICH  KEY")
    top, bottom = 0.18, 0.80
    pitch = (bottom - top) / max(len(keys) - 1, 1)
    for i, (_name, xfp) in enumerate(keys):
        y = int(h * (top + i * pitch))
        owns = xfp in owners
        if i == selected:
            d.rounded_rectangle([int(w * 0.04), y - int(h * min(pitch, 0.2) * 0.46),
                                 int(w * 0.96), y + int(h * min(pitch, 0.2) * 0.46)],
                                radius=4, outline=OCHRE)
        _fit(d, (int(w * 0.08), y), (xfp or "????????").upper(),
             int(h * 0.065), CREAM if owns else GREY, "lm",
             int(w * 0.50), mono=True)
        _fit(d, (int(w * 0.92), y), "owns the inputs" if owns else "not this one",
             int(h * 0.04), OCHRE if owns else GREY, "rm", int(w * 0.38))
    _actions(d, w, h, ["BACK", "SIGN WITH IT"], 1)
    return img


def keymaterial_warning(w, h, kind="descriptor", selected=1):
    """Shown before accepting an xprv or descriptor (A-14): the QR IS the
    wallet — no passphrase layer protects it."""
    img, d = _frame(w, h, f"SCAN  {kind.upper()}")
    d.text((w // 2, int(h * 0.30)), "This QR IS the wallet.",
           font=_font(int(h * 0.075)), fill=RED, anchor="mm")
    lines = ["There is no passphrase layer on a raw " + kind + ".",
             "Anyone holding this code holds the funds.",
             "Scan it in private."]
    _fit_block(d, lines,
               [(w // 2, int(h * (0.46 + i * 0.09))) for i in range(len(lines))],
               int(h * 0.05), CREAM, "mm", int(w * 0.94))
    _actions(d, w, h, ["BACK", "SCAN"], selected)
    return img


#: LETTERS FIRST, digits after them. base58's own order puts the digits
#: first, which made the top row of the lowercase grid `1..9abcd` and
#: the top row of the capitals `A..N`, so pressing C changed the whole
#: shape of what you were reading. Letters first means both grids open
#: with the same letters in the same places, and only the tail moves
#: (Ben, on the board, 2026-09-18). Nothing here encodes or decodes
#: base58; this string is the keyboard and its order is a layout.
BASE58 = ("abcdefghijkmnopqrstuvwxyz123456789"
          "ABCDEFGHJKLMNPQRSTUVWXYZ")                      # 58: no 0, O, I, l
DESCRIPTOR_CHARSET = BASE58 + "0()[]'/*#hl"                 # 70


# A passphrase alphabet went with the file backup: the only thing this
# device asks you to type now is a key, and a key is base58 (PLAN A-24).
#: Everything a derivation path is made of and nothing else. A typed
#: path is the only route to a blinded xpub (M1 decision 3), and it is
#: also where a typo is unrecoverable, so the grid offers no character
#: that cannot appear in one.
PATH_CHARSET = "0123456789h'/"

CHARSETS = {"xprv": BASE58, "descriptor": DESCRIPTOR_CHARSET,
            "path": PATH_CHARSET}

#: MODES, not pages (map typing, T2). SeedSigner refuses to page a
#: charset at all: its keyboard raises if one will not fit, and the
#: page-turn key it ships is used by no screen. Ben hit the same wall
#: from the other side, walking off an edge to find the capitals with
#: nothing saying they were there.
#:
#: base58 splits 34 and 24. TWELVE columns by THREE rows holds 36, and
#: the third row is what pays for the typed text to be shown as the
#: numbered boxes the paper backup uses. Measured against the pocket
#: panel's budget: two rows of boxes and four grid rows come to 0.995 of
#: the height with no margin at all; three grid rows come to 0.870.
#:
#: Cells go from 26 pixels wide to 18 on a 240 panel. They are pointed
#: at with a cursor, never touched, so what matters is that the glyph is
#: legible and not that the cell is thumb-sized.
GRID_COLS = 13
GRID_ROWS = 3

#: Two keys ON the grid that move the caret instead of typing. This is
#: SeedSigner's answer (map typing, T1): its cursor-left, cursor-right
#: and backspace are keys on the keyboard's last row, reached by
#: navigating to them, so there is no mode to discover and no button to
#: learn. Ours needs only the two arrows, because B already deletes.
#:
#: They are why the grid is 13 columns and not 12: base58's lowercase is
#: 34 and a descriptor's is 36, and both need room for these beside them.
#: ASCII, because the panel font has no arrow glyphs and drew them as
#: empty boxes. Neither character appears in any charset, so neither can
#: be mistaken for one a person meant to type.
CARET_LEFT, CARET_RIGHT = "<", ">"
CARET_KEYS = (CARET_LEFT, CARET_RIGHT)

def modes(name):
    """The mode list for a charset: (button label, characters) pairs.

    DERIVED, never typed beside the charset. A hand-written mode list is
    the two-lists defect of TESTING.md rule 11 in its other form: add a
    character to a charset and one of the two lists forgets it.

    A charset that fits one grid gets ONE mode and no button, because a
    mode button that never changes anything is a control to learn for
    nothing. Only when it does not fit is it split, and then by the
    thing a person already knows about characters: case, then symbols.
    """
    # DEDUPED, order kept. DESCRIPTOR_CHARSET is BASE58 + "0()[]'/*#hl",
    # and base58's lowercase already holds an h, so the grid has been
    # drawing that character twice since it was written. Two cells that
    # type the same thing gives no choice and wastes a cell, on the one
    # screen with none to spare.
    chars = "".join(dict.fromkeys(CHARSETS[name]))
    if len(chars) <= GRID_COLS * GRID_ROWS:
        return [("", chars)]
    lower = "".join(c for c in chars if not c.isupper() and not _is_symbol(c))
    upper = "".join(c for c in chars if c.isupper())
    symbol = "".join(c for c in chars if _is_symbol(c))
    out = [("abc", lower), ("ABC", upper)]
    if symbol:
        out.append(("#/*", symbol))
    return [(label, run) for label, run in out if run]


def _is_symbol(c):
    return not c.isalnum()


def mode_cells(chars):
    """One mode's cells: its characters, then the two caret keys."""
    return list(chars) + list(CARET_KEYS)


def mode_grid(chars):
    """One mode's cells as rows, for drawing and for navigation."""
    cells = mode_cells(chars)
    return ["".join(cells[i:i + GRID_COLS])
            for i in range(0, len(cells), GRID_COLS)]


#: How the KEY is laid out, on paper and now on the typing screen too.
#: 4-character groups, three to a row, four rows to a page: 48 characters
#: a page and 28 boxes for a 111-character key.
GROUPS_PER_ROW = 3
ROWS_PER_PAGE = 4

#: The entry screen puts TWO boxes on a row where the paper page puts
#: three. The number moved to the left of its box at the size of the
#: characters (Ben, on the board, 2026-09-18), and a number that size
#: costs width no third box can pay. Nothing is lost by it: the box
#: NUMBER is what names a place on either screen, and box 8 is box 8 on
#: both whatever shape the rows are.
ENTRY_GROUPS_PER_ROW = 2
#: Three rows of two, so the same 24 characters stay in view as the
#: three-across layout showed, at nearly twice the size.
ENTRY_ROWS_SHOWN = 3
CHARS_PER_PAGE = GROUPS_PER_ROW * ROWS_PER_PAGE * 4


def text_entry(w, h, title, text, cursor=0, charset="xprv", mode=0,
               secret=False, actions_sel=None, caret=None, hint=None,
               actions=("CANCEL", "DONE"), wrong=(), want_len=None,
               box_numbers=None):
    """Typing, shown the way the backup is shown: numbered boxes of four.

    The old screen drew a flat echo line with a caret while the backup
    page drew numbered 4-character groups, and a person checking one
    against the other did the mapping in their head. Ben, on the board,
    2026-09-18: "the chars written should be like the ones shown ... show
    a number and then the box with 4 characters, so we can easily
    navigate it." Box 8 is now a thing that can be found.

    `caret` is the position being typed or edited. `wrong` holds the
    positions that do not match the paper, drawn with a red outline, so
    the screen says WHICH to fix rather than how many.

    THE BOXES ARE NUMBERED ONCE, 1 to 28 for a 111-character key, and
    the screen scrolls rather than paging. It used to page 48
    characters at a time and number every screenful from 1, so 12 was
    the highest number the device ever drew and boxes 13 to 28 did not
    appear to exist: "I STILL can not enter more than the 12th box"
    (Ben, on the board, 2026-09-19, the second of three reports).

    `box_numbers` is what the boxes on this screenful are CALLED, and
    they need not run on: the recheck shows boxes 7, 14 and 22 together
    because those are the three a person has to read off their paper.
    None means 1, 2, 3 and so on, which is every other screen. A box 7
    drawn as box 1 sends somebody to the wrong place on their paper,
    which is the defect this numbering exists to prevent.

    `mode` indexes `modes(charset)`. One mode at a time, never a paged
    alphabet: see that function for why.
    """
    img, d = _frame(w, h, title)
    runs = modes(charset)
    label, chars = runs[mode % len(runs)]
    rows = mode_grid(chars)

    # --- the typed text, as numbered boxes -----------------------------
    # ONE EMPTY BOX AHEAD. A box appeared when its first character was
    # typed, so box 2 did not exist until you had already committed to
    # it, and the screen gave no sign there was more to type (Ben, on
    # the board, 2026-09-18). Where the length is known, that answers
    # it instead, and every box of the page is drawn from the start.
    width = want_len or (max(len(text), caret or 0) // 4 + 1) * 4
    padded = text + " " * max(0, width - len(text))
    groups = _groups(padded) or [""]
    here = (caret if caret is not None else max(0, len(text) - 1)) // 4
    row_of = here // ENTRY_GROUPS_PER_ROW
    total_rows = -(-len(groups) // ENTRY_GROUPS_PER_ROW)
    shown = min(ENTRY_ROWS_SHOWN, total_rows)
    first = max(0, min(row_of - 1 if row_of else 0,
                       max(0, total_rows - shown)))
    slot = int(w * 0.094)
    num_w = int(w * 0.075)
    gap = int(w * 0.022)
    pitch = num_w + slot * 4 + gap
    left = (w - (pitch * ENTRY_GROUPS_PER_ROW - gap)) // 2
    y = int(h * 0.19)
    for r in range(first, first + shown):
        x = left
        for g in range(r * ENTRY_GROUPS_PER_ROW,
                       min((r + 1) * ENTRY_GROUPS_PER_ROW, len(groups))):
            number = box_numbers[g] if box_numbers else g + 1
            _box(d, x + num_w, y, groups[g], number, w, h, secret,
                 caret, wrong, g * 4, slot, g == here)
            x += pitch
        y += int(h * 0.125)

    # The box numbers give the position, but only the bar says there is
    # more below, and this panel has taught that lesson twice already.
    if total_rows > shown:
        scrollbar(d, w, int(h * 0.155), int(h * 0.34), first,
                  total_rows, shown)

    # --- the character grid --------------------------------------------
    # The mode and the hint share one line, where they were two. The row
    # of boxes above needed the height and reads at arm's length; these
    # two are read once.
    if label or hint:
        _row(d, int(w * 0.05), int(w * 0.95), int(h * 0.535),
             label, hint or "", int(h * 0.042), int(h * 0.042),
             OCHRE, GREY, int(w * 0.04))
    cell_w, cell_h = w // (GRID_COLS + 1), int(h * 0.105)
    x0, y0 = (w - GRID_COLS * cell_w) // 2, int(h * 0.60)
    for r, run in enumerate(rows):
        for c, ch in enumerate(run):
            i = r * GRID_COLS + c
            gx = x0 + c * cell_w + cell_w // 2
            gy = y0 + r * cell_h
            if i == cursor:
                # FILLED while the grid has focus, an outline while the
                # buttons do. Exactly one thing on the screen is filled
                # gold at a time, which is what makes DOWN to the bar
                # and UP back again visible at all.
                box = [gx - cell_w // 2 + 1, gy - cell_h // 2 + 1,
                       gx + cell_w // 2 - 1, gy + cell_h // 2 - 1]
                if actions_sel is None:
                    d.rectangle(box, fill=OCHRE)
                else:
                    d.rectangle(box, outline=OCHRE, width=1)
            here = i == cursor and actions_sel is None
            _fit(d, (gx, gy), ch, max(MONO_FLOOR, int(h * 0.05)),
                 INK if here else CREAM, "mm", cell_w, mono=True)
    _actions(d, w, h, list(actions), actions_sel)
    return img


def _box(d, x, y, group, number, w, h, secret, caret, wrong, index,
         slot, active):
    """One numbered 4-character box, with its number beside it.

    The number sits to the LEFT of the box at the size of the characters
    (Ben, on the board, 2026-09-18). It was small grey type above the
    box, where it collided with the row above and could not be read from
    the distance the characters are read from. "Box 8, word 3" is how a
    person says where they are, so both halves of that have to carry.

    The box holding the caret is outlined in gold. A two-pixel underline
    was the only mark of where you were, and finding it took a hunt.

    Both the characters and the number are drawn in the key face. The
    number is not key material, but it is read as part of the box, and a
    proportional digit beside a monospaced group sits at the wrong pitch.
    """
    size = int(h * 0.072)
    bw, bh = slot * 4, int(h * 0.105)
    edge = OCHRE if active else GREY
    _fit(d, (x - int(w * 0.018), y), str(number), size, edge, "rm",
         int(w * 0.06), mono=True)
    d.rectangle([x, y - bh // 2, x + bw, y + bh // 2],
                outline=edge, width=2 if active else 1)
    for i, ch in enumerate(group):
        cx = x + slot * (i + 0.5)
        pos = index + i
        if pos in wrong:
            # ON the border, top and bottom, at the caret's thickness
            # (Ben, 2026-09-19: "I want the red around incorrect ones
            # to be like our underscore where it shows over the border
            # and the same thickness"). Drawn inside the box it was a
            # second rectangle competing with the first for interior
            # height the characters want.
            for edge in (y - bh // 2, y + bh // 2):
                d.rectangle([int(cx - slot / 2) + 2, edge - 1,
                             int(cx + slot / 2) - 2, edge + 1], fill=RED)
        if caret is not None and pos == caret:
            # ON the border, not above it. A gold bar inside the box
            # took interior height the bigger type now wants, and two
            # marks stacked in one box read as crammed (Ben, on the
            # board, 2026-09-18). Drawn in ink, it cuts a notch in the
            # gold edge instead, and costs the characters nothing.
            d.rectangle([int(cx - slot / 2) + 3, y + bh // 2 - 1,
                         int(cx + slot / 2) - 3, y + bh // 2 + 1],
                        fill=INK)
            # Drawn AFTER the red, so the notch reads on a slot that is
            # both wrong and where you are standing.
        _fit(d, (cx, y), "*" if secret and ch != " " else ch, size,
             CREAM if ch != " " else GREY, "mm", int(slot * 0.9), mono=True)

def wrong_character(w, h, box, char, typed, want):
    """The screen that stops you carrying on with a wrong character.

    It fires when somebody tries to move FORWARD past one, never when
    they fix it in place, so a slipped thumb they catch themselves
    costs nothing (map correction, C1).

    Both characters, big, because telling them apart is the whole job:
    the person is holding the paper and decides in a glance whether
    they mistyped or wrote it down wrong. The device never guesses
    which, and never needs to.
    """
    img, d = _frame(w, h, f"BOX  {box}  ·  CHARACTER  {char}")
    size = int(h * 0.11)
    left, right = int(w * 0.08), int(w * 0.72)
    for y, label, ch, fill in ((0.34, "You typed", typed, RED),
                               (0.50, "The key is", want, OCHRE)):
        _fit(d, (left, int(h * y)), label, int(h * 0.055), GREY, "lm",
             int(w * 0.55))
        d.rectangle([right - int(w * 0.02), int(h * y) - size // 2 - 3,
                     right + size + int(w * 0.02), int(h * y) + size // 2 + 3],
                    outline=fill, width=2)
        _fit(d, (right + size // 2, int(h * y)), ch, size, fill, "mm",
             size * 2, mono=True)
    _fit_block(d, [f"Write {want} on your paper at box {box},",
                   f"character {char}. I will fix it here."],
               [(w // 2, int(h * 0.70)), (w // 2, int(h * 0.78))],
               int(h * 0.05), CREAM, "mm", int(w * 0.92))
    _actions(d, w, h, ["OK"], 0)
    return img


def corrections(w, h, made, xfp, page=0, first=0, actions_sel=1):
    """What the check says when the paper and the key disagreed.

    `made` is every place a character was corrected, as
    (box, character, what it should be). Counted per CHARACTER and not
    per box, because that is what the interruption named and what the
    person wrote on their paper.

    THE LIST SAYS WHAT TO WRITE. It named the place and not the
    character, so the one person it exists for, somebody who did not
    write the corrections down as they went, was told where to look and
    not what to put there (Ben, 2026-09-23: "the 7 corrections don't
    show what the char was and what it should be. Why? I asked for
    that."). What they typed wrong is gone and does not matter; what it
    should be is the whole point.

    TWO SCREENS. The claim and the warning need room to be read, and a
    list of corrections underneath them squeezed the warning to grey
    type half the size of everything else (measured while prototyping,
    map correction C4). The warning is the part that matters, so it
    gets the first screen whole, and DOWN shows the places.

    The list is a safety net for somebody who did not write the
    corrections down as they were told to, one at a time, on the way
    through. Every claim here is one the device verified; the thing it
    cannot verify, that the paper was actually corrected, is a warning
    and never an assertion (map correction, C2).
    """
    n = len(made)
    plural = "s" if n != 1 else ""
    img, d = _frame(w, h, f"{n}  CORRECTION{plural.upper()}")
    if page == 0:
        # Ben's words, 2026-09-24: "This key with your 1 correction
        # opens the key with the fingerprint GE8942A1". It said "opens
        # D2B7E45C.", which names the thing without saying what the
        # name IS, and a person who has never met a fingerprint reads
        # eight characters of nothing.
        FINGERPRINT = 2                         # the line drawn in the key face
        lines = [f"This key, with your {n} correction{plural},",
                 "opens the key with the fingerprint",
                 "",
                 "",
                 "You may want to check it one more time.",
                 "",
                 f"If you did not write {'those' if n != 1 else 'that'} "
                 f"{n} down and test",
                 "them, you may not be able to recover",
                 "this key or its funds."]
        ys = [(w // 2, int(h * (0.20 + i * 0.068)))
              for i in range(len(lines))]
        size = _fit_block(d, lines, ys, int(h * 0.050), CREAM, "mm",
                          int(w * 0.92))
        _fit(d, ys[FINGERPRINT], f"{xfp.upper()}.", size, OCHRE, "mm",
             int(w * 0.92), mono=True)
        # NO HINT LINE. "DOWN for the 3 places" was a second way of
        # saying what the bar says, in gold, on a screen already full of
        # warning (Ben, 2026-09-23: "just show the scroll bar if we
        # scroll"). Every other paged screen on this device uses the
        # bar alone, and E-1 exists because they did not.
        scrollbar(d, w, int(h * 0.20), int(h * 0.60), 0, 2)
    else:
        rows = [f"Box {b}, character {c} is {ch}" for b, c, ch in made]
        shown = rows[first:first + CORRECTIONS_SHOWN]
        # In the key face whole. The row is a box number, a character
        # number and the character to write, and the character is the
        # payload: a row that says `k` where the key wants `K` sends
        # somebody to a wallet that is not theirs.
        _fit_block(d, shown,
                   [(w // 2, int(h * (0.30 + i * 0.11)))
                    for i in range(len(shown))],
                   int(h * 0.06), OCHRE, "mm", int(w * 0.86), mono=True)
        _fit(d, (w // 2, int(h * 0.80)), "Write these on your paper.",
             int(h * 0.045), GREY, "mm", int(w * 0.92))
        # The bar covers BOTH axes: which of the two screens you are on,
        # and how far down the list. One track, because two would be two
        # things to read on a screen whose job is to be read once.
        rows_beyond = max(len(rows) - CORRECTIONS_SHOWN, 0)
        scrollbar(d, w, int(h * 0.20), int(h * 0.60), 1 + first,
                  2 + rows_beyond)
    # BOTH WAYS ON. It offered DONE alone, which is a screen that tells
    # you three things are broken and then gives you one button, the
    # same shape as the VERIFY label that did nothing (Ben, 2026-09-23).
    # RECHECK is pre-selected for the reason CHECK IT is on the backup
    # page: the safe choice belongs under the thumb.
    _actions(d, w, h, ["EXIT", "RECHECK"], actions_sel)
    return img


def text_pages(text):
    """Split a backup string into screenfuls, in order, losing nothing.

    Core's
    master private key is 111 characters, which is three screenfuls.
    Drawing it as
    one column ran the last third off the bottom of the panel, so the user was
    asked to transcribe characters that never rendered.
    """
    return [text[i:i + CHARS_PER_PAGE]
            for i in range(0, max(len(text), 1), CHARS_PER_PAGE)]


def splash(w, h, build=None, dev=()):
    """The first frame the device paints, before bitcoind is up.

    Two tones only (ink ground, cream text) and every string measured, so
    it survives a 1-bit render and both panel sizes. It used to stencil a
    mark from a brand PNG; it draws type only now, and the file went with
    the rest of the branding on 2026-09-07.

    IT SAYS WHAT THE CARD IS. `dev` holds the reasons this one is a
    development card, live ones read off the running system and not
    markers a script left behind. A card with SSH and a radio up is the
    card docs/TESTER-PACK.md warns about, and until now the only way to
    find that out was to remember to run a script.

    Coinkite's reason, for a device with a real bootloader and a signed
    image: "Development firmware gets a warning and forced delay on
    every boot. It cannot quietly pass as factory firmware." We have no
    bootloader to enforce anything, so this is the weaker version: the
    screen says so, and `splash.py` holds it there.

    `build` is what provision.sh recorded the card was built from. It is
    drawn whatever the card is, so two devices can be told apart and a
    tester can quote it in a report.
    """
    img, d = _frame(w, h)
    cx = w // 2
    _fit(d, (cx, int(h * 0.26)), "BITCOIN BUTLERS",
         int(h * 0.070), OCHRE, "mm", int(w * 0.90))
    _fit(d, (cx, int(h * 0.37)), "presents",
         int(h * 0.045), GREY, "mm", int(w * 0.90))
    _fit(d, (cx, int(h * 0.55)), "CORE SIGNER", int(h * 0.14), CREAM, "mm",
         int(w * 0.92))
    if dev:
        _fit(d, (cx, int(h * 0.76)), "DEVELOPMENT CARD", int(h * 0.055),
             RED, "mm", int(w * 0.92))
        _fit(d, (cx, int(h * 0.84)), ", ".join(dev), int(h * 0.042),
             RED, "mm", int(w * 0.92))
    if build:
        _fit(d, (cx, int(h * 0.93)), build, int(h * 0.040), GREY, "mm",
             int(w * 0.92))
    return img

# ---- backup display -------------------------------------------------
# These were named codex32_* because codex32 shares were the first
# thing paged across the panel. They are generic: A-22 keeps them for
# Core's master private key, which is the pure signer's only backup.


def backup_page(w, h, chunk, label, page=0, pages=1, actions_sel=1):
    """One screenful of the key, on its way to paper.

    `chunk` is already one page's worth (see text_pages). `label` names the
    key, "KEY  D2B7E45C", so the paper says which key it opens.
    """
    title = f"{label}  ·  PRIVATE  KEY"
    if pages > 1:
        title = f"{label}  ·  PART  {page + 1}/{pages}"
    img, d = _frame(w, h, title)
    groups = _groups(chunk)
    # NUMBERED, 1 to 28 across the whole key, exactly as the typing
    # screen numbers them (map correction, C7). Two things close at
    # once: a skipped character leaves a box with three in it, seen
    # while writing rather than days later, and "box 7, character 3"
    # becomes a glance instead of counting seven groups along your own
    # handwriting. Until now the paper carried no box numbers at all,
    # so every message the device sent about a box was addressed to a
    # numbering nobody could see.
    first = page * CHARS_PER_PAGE // 4 + 1
    size = int(h * 0.068)
    num_w = int(w * 0.055)
    for row_start in range(0, len(groups), GROUPS_PER_ROW):
        y = int(h * (0.26 + (row_start // GROUPS_PER_ROW) * 0.13))
        row = groups[row_start:row_start + GROUPS_PER_ROW]
        cell = int(w * 0.92) // GROUPS_PER_ROW
        x0 = (w - cell * len(row)) // 2
        for i, group in enumerate(row):
            x = x0 + i * cell
            _fit(d, (x + num_w, y), str(first + row_start + i), size,
                 GREY, "rm", num_w, mono=True)
            _fit(d, (x + num_w + (cell - num_w) // 2, y), group, size,
                 CREAM, "mm", cell - num_w - int(w * 0.01), mono=True)
    if pages > 1:
        _fit(d, (w // 2, int(h * 0.79)),
             f"Characters {page * CHARS_PER_PAGE + 1}"
             f"-{page * CHARS_PER_PAGE + len(chunk)} of the key",
             int(h * 0.045), OCHRE, "mm", int(w * 0.92))
    else:
        _fit(d, (w // 2, int(h * 0.79)),
             "Write this down. It opens the wallet",
             int(h * 0.045), OCHRE, "mm", int(w * 0.92))
    if pages > 1:
        scrollbar(d, w, int(h * 0.16), int(h * 0.62), page, pages)
    if page + 1 < pages:
        _actions(d, w, h, ["ABORT" if page == 0 else "BACK", "NEXT"], 1)
    else:
        # The last page is where the writing is finished, so both ways on
        # are real choices and the bar is live. CHECK IT is pre-selected
        # and DONE is one press LEFT (Ben, 2026-09-19: the safe choice
        # belongs under the thumb, and leaving without checking is the
        # risky one). It was the other way round, and the default here
        # stayed at DONE for four days after the caller stopped using
        # it: only main.py's `sel = 1` was holding the rule up (two-axis
        # review, 2026-09-23).
        _actions(d, w, h, ["DONE", "CHECK IT"], actions_sel)
    return img
def verified(w, h, kind="ok", note=None):
    """`kind` may carry newlines; each line is fitted separately.

    `note` is the small print under the verdict: what this screen did
    NOT check. The backup check prefills the 16 characters every Core
    master key on a network begins with, so those four boxes are never
    compared against the paper, and "Your paper opens key X" was a
    stronger claim than the device had earned (two-axis review,
    2026-09-23, against map correction C2: "Every claim on it is one the
    device actually verified").
    """
    img, d = _frame(w, h)
    _status_circle(img, d, w, h, "VALID", OCHRE)
    for i, line in enumerate(kind.split("\n")):
        _fit(d, (w // 2, int(h * (0.62 + i * 0.065))), line,
             int(h * 0.06), CREAM, "mm", int(w * 0.92))
    if note:
        _fit_block(d, note.split("\n"),
                   [(w // 2, int(h * (0.80 + i * 0.058)))
                    for i in range(len(note.split("\n")))],
                   int(h * 0.040), GREY, "mm", int(w * 0.92))
    _actions(d, w, h, ["DONE"], 0)
    return img
