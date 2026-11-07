"""M0 go/no-go gate: does wallet-only bitcoind fit and sign on 512MB?

Run ON THE PI (works on a dev machine too, with reduced measurements):
    python3 m0/m0_gate.py [--inputs 250]

What it does, with the exact production memory flags from m0/bitcoin.conf:
  1. Starts bitcoind on regtest in a temp datadir on the SD card
     (/var/tmp, or --datadir-root), never in tmpfs.
  2. Opens a Core Signer session (importdescriptors), timed.
  3. A miner wallet funds Core Signer with N separate UTXOs.
  4. Core Signer builds and signs a PSBT spending ALL N inputs (the stress case:
     PSBT size and signing cost scale with input count).
  5. Records peak bitcoind RSS (VmHWM), system MemAvailable, and timings.

PASS condition (from PLAN.md): stress PSBT signs, and on the Pi
MemAvailable never drops below 100MB.
"""

import argparse
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "coresigner"))
import signer  # noqa: E402

# A-22 removed the BIP39 shim. This is the key that mnemonic
# produced on a test network, so the gate measures the same wallet
# it always did.
XPRV = ("tprv8ZgxMBicQKsPe5YMU9gHen4Ez3ApihUfykaqUorj9t6FDqy3nP6eoXiAo2ssvp"
        "AjoLroQxHqr3R5nE3a5dU3DHTjTgJDd7zrbniJr6nrCzd")
FLAGS = ["-regtest", "-dbcache=4", "-maxmempool=5", "-rpcthreads=1",
         "-networkactive=0", "-listen=0", "-server=1",
         "-fallbackfee=0.0001", "-debuglogfile=0"]
# Module-level so a test can stand a directory in for the board's.
PROC = Path("/proc")
# Not /tmp, which is tmpfs on Trixie: a datadir there puts a preallocated
# 16MB blk00000.dat in the very RAM the gate measures, and the device's
# own datadir already holds one (2026-11-06 diagnosis: 18MB of Shmem).
# provision.sh works on disk in /var/tmp for the same reason.
DATADIR_ROOT = Path("/var/tmp")
# Where gates before this one made their datadirs.
TMP = Path("/tmp")
DATADIR_PREFIX = "coresigner-m0-"
RAM_FILESYSTEMS = {"tmpfs", "ramfs"}


def vm_hwm_mb(pid):
    """Peak RSS in MB. pid may be "self" for this process."""
    status = Path(f"/proc/{pid}/status")
    if not status.exists():  # dev machine (macOS): current RSS via ps instead
        import os
        real = os.getpid() if pid == "self" else pid
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(real)],
                             capture_output=True, text=True).stdout.strip()
        return int(out) // 1024 if out else None
    for line in status.read_text().splitlines():
        if line.startswith("VmHWM"):
            return int(line.split()[1]) // 1024
    return None


def meminfo_mb(field):
    meminfo = PROC / "meminfo"
    if not meminfo.exists():
        return None
    for line in meminfo.read_text().splitlines():
        if line.startswith(field + ":"):
            return int(line.split()[1]) // 1024
    return None


def mem_available_mb():
    return meminfo_mb("MemAvailable")


def filesystem_of(path):
    """(mount point, fstype) holding path, or None where /proc/mounts is
    absent (dev machine: no verdict either way)."""
    mounts = PROC / "mounts"
    if not mounts.exists():
        return None
    target = str(Path(path).resolve())
    best = None
    for line in mounts.read_text().splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        # The kernel writes a space in a mount point as \040. It lists
        # mounts in the order made, and a later mount at or above a path
        # hides what was below it, so the last line that holds it wins.
        point = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)),
                       parts[1])
        if (target == point or point == "/"
                or target.startswith(point.rstrip("/") + "/")):
            best = (point, parts[2])
    return best


def preflight(root):
    """Exit 2 before bitcoind starts if the run would measure the gate's
    own setup instead of the device."""
    fs = filesystem_of(root)
    if fs and fs[1] in RAM_FILESYSTEMS:
        print(f"M0 INVALID: datadir root {root} is on {fs[1]} at {fs[0]}."
              " A datadir in RAM costs the headroom under test.")
        print("Fix: pass --datadir-root with a path on the SD card.")
        sys.exit(2)
    # SIGKILL runs no handler, so a killed gate can still leave one. In
    # tmpfs it holds RAM; on the card it holds nothing, but it says a run
    # died and its numbers are missing.
    left = sorted(p for place in {TMP, Path(root)}
                  for p in place.glob(DATADIR_PREFIX + "*"))
    if left:
        print("M0 INVALID: a gate datadir is already present:")
        for p in left:
            print(f"  {p}")
        print("A killed gate leaves one, and in tmpfs it holds the RAM under"
              " test. Stop any running gate and bitcoind, then remove it.")
        sys.exit(2)
    # The low-water follows the idle value: the same code on the same Core
    # read 22.7MB apart before and after a reboot (2026-11-06). Shmem is
    # tmpfs, where a leftover datadir would show.
    idle = {"idle MemAvailable (MB)": mem_available_mb(),
            "idle Shmem (MB)": meminfo_mb("Shmem")}
    for k, v in idle.items():
        print(f"  {k}: {'n/a (not Linux)' if v is None else v}")
    return idle


def swap_active_mb():
    """Active swap in MB. Under swap, peak RSS reads low and MemAvailable
    reads high, so no verdict is possible (ticket 01,
    the zero2w-m0-fixes map, archived; see
    docs/wayfinder/README.md)."""
    swaps = Path("/proc/swaps")
    if not swaps.exists():
        return 0  # not Linux: dev run, no verdict either way
    total = 0
    for line in swaps.read_text().splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 3:
            total += int(parts[2])  # size column is KB
    # Ceiling division: 1KB of active swap must still trip the guard.
    return (total + 1023) // 1024


def soc_temp_c():
    """SoC temperature in C, or None off a Pi. ORDER.md's cooling decision
    drops the heatsink, so the gate has to show what the SoC reaches."""
    zone = Path("/sys/class/thermal/thermal_zone0/temp")
    if not zone.exists():
        return None
    try:
        return int(zone.read_text().strip()) / 1000.0
    except ValueError:
        return None


THROTTLE_BITS = {0: "under-voltage NOW", 1: "arm frequency capped NOW",
                 2: "throttled NOW", 3: "soft temp limit NOW",
                 16: "under-voltage since boot", 17: "arm freq capped since boot",
                 18: "throttled since boot", 19: "soft temp limit since boot"}


def throttled():
    """(raw, [reasons]) from vcgencmd, or (None, []) where it is absent.
    A weak micro-USB supply shows up here and nowhere else in the report."""
    vc = shutil.which("vcgencmd")
    if vc is None:
        return None, []
    out = subprocess.run([vc, "get_throttled"], capture_output=True,
                         text=True).stdout.strip()
    if "=" not in out:
        return None, []
    raw = out.split("=", 1)[1]
    try:
        bits = int(raw, 16)
    except ValueError:
        return raw, []
    return raw, [name for bit, name in THROTTLE_BITS.items() if bits & (1 << bit)]


def _sample(stop, track):
    """Sample MemAvailable and SoC temperature every 200ms: the true low
    point falls between RPC calls, so two spot samples are not enough
    (ticket 02). Temperature peaks between calls the same way."""
    while not stop.wait(0.2):
        m = mem_available_mb()
        if m is not None and (track["mem"] is None or m < track["mem"]):
            track["mem"] = m
        t = soc_temp_c()
        if t is not None and (track["temp"] is None or t > track["temp"]):
            track["temp"] = t


def _exit_on_signal(signum, _frame):
    """SIGTERM ends Python without running `finally`, so a runner's
    timeout left bitcoind running and its datadir in place. As an
    exception it unwinds through the cleanup like any other."""
    sys.exit(128 + signum)


def _build_quorum(rpc, threshold, total, report):
    """A watch-only M-of-N holding Core Signer's cosigner key and strangers.

    Core Signer's own session wallet is NOT changed: it keeps the four standard
    policies, which is what a loaded key really has. The quorum is the
    coordinator's, as the multisig-cosigner map says it always is.
    """
    path = signer.cosigner_path(rpc)
    keys = [signer.cosigner_key(rpc, signer.WALLET, path)]
    for i in range(total - 1):
        rpc.call("createwallet", f"cosigner{i}")
        keys.append(signer.cosigner_key(rpc, f"cosigner{i}", path))
    rpc.call("createwallet", "quorum", True, True, "", False, True)
    for change in (0, 1):
        inner = (f"sortedmulti({threshold},"
                 + ",".join(f"{k}/{change}/*" for k in keys) + ")")
        checksum = rpc.call("getdescriptorinfo", f"wsh({inner})",
                            stdin=True)["checksum"]
        rpc.call("importdescriptors",
                 [{"desc": f"wsh({inner})#{checksum}", "active": True,
                   "internal": bool(change), "timestamp": "now",
                   "range": [0, 250]}], wallet="quorum", stdin=True)
    report["quorum"] = f"{threshold}-of-{total} P2WSH at m/{path}"
    return "quorum"


def main():
    args = argparse.ArgumentParser()
    args.add_argument("--inputs", type=int, default=250)
    # How many outputs each funding transaction has. Every input's
    # non_witness_utxo is the whole transaction that paid it, so this sets
    # the PSBT's size per input and it dominates everything downstream.
    # Measured 2026-09-03: 100 gives 2778 bytes per input, 2 gives 378, a
    # factor of 7.3. 100 models consolidating exchange batch withdrawals,
    # which is the real worst case. 2 models ordinary payments.
    args.add_argument("--funding-batch", type=int, default=100)
    # M5: the same gate against a QUORUM. A multisig input carries a
    # witness script and a derivation entry per cosigner rather than one,
    # so the same input count is a larger PSBT and a larger decode. M9
    # also undropped `witness_script` and `bip32_derivs` for the review
    # screen, and signs through `sign_at_told_paths`, which imports a
    # branch into a scratch wallet. This measures the path that ships
    # (TESTING.md rule 3), not a convenient one.
    args.add_argument("--quorum", metavar="M-of-N",
                      help="measure a multisig share, e.g. 2-of-3")
    args.add_argument("--datadir-root", type=Path, default=DATADIR_ROOT,
                      help="where the temp datadir goes; never tmpfs")
    parsed = args.parse_args()
    n, batch = parsed.inputs, parsed.funding_batch
    quorum = None
    if parsed.quorum:
        threshold, total = (int(x) for x in parsed.quorum.split("-of-"))
        quorum = (threshold, total)

    swap = swap_active_mb()
    if swap:
        print(f"M0 INVALID: {swap}MB of swap is active. Swap makes RSS read"
              " low and MemAvailable read high; no verdict is possible.")
        # swapoff -a alone is not enough on Trixie. systemd-zram-generator
        # owns dev-zram0.swap, swap.target wants it, so systemd re-activates
        # the unit seconds after the device goes away. Stop the unit, and do
        # it in the same shell as the run so nothing can race it.
        print("Fix, in one session (both revert at reboot):")
        print("  sudo systemctl stop dev-zram0.swap   # Trixie: zram, or it")
        print("                                       # comes straight back")
        print("  sudo swapoff -a                      # any disk swap left")
        sys.exit(2)
    idle = preflight(parsed.datadir_root)

    stop_sampler = threading.Event()
    track = {"mem": None, "temp": None}  # floor and peak at 200ms resolution
    threading.Thread(target=_sample,
                     args=(stop_sampler, track), daemon=True).start()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _exit_on_signal)
    datadir = daemon = rpc = None
    report = dict(idle)
    low_water = None
    try:
        datadir = tempfile.mkdtemp(prefix=DATADIR_PREFIX,
                                   dir=parsed.datadir_root)
        rpc = signer.Rpc(datadir, chain="regtest")
        t0 = time.time()
        daemon = subprocess.Popen(["bitcoind", f"-datadir={datadir}", *FLAGS],
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
        for _ in range(240):
            try:
                rpc.call("getblockcount")
                break
            except RuntimeError:
                time.sleep(0.5)
        report["bitcoind start (s)"] = round(time.time() - t0, 1)

        t = time.time()
        signer.open_session_xprv(rpc, XPRV)
        report["session open: importdescriptors (s)"] = round(time.time() - t, 1)

        # Miner funds Core Signer with n UTXOs, batched to keep this quick.
        rpc.call("createwallet", "miner")
        mine_addr = rpc.call("getnewaddress", wallet="miner")
        rpc.call("generatetoaddress", 120, mine_addr)
        spender = signer.WALLET
        if quorum:
            spender = _build_quorum(rpc, *quorum, report=report)
        coresigner_addrs = [rpc.call("getnewaddress", wallet=spender)
                       for _ in range(min(n, 200))]
        sent = 0
        while sent < n:
            pay = {coresigner_addrs[(sent + i) % len(coresigner_addrs)]: 0.01
                   for i in range(min(batch, n - sent))}
            rpc.call("send", pay, wallet="miner")
            sent += len(pay)
            rpc.call("generatetoaddress", 1, mine_addr)
        utxos = len(rpc.call("listunspent", wallet=spender))
        report["coresigner utxos funded"] = utxos
        low_water = mem_available_mb()

        # The stress PSBT: spend everything (all inputs, one output).
        t = time.time()
        dest = rpc.call("getnewaddress", wallet="miner")
        inputs = [{"txid": u["txid"], "vout": u["vout"]}
                  for u in rpc.call("listunspent", wallet=spender)]
        total = sum(float(u["amount"]) for u in
                    rpc.call("listunspent", wallet=spender))
        # subtractFeeFromOutputs keeps this a single output with no change,
        # which is the shape the stress case wants. A hard-coded fee reserve
        # was wrong at both ends: 60x the real fee at 250 inputs, and larger
        # than the whole funded amount below about 10 inputs, where it failed
        # with "Transaction amount too small".
        funded = rpc.call("walletcreatefundedpsbt", inputs,
                          [{dest: round(total, 8)}], 0,
                          {"fee_rate": 5, "subtractFeeFromOutputs": [0]},
                          True, wallet=spender, stdin=True)
        report["stress psbt inputs"] = len(inputs)
        report["funding batch (outputs per tx)"] = batch
        review = signer.describe_psbt(rpc, funded["psbt"])
        if quorum:
            # The shipped path: the loaded key holds the four standard
            # policies and nothing at a BIP48 path, so `sign_psbt` falls
            # through to `sign_at_told_paths` and imports the branch the
            # PSBT names into a scratch wallet.
            signed = signer.sign_psbt(
                rpc, funded["psbt"], wallet=signer.WALLET,
                xfp=signer.master_fingerprint(rpc, signer.WALLET))
            assert signed["added"], "Core Signer signed no share of the quorum"
            report["quorum the review read"] = review["quorum"]
        else:
            signed = signer.sign_psbt(rpc, funded["psbt"])
            assert signed["complete"], "stress PSBT did not fully sign"
        report["build+review+sign stress PSBT (s)"] = round(time.time() - t, 1)
        report["fee shown (rBTC)"] = review["fee_btc"]
        report["stress psbt size (KB)"] = len(funded["psbt"]) // 1024
        report["peak bitcoind RSS (MB)"] = vm_hwm_mb(daemon.pid)
        # The gate's own process is part of the device's budget too: on the
        # real device this is coresigner/main.py, holding the same PSBT string and
        # the same decodepsbt JSON. Reporting only the daemon hid 45MB of the
        # loss between 180 and 210 inputs.
        report["peak gate process RSS (MB)"] = vm_hwm_mb("self")
        mem_now = mem_available_mb()
        floors = (track["mem"], low_water, mem_now)
        report["MemAvailable low-water (MB)"] = (
            min(x for x in floors if x is not None)
            if any(x is not None for x in floors) else "n/a (not Linux)")
        if track["temp"] is not None:
            report["peak SoC temperature (C)"] = round(track["temp"], 1)
        raw, reasons = throttled()
        if raw is not None:
            report["vcgencmd get_throttled"] = raw

        print("\nM0 GATE REPORT")
        for k, v in report.items():
            print(f"  {k}: {v}")
        rss = report["peak bitcoind RSS (MB)"]
        mem = report["MemAvailable low-water (MB)"]
        for reason in reasons:
            # Not a fail: the pass line is memory (PLAN.md), and ORDER.md
            # rules that throttling costs sign time and nothing else.
            # Under-voltage is still worth shouting about, because a weak
            # supply can spoil every other number above.
            print(f"  !! {reason}")
        if isinstance(mem, int):
            verdict = "PASS" if mem >= 100 else "FAIL"
            print(f"\nM0 {verdict}: headroom {mem}MB (need >=100MB)")
        else:
            print(f"\nM0 (dev run): signing works; RSS now ~{rss}MB. "
                  "Run on the Pi for the real verdict.")
    finally:
        # A second signal must not cut the cleanup short.
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, signal.SIG_IGN)
        stop_sampler.set()
        if daemon is not None:
            try:
                rpc.call("stop")
                daemon.wait(timeout=60)
            except Exception:
                daemon.kill()
                daemon.wait()
        if datadir is not None:
            shutil.rmtree(datadir, ignore_errors=True)


if __name__ == "__main__":
    main()
