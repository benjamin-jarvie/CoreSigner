# Core Signer: a stateless, air-gapped PSBT signer that runs Bitcoin Core's wallet
*Plan v1 → devil's advocate → v2 → devil's advocate → v3. 2026-08-17.*

## Review outcome (Ben, 2026-08-17)

- Approved: hardware on hand, Sparrow as v1 coordinator, v1 scope freeze.
- Shim disclosure: **explicit, not fine print.** The README leads with it (A-6 superseded).
- Name: **Core Signer.** Tagline: *Core's keys, nothing kept.*
- Shim built and passing all official vectors (`shim/test_shim.py`). Next gate: M0.
  **Superseded by A-22: the shim and its tests are deleted. Keys arrive as
  xprv or descriptor, so nothing in the tree turns words into a seed.**

## Amendments after review (Ben, 2026-08-17, second pass)

- **A-10: dual transfer channels.** PSBT in/out via animated QR *and* via file
  on a USB stick (OTG port). The boot microSD cannot be hot-swapped, so
  file transfer is USB, Coldcard-style. QR remains the reference channel;
  USB removes the size ceiling. Both are opaque-PSBT-only.
- **A-11: language decision — Python, with a law.** Rust runs fine on the Pi
  (aarch64 is first-class; no compile theater), but it hardens the wrong
  layer: all crypto and all untrusted-input parsing live in Bitcoin Core
  regardless. Rust would also force the shim onto crates.io dependencies,
  destroying its stdlib-only claim, and the Pi camera/LCD ecosystem is
  Python-mature (SeedSigner's own drivers). Decision: Python for v1, under
  this frozen rule: **Core Signer's code never parses untrusted bytes. PSBTs pass
  through as opaque strings; Bitcoin Core is the only parser.** The zbar QR
  decoder (C, both languages) gets length caps and charset checks before its
  output is used. A Rust front-end rewrite is noted as a legitimate v2
  hardening step once v1 is a working reference.
- **A-12: hot-swap microSD channel is now a v1 requirement (Ben, second pass).**
  The mechanism is SeedSigner OS's trick: the operating system runs entirely
  from RAM after boot, so the boot microSD can be removed and reused as the
  PSBT sled (`/mnt/microsd` watcher). For Core Signer this means a minimal
  RAM-resident image (buildroot-style) carrying bitcoind + Python + the UI.
  *Devil's advocate, on the record:* this is now the riskiest item in the
  project. The rootfs, bitcoind, the ramdisk datadir and the UI must all
  share 512MB; a full Raspberry Pi OS cannot run from RAM at this size, so
  M3 becomes a real OS-image engineering task, not a hardening pass.
  Mitigations: M0 measures bitcoind's true footprint first; development
  through M2 stays on stock Raspberry Pi OS with the USB-stick channel
  (which remains in v1 as the fallback transfer path); the RAM-resident
  image is developed as M3 with a defined fallback (v1 ships with USB-only
  transfer if 512MB cannot hold the RAM image; microSD hot-swap then waits
  for a 1GB-class board). Side benefit if it lands: statelessness becomes
  structural — the OS itself is immutable and nothing can persist anywhere.
- **A-12b: how SeedSigner actually does it, read from their build
  (2026-09-08).** `seedsigner-os` at `c4cb767`, target `pi02w`, which is
  the same board. The mechanism is not a read-only mount and not an
  overlayfs. It is `BR2_TARGET_ROOTFS_INITRAMFS=y`: the whole root
  filesystem is a gzipped cpio inside the kernel image, and the card
  carries **one 50MB FAT partition** holding firmware, kernel and
  overlays. Their `/etc/fstab` mounts `/dev/root` `noauto`. So there is no
  writable filesystem on the card at all, and the hot-swap in A-12 works
  because after boot the card is not holding anything.

  That is the difference between Core Signer's statelessness and theirs, and it
  is worth stating plainly before M3 starts. **Ours is a policy: a tmpfs
  datadir, `harden.sh`, no swap, and a leak check that says so. Theirs is
  a property of the medium.** A policy is a set of things that must all
  keep being true. A medium with nowhere to write is one thing that is
  true. `tests/test_no_persistence.py` and `image/leak-check.sh` exist
  because ours needs proving on every run; theirs would not.

  What this changes about the A-12 risk: **it is smaller than the
  amendment feared, and the arithmetic now exists.** Measured 2026-09-08,
  all of it from the shipped artefacts rather than from estimates.

  | | |
  |---|---|
  | SeedSigner 0.8.7 pi02w, whole card | 33MB (29.5MB `zImage`, 3.7MB firmware) |
  | that `zImage`, decompressed (kernel **and** initramfs) | 36.7MB |
  | `bitcoind` 32.1 aarch64, stripped | **16.3MB** |
  | `bitcoin-cli` 32.1 | 7.5MB |
  | Raspberry Pi OS Lite, resident, on the board today | **~168MB** |

  That last figure is the one that decides it, and it was already in
  FLASH.md waiting to be subtracted. At 250 ordinary inputs the board
  reports bitcoind 65MB, Core Signer 21MB and 226MB MemAvailable, against
  480MB after `gpu_mem=32`. So 254MB is genuinely resident and 168MB of
  it is the operating system.

  **A RAM-resident image is very likely to use LESS memory than what we
  run now, and the mechanism is the opposite of the intuition.** An
  initramfs is unpacked into a tmpfs that is held for ever and cannot be
  reclaimed without swap, which we do not have. A disk rootfs is read
  through the page cache, which the kernel CAN evict. Per byte, the
  initramfs is the expensive one. It wins anyway, because a
  purpose-built image at 40 to 90MB resident beats a general-purpose
  distribution's 168MB working set by more than the reclaim advantage is
  worth. SeedSigner already carries Python, numpy, picamera and pyzbar,
  which is most of what Core Signer needs; adding 24MB of Core binaries to
  their 37MB is not what breaks a 512MB board.

  The two Core rows were 16.4MB and 2.6MB for 31.1, so the sum here was
  19MB. Re-measured from the 32.1 tarball on 2026-11-04: `bitcoin-cli`
  grew to 7.5MB, and the sum moved by 5MB. The conclusion does not move.

  What would confirm it: build once and read MemAvailable on the same
  250-input run. Until then this is arithmetic on measured parts, which
  is better than the guess A-12 recorded and is still not a measurement
  of the thing itself.
- **A-12c: their reproducibility traps, taken rather than re-paid
  (2026-09-08).** M3 requires an image hash reproducible on a second
  machine. Their build carries a list of things that break that, each of
  which cost somebody a failed diff:

  - `BR2_REPRODUCIBLE=y`, then `SOURCE_DATE_EPOCH=1 PYTHONHASHSEED=0`
    with `compileall --invalidation-mode=checked-hash` for every `.pyc`.
  - `mkfs.vfat --invariant -i ba5eba11` and a fixed partition
    `label-id`, because both default to something derived from the clock.
  - `touch -d` a fixed timestamp across every boot file.
  - Sorted shell globbing instead of copying a directory, because
    "mcopy doesn't copy directories deterministically" (their comment).
  - **Deleting files that record the BUILD HOST rather than the target.**
    Two of them: Python's `_sysconfigdata`, which embeds the configure
    triplet, and `libstdc++`, whose `.text` differs between an x86_64 and
    an aarch64 build host because GCC 13.3 orders two spill slots in
    `from_chars` differently. Nothing in their image links against it, so
    they remove it.

  That last one is the shape of trap that a first attempt at a
  reproducible build always hits and never predicts: the artefact is
  byte-identical everywhere except one library nothing uses.
- **A-13: bigger display in v1 (Ben, second pass).** Target is the
  SeedSigner-Plus-class 2.4" ILI9341 at 320×240 (driver vendored from
  SeedSigner at `hw/vendor/ili9341.py`; the 3.5" ILI9486 is named in
  SeedSigner's settings but has no driver in their main repo yet). The UI
  renders resolution-independent PIL frames and asks the driver for its
  size, so the 1.3" ST7789 stays supported as the compact build.
  **SUPERSEDED by the A-13b finding below.**
- **A-13b: the display is Ben's own SeedSigner+ hat (resolved 2026-08-17).**
  Ben stocks the SeedSigner+ (Bitcoin Butlers shop product `seedsigner-plus`)
  and holds its PCBs: **2.8" IPS, 320x240, ST7789**, a standard 40-pin GPIO
  HAT with on-board d-pad/buttons, USB-C power input and microSD extender.
  It runs stock SeedSigner firmware with only the `st7789_320x240` display
  setting — which proves the buttons use the same GPIO map as the 1.3" hat
  and the controller is the ST7789 already vendored. Driver work = the
  320x240 init variant (width/height swap + 90-degree rotation, per
  SeedSigner's display factory). Nothing to buy for the display; the
  Pimoroni Display HAT Mini is off the list. Verify on arrival of the CM4:
  (1) power from ONE USB-C only (hat back-feeds 5V via GPIO; the carrier has
  its own input); (2) camera uses a STANDARD-width CSI ribbon on the carrier,
  not the Plus kit's Zero-width ribbon; (3) enclosure fit of the metal Plus
  case over the carrier stack.
- **A-14: Core-native seed entry (Ben, 2026-08-17, third pass).** Three input
  modes, in order of purity: (1) **private descriptor via static QR** — pure
  Core, self-describing (path + script type + checksum), no shim, no
  hardcoded derivation; (2) **xprv via static QR or text** — pure Core,
  Core Signer applies BIP84/86; (3) **BIP39 words / SeedQR** — the shim path,
  default, because the world's backups (including Ben's) are steel word
  plates. Both new formats fit a single static QR (~112 / ~150 chars) and
  base58check makes a bad scan fail loudly. Stated trade-offs: descriptor
  backups are print/engrave media, not stampable words; no BIP39-style
  passphrase layer on a raw xprv (the QR IS the wallet — say so on screen).
  Strategic note: mode 1 makes Core Signer the first signer with a shim-free,
  fully Core-native path; candidate default for onboarding fresh wallets.

- **A-15: primary board is now the CM4 (Ben, 2026-08-17, fourth pass).**
  **Raspberry Pi CM4 Lite, no-wireless, 2GB** (CM4002000; 4GB CM4004000 an
  acceptable substitute) on a **Waveshare CM4-IO-BASE-B** carrier. Rationale:
  no radio by manufacture (kills the air-gap critique with physics, no
  rework) and 2GB removes the A-12 RAM-resident-OS risk entirely. Display:
  **Pimoroni Display HAT Mini** (2.0" ST7789 320x240, 4 buttons, HAT form) on
  the carrier's standard 40-pin header — pin remap vs the vendored driver to
  be prepped before it arrives. The Zero 2 W becomes the dev mule and the
  pocket build in the existing SeedSigner case; M0's 512MB question now only
  gates the pocket build, not v1. Existing Zero-format spine PCBs are
  electrically compatible with the carrier's header but mechanically
  Zero-shaped; pending Ben identifying the exact PCB. UI must support
  4-button (no joystick) navigation as the primary scheme.
  **Superseded by A-15b: see A-15c for the control surface actually built.**

- **A-15b (2026-08-18): A-15's display and button scheme are superseded.**
  The Display HAT Mini and its "4-button primary scheme" died with A-13b:
  the SeedSigner+ hat (d-pad + three keys, same GPIO map as the 1.3" hat)
  is the primary control surface, on the CM4 carrier per A-15's board
  decision, which stands. Navigation is d-pad + A/B/C.

- **A-15c (2026-09-02): the control surface, as built (Ben).** A-15's
  "4-button primary scheme" belonged to the Pimoroni Display HAT Mini,
  which A-13b/A-15b retired. Every board Core Signer targets carries a 5-way
  joystick plus three keys: the SeedSigner+ hat on the CM4 build, and the
  WaveShare 1.3" hat on the Zero 2 W pocket build. The UI uses all eight
  controls; a 4-button-only scheme is explicitly NOT a requirement, and
  reintroducing one would roughly double seed and codex32 entry cost
  (grid entry would become two-stage row-then-column). The audit's D10
  is closed by this amendment: the brief was stale, the code was right.
  GPIO is identical across CM4 carrier, Zero 1.3 and Zero 2 W, because
  the carrier presents the standard Raspberry Pi 40-pin header; the pin
  map in hw/HARDWARE.md is unchanged across all three.
- **A-16: dev image vs release image (2026-08-18).** Two distinct images per
  release, both hashed in the pinned tuple. DEV: SSH enabled over the
  carrier's wired Ethernet (no radio involved; the cable is the visible,
  removable dev channel), HDMI console. RELEASE: no SSH server, network
  services masked, Ethernet driver blacklisted (stack removed entirely once
  M3's RAM-resident image lands). The air-gap claim attaches to the release
  image only. Desoldering the RJ45 remains a documented option for a
  paranoid build (through-hole part; far easier than radio rework).
- **A-17: UI design reference — Bitcoin Core App (2026-08-18).** The QML
  interface at bitcoincore.app (repo: bitcoin-core/gui-qml, qt6 branch) is a
  COMMUNITY project with the Bitcoin Design community — it sits in the
  bitcoin-core GitHub org but is NOT an official Bitcoin Core team product;
  record it accurately. Use: mine its transaction-review and send-flow
  patterns (Bitcoin Design Guide lineage, MIT) when refining Core Signer's review
  screens, so Core Signer's UI reads like the Core family. Watch item: if it grows
  PSBT-by-QR/file coordinator flows, the full stack becomes Core node +
  Core App coordinator + Core Signer signer. Context (2026-08-18): Ava Chow put
  HWI into minimal maintenance (bitcoin-core/HWI#850, successor: BHWI by
  Wizardsardine) — Core Signer has no HWI dependency (PSBT-native by design),
  and any future Core App hardware-wallet story runs through BHWI while
  its Core Signer story would be plain PSBT.

- **A-18: codex32 as a first-class Core-native mode (Ben, 2026-08-18 —
  "stop worrying about frontrunning").** Fractal's work is a SeedSigner
  fork UI; ours is different in kind: codex32 used DIRECTLY with Bitcoin
  Core, which is what BIP93 was designed for (it encodes BIP32 master
  seeds — no BIP39, no PBKDF2, anywhere).

  Scope, three capabilities:
  1. **Import**: scan or key in a codex32 string (or k shares) →
     validate/combine → master seed → xprv → `importdescriptors`. Sits
     beside A-14's modes as the fourth entry path, and arguably the
     best-suited to button entry: bech32's 32-character alphabet on a
     d-pad beats cycling 26 letters toward 2048 words.
  2. **Backup**: encode an externally-born seed (cards/dice) as a
     codex32 string, and optionally split k-of-n, rendered on-screen and
     as printable share sheets. Answers the Westgate constraint (Core
     cannot export seeds) at the layer where the seed already lives.
  3. **Verify**: recompute a share's checksum on demand — the
     zero-re-exposure integrity check is the standard's killer property
     and costs us one function.

  Implementation rules would mirror the shim: a `codex32.py` module
  (post-v1, not written; the name is the proposal, not a file),
  **stdlib-only, no EC math** (BCH checksum + GF(32) share arithmetic is
  table math), frozen once written, tested against BIP93's own vectors
  AND cross-checked against BlockstreamResearch/codex32's reference
  implementation. It is a second translator that sees secret material;
  it inherits the shim's disclosure obligations (README section, hash
  pinned in SHIM_HASH-style file, printed asterisk).

  Devil's advocate, on the record: (a) v1 scope is frozen and stays
  frozen — this is the v1.1 headline, not scope creep into v1; nothing
  in it blocks or reorders M0–M4. (b) Two hand-rolled secret-handling
  modules is one more than one; mitigation is the same vector discipline
  plus cross-implementation checks, and GF(32) table math is closer to
  the shim's hashing than to real cryptography. (c) Seed-splitting is
  explicitly discouraged by one side of a live expert debate — Core Signer
  IMPLEMENTS the standard and takes no position; split is a capability
  behind an explicit menu choice, with the debate acknowledged on
  screen ("some practitioners discourage splitting seeds; shares are
  optional"). (d) Fractal compatibility: his share-QR conventions, once
  published, become an interop target, not a dependency.

- **A-18 progress (2026-08-19): codex32 v1.1 SOFTWARE-COMPLETE.**
  main.py wires all three capabilities against the closed map: import
  (scan multi-share payloads, or 4x8 grid entry with per-share checksum
  verdicts, k auto-detected from the first share, duplicates refused),
  verify (tools menu; checksum only, zero derivation), and backup (words
  -> BIP32 master seed -> codex32 secret, or a 2-of-3 split whose
  randomness is derived deterministically from the seed via domain-
  separated HMAC-SHA512 — no device RNG exists or is used, and shares
  re-derive identically). Seed menu now six entries; tools reachable
  from home (R). e2e session E proves shares->wallet->sign on regtest;
  all seven suites green. Hardware-blocked remainder unchanged: camera
  QR (M1), device bring-up (M0), RAM-resident image (M3).

- **A-19: opt-in seed generation with Bitcoin Core's RNG (Ben, 2026-08-20).**
  Ben's call, in his words: Core is the most trusted software there is, so
  if you are going to trust any RNG, trust that one. Against other devices
  this is a selling point, because their RNG is a vendor's and Core Signer's is
  the reference implementation's.

  **What it changes.** A-9 froze v1 with *no on-device seed generation*, and
  A-18 recorded that no device RNG exists or is used. Both stand for
  CORESIGNER's own code: Core Signer still contains no RNG, calls no `os.urandom` and
  imports neither `random` nor `secrets` (tests/test_generate.py enforces
  this statically and at import time). What is now permitted is asking
  BITCOIN CORE for entropy, on an explicit opt-in path, in a v1.1 tool that
  sits beside the codex32 tools rather than in the seed-entry flow.

  **A-19a: the tradeoff screen and the forced backup are removed (Ben,
  2026-09-05).** New key showed three lines about what software entropy
  can and cannot prove, and made you press A to accept them. It then walked
  you into a backup you could not leave without losing the key.

  Ben's call, in his words: *"when I click generate key it should just
  generate"*, and *"Backup key or file should be an option after the key is
  created, as it is, why is that in the flow?"*

  The tradeoff is not deleted, it is moved. The README states it under
  Core's entropy, at a length a 320x240 panel cannot carry, and it now
  states it more accurately than the screen did: the software IS auditable,
  because Core's builds are reproducible and the hashes are pinned in
  `image/PINS`; the silicon and the kernel under it are not. The screen's
  version said software entropy could not be audited at all, which was
  wrong, and it was wrong in a place with no room to correct it.

  **A-19b: Core's RNG is the only way to create a key here, and the
  "cards and dice by default" claim was false (Ben, 2026-09-05).** He
  asked whether Core's RNG is safe as a default over dice, and noted that
  cards cannot work because this is BIP32 and not BIP39. Checking it
  showed the README had been claiming a path that does not exist.

  Dice cannot make a key on this device. Turning rolls into a key is BIP32
  master derivation, HMAC-SHA512 over the entropy, and two things would
  have to be true for that: Core Signer would need a cryptographic primitive,
  which A-22 forbids and `tests/test_integrity.py` enforces; or Core would
  need an RPC that accepts raw entropy, and `sethdseed` went with the
  legacy wallets. Confirmed against v31.1 on the board: `help sethdseed`
  answers "unknown command". `tests/test_generate.py` now watches that
  fact, so if a future Core reopens the door the dice question reopens
  with it.

  So the choice is Core generating the key here, or bringing an xprv made
  on another machine and trusting that machine's RNG. The README said
  "cards and dice by default" in one place and "out of scope for v1:
  dice entropy" sixty lines below. The second was right. Both are now
  consistent, and the entropy is stated in numbers rather than adjectives:
  the kernel credits the SoC generator a full bit per bit and it delivers
  about 950,000 credited bits per second against a 256-bit requirement.
  The honest caveat is concentration, not scarcity: fewer independent
  sources sit behind those bits than on a laptop (map R2, R4).

  A-19's substance is untouched: `createwallet` makes the master key with
  Core's own RNG, Core Signer signs with that same wallet, and no line of ours
  sits between the two. Backup key is a row on the key's own menu, taken
  when the user wants it. `tests/e2e_session.py` session G asserts the
  backup menu does not follow generation at once.

- **A-24: the private key leaves on paper only (Ben, 2026-09-05).** The
  encrypted file backup, its passphrase, and restore-from-file are cut.
  Backup key goes straight to the pages. Ben's words: "you can and should
  only be able to export by writing it down on paper, no encryption
  needed."

  **What it removes.** `signer.backup_encrypted`, `restore_encrypted`,
  `find_backups`; `main._backup_file`, `_ask_passphrase`,
  `_confirm_no_passphrase`, `_key_from_file`, `_backup`;
  `screens.encrypt_menu`, `no_passphrase_warning`, `restore_menu`,
  `backup_menu`, `BACKUP_OPTIONS`, `fingerprint_of_backup`, and the
  passphrase alphabet. 179 lines of shipped code, and Restore from file
  leaves the KEYS screen with it.

  **What it keeps.** The file channel, for PUBLIC data only: the
  watch-only wallet file Bitcoin Core needs because it reads no QR, and
  PSBTs. The worst a hostile card can do there is hand Core something Core
  rejects.

  **Why.** This board has one card slot and that card is the boot device,
  so an encrypted backup would go either to the boot card, which A-23 kept
  hedging about, or to a USB stick. Drawing the line at the key rather
  than the medium means a key that was never written to a medium cannot be
  taken off one. It also deletes the third of the three moments Core Signer sees
  a key: the backup passphrase on its way to `encryptwallet`. Two moments
  remain, in and to paper.

  **One correction to the premise, recorded because it changes the
  reasoning and not the decision.** USB host already works on the Zero 2 W
  (`otg_mode=1`, `dwc2` in host mode, gadget modules blacklisted), so a
  stick was available and this was a choice rather than a hardware
  constraint. Ben confirmed it after hearing that.

  **The cost, stated plainly.** The only backup is now 111 handwritten
  characters. No second copy, no encryption at rest, and the only way back
  is scanning or typing them. That is what makes the paper check built the
  same day load-bearing rather than a nicety.

  **Where the removed code goes.** butlers-playground, this repo's fork,
  which is 26 commits behind. Map ticket N4 carries that job.

- **A-20: the outbound QR carries fountain parts (Ben, 2026-09-03).**
  `psbt_to_frames` used to return exactly one pure cycle, which the display
  looped. That is fragile in a way only Sparrow's decoder shows. Core Signer renders
  244-character UR frames as a 49x49 QR; with the quiet zone that is 53
  modules, and the 320x240 panel allows `box_size = 240 // 53 = 4`. So Core Signer
  renders at exactly 4.0 pixels per module and cannot go higher without fewer
  modules. Measured over 375 frames
  (`tests/m1/outbound_margin.py`), three of them (0.8%) cannot be decoded by
  **zxing**, the library Sparrow uses, while `pyzbar` reads them. The failure
  is deterministic, five attempts out of five, so looping a pure cycle shows
  the scanner the same unreadable image forever. At 13 to 21 frames per PSBT
  that is roughly one transfer in seven that can never complete.

  **The decision.** Emit `seq_len * FOUNTAIN_REDUNDANCY` frames, with
  redundancy 2. Everything past the pure cycle is a fountain part and can stand
  in for one the scanner never got, which is what Sparrow's own `UREncoder`
  does when it sends to us. Proved by dropping each pure part in turn: 11 of 11
  individually droppable.

  **What it costs.** The frame count doubles, 21 to 42 for a six-input P2WPKH
  PSBT and 13 to 26 for P2TR, so a scanner that reads everything first time
  waits twice as long. Ben took that over a transfer that can hang.

  **Rejected.** Lowering `MAX_FRAGMENT_LEN` to raise the margin: it adds frames
  too, and only makes the failure rarer rather than recoverable. Accepting it
  and documenting a restart: it asks the user to diagnose a symptom whose cause
  they cannot see, on a device whose claim is that you can trust what it shows
  you.

  Reasoning and rejected options in full:
  the m1-qr-without-optics map, ticket 09 (archived; see docs/wayfinder/README.md).

  **Mechanism (revised same day at Ben's direction: EXACTLY as a Core
  wallet, no shaping).** `signer.generate_wallet()` calls `createwallet`,
  so Core generates the master key with its own `GetStrongRandBytes` and
  derives its standard descriptor set — key generation identical to any
  Core wallet's birth. Core Signer then USES that very wallet to sign; nothing
  is re-derived. The backup shown to the user is Core's own MASTER XPRV,
  read verbatim from the descriptors Core wrote (`listdescriptors true`
  stores the depth-0 master in every descriptor; empirically verified,
  and the code asserts all descriptors share one master). Nothing of ours
  sits between Core's RNG and the paper: no extraction, no hashing, no
  encoding of ours. Statelessness holds: the wallet lives in the ramdisk
  session and close_session deletes it.

  **Backup form.** The master xprv string, in Core's own base58check
  encoding, transcribed in 4-char groups. NOT codex32: an xprv is a BIP32
  node (key + chain code), not a seed preimage, and BIP93 encodes seed
  preimages — there is no seed to encode when Core births the master
  directly. No split option either, for the same reason; guardianship of
  an xprv backup is Kaitiaki's lane. Restore is the existing xprv entry
  mode (pure Core), which recreates the BIP84/86 active set — the only
  paths Core Signer ever hands out addresses from (verified by test: restored
  wallet derives identical BIP84 addresses). Core cannot produce BIP39
  words and Core Signer will not invent them; the screen says so.

  **Verification.** The user confirms transcription, then Core Signer re-derives
  the wallet from the codex32 strings it displayed (shares are recombined,
  not read back out of memory) and shows the first receive address, so the
  transcription can be checked later against any wallet restored from it.

  **The honest caveat, corrected 2026-09-05 (Ben).** The first version of
  this said software entropy is "unverifiable by inspection", and that was
  too broad. Two questions, one answer each.

  The CODE is verifiable. Core's builds are reproducible, so the binary
  corresponds to source anyone can read. Eight signatures on the 32.1
  hashes were checked out of band on 2026-11-04, from a different host to
  the one serving the binary (31.1: eleven, on 2026-09-03). The hash is pinned in image/PINS and
  provisioning refuses to install without it. The M3 release image gets the
  same treatment for the whole card.

  The INPUTS and the OUTPUT are not. The kernel's getrandom, the processor
  and the SoC's hardware generator all sit outside Core's reproducible
  build, and verified code fed by a compromised kernel produces exactly the
  bytes it is given. Randomness also cannot be checked after the fact: a
  backdoored generator and a good one produce output that looks the same,
  which is the point of a backdoored one.

  So cards and dice do not fix the software, which was never the weak part.
  They move the unpredictable step out of the machine, into something you
  watch happen. A-19 adds an option; it does not move the recommendation.


- **A-21: M0 ran on the board (2026-09-03). The verdict depends on the PSBT's
  shape, not on its input count.** First run: FAIL, 48MB of headroom against
  the 100MB line. The cause was not the board. It was reserving 64MB for a GPU
  it never uses, on a device whose only panel is a 320x240 ST7789 on SPI.

  **`gpu_mem=32`, and the KMS overlay stays.** Six configurations measured, one
  reboot each. Disabling `vc4-kms-v3d` gains nothing at all, 414MB against
  415MB, so it is left enabled and the HDMI console keeps working. Every
  megabyte comes from the split. `gpu_mem=16` gains the most and is below the
  floor: it kills the VideoCore services with `vc_sm_cma_vchi_init: failed to
  open VCHI service (-22)`, which takes `bcm2835_isp` with it, and that is the
  ISP libcamera uses. A control run on the stock config prints
  `[vc_sm_connected_init]: installed successfully` with no error lines, so the
  regression was ours. 32 is the smallest split that stays healthy: 447MB
  usable, 307MB free at idle, `picamera2` imports, both spidev nodes present.

  **What the gate then measured, all at 250 inputs on `gpu_mem=32`:**

  | funding shape | PSBT | bitcoind | Core Signer | headroom | |
  |---|---|---|---|---|---|
  | 2 outputs per tx (ordinary payments) | 92KB | 65MB | 21MB | **226MB** | PASS |
  | 100 outputs per tx (exchange batches) | 980KB | 126MB | 64MB | 97MB | FAIL |

  Same board, same input count, 131MB apart. Every input's `non_witness_utxo`
  is the whole transaction that paid it, so the funding shape sets the PSBT
  size per input: 378 bytes against 2778, a factor of 7.3.

  **Two things this settles.** The 100MB rule was written as though bitcoind
  were the only consumer; Core Signer's own process is a third of the total at the
  worst case, and nobody had measured it. And the old harness hard-coded 100
  outputs per funding transaction to fund quickly, which accidentally modelled
  consolidating exchange batch withdrawals. That is a real worst case, so it
  stays the default, but `--funding-batch` now makes the shape visible and
  selectable, and the report prints it.

  **The pocket build's honest ceiling: it signs 250 ordinary inputs with more
  than double the required headroom, and falls 3MB short of the line on 250
  batch-withdrawal inputs.** A-15 already ruled that M0's 512MB question gates
  the pocket build and not v1, and v1 is the CM4 with 2GB.

  Reducing Core Signer's 64MB is the one fix that makes Core Signer better rather than the
  board bigger: `describe_psbt` parses the full `decodepsbt` document, which
  at the worst case expands 25,000 output objects it never reads, when all it
  needs is `tx.vout` and the fee. Not attempted. Its own ticket.

- **A-22: Core Signer forks. `main` is a pure signer with ZERO code that touches
  secrets (Ben, 2026-09-04).**

  JW Weatherman, told Core Signer is a tiny UI over Core: *"your only focus should
  be in minimizing any additional code you add. As soon as code review is
  required you are half way to a rug product... If you can add no code at all
  that's the ideal. Next best is the tiniest UI needed and absolutely nothing
  more."*

  Measured the same day. Layer 1, the code that transforms secret material,
  was 342 lines: the 50-line BIP39 shim, 254 lines of codex32 and 38 of
  SeedQR. **codex32 alone was 74% of it.**

  **The decision: `main` carries none of them. Layer 1 becomes zero lines.**

  Keys reach Core three ways, and Core Signer transforms nothing on any of them:
  Core generates one with its own RNG (A-19), or the user supplies an **xprv**
  or a **descriptor**, typed or scanned, which Core Signer passes to
  `importdescriptors` as an opaque string. Bitcoin Core has no BIP39 and never
  will, so the shim existed only to accept a seed phrase.

  The claim stops being "read one page" and becomes **"there is nothing to
  read"**.

  **The cost, stated plainly.** Core Signer cannot accept a 12 or 24 word seed
  phrase. Nobody can bring the words from an existing hardware wallet. A
  backup is Core's 111-character master xprv, not words, and it cannot be
  split. Ben took that cost knowingly: the purity is the product.

  **The lab branch** carries everything removed, plus everything the
  key-provenance map decided, plus silent payments later. It is for Ben and
  the few who read code and want the device to do more than generate and sign.
  It merges `main` forward, so every signer fix reaches it and no fix is ever
  applied twice.

  Consequences for this plan: A-18's codex32 backup, A-14's SeedQR input mode
  and the BIP39 word-entry flow all move to the lab. M4's "metal-backed seed"
  means an xprv on metal for `main`.

- **A-23: the file backup is Core's own, and the SD-card rule is amended
  (Ben, 2026-09-04).** Charted with the e2e-before-testers map
  (the e2e-before-testers map (archived; see docs/wayfinder/README.md)), where each decision has a
  ticket.

  **The file backup.** `encryptwallet` then `backupwallet`, Core's own
  commands, give a passphrase-encrypted wallet file that another Core
  restores with `restorewallet` and unlocks with `walletpassphrase`. Proven
  2026-09-04 on two regtest nodes: no plaintext key in the file, spend
  refused without the passphrase, spend allowed after. Core Signer's part is one
  passphrase screen and two RPC calls. Ben's condition, met: it is what Core
  does, so Core Signer's value proposition and line count do not move.

  **The fixed decision "No keys on the SD card, ever" now reads:** Core Signer
  never writes a key on its own. A backup the user asks for, encrypted by
  Core with a passphrase the user typed, written to a medium the user names
  (the USB stick or the boot card, asked every time), is allowed. The
  README states it plainly.

  **Superseded by A-24 (2026-09-05), one day later.** The encrypted file
  backup is deleted. The rule went back to being about the KEY rather than
  the medium: the private key leaves this device on paper and no other
  way. A-23's reasoning about which medium is safe is kept because it is
  what A-24 answered, not because any of it is still in force.

  **Also decided in the same session, recorded in the map's tickets:** home
  becomes SeedSigner's Scan, Key, Tools, Settings, with generation under
  Tools; Core Signer holds up to five keys at once, one Core wallet per key named
  by fingerprint, measured at about 3MB each on the Zero 2 W; Scan detects
  by content; export follows SeedSigner's wallet chooser with a plain
  descriptor QR, no UR, and a watch-only wallet file for Bitcoin Core;
  menus use Core's vocabulary (Receiving addresses, not an address
  explorer). Mainnet only for this map; a network switch is out of scope.

## Post-v1 todo / hardening backlog (from the round-2 audit, 2026-08-18)

- **Secret hygiene: xprv-bearing RPC params travel as bitcoin-cli argv**,
  visible in process listings during the seconds of import. Single-user
  device, but move secret-bearing calls to `bitcoin-cli -stdin` at M2.
- Passphrase and typed xprv/descriptor entry have no on-device UI yet
  (dev-mode args only); build both text-entry screens at M2 alongside the
  camera. Testnet subdir map assumes testnet3; revisit if Core defaults to
  testnet4.

## Post-v1 todo

- **Codex32 (BIP93) seed entry** as a fourth input mode: bech32 seed shares,
  k-of-n recovery, hand-verifiable checksums; authored by Wuille/O'Connor.
  FractalEncrypt has a full SeedSigner implementation in progress
  (FractalEncrypt/FractalEncrypt_seedsigner, Codex32_Implementation branch).
  **SUPERSEDED by A-18 (Ben lifted the front-run concern 2026-08-18);
  codex32 is implemented as a v1.1 Core-native mode.** Original note:
  deferred until Fractal publishes (Ben, 2026-08-17). Revisit after his release; his QR share specs
  would be the compatibility target. BIP93 read in full (2026-08-18):
  codex32 encodes BIP32 MASTER SEEDS (not BIP39 entropy); checksum, split
  and recover are all pen-and-paper (BCH lookup tables). As an input mode
  it would sit beside the Core-native modes (seed -> xprv, no PBKDF2),
  and hand-verifiable shares allow decades of backup integrity checks
  with zero re-exposure to hardware. Constraint (Westgate): Core does not
  export seeds, so codex32 backup applies to externally-born seeds
  (cards), which is Core Signer's model anyway. Momentum, evidence-graded (2026-08-18): Westgate says he is building a
  CLI/GUI and was commissioned by Blockstream for Jade — but Jade firmware
  at master contains ZERO codex32 code; the only repo trace is open issue
  Blockstream/Jade#129 ("Wen codex32?", Apr 2024, Blockstream planning a
  manual-entropy seed product) with Westgate's own Sep 2024 comment asking
  why no PR exists. Treat as intention, not commitment; no urgency for
  Core Signer's timing. The Fractal deferral is about his SeedSigner UI;
  supporting the published BIP itself is a separable later decision
  (Ben's call).
- Anti-exfil: not implementable via Core's RPC today (no nonce hook in
  walletprocesspsbt); documented as a stated trade-off in the README. Watch
  upstream Core for any sign-to-contract / anti-exfil RPC support.

## The idea in three sentences

Take SeedSigner's hardware (camera, small screen, no persistent secrets) and replace its
signing brain (embit, a Python reimplementation) with Bitcoin Core itself, running
wallet-only and offline. The device scans a PSBT as QR, shows the fee and outputs
computed by Core, signs with `walletprocesspsbt`, and returns the signed PSBT as QR.
Nothing is retained: the wallet lives on a ramdisk and dies at power-off.

**Why:** every DIY signer today trusts a rewrite of Bitcoin's wallet logic. This device
trusts the most reviewed Bitcoin code that exists, on hardware you assemble yourself.

## Fixed decisions (from Ben)

- Board: **Raspberry Pi Zero 2 W** (official aarch64 Core binaries run; no cross-compile).
- **Stateless.** No keys on the SD card, ever. SeedSigner's security model, Core's code.
- Existing kit reused: SeedSigner cases, WaveShare 1.3" LCD hat, Pi camera, metal seed plates.

---

## Plan v1

### Hardware
1. Pi Zero 2 W in the existing SeedSigner case with the existing LCD hat and camera.
2. Radios disabled in firmware: `dtoverlay=disable-wifi`, `dtoverlay=disable-bt` in
   `config.txt`, plus blacklisted kernel modules and disabled services.

### Software stack
1. Raspberry Pi OS Lite **64-bit** (needed for the official `aarch64-linux-gnu` Core binary).
2. Official Bitcoin Core release binary, signatures verified during image build.
3. `bitcoind` config: `-networkactive=0 -listen=0 -dbcache=4 -maxmempool=5
   -rpcthreads=1`, datadir on a **tmpfs ramdisk**. (`blocksonly` was in v1 of
   this plan and removed: it rejects the node's own wallet txs in the M0
   harness and buys nothing offline — see m0/bitcoin.conf.)
4. A thin Python front end (~300–500 lines): screen menus, camera QR scan (zbar),
   QR display (animated for large PSBTs), and RPC calls to localhost. It performs
   **no cryptography**.

### Session flow (stateless)
1. Power on. bitcoind starts against a fresh ramdisk datadir.
2. User enters the BIP39 seed (SeedSigner-style word entry or SeedQR scan).
3. Mnemonic → xprv conversion, then `createwallet(blank=true)` +
   `importdescriptors` with private descriptors (BIP84/86 paths).
4. Scan PSBT from coordinator (Sparrow, Specter, or Core itself).
5. `decodepsbt` / `analyzepsbt` → screen shows outputs, amounts, **fee** (Core's numbers).
6. User confirms → `walletprocesspsbt` → signed PSBT rendered as animated QR.
7. Power off. Ramdisk gone; device holds nothing.

### Milestones
- M0: bitcoind boots and signs a testnet PSBT over RPC on the Zero 2 W (no UI). Measure RAM.
- M1: full QR round trip with Sparrow watch-only, testnet.
- M2: stateless flow (seed entry each session), UI on the LCD hat.
- M3: hardening (radio kill verification, reproducible image build), mainnet trial with small sats.

---

## Devil's advocate, round 1

**DA-1. "Core's logic only" is already broken at step 3.** Bitcoin Core has no BIP39.
The mnemonic→xprv step must come from somewhere, and if that somewhere is embit, the
project's thesis collapses: the seed derivation, the root of everything, is the exact
reimplementation we set out to remove. This is the strongest objection to the whole design.

**DA-2. 512MB is asserted, not measured.** A 64-bit OS has a bigger memory footprint than
32-bit, bitcoind's minimal RSS on this config is undocumented, and the ramdisk *also*
comes out of the same 512MB. If bitcoind plus OS plus tmpfs plus the Python UI does not
fit, the plan dies at M0 and the milestones above pretend otherwise.

**DA-3. Seed entry each session is the attack surface.** Statelessness means typing or
scanning the seed on every use. A tampered image or a hidden persistence path (swap,
journald on SD, core dumps) could leak it. SeedSigner mitigates with a tiny auditable
codebase and no writable persistence during operation; this design boots a full Linux
with a writable root filesystem.

**DA-4. Why does this exist? Sparrow + SeedSigner already works.** Steelman the null
hypothesis: embit is small, heavily used, and easier to audit end-to-end than bitcoind.
"More reviewed code" is not automatically "smaller attack surface": bitcoind is two
orders of magnitude more code, and most of its review effort targets consensus, not
the wallet. The honest claim must be narrower.

**DA-5. Radio disable in firmware is a config line, not an air gap.** `config.txt` lives
on the SD card; anyone (or any compromise at image-build time) can re-enable WiFi by
editing a text file. The Zero 1.3's advantage was physics. Calling this device
"air-gapped" without a hardware step overstates it.

**DA-6. Blank-datadir chain assumptions.** With no chain, Core cannot check that the
PSBT's inputs exist or that locktimes/sequences make sense against the tip. Fine, all
signers share this limit, but `analyzepsbt`'s fee number depends entirely on the
coordinator-supplied `witness_utxo` amounts. A malicious coordinator can lie about the
fee to any air-gapped signer. Do not market fee display as stronger than it is.

## Amendments → Plan v2

**A-1 (answers DA-1).** No embit, no external crypto library. BIP39→seed is
PBKDF2-HMAC-SHA512 (Python stdlib `hashlib.pbkdf2_hmac`); seed→xprv is one
HMAC-SHA512 with key `"Bitcoin seed"` plus Base58Check encoding: about 60 lines of
stdlib-only code, no elliptic-curve math anywhere, verifiable line-by-line against the
BIP32/BIP39 test vectors, frozen once written. All EC operations (child key derivation,
signing) happen inside Core via the imported xprv descriptor. The trust statement
becomes exact: *stdlib hashing for seed decode, Core for every key operation.*
Also support scanning the existing SeedQR format so Ben's current backups work as-is.

**A-2 (answers DA-2).** M0 is promoted to a **go/no-go gate with numbers**: boot the
64-bit Lite image, run bitcoind on the stated flags, import a descriptor wallet, sign a
1000-input stress PSBT, and record peak RSS and free memory. Mitigations pre-planned:
zram instead of disk swap (never SD swap: seed material must not touch flash),
`rpcthreads=1`, and if 64-bit truly does not fit, the documented fallback is the same
design on a Pi 3A+/CM4-based board, not a return to cross-compiling.

**A-3 (answers DA-3, DA-5).** Hardening becomes part of the design, not a milestone tail:
read-only root filesystem (overlayfs), no swap-to-disk ever, journald to tmpfs,
core dumps off, and the radio kill done **twice**: firmware overlay *and* the physical
option documented (the Zero 2 W wireless front-end component is known and removable;
offer it as the "1.3-grade" assurance step for those with a hot-air station, with the
firmware-only path as standard). Image built reproducibly with published hashes.

**A-4 (answers DA-4).** Rewrite the claim. Not "smaller attack surface" but a **different
trust root**: SeedSigner asks you to trust embit's reimplementation of wallet logic;
this asks you to trust Bitcoin Core's implementation of wallet logic. Both are honest
choices; this project exists so the second one is *available* on DIY hardware, and as
a teaching artefact (the signer speaks the same RPCs as the node). Also the practical
win: descriptor semantics, PSBT handling and sighash logic are bit-for-bit the same
code Core uses, so coordinator/signer disagreements vanish by construction.

**A-5 (answers DA-6).** The fee screen states its own epistemics: fee is computed by
Core *from coordinator-supplied input amounts*. Show the fee **and** the total input
sum, and print the one-line warning SeedSigner users already know. No overclaiming.

---

## Devil's advocate, round 2

**DA-7. The 60-line stdlib shim still writes itself into the TCB.** Sixty lines with a
frozen hash and test vectors is defensible, but who reviews it? If the answer is "Ben
and Claude", the project's core marketing claim ("you trust Core, not a rewrite")
carries an asterisk that must be printed, not hidden.

**DA-8. Read-only root vs stock Raspberry Pi OS drift.** Overlayfs-on-Lite is a
well-trodden path, but every OS update reopens the hardening questions. A general-purpose
OS is the price of running Core; fine, but the image build must pin versions and the
project must state that its security posture is "hardened general-purpose Linux",
which is below SeedSigner's "no OS persistence to speak of" and above a laptop.

**DA-9. Boot time and UX honesty.** 64-bit Lite boot plus bitcoind init plus seed entry
plus descriptor import will plausibly cost 60–120 seconds before the first scan. For a
cold-storage tool used monthly this is fine, but if the real UX lands at three minutes,
users will stop powering off between steps, which erodes statelessness in practice.
Measure at M2 and set a hard budget.

**DA-10. Scope creep is the likeliest death.** Multisig registration, taproot script
paths, message signing, dice entropy, passphrase support: each is a reasonable ask and
each doubles the UI. SeedSigner took years to cover this surface. Version 1 must refuse
almost everything.

**DA-11. Core version upgrades.** Each Core release can change RPC fields
(`analyzepsbt`/`decodepsbt` output has changed before). Pin one Core version per image
release; the front end targets exactly that version. Never "latest".

## Amendments → Plan v3 (the reviewable plan)

**A-6 (answers DA-7).** The shim ships in the repo as a single file with its SHA256 in
the README, its test-vector run in CI, and an explicit invitation for external review.
The asterisk is printed. Long-term option noted: Sjors Provoost's Rust BIP39 tool
(bitcoin PR #32115) as an alternative shim from a Core contributor.

**A-7 (answers DA-8, DA-11).** One pinned tuple per release:
{RPi OS Lite 64-bit image hash, Core version + binary hash, front-end commit}.
The image is built by script from those pins. No apt upgrades on-device; updating means
reflashing. This is also what makes the build reproducible and teachable.

**A-8 (answers DA-9).** Hard UX budget: **power-on to ready-to-scan in under 90 seconds**
(bitcoind starts during seed entry, so the human is the critical path). If M2 misses
the budget, cut boot (systemd mask-list, no HDMI probe, quiet kernel) before touching
the security posture.

**A-9 (answers DA-10).** **V1 scope, frozen:** single-sig BIP84 and BIP86, BIP39 with
optional passphrase, SeedQR in, PSBT in/out via animated QR (UR2 for Sparrow
compatibility), fee/output review screen. Explicitly out of v1: multisig, message
signing, address explorer, dice entropy, on-device seed generation. (Seed generation
stays out precisely so the device never needs an entropy story; it signs for seeds
that already live on metal.)

### Final build sequence

| Gate | Deliverable | Pass condition |
|---|---|---|
| **M0** | bitcoind wallet-only on Zero 2 W, headless | Signs stress-test PSBT; peak RSS recorded; ≥100MB headroom |
| **M1** | QR round trip vs Sparrow watch-only, testnet | Fee/outputs on screen match Sparrow; signed PSBT broadcasts |
| **M2** | Stateless UI on LCD hat | Power-on→ready < 90s; power cycle provably wipes (RAM/ramdisk audit) |
| **M3** | Hardened reproducible image | Read-only root, radios dead (`rfkill` + no wlan0), image hash reproducible on second machine |
| **M4** | Mainnet trial | Small-sats spend from a metal-backed seed, end to end |

### Cost
Zero build cost beyond what is on hand if a spare Zero 2 W exists; otherwise one
Zero 2 W (~NZ$40). Everything else (case, hat, camera, SD) is SeedSigner kit already owned.

### What I need from Ben at review
1. Confirm a Zero 2 W is on hand (or I list one to order) and which coordinator is the
   v1 target (Sparrow assumed).
2. Approve the v1 scope freeze (A-9), especially *no on-device seed generation*.
3. Approve the shim asterisk (A-6): 60 lines of stdlib hashing sit outside Core.
4. Name the project.
