"""The vendored mono face must keep the loop on `k`, at the sizes we draw.

Ben could not tell `k` from `K` on the board. In the prose face they are
one shape at two heights, so a 17px panel offers nothing to separate
them. The mono face carries a LOOPED k: the arm closes into a circle on
the stem, and the capital stays two straight diagonals.

A loop is a hole, so this suite counts holes rather than trusting an eye.
Of 48 faces measured on 2026-09-25 I twice recommended one whose `k` has
no loop at all, because a curved arm reads like a loop and is not one.
The count cannot be talked into it.

The floor is 13px. At 12px the counter fills and the loop is gone, so no
screen may draw a key character smaller than that. The suite checks the
sizes the screens actually ask for, not a range somebody hoped was right.

Rule 2: the character list here is derived from the source, not imported
from tools/make_mono_font.py. If a screen gains a character the tool does
not cut, these two disagree and this suite fails.

Run: python3 tests/test_mono_font.py
"""
import re
import sys
from collections import deque
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT = ROOT / "hw" / "vendor" / "fonts" / "iosevka-mono-subset.ttf"
SOURCE = [ROOT / "coresigner" / "screens.py", ROOT / "coresigner" / "main.py"]

#: Smallest type the mono face may be drawn at. Below this the loop fills.
FLOOR = 13
#: Largest the device asks for, `wrong_character` at h * 0.10 on a 240px panel.
CEILING = 28

fails = []


def ok(m):
    print("ok  ", m)


def bad(m):
    print("FAIL", m)
    fails.append(m)


def counters(font_path, size, char):
    """How many enclosed holes the drawn character has.

    Renders it, floods the background in from the border, and counts the
    background regions the flood could not reach. A looped k leaves one.
    A straight k leaves none. This is deliberately ignorant of outlines
    and contours: it asks what the PANEL shows, at the size the panel
    shows it, after antialiasing has been forced to black or white.
    """
    face = ImageFont.truetype(str(font_path), size)
    canvas = Image.new("L", (size * 4, size * 4), 0)
    draw = ImageDraw.Draw(canvas)
    left, top, right, bottom = draw.textbbox((0, 0), char, font=face)
    draw.text((size * 2 - (right - left) // 2 - left,
               size * 2 - (bottom - top) // 2 - top),
              char, font=face, fill=255)
    box = canvas.getbbox()
    if box is None:
        return -1
    crop = canvas.crop((box[0] - 2, box[1] - 2, box[2] + 2, box[3] + 2))
    px = crop.load()
    w, h = crop.size
    ink = [[px[x, y] > 127 for x in range(w)] for y in range(h)]
    seen = [[False] * w for _ in range(h)]
    queue = deque()

    def push(x, y):
        if not ink[y][x] and not seen[y][x]:
            seen[y][x] = True
            queue.append((x, y))

    def drain():
        while queue:
            x, y = queue.popleft()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    push(nx, ny)

    for x in range(w):
        push(x, 0)
        push(x, h - 1)
    for y in range(h):
        push(0, y)
        push(w - 1, y)
    drain()
    holes = 0
    for y in range(h):
        for x in range(w):
            if not ink[y][x] and not seen[y][x]:
                holes += 1
                push(x, y)
                drain()
    return holes


def drawn_characters():
    """Every character the screens can paint, read out of the source."""
    found = set()
    for path in SOURCE:
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r'"([^"\\\n]*)"|\'([^\'\\\n]*)\'', text):
            found |= set(match.group(1) or match.group(2) or "")
    # The charsets are built by concatenation, so the literals above miss
    # nothing, but say the alphabet out loud anyway: a key is typed from it.
    found |= set("abcdefghijkmnopqrstuvwxyz123456789"
                 "ABCDEFGHJKLMNPQRSTUVWXYZ0()[]'/*#hl")
    return {c for c in found if c.isprintable()}


def main():
    if not FONT.exists():
        bad(f"{FONT} is missing. Build it: python3 tools/make_mono_font.py")
        return 1

    # 1. The loop is there, at every size a key character is drawn at.
    for size in range(FLOOR, CEILING + 1):
        holes = counters(FONT, size, "k")
        if holes == 1:
            ok(f"k has its loop at {size}px")
        else:
            bad(f"k has {holes} holes at {size}px, want 1. The loop is gone.")

    # 2. The capital must NOT have one, or the loop separates nothing.
    for size in range(FLOOR, CEILING + 1):
        holes = counters(FONT, size, "K")
        if holes == 0:
            ok(f"K stays open at {size}px")
        else:
            bad(f"K has {holes} holes at {size}px. It reads as a lowercase k.")

    # 3. Below the floor the loop fills. Assert that, so nobody "fixes" a
    #    layout by shrinking a key character until it lies.
    holes = counters(FONT, FLOOR - 1, "k")
    if holes == 0:
        ok(f"the loop is gone at {FLOOR - 1}px, which is why {FLOOR} is the floor")
    else:
        bad(f"k still has {holes} holes at {FLOOR - 1}px. FLOOR is wrong.")

    # 4. Coverage. A character the font lacks draws as .notdef, which on a
    #    key screen is a character silently changed. Asked through Pillow
    #    rather than the font tables, because Pillow is what the device
    #    draws with, and it is the only library the signer has.
    face = ImageFont.truetype(str(FONT), 17)
    absent = bytes(face.getmask(""))      # private use, cut by subset
    missing = []
    for char in sorted(drawn_characters()):
        if char == " ":
            continue                            # blank by design, not absent
        if bytes(face.getmask(char)) == absent:
            missing.append(char)
    if missing:
        bad(f"the face cannot draw {len(missing)}: {''.join(missing)!r}. "
            f"Rebuild with tools/make_mono_font.py")
    else:
        ok(f"every one of {len(drawn_characters())} drawn characters is cut")

    print()
    print(f"{len(fails)} failed" if fails else "all good")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
