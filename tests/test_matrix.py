"""Exhaustive signing coverage matrix for Core Signer, on regtest.

Proves a valid, broadcast-able SIGHASH_ALL signature for every meaningful
combination of seed-entry mode x script type x input count x output shape.

The pattern mirrors tests/e2e_regtest.py and tests/e2e_session.py:
spin bitcoind -regtest in a tempdir, open a Core Signer session via signer.py,
let a watch-only coordinator fund and build the PSBT, Core Signer describes and
signs, then finalize + broadcast + confirm on regtest.

Matrix axes
-----------
              xprv  (open_session_xprv)
              desc  (open_session_descriptors, both script types)
  script    : BIP84 wpkh (bech32) and BIP86 tr (bech32m keyspend)
  inputs    : 1, 2, 10 UTXOs, all spent
  outputs   : single+change, two+change, single no-change (subtract fee)

There is no SeedQR and no words path any more. PLAN A-22 removed both,
along with the suite that covered the QR pixels, so the two entry modes
above are the whole matrix.

Sighash assertion
-----------------
  wpkh  : witness = [DER-sig || sighash, pubkey]; SIGHASH_ALL => sig ends 0x01.
  tr    : default keyspend witness = [64-byte Schnorr sig], no appended
          sighash byte => SIGHASH_DEFAULT, which is SIGHASH_ALL semantics.

Run: python3 tests/test_matrix.py
"""

import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "coresigner"))
import signer  # noqa: E402

# A-22: the pure signer has no BIP39. This is exactly the key the old
# "abandon x11 about" mnemonic produced on regtest, so every address,
# fee and signature these tests assert is unchanged.
XPRV = "tprv8ZgxMBicQKsPe5YMU9gHen4Ez3ApihUfykaqUorj9t6FDqy3nP6eoXiAo2ssvpAjoLroQxHqr3R5nE3a5dU3DHTjTgJDd7zrbniJr6nrCzd"
# A-22: the codex32 paths moved to the lab with the module.
MINER = "miner"




def open_mode(rpc, mode):
    """Open the Core Signer wallet through one seed-entry mode."""
    if mode == "words" or mode == "xprv":
        signer.open_session_xprv(rpc, XPRV)
    elif mode == "desc":
        xprv = XPRV
        descs = [
            f"wpkh({xprv}/84h/1h/0h/0/*)", f"wpkh({xprv}/84h/1h/0h/1/*)",
            f"tr({xprv}/86h/1h/0h/0/*)",   f"tr({xprv}/86h/1h/0h/1/*)",
        ]
        signer.open_session_descriptors(rpc, descs)
    else:
        raise ValueError(mode)


def wait_rpc(rpc):
    for _ in range(60):
        try:
            rpc.call("getblockcount")
            return
        except RuntimeError:
            time.sleep(0.5)
    raise RuntimeError("bitcoind never came up")


def run_cell(rpc, watch, miner_addr, mode, script, n_inputs, shape):
    """One matrix cell. Returns the confirmed txid. Raises on any failure."""
    addr_type = "bech32" if script == 84 else "bech32m"

    # Coordinator hands out Core Signer-owned receive addresses of the chosen type,
    # the miner funds each with exactly 1.0 rBTC in one funding tx.
    addrs = [rpc.call("getnewaddress", "", addr_type, wallet=watch)
             for _ in range(n_inputs)]
    rpc.call("sendmany", "", {a: 1.0 for a in addrs}, wallet=MINER)
    rpc.call("generatetoaddress", 1, miner_addr)     # confirm + pay the miner

    # Select exactly those UTXOs. add_inputs=False freezes the input count.
    utxos = [u for u in rpc.call("listunspent", 1, 9999, addrs, wallet=watch)]
    assert len(utxos) == n_inputs, \
        f"expected {n_inputs} utxos, got {len(utxos)}"
    for u in utxos:
        got = rpc.call("getaddressinfo", u["address"], wallet=watch)
        assert got["witness_version"] == (0 if script == 84 else 1), \
            f"funded a {got.get('witness_version')} utxo for BIP{script}"
    inputs = [{"txid": u["txid"], "vout": u["vout"]} for u in utxos]
    in_sum = sum((Decimal(str(u["amount"])) for u in utxos), Decimal(0))

    d1 = rpc.call("getnewaddress", wallet=watch)
    options = {"add_inputs": False, "fee_rate": 10, "change_type": addr_type}
    if shape == "single_change":
        outs = [{d1: float(in_sum / 2)}]
        want_out = 2                                  # dest + change
    elif shape == "two_change":
        d2 = rpc.call("getnewaddress", wallet=watch)
        each = (in_sum / 3).quantize(Decimal("0.00000001"))
        outs = [{d1: float(each)}, {d2: float(each)}]
        want_out = 3                                  # dest + dest + change
    elif shape == "single_nochange":
        outs = [{d1: float(in_sum)}]                  # whole sum; fee from it
        options["subtractFeeFromOutputs"] = [0]
        want_out = 1                                  # no change added
    else:
        raise ValueError(shape)

    funded = rpc.call("walletcreatefundedpsbt", inputs, outs, 0, options,
                      True, wallet=watch)
    psbt = funded["psbt"]
    decoded = rpc.call("decodepsbt", psbt)
    # `inputs` and `outputs` hold one map per input and per output in a
    # PSBT of either version. `tx` exists only in v0, and Core 32 funds v2.
    assert len(decoded["outputs"]) == want_out, \
        f"{shape}: {len(decoded['outputs'])} outputs, want {want_out}"
    assert len(decoded["inputs"]) == n_inputs

    # Core Signer review screen + signature (the security boundary).
    review = signer.describe_psbt(rpc, psbt)
    assert review["input_count"] == n_inputs
    assert review["fee_btc"] is not None, "review lost the fee"
    signed = signer.sign_psbt(rpc, psbt)
    assert signed["complete"], "Core Signer did not fully sign"

    # Finalize, inspect the witness for SIGHASH type, broadcast, confirm.
    final = rpc.call("finalizepsbt", signed["psbt"])
    assert final.get("complete"), "finalize incomplete"
    tx = rpc.call("decoderawtransaction", final["hex"])
    for vin in tx["vin"]:
        wit = vin.get("txinwitness")
        assert wit, "no witness on a segwit input"
        if script == 84:
            sig = wit[0]
            assert len(wit) == 2, f"wpkh witness items {len(wit)}"
            assert sig.endswith("01"), \
                f"wpkh sighash byte {sig[-2:]} != 01 (SIGHASH_ALL)"
        else:
            assert len(wit) == 1, f"tr keyspend witness items {len(wit)}"
            sig_bytes = len(wit[0]) // 2
            assert sig_bytes == 64, \
                f"tr sig {sig_bytes} bytes; not default SIGHASH_ALL keyspend"

    txid = rpc.call("sendrawtransaction", final["hex"])
    rpc.call("generatetoaddress", 1, miner_addr)
    conf = rpc.call("gettransaction", txid, wallet=watch)["confirmations"]
    assert conf >= 1, "tx not confirmed"
    return txid


def main():
    datadir = tempfile.mkdtemp(prefix="coresigner-matrix-")
    import random as _rnd
    _port = _rnd.randint(20000, 60000)
    (Path(datadir) / "bitcoin.conf").write_text(
        "regtest=1\n[regtest]\nrpcport=%d\n" % _port)
    daemon = subprocess.Popen(
        ["bitcoind", "-regtest", f"-datadir={datadir}", "-listen=0",
         "-fallbackfee=0.0001", "-server=1", "-debuglogfile=0"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rpc = signer.Rpc(datadir, chain="regtest")

    # A-22: the two codex modes went to the lab; "words" is now the xprv
    # the old mnemonic produced, so the same wallet is exercised.
    modes = ["xprv", "desc"]
    scripts = [84, 86]
    input_counts = [1, 2, 10]
    shapes = ["single_change", "two_change", "single_nochange"]

    passed = 0
    failed = 0
    try:
        wait_rpc(rpc)
        # Miner wallet: real keys, matured coinbase to fund every cell.
        rpc.call("createwallet", MINER, False, False, "", False, True)
        miner_addr = rpc.call("getnewaddress", wallet=MINER)
        rpc.call("generatetoaddress", 300, miner_addr)

        for mode in modes:
            open_mode(rpc, mode)
            pubs = signer.public_descriptors(rpc)
            watch = f"watch_{mode}".replace("-", "_")
            rpc.call("createwallet", watch, True, True, "", False, True)
            rpc.call("importdescriptors",
                     [{"desc": d, "active": True, "timestamp": "now",
                       "range": [0, 200], "internal": "/1/*" in d}
                      for d in pubs], wallet=watch)

            for script in scripts:
                for n_inputs in input_counts:
                    for shape in shapes:
                        cell = (f"{mode:9s} BIP{script} "
                                f"{n_inputs:2d}in {shape:15s}")
                        try:
                            txid = run_cell(rpc, watch, miner_addr, mode,
                                            script, n_inputs, shape)
                            passed += 1
                            print(f"ok   {cell} -> confirmed {txid[:12]}…")
                        except Exception as exc:
                            failed += 1
                            print(f"FAIL {cell}: {exc}")
                            traceback.print_exc()

            signer.close_session(rpc)

        total = passed + failed
        print(f"\n{passed}/{total} cells passed "
              f"({len(modes)} modes x {len(scripts)} scripts x "
              f"{len(input_counts)} input-counts x {len(shapes)} shapes)")
        if failed:
            print(f"MATRIX FAIL: {failed} cell(s) failed")
            sys.exit(1)
        print(f"MATRIX PASS: all {total} signing cells broadcast and confirmed")
    finally:
        try:
            rpc.call("stop")
            daemon.wait(timeout=30)
        except Exception:
            daemon.kill()
        shutil.rmtree(datadir, ignore_errors=True)


if __name__ == "__main__":
    main()
