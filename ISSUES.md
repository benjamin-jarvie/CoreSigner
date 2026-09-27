# Known issues

Open defects and gaps, recorded so they are not lost between sessions.
**Fixed items leave this file and live in the git history instead.** That
rule was written at the top of this file and then broken: on 2026-09-06
it held 112 lines of fixed history and an "Open" section where six of
eight items had already been fixed. Audit A9 emptied it.

Where a fixed item taught something, the lesson is in
[TESTING.md](TESTING.md) as a numbered rule, which is the document that
gets read. Where it changed a decision, it is a PLAN amendment. Neither
needs a copy here.

Last reviewed 2026-09-06, audit A9. Every claim below was checked against
the source on that date.

E-6, the export QR mask threshold that failed about one run in forty,
was opened and closed on 2026-09-11. The flake was the FIXTURE: the
suite generated a fresh key every run and asserted 6 of 8 masks decode,
where 720 measured renders put 8 of 8 at about 94%, 7 at 5% and 5 at
0.3%. It now runs a pinned corpus of descriptors measured awkward, and
the floor is 3, which is what the export's own mask cycle needs.

E-5, the export QR that about one key in a hundred could never show, was
opened and closed on 2026-09-09. It is not here because this file's own
rule is that fixed items leave; the measurements are in the commit.

## Open

### E-8 BlueWallet imports one of our four policies, and hangs on another

Ben scanned the public key export into BlueWallet and it hung (2026-09-24).
Not a PSBT: the watch-only import. Traced against BlueWallet 7.2.0's own
source, `class/wallets/abstract-wallet.ts` `setSecret()`.

**Our 8-mask cycling is not the cause.** BlueWallet's scanner keys a cache
on `sha256` of the decoded payload and drops a repeat, so all eight masks
are one scan to it (`screen/send/ScanQRCode.tsx`, `scannedCache`/`HashIt`).
A single static QR of the same text behaves identically, and the
hold-a-mask press on the export screen is how a tester shows that.

**What actually happens to each descriptor we export:**

| export | BlueWallet 7.2.0 |
|---|---|
| mainnet `wpkh` m/84h | imports correctly |
| mainnet `sh(wpkh)` m/49h | **throws, and the import hangs** |
| mainnet `pkh` m/44h | fails to import, malformed secret |
| mainnet `tr` m/86h | fails to import, malformed secret |
| any testnet | fails to import, no `tpub` support |

Line 236 strips `[fingerprint/path]` off the front before anything else,
sets the fingerprint and the path from it, and for m/84h converts the
xpub to a zpub with `this.secret.split('/')[0]`, which drops our `/0/*`
and the `#checksum`. That is why native segwit works.

**The m/49h branch three lines below is missing that `.split('/')[0]`.**
It hands `xpub…/0/*))#checksum` straight to `_xpubToYpub`, whose
`b58.decode` throws `Non-base58 character`. `wo1.setSecret(text)` at
`class/wallet-import.ts:364` is not inside a try/catch, so the throw
escapes the import generator after it has yielded `progress: 'watch
only'`, and nothing is ever shown. That is the hang.

For m/44h and m/86h no conversion runs at all, so the secret keeps
`/0/*)#checksum` and `valid()` rejects it. The device says it cannot
import, which is wrong but honest.

The output-descriptor branch at line 314, which does have its own bugs
(it slices the fingerprint with the `[` still attached), is NEVER REACHED
by our payloads: line 236 has already replaced `this.secret` with the
xpub. An earlier draft of this entry said that branch was the cause. It
is not.

**The cheap fix on our side: export the key-origin form**, with no
wrapper, no `/0/*` and no checksum:

    [73c5da0a/84h/0h/0h]xpub6Bosf…Mdj

Traced: that imports correctly for m/44h, m/49h and m/84h, with the right
fingerprint and path, and BlueWallet does the ypub/zpub conversion
itself. It is BIP380 key-origin syntax, not a format we invented, and it
costs no new code that touches key material: `signer.origin_of` already
takes the fingerprint and path off a descriptor.

**It must NOT be offered for `tr` or for testnet.** For m/86h a bare
xpub makes BlueWallet build an `HDLegacyP2PKHWallet` (`init()`,
watch-only-wallet.ts:77), so it would import "successfully" and show
LEGACY addresses for a taproot account. A silent wrong-address import is
worse than a refusal. Testnet cannot work at all: `xpubIndex` searches
for `xpub`, `ypub` and `zpub` and never `tpub`.

`UR:CRYPTO-OUTPUT` does not help either: BlueWallet decodes it with
`CryptoOutput.fromCBOR(...).toString()`, back to a descriptor string and
back into the same path (`blue_modules/ur/index.js:285`).

Open, and Ben's call.

This also corrects E-4's research, which said BlueWallet reads the same
plain descriptor QR as Sparrow. Sparrow does. BlueWallet reads one of our
four policies.

### E-4 The five coordinators are unproven on a device

The coordinator chooser was removed from the export because the research
says Sparrow, BlueWallet and Green all read the same plain descriptor QR
for `wpkh` and `tr`, Bull Bitcoin reads `wpkh` only, and Core reads no QR
at all and takes a file. The research is in
the e2e-before-testers map, tickets 19, 20 and 21 (archived; see docs/wayfinder/README.md), and it was
read out of each project's source.

**None of it is proven on a device.** Tickets 18 and 22 are the proofs,
and both need Ben, a phone and the Sparrow laptop. Sparrow's half is now
covered by `tests/sparrow/`, which drives Sparrow 2.5.4's own library out
of its verified release, so what is left is the two phone wallets and
Bull Bitcoin.

Carried into the beta audit as
[A10](docs/wayfinder/beta-audit/tickets/A10-carry-forward.md).

### The build the board actually is

Both `README.md` and `hw/HARDWARE.md` pair the panel with the compute
module: the primary build is the CM4 with a 2.8" 320×240 hat, the pocket
build is the Zero 2 W with a 1.3" 240×240. Asked on the board on
2026-09-06, a Zero 2 W answered 320×240, which is neither.

Nothing is broken by it. Every screen is written for both sizes and
`tests/test_screen_fit.py` renders both. The wording is wrong, and
naming the third combination is Ben's call. Recorded in
`hw/HARDWARE.md` and `CONTEXT.md` as a measurement in the meantime
(audit A8).

### QR is the only channel that closes a loop on the pocket build

Not a defect, and worth writing down because it changes what a tester
must be given. On a Zero 2 W the card is the boot device, so reading it
elsewhere means powering off and the tmpfs datadir dies with the session.
USB host genuinely works (`otg_mode=1`, `dtoverlay=dwc2,dr_mode=host`,
checked on the board 2026-09-06) but needs a micro-USB OTG adapter and a
cutout in the case.

So a tester with no adapter has exactly one way in and out: the camera
and the panel. The outbound margin is thin by construction, 4.0 pixels
per module with no room above it, and measured at 8 misses in 750 frames
on 2026-09-06. The panel loops, so a miss costs a cycle.

## Standing hardware-blocked work

Not defects. Recorded so the list above is not confused with them.

- M1: camera QR capture on the board. The dev harness and `tests/m1`
  cover the scan rules; the lens has not been pointed at a coordinator.
- M2: display and GPIO bring-up beyond what the board already runs.
- M3: RAM-resident release image, radio kill verification, reproducible
  build.

M0 passed on the Pi Zero 2 W on 2026-09-03: 226MB of headroom signing 250
ordinary inputs (PLAN A-21).

## What was here, and where it went

- **I-1 to I-11**, the two-axis reviews of 2026-09-02 and 2026-09-03, and
  the test gaps they found. All fixed. The rules they produced are
  TESTING.md 1 to 11; the cropped-QR and silent-power-off pair is rule 7,
  and the zxing frame that Core Signer's own decoder could read is rule 8.
- **D17** (teardown failure is silent) and **D18** (load, review and
  signing errors bypass UI recovery). Both fixed. `Session.run` reports a
  failed teardown as "key not cleared" instead of discarding it, and
  `Session.HANDLED` catches `RuntimeError`, `OSError`,
  `FileChannelError` and `QrChannelError` around every flow the home
  screen dispatches. Sessions K7 and K8 drive the file-error path.
- **E-1** (no scroll bars). Fixed: six screens draw one and
  `tests/test_scroll.py` asserts that every screen taking a page or an
  index has one.
- **E-2** (the export was four screens deep with no map) and **E-3**
  ("PUBLIC KEY" is not what the screen holds). Both fixed on the board
  with Ben on 2026-09-05: the flow is script type, then how it leaves,
  then the key, then the addresses, and the QR carries its fingerprint,
  policy and derivation path underneath.
- **E-5** (Core's file had nowhere to go). Fixed: `--card-dir` exists and
  session K13 gives the device a stick and a card and proves the file
  lands on the one chosen.
- **E-6** (two of Core's four script policies). Fixed: all four are
  exported and browsable, LEFT and RIGHT walk them, and session K12
  proves each policy's own addresses reach the panel.
- **The image a tester would flash is not pinned.** Fixed 2026-09-23.
  `OS_IMAGE_SHA256` named a 2026-05-13 image whose URL is 404 and which
  the board was never running; the image it IS running is pinned, its
  524,875,608 bytes downloaded and hashed rather than the published
  number copied. The six .deb files apt used to fetch from a rolling
  index are pinned by url, sha256 and size, and `prepare-sd.sh` was
  already stamping each card with its own commit and tarball hash, so
  `CORESIGNER_COMMIT="HEAD"` is a template and never was a gap.
  `tests/test_pins.py` keeps it that way.
- **E-7** (the receive address screen, and hardened paths, read wrong).
  All five fixed on 2026-09-18. The title says "1st receive address" and
  counts in ordinals; the policy is drawn only where LEFT and RIGHT can
  change it, and those presses now do nothing on the three after an
  export, where the policy was chosen two screens before; the
  compare-every-group footer is gone and its reasoning is a section of
  docs/TESTER-PACK.md; the type grew from 16px to 21px by choosing the
  row shape instead of fixing four groups to a row; and the caption
  under an export QR writes `m/84'/0'/0'` the way Sparrow does. Only
  captions are reshaped: a descriptor carries a checksum over its exact
  characters, so `export_text` still draws what Core said.
