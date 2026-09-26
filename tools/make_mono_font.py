"""Build the vendored mono face, the one the device draws KEY CHARACTERS in.

Why a second face at all. Every character a person copies onto paper or
compares against a coordinator is read one character at a time, and the
pair that defeated Ben on the board was `k` against `K`: in the prose
face they are the same shape at two heights, so a 17px panel gives you
nothing to tell them apart. A LOOPED lowercase k solves it, because the
arm closes into a circle on the stem and the capital stays two straight
diagonals. Of 48 open-licence faces measured, four had a closed loop and
three of those lost it at panel size (2026-09-25).

Iosevka has it, in an alternate. Its default `k` is straight, and
`k.cv46-21` is the plain looped one: no serifs, so nothing spends pixels
at 17px. This tool swaps that glyph into the `k` slot and subsets the
result, which is why the font is built here rather than downloaded.

The test that proves the output is right lives in tests/test_mono_font.py:
it counts the enclosed counter in `k` at every size the device draws, and
checks coverage against the characters the source actually paints. That
test derives its own character list from the source, so it disagrees with
CHARSET below the moment a screen gains a character (TESTING.md rule 2).

USAGE
    python3 tools/make_mono_font.py path/to/IosevkaCurly-SemiBold.ttf

Get that file from the pinned upstream release named in image/PINS. The
tool refuses any other file, because a font nobody verified is a font
that can draw a `6` where the key says `b`.
"""

import hashlib
import sys
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools import subset

#: The upstream file this face is cut from. Iosevka v34.8.1, the TTF out of
#: PkgTTF-IosevkaCurly-34.8.1.zip. image/PINS carries the same digest, so a
#: rebuild that reaches a different file stops here and not on the panel.
SOURCE_NAME = "IosevkaCurly-SemiBold.ttf"
SOURCE_SHA256 = "cb0fad8ef725e3232911eb90cfb1a3d704937ef56b7f8cb43b02b8432d1ea1eb"

#: The looped lowercase k. Iosevka ships 40 alternates for this one letter
#: and eight of them close into a loop; cv46-21 is the plain one, with no
#: serif on the stem or the leg.
LOOPED_K = "k.cv46-21"

#: Every character the device can paint, as one string. Printable ASCII
#: covers base58, the descriptor charset and every label; `·` separates
#: fingerprints on a cosigner row and `…` shortens a line that will not fit.
CHARSET = "".join(chr(c) for c in range(0x20, 0x7F)) + "·…"

OUT = Path(__file__).resolve().parent.parent / "hw" / "vendor" / "fonts" / "iosevka-mono-subset.ttf"


def build(source, out=OUT):
    """Swap in the looped k, subset, and write the face the device loads."""
    digest = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    if digest != SOURCE_SHA256:
        raise SystemExit(
            f"{source} is sha256 {digest}\n"
            f"expected {SOURCE_SHA256} ({SOURCE_NAME}, see image/PINS)")

    font = TTFont(source)
    if LOOPED_K not in font.getGlyphOrder():
        raise SystemExit(f"{source} has no glyph {LOOPED_K}")
    # Point U+006B at the looped alternate. Only the cmap moves: the glyph
    # itself is Iosevka's, untouched, so this is a choice between shapes
    # the release already carries and not a new drawing.
    for table in font["cmap"].tables:
        if 0x6B in table.cmap:
            table.cmap[0x6B] = LOOPED_K
    swapped = Path(out).with_suffix(".swapped.ttf")
    font.save(swapped)
    font.close()

    # Drop the layout tables with the rest. The device draws one character
    # at a time into a box, so kerning and the 99 alternate sets are weight
    # the Pi carries for nothing.
    subset.main([str(swapped), f"--output-file={out}", f"--text={CHARSET}",
                 "--layout-features=", "--no-hinting", "--desubroutinize"])
    swapped.unlink()
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__.strip().splitlines()[-4].strip())
    written = build(sys.argv[1])
    print(f"{written}  {written.stat().st_size} bytes")
