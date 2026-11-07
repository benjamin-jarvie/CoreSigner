"""The M0 gate must measure the device, not its own leftovers.

The 2-of-3 gate at 120 inputs failed 5 of 5 on the board and then passed
4 of 4 after a reboot, on the same code and the same Core (CORKY M0 gate
diagnosis, 2026-11-06). Before the reboot about 20MB more sat in tmpfs.
The gate made its datadir in /tmp, which is tmpfs on Trixie, and removed
it only in `finally`, which Python never reaches on SIGTERM or SIGKILL.
So each killed gate left a 16MB blk00000.dat in RAM for the next run to
be measured against.

/proc is the only part that needs the board, so it is the only part
faked: a directory with the board's own meminfo and mounts lines. The
signal check runs the real script with a fake bitcoind on PATH.

Run: python3 tests/test_m0_gate.py
"""
import io
import os
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "m0"))
import m0_gate  # noqa: E402

fails = []


def ok(m):
    print("ok  ", m)


def bad(m):
    print("FAIL", m)
    fails.append(m)


# The Zero 2 W's lines, read off the board after the 2026-11-06 reboot.
MEMINFO = """MemTotal:         426384 kB
MemFree:          201480 kB
MemAvailable:     297984 kB
Shmem:             19456 kB
"""
# The mount points sit under the test's own directory, resolved, because
# the gate resolves symlinks and macOS's /tmp is a link to /private/tmp.
MOUNTS = """/dev/mmcblk0p2 / ext4 rw,noatime 0 0
tmpfs {w}/tmp tmpfs rw,nosuid,nodev,size=213192k 0 0
tmpfs {w}/run/coresigner tmpfs rw,nosuid,nodev,size=131072k,mode=700 0 0
ramfs {w}/ram\\040disk ramfs rw,relatime 0 0
"""


def fake_board(work):
    """A /proc and a /tmp to stand in for the board's, both empty of
    leftovers, so a check sees only what the test puts there."""
    work = work.resolve()
    proc = work / "proc"
    proc.mkdir()
    (proc / "meminfo").write_text(MEMINFO)
    (proc / "mounts").write_text(MOUNTS.format(w=work))
    tmp = work / "tmp"
    tmp.mkdir()
    m0_gate.PROC = proc
    m0_gate.TMP = tmp
    return tmp


def preflight(root):
    """(exit code or None, printed text) for one preflight call."""
    out = io.StringIO()
    code = None
    with redirect_stdout(out):
        try:
            m0_gate.preflight(Path(root))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue()


# ---- the datadir must be off tmpfs
with tempfile.TemporaryDirectory() as w:
    tmp = fake_board(Path(w))
    code, said = preflight(tmp)
    if code == 2 and "tmpfs" in said and f"{tmp}" in said:
        ok("a datadir root on tmpfs exits 2 and names the mount")
    else:
        bad(f"a datadir root on tmpfs gave exit {code!r}: {said!r}")
    code, said = preflight(tmp.parent / "run" / "coresigner" / "m0")
    if code == 2 and "run/coresigner" in said:
        ok("a root below a tmpfs mount is refused by the mount that holds it")
    else:
        bad(f"a root below the ramdisk gave exit {code!r}: {said!r}")
    sd = tmp.parent / "var" / "tmp"
    sd.mkdir(parents=True)
    code, said = preflight(sd)
    if code is None:
        ok("a root on the SD card's ext4 passes")
    else:
        bad(f"a root on ext4 was refused, exit {code!r}: {said!r}")
    # A mount over /run made after the ramdisk hides the ramdisk, so the
    # longest mount point is not the one that holds the path.
    with (m0_gate.PROC / "mounts").open("a") as f:
        f.write(f"/dev/sda1 {tmp.parent}/run ext4 rw 0 0\n")
    code, said = preflight(tmp.parent / "run" / "coresigner" / "m0")
    if code is None:
        ok("a later mount over a parent hides the ramdisk below it")
    else:
        bad(f"a hidden ramdisk still refused, exit {code!r}: {said!r}")
    code, said = preflight(sd)
    if code is None:
        ok("a root on the SD card's ext4 still passes")
    else:
        bad(f"a root on ext4 was refused, exit {code!r}: {said!r}")
    spaced = tmp.parent / "ram disk"
    spaced.mkdir()
    code, said = preflight(spaced)
    if code == 2 and "ramfs" in said:
        ok("a ramfs mount point with an escaped space is found and refused")
    else:
        bad(f"a root on the ramfs 'ram disk' mount gave exit {code!r}: {said!r}")


# ---- a datadir a killed gate left behind stops the run
with tempfile.TemporaryDirectory() as w:
    tmp = fake_board(Path(w))
    sd = tmp.parent / "var" / "tmp"
    sd.mkdir(parents=True)
    for place, name in ((tmp, "/tmp"), (sd, "the datadir root")):
        left = place / "coresigner-m0-k1ll3d"
        (left / "regtest" / "blocks").mkdir(parents=True)
        code, said = preflight(sd)
        if code == 2 and str(left) in said:
            ok(f"a leftover coresigner-m0-* in {name} exits 2 and names it")
        else:
            bad(f"a leftover in {place} gave exit {code!r}: {said!r}")
        left.rename(place / "kept-by-hand")
    code, said = preflight(sd)
    if code is None:
        ok("other directories in /tmp and the root do not stop the run")
    else:
        bad(f"no leftover, yet exit {code!r}: {said!r}")


# ---- the idle baseline is on record before bitcoind starts
# A1-A5 and G1-G4 ran the same code on the same Core 22.7MB apart, and
# only the idle MemAvailable and Shmem before each run told them apart.
with tempfile.TemporaryDirectory() as w:
    tmp = fake_board(Path(w))
    sd = tmp.parent / "var" / "tmp"
    sd.mkdir(parents=True)
    code, said = preflight(sd)
    # 297984 kB and 19456 kB, floored to MB as the report floors them.
    if (code is None and "idle MemAvailable (MB): 291" in said
            and "idle Shmem (MB): 19" in said):
        ok("preflight prints idle MemAvailable 291MB and Shmem 19MB")
    else:
        bad(f"idle values missing or wrong: {said!r}")


# ---- SIGTERM and SIGINT stop bitcoind and remove the datadir
# The real script, as the board's runner starts it. bitcoind is a fake
# that records its pid and waits; bitcoin-cli answers as Core does while
# bitcoind is still starting, so the gate sits in its wait loop, which is
# where a runner's timeout or Ctrl-C finds it. It reads the real /tmp, so
# a leftover from a dev run there stops it with exit 2 and says why.
FAKE_BITCOIND = """#!/bin/sh
echo $$ > "$M0_FAKE/bitcoind.pid"
exec sleep 600
"""
FAKE_CLI = """#!/bin/sh
echo "error: Could not connect to the server 127.0.0.1:18443" >&2
exit 1
"""


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_for(predicate, seconds):
    end = time.time() + seconds
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.1)
    return False


for sig in (signal.SIGTERM, signal.SIGINT):
    with tempfile.TemporaryDirectory() as w:
        w = Path(w).resolve()
        bins = w / "bin"
        bins.mkdir()
        for name, body in (("bitcoind", FAKE_BITCOIND),
                           ("bitcoin-cli", FAKE_CLI)):
            (bins / name).write_text(body)
            (bins / name).chmod(0o755)
        root = w / "sd"
        root.mkdir()
        env = dict(os.environ, M0_FAKE=str(w),
                   PATH=f"{bins}{os.pathsep}{os.environ['PATH']}")
        gate = subprocess.Popen(
            [sys.executable, str(ROOT / "m0" / "m0_gate.py"),
             "--inputs", "3", "--datadir-root", str(root)],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True)
        pidfile = w / "bitcoind.pid"
        started = wait_for(lambda f=pidfile: f.exists()
                           and f.read_text().strip(), 15)
        made = list(root.glob("coresigner-m0-*"))
        if not started or not made:
            gate.kill()
            out = gate.communicate()[0]
            bad(f"{sig.name}: the gate never reached its wait loop: {out!r}")
            continue
        pid = int(pidfile.read_text())
        gate.send_signal(sig)
        try:
            out = gate.communicate(timeout=30)[0]
        except subprocess.TimeoutExpired:
            gate.kill()
            out = gate.communicate()[0]
            bad(f"{sig.name}: the gate did not exit within 30s: {out!r}")
            continue
        gone = wait_for(lambda p=pid: not alive(p), 5)
        left = list(root.glob("coresigner-m0-*"))
        if gate.returncode == 128 + sig and gone and not left:
            ok(f"{sig.name}: exit {gate.returncode}, bitcoind stopped,"
               " datadir removed")
        else:
            bad(f"{sig.name}: exit {gate.returncode}, bitcoind"
                f" {'stopped' if gone else 'still running'}, left {left}:"
                f" {out!r}")
        if alive(pid):
            os.kill(pid, signal.SIGKILL)


print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
