# M0 on the Pi: flash, boot, run, read numbers

Your part is about 20 minutes of hands-on time per board. The Pi does the
rest. Two boards are in scope and they need different network paths.

| Board | RAM | Network | Card slot |
|---|---|---|---|
| Pi Zero 2 W | 512MB | WiFi, 2.4GHz only, set in Imager | on the board |
| CM4 Lite on CM4-IO-BASE-B | 2GB | Ethernet cable, no radio on CM4002000 | on the carrier |

**Run the Zero 2 W first.** It carries the gate. M0 asks whether wallet-only
bitcoind fits in 512MB, and only the Zero can answer that. The CM4 has 2GB
and passes by arithmetic, so its run is a smoke test of the same toolchain
on the primary build, not a gate.

## 1. Flash (on the Mac)

1. Raspberry Pi Imager, device **Raspberry Pi Zero 2 W** (or **CM4**), OS
   **Raspberry Pi OS Lite (64-bit)**. 64-bit is required: the official Core
   binary is aarch64.
2. In Imager's settings (gear icon): hostname `coresigner`, enable SSH with a
   password, set locale. For the Zero 2 W add your **2.4GHz** WiFi. The
   Zero 2 W radio is single band and does not see a 5GHz network.
   Radios die at M3, not M0.
3. Flash to a spare card, one per board. Not your SeedSigner card.
4. Reinsert the card. macOS mounts the boot partition, then run:

   ```bash
   cd ~/clawd/projects/coresigner && ./image/prepare-sd.sh
   ```

   This copies Core Signer, the pinned build tuple and the provisioning script to
   the card. Eject.

## 2. First boot

**Zero 2 W:** power through the micro-USB port silkscreened `PWR IN`, the
one at the outer corner. The middle port marked `USB` is data and does not
power the board.

**CM4:** fit the CM4 to the carrier, put the card in the carrier's microSD
slot, and leave the **BOOT jumper open**. Closed means USB boot for writing
eMMC, which a Lite module does not have. Plug the Ethernet cable in. Power
through the carrier's USB-C with the 15.3W supply.

Then, from the Mac:

```bash
ssh <user>@coresigner.local
```

## 3. Provision

On the Pi:

```bash
sudo bash /boot/firmware/coresigner-provision.sh
```

This installs Bitcoin Core 32.1 against the hash pinned in `image/PINS`,
installs the display, GPIO, camera and QR packages, unpacks Core Signer to
`/opt/coresigner`, makes the ramdisk datadir, and turns on SPI for the hat. It is
idempotent. On a Zero 2 W over WiFi it takes about 15 minutes, most of it
`python3-picamera2`.

The Core hash was verified out of band on 2026-09-03: 11 good GPG signatures
on `SHA256SUMS`, keys taken from the guix.sigs repo rather than from
bitcoincore.org, and four builders attest the same hash independently. The
script refuses to install Core against an unpinned hash, on purpose. A
checksum fetched from the server that served the binary proves nothing.

## 4. Run the gate

Do all of it in **one** SSH session. Both commands revert at reboot.

```bash
sudo systemctl stop dev-zram0.swap   # see below
sudo swapoff -a                      # any disk swap as well
cd /opt/coresigner && python3 m0/m0_gate.py --inputs 250
```

RPi OS enables swap by default, and swap falsifies both gate numbers, so
the gate refuses to run with it on. On Trixie `swapoff -a` on its own does
not hold: `systemd-zram-generator` owns `dev-zram0.swap`, `swap.target`
wants it, and systemd re-activates the unit seconds after the device goes
away. Measured 2026-09-03: swap was gone in one SSH session and back in
the next. Stop the unit, and stay in the same session.

## 5. Read the verdict

The script prints `M0 PASS` or `M0 FAIL` with the numbers. Record:

- peak bitcoind RSS (MB)
- MemAvailable low-water (MB)  <- the pass line: **must stay >= 100**
- peak SoC temperature (C), and any `!!` throttle line
- the three timings (bitcoind start, session open, stress sign)

**Two more, while the board is in front of you.** Neither is part of the
gate and both are unmeasured, which is why they are worth taking now
rather than wishing for later:

- **Boot time**, power to Core Signer's own splash. Never recorded. This flash
  adds `boot_delay=0` and `disable_splash=1`, both taken from SeedSigner
  OS's config for this same board, so the number is worth having.
- **MemAvailable with nothing running.** `free -m` before you start
  bitcoind, after a clean boot. PLAN A-12b works out that Raspberry Pi OS
  Lite costs about 168MB resident, by subtracting the M0 numbers from the
  480MB the board has after `gpu_mem=32`. That subtraction decides
  whether the RAM-resident image of A-12 saves memory or spends it, and
  one `free -m` measures it directly instead of deriving it.

Temperature is in the report because ORDER.md drops the heatsink to save
7.5mm of case. Throttling costs sign time and nothing else, so it does not
fail the gate. An `under-voltage` line is different: it means the supply is
weak, and a weak supply spoils every other number above it.

## 6. What a Zero 2 W actually does (measured 2026-09-03)

All on `gpu_mem=32`, swap off. `--funding-batch` sets how many outputs each
funding transaction has, which sets the PSBT size per input, because every
input carries the whole transaction that paid it as its `non_witness_utxo`.

| inputs | funding shape | PSBT | bitcoind | Core Signer | headroom | |
|---|---|---|---|---|---|---|
| 250 | 2 (ordinary payments) | 92KB | 65MB | 21MB | 226MB | PASS |
| 500 | 2 | 184KB | 70MB | 26MB | 206MB | PASS |
| 1000 | 2 | 368KB | 82MB | 32MB | 184MB | PASS |
| 250 | 100 (exchange batches) | 980KB | 126MB | 64MB | 97MB | FAIL |

The default is 100, which is the worst case and the one that fails, by 3MB.
Do not read the failure as "512MB is not enough". The same board signs a
thousand ordinary inputs with 184MB spare. See PLAN.md A-21.

The old reference figure here said bitcoind RSS about 99MB, taken from the
Mac. It was wrong in a way the Mac could not show: see TESTING.md
rule 9, which is the rule that defect produced.

Paste the report block back to Claude and M0 is closed either way.
