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
import hashlib
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

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

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


def tpub_that_spells_tprv(tpub):
    """The same public key under a chain code searched until the base58
    spells `tprv` inside it. A key read that starts a match there takes
    part of a public key as a private key, which the lookbehind in
    _XPRV_RE stops. The base58 here is written from BIP32's layout, not
    from the code under test (TESTING.md rule 2)."""
    n = 0
    for c in tpub:
        n = n * 58 + B58.index(c)
    raw = n.to_bytes(82, "big")
    if hashlib.sha256(hashlib.sha256(raw[:78]).digest()).digest()[:4] \
            != raw[78:]:
        raise AssertionError("Core's tpub did not decode")
    # version..child number, then the chain code, then the public key.
    head, key = raw[:13], raw[45:78]
    for i in range(10_000_000):
        payload = head + hashlib.sha256(i.to_bytes(8, "big")).digest() + key
        payload += hashlib.sha256(
            hashlib.sha256(payload).digest()).digest()[:4]
        n, text = int.from_bytes(payload, "big"), ""
        while n:
            n, r = divmod(n, 58)
            text = B58[r] + text
        if "tprv" in text:
            return text
    raise AssertionError("no chain code spelled tprv in 10^7 tries")


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
        # Sliced out of Core's own `pkh(tprv.../44h/...)`, so the answer
        # does not come from the pattern under test (TESTING.md rule 2).
        priv = rpc.call("listdescriptors", True,
                        wallet="other")["descriptors"][0]["desc"]
        other_prv = priv[len("pkh("):].split("/")[0]
        if not other_prv.startswith("tprv8ZgxMBicQKsP"):
            bad(f"Core's first private descriptor is not pkh: {priv[:4]}")

        # The same public key, so Core's own check is what accepts it.
        spells = tpub_that_spells_tprv(other_pub)
        for desc in (f"tr({spells}/0/*,pk({WIF}))",
                     f"tr({KEY}/86h/1h/0h/0/*,pk({spells}/0/*))"):
            rpc.call("getdescriptorinfo", desc)
        ok(f"Core accepts a tpub that spells tprv at {spells.index('tprv')}")

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
            ("a WIF, and a tpub that spells tprv",
             f"tr({spells}/0/*,pk({WIF}))", None),
            ("k, and a tpub that spells tprv",
             f"tr({mine},pk({spells}/0/*))", KEY),
        ]
        for what, desc, want in cases:
            wallet = signer.open_session_descriptors(rpc, [desc])
            shown, held = backup_screen(rpc, wallet)
            if any(k in other_pub or k in spells for k in shown):
                bad(f"{what}: the backup shows the other party's public key"
                    " or a part of it")
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

        # A checksum is eight characters of its own alphabet, which holds
        # x, p, r, v and t, and a 0 that base58 does not. So "#a0tprvqz"
        # can be a real checksum, and the key read must not find a second
        # key in it. Core's own descriptors, with only the checksum set.
        listed = rpc.call("listdescriptors", True,
                          wallet=made)["descriptors"]

        class Checksums:
            def call(self, method, *a, **k):
                return {"descriptors": [
                    {**d, "desc": d["desc"].split("#")[0] + "#a0tprvqz"}
                    for d in listed]}

        try:
            got = signer.master_xprv(Checksums(), wallet=made)
            if got == shown[0]:
                ok("a checksum that spells tprv is not read as a key")
            else:
                bad(f"a checksum changed the key read: {signer.redact(got)}")
        except RuntimeError as exc:
            bad(f"a checksum that spells tprv refused the key: {exc}")
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
