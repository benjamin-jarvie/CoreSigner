"""Backup key shows a private key or no key, never a public key (E-9).

`master_xprv` took the key after the LAST `(` in each private descriptor.
Core accepts a scanned `tr(k,pk(other))` with the warning "Not all private
keys provided", and there the last `(` belongs to the script leaf, so the
paper backup showed the other party's tpub (measured on Core 31.1 and
32.1, 2026-11-05). The one-master check could not catch it, because one
descriptor holds one key.

Each descriptor below goes through the real `open_session_descriptors`
into a real Core, and the other party's keys are Core's own (TESTING.md
rule 1). The backup is then read through the real Backup key screen.

Run: python3 tests/test_backup_key.py (needs bitcoind)
"""
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "coresigner"))
import hal                              # noqa: E402
import main as coresigner_main          # noqa: E402
import signer                           # noqa: E402

# A real regtest master private key, the one every other suite signs with.
KEY = ("tprv8ZgxMBicQKsPe5YMU9gHen4Ez3ApihUfykaqUorj9t6FDqy3nP6eoXiAo2ss"
       "vpAjoLroQxHqr3R5nE3a5dU3DHTjTgJDd7zrbniJr6nrCzd")
# A compressed regtest WIF. Core checks it on import, so the literal is
# Core's to accept or refuse.
WIF = "cVt4o7BGAig1UXywgGSmARhxMdzP5qvQsxKkSsc1XEkw3tDTQFpy"

fails = []


def ok(m):
    print("ok  ", m)


def bad(m):
    fails.append(m)
    print("FAIL", m)


class Blind:
    width, height = 320, 240

    def show(self, image, sensitive=False):
        pass


def backup_screen(rpc, wallet):
    """What Backup key puts in front of the user: the key it shows, or the
    refusal it holds. Only the key pages are replaced, so the read and the
    refusal are the code that ships."""
    sess = coresigner_main.Session(Blind(), hal.DevButtons("a"), rpc,
                                   animate=False)
    shown, held = [], []
    sess._show_backup = lambda key, label: shown.append(key)
    real_hold = sess._hold

    def hold(detail, ok=False):
        held.append(detail)
        real_hold(detail, ok=ok)

    sess._hold = hold
    sess._backup_paper(wallet, signer.master_fingerprint(rpc, wallet=wallet))
    return shown, held


def main():
    datadir = tempfile.mkdtemp(prefix="backupkey-")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    (Path(datadir) / "bitcoin.conf").write_text(
        f"regtest=1\n[regtest]\nrpcport={port}\n")
    daemon = subprocess.Popen(
        ["bitcoind", f"-datadir={datadir}", "-regtest", "-networkactive=0",
         "-listen=0", "-server=1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rpc = signer.Rpc(datadir, chain="regtest")
    try:
        for _ in range(120):
            try:
                rpc.call("getblockcount")
                break
            except RuntimeError:
                time.sleep(0.5)

        # The other party: a key Core made, read the way Core lists it.
        rpc.call("createwallet", "other")
        pub = rpc.call("listdescriptors", wallet="other")["descriptors"][0]
        other_pub = pub["desc"][pub["desc"].index("]") + 1:].split("/")[0]
        priv = rpc.call("listdescriptors", True,
                        wallet="other")["descriptors"][0]
        other_prv = re.search(r"tprv[1-9A-HJ-NP-Za-km-z]+",
                              priv["desc"]).group(0)

        mine = f"{KEY}/86h/1h/0h/0/*"
        cases = [
            # (what, descriptor, the key the backup shows or None)
            ("tr(k,pk(other))", f"tr({mine},pk({other_pub}/0/*))", KEY),
            ("tr(k,{pk(other),pk(other)})",
             f"tr({mine},{{pk({other_pub}/0/*),pk({other_pub}/1/*)}})", KEY),
            ("tr(other,pk(k))", f"tr({other_pub}/0/*,pk({mine}))", KEY),
            ("a key with its origin in front",
             f"wpkh([73c5da0a/84h/1h/0h]{other_prv}/0/*)", other_prv),
            ("two private keys", f"tr({mine},pk({other_prv}/0/*))", None),
            ("a WIF and no xprv", f"tr({other_pub}/0/*,pk({WIF}))", None),
        ]
        for what, desc, want in cases:
            wallet = signer.open_session_descriptors(rpc, [desc])
            shown, held = backup_screen(rpc, wallet)
            if other_pub in shown:
                bad(f"{what}: the backup shows the other party's public key")
            elif want is not None and shown == [want]:
                ok(f"{what}: the backup shows the private key")
            elif want is None and not shown and held:
                ok(f"{what}: refused, no key shown ({held[0]})")
            else:
                bad(f"{what}: shown {[signer.redact(k)[:24] for k in shown]}"
                    f", held {held}")
            signer.close_session(rpc)

        # A key Core made holds one master across its eight descriptors.
        made = signer.generate_wallet(rpc)
        shown, held = backup_screen(rpc, made)
        if len(shown) == 1 and shown[0].startswith("tprv8ZgxMBicQKsP"):
            ok("a key Core made shows its depth-0 master")
        else:
            bad(f"a key Core made: shown {len(shown)} keys, held {held}")
        signer.close_session(rpc)
    finally:
        daemon.terminate()
        daemon.wait()
        shutil.rmtree(datadir, ignore_errors=True)
    if fails:
        print(f"\nBACKUP KEY FAILED ({len(fails)})")
        return 1
    print("\nBACKUP KEY PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
