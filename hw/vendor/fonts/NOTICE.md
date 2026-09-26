# Vendored icon font

`fa-solid-subset.ttf` is a seven-glyph subset of Font Awesome 5 Free Solid.

- Icons: Font Awesome Free, licensed CC BY 4.0
  (https://fontawesome.com/license/free). Attribution: Font Awesome by
  Fonticons, Inc. (https://fontawesome.com).
- The font file format is licensed SIL OFL 1.1.

Glyphs included and where Core Signer uses them:

| codepoint | name               | Core Signer use             |
|-----------|--------------------|-----------------------|
| U+F5B7    | signature          | home: sign            |
| U+F084    | key                | home: key             |
| U+F7D9    | tools              | home: tools           |
| U+F013    | cog                | home: settings        |
| U+F011    | power-off          | settings: power off   |
| U+F05A    | info-circle        | settings: about       |
| U+F019    | download           | (kept; no screen yet) |

Rebuilt 2026-09-05, twice: the second time to swap the qrcode glyph
(U+F029) for signature (U+F5B7), because a QR code is how the sign
tile WORKS and not what it DOES (Ben, 2026-09-05). Built with fonttools (`python3 -m fontTools.subset`) from the
`fa-solid-900.ttf` that ships inside the sha256-verified Sparrow 2.5.4
release (`tests/sparrow/.build/ext/com.sparrowwallet.sparrow/font/`), so the
source file is one that is already verified for this repo. No other glyphs
are present.


# Vendored key face

`iosevka-mono-subset.ttf` is Iosevka Curly SemiBold, cut to the 89
characters this device paints, with one of its glyphs moved.

- Iosevka, licensed SIL OFL 1.1 (https://github.com/be5invis/Iosevka).
  Copyright Renzhi Li (aka Belleve Invis).
- Source release, hash and the build command are pinned in `image/PINS`
  as `IOSEVKA_*`. Rebuild with `python3 tools/make_mono_font.py <ttf>`,
  which refuses a file whose sha256 does not match the pin.

**The lowercase `k` is `k.cv46-21`, moved into the `k` slot.** Iosevka
ships 40 alternates for that letter and eight of them close the arm into
a loop; cv46-21 is the plain one, with no serif on the stem or the leg.
Only the cmap moves, so the glyph is Iosevka's own drawing and this is a
choice between shapes the release already carries.

Why: in the prose face `k` and `K` are one shape at two heights, and at
17px on the panel there is nothing to tell them apart. Ben could not
separate them reading a key off the board (2026-09-24). A looped k is a
different letter, not a smaller one. Of 48 open-licence faces measured,
four had a closed loop and three of those lost it at panel size.

`tests/test_mono_font.py` counts the enclosed counter in `k` at every
size the screens draw, and fails if the loop is gone. 13px is the floor:
at 12px the counter fills.
