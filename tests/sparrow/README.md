# Sparrow interop

Core Signer's other tests build PSBTs with Bitcoin Core and sign them with Bitcoin
Core. That proves the pipeline, and it proves nothing about a second
implementation. This suite closes that gap: **Sparrow builds, Core Signer signs,
Core broadcasts.**

The PSBTs come from Sparrow Wallet's own library. `setup.sh` downloads the
signed Sparrow 2.5.4 macOS release, checks its sha256 against Sparrow's
published manifest, and extracts the jlink image with `jimage`. `SparrowGen`
then drives the same classes the Sparrow GUI drives:

- `Wallet.createWalletTransaction()` for coin selection and change derivation
- `WalletTransaction.createPSBT()` for construction
- `PSBT.getForExport()` for the downgrade, which is exactly what
  `HeadersController.savePSBT()` and the QR export call

Nothing is reimplemented, so a pass means Sparrow's real output is signable.

## Run

    ./setup.sh                       # one time, downloads and verifies
    python3 test_sparrow_interop.py  # 22 checks: the PSBT matrix
    python3 test_qr_airgap.py        # 20 checks: the QR channel itself
    python3 inventory.py             # what Sparrow puts in a PSBT

`setup.sh` installs nothing. Both downloads live in `.build/`, which is
gitignored. It needs its own JDK because Sparrow 2.5.4 ships class file
version 69, which JDK 24 and older refuse to read.

## Matrix, 22 checks, all green against Core v32.1

Per script type (BIP84 `P2WPKH`, BIP86 `P2TR`):

| Check | Why it is here |
|---|---|
| receive derivation matches Core, 12 addresses | Sparrow and Core must agree on the address before anything else matters |
| change derivation matches Core, 12 addresses | same, for the internal branch |
| 1, 2, 3 and 10 receive inputs | input count, and Core Signer's paging |
| change-branch input | exercises the internal descriptor |
| mixed receive and change inputs | both descriptors in one transaction |
| 2 payments plus change | more than one output |
| send max, no change output | the one-output shape |
| PSBTv2 review matches Sparrow | Core decodes v2 from 32.0, in a second shape, see below |

## The QR air gap, 20 checks, all green

`test_sparrow_interop.py` hands base64 strings between the two, which skips the
channel. `test_qr_airgap.py` drives the real one, on a 6-input PSBT:

1. Sparrow's `UREncoder` produces `ur:crypto-psbt` parts, upper-cased, exactly
   as `QRDisplayDialog:245` animates them. Both of Sparrow's density settings
   are covered: `NORMAL` (400) and `LOW` (80).
2. Core Signer's `PsbtScan` reassembles them under the real device scan
   rules, byte-identical.
3. Core Signer signs.
4. Core Signer's `psbt_to_frames` and `frames_to_images` render real PNGs, sized and
   letterboxed for the SeedSigner+ hat's 320x240 panel (PLAN A-15c).
5. Sparrow's zxing reader decodes every one of those PNGs.
6. Sparrow's `URDecoder` rebuilds the PSBT, byte-identical.
7. Sparrow's drongo parses it and counts 6 of 6 inputs signed.
8. Core finalizes and broadcasts, and it confirms.

Measured on a 6-input transaction:

| | P2WPKH | P2TR |
|---|---|---|
| Sparrow PSBT | 2396 base64 chars | 1908 |
| Sparrow parts at `NORMAL` | 5, up to 775 chars | 4, up to 771 |
| Sparrow parts at `LOW` | 23, up to 213 chars | 18, up to 215 |
| Core Signer frames back | 21 | 13 |
| Core Signer QR size on the panel | 212px | 212px |

The suites share `harness.py`: one regtest bring-up, one Java runner, one
pass/fail tally.

Two things this settles. Sparrow upper-cases every fragment for alphanumeric QR
mode, and Core Signer's charset guard lower-cases before checking, so the case
mismatch is harmless. And Sparrow's `NORMAL` fragments reach 775 characters,
because `maxUrFragmentLength` counts bytes and bytewords roughly doubles them;
Core Signer's `MAX_FRAME_CHARS = 3000` guard clears that with room.

**The scan direction stops at the string.** `CameraQrSource` still raises
`camera not yet wired (M1)`, so there is no image decoder on the device to
test. Every line of the QR channel Core Signer has actually built is covered. The
optical read is M1 hardware work and this suite cannot stand in for it.

## The PSBTv2 boundary

Sparrow 2.4.0 made PSBTv2 its default internal representation. Bitcoin Core
31.1 set `PSBT_HIGHEST_VERSION = 0` and threw `Unsupported version number` on
anything higher, so Core Signer on 31.1 refused every v2. Bitcoin Core 32.0
raised it to 2 (`src/psbt.h:97` at v32.1), and a version above 2 still throws
(`src/psbt.h:1485`). From 32.0, `walletcreatefundedpsbt` also makes a v2 by
default.

Core's decode of a v2 has no `tx`. Each output carries `amount` and `script`,
and each input carries `previous_txid`, `previous_vout` and, when the PSBT has
it, `sequence`. `signer._review_shape` reads both shapes, and every number on
the review screen is still Core's.

`PSBT.getForExport()` still downgrades to v0 on the way out. Its own comment
states the exception:

    //Export as PSBTv0 unless silent payments are present

The v2 check takes Sparrow's raw PSBTv2, the shape before that downgrade, and
asserts that the review shows the fee and outputs Sparrow computed. No check
here builds a silent-payment transaction, so this suite does not show what
Core Signer does with one.

## What Sparrow sends

From `inventory.py`, one input and two outputs:

| | P2WPKH | P2TR |
|---|---|---|
| global | `UNSIGNED_TX`, `GLOBAL_XPUB` | `UNSIGNED_TX`, `GLOBAL_XPUB` |
| input | `non_witness_utxo`, `witness_utxo`, `sighash=ALL`, `bip32_derivs` | `witness_utxo`, `sighash=DEFAULT`, `taproot_bip32_derivs`, `taproot_internal_key` |
| change output | `bip32_derivs` | `taproot_internal_key`, `taproot_bip32_derivs` |

Field names are Core's, because `decodepsbt` does the parsing. Note the
sighash difference: Sparrow sets `SIGHASH_ALL` for segwit v0 and
`SIGHASH_DEFAULT` for taproot, which is correct BIP341 behaviour. Core reports
`SIGHASH_DEFAULT` as an **empty string**, so any presence test written on
truthiness hides it.

Sparrow sends the whole previous transaction for segwit v0 and omits it for
taproot, which follows BIP371. Core Signer reads both.

## Anti-fee-sniping, and why one test failed first

For `P2TR` only, Sparrow calls `applySequenceAntiFeeSniping()`
(`Wallet.java:1126`). It then picks one of two methods at random:

- `nLockTime` set to the current height, sometimes back-dated by up to 100
  blocks
- per-input `nSequence` set to that input's confirmation count, a BIP68
  relative lock, sometimes reduced by up to 100

The second method makes the transaction depend on real chain depth. An early
version of this harness fed drongo a stale UTXO height, so the sequence
claimed more confirmations than the input had and Core answered
`non-BIP68-final`. The harness was wrong, not Core Signer. Each UTXO now carries its
own height.

Core Signer signs both forms unchanged: the final transaction keeps the `nLockTime`
and every `nSequence` Sparrow chose.
