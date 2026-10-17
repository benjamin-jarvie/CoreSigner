# Can the cameras read?

**Status: NOT DONE.** This is item 3 of the
[A11 verdict](wayfinder/beta-audit/tickets/A11-the-verdict.md) and the
oldest open issue, E-4. It is the last thing standing between a
supervised pilot and a tester holding a device.

A signer whose QR a coordinator cannot read is not a signer. Sparrow's
**library** is proven to exhaustion: 197 checks drive its own decoder out
of its verified release. A lens is not a library. Nobody has pointed a
camera at this panel.

Run this BEFORE `image/harden.sh`. Hardening takes SSH with it, and
every diagnosis below is easier with a shell.

## Two different camera problems

They are not one job and they need different things from you.

**1. The device's camera reads a QR off the laptop screen.** This is how
a transaction gets IN. `tests/hw_camera.py` already exists for it: it
paints what the camera sees onto the LCD so the operator can aim, and
names every QR it decodes. You run it and hold the board up to Sparrow.

**2. A phone or webcam reads the device's screen.** This is how the
signed transaction and the public key get OUT. Nothing exists for it and
nothing can, because Core Signer cannot drive somebody else's camera.
You point and look.

Both must work or the device is useless, and neither has been done.

`tests/sparrow/` proves the BYTES leaving the device are right, and that
zxing decodes the image the panel draws. What no software can prove is
that a real lens, in real light, at arm's length, resolves it.

## Setup

Board on the desk, running, SSH still alive. Sparrow on the laptop. One
phone wallet. Room light as a person would actually use, not a lamp
aimed at the screen.

## Part A. The device's camera reads a QR

    ssh coresigner-ip
    sudo python3 /opt/coresigner/tests/hw_camera.py --seconds 90

Paints the viewfinder on the LCD and names every QR it decodes. Show it
a PSBT QR from Sparrow. **Pass:** it decodes within a few seconds of
being aimed, without a tripod.

## Part B. Sparrow and a phone read the device's screen

On the device: `Key → Export public key → <a policy> → QR code`.
The screen cycles all eight mask patterns at 0.3s. Hold each coordinator
in front of it as a person would.

Do all four policies. Native segwit and Taproot are what most people
use; Nested segwit is the awkward one at 61 modules against the others'
57.

## Part C. The same, for a multisig key. The hard one

Multisig is in the pilot, so this is not optional.

On the device:

    `Key → Export public key → Multisig… → Native segwit → QR code`

That payload is a whole descriptor,
`wsh(sortedmulti(1,[xfp/48h/1h/0h/2h]tpub...))#checksum`, **167
characters against a single-sig 148**. A longer string is a denser code,
so this is the hardest thing the panel has to show.

The typed-path row that made a LONGER payload is off for the pilot
(2026-09-16), so this is the densest code the panel has to show.

In Sparrow: a 2-of-3 P2WSH wallet, keystore -> **Airgapped Hardware
Wallet** -> **Specter DIY** -> **Scan...**.

## Record the result here

| # | what | coordinator | read? | notes |
|---|---|---|---|---|
| A | device reads a PSBT QR | Core Signer's camera | **YES** | 2026-09-18: 611-char UR:CRYPTO-PSBT, SEQUENCE COMPLETE in 36.8s, 7.0 fps, hand-held off a laptop screen |
| B1 | Native segwit | Sparrow | **YES** | Ben, on the board: Sparrow read the exported xpub and created a wallet from it. 148 characters, one static code. Date and policy not recorded at the time; Ben to fill in. |
| B2 | Taproot | Sparrow | | |
| B3 | Nested segwit | Sparrow | | |
| B4 | Native segwit | phone | | |
| C1 | Cosigner P2WSH, 167 chars | Sparrow | | |
| C3 | Cosigner P2WSH | phone | | |

## What B1 settles, and what it does not

**Density on the way out is largely answered.** A signed PSBT leaves as
an animated UR loop of about 100 characters a frame
(`qrchannel.MAX_FRAGMENT_LEN`), and every one of those frames is a
sparser code than the 148-character descriptor Sparrow has already read
off this panel.

**Two things B1 does not touch, and they fail differently:**

1. **C1, at 167 characters**, is denser than anything read so far and is
   the hardest static code the device draws.
2. **The animation.** A loop is not a still. It asks whether Sparrow
   tracks a multi-frame sequence at our frame rate, and whether the
   fountain parts do their job when a frame is missed. About one frame
   in 125 is deterministically unreadable by zxing at our 4.0 pixels per
   module, which is why the loop carries fountain parts at all
   (`qrchannel.psbt_to_frames`). That is a timing and state-machine
   question, not a legibility one, and a still QR cannot answer it.

## What counts as a pass

**Read within one mask cycle, 2.4 seconds, held by hand at a comfortable
distance.** Not "reads if you brace it against a book for a minute".

A miss costs a cycle and the panel loops, so one slow read is survivable
and a consistent failure is not. If a row fails, say which mask was on
screen if you can see it, and keep the descriptor: `tests/sparrow/
test_export_interop.py` has a pinned corpus for exactly this and a real
failure belongs in it.

## If C fails and B passes

Then the cosigner QR is too dense for this panel and the answer is the
file channel, not the camera. `signer.write_cosigner` already writes the
one-liner to a stick or the card, and M8 measured that Sparrow's Specter
DIY importer reads it. Say so and the pilot ships the file route for
multisig.
