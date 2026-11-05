"""Core Signer <-> Sparrow interop matrix.

Every PSBT is built by Sparrow Wallet's own library (drongo), extracted from
the signed Sparrow 2.5.4 release. Nothing is reimplemented: the wallet model,
coin selection, change derivation, PSBT construction and the export downgrade
are Sparrow's own code paths (HeadersController.savePSBT calls getForExport()).

Core Signer signs with unmodified Bitcoin Core. Core then finalizes and broadcasts on
regtest. A case passes only when the transaction confirms.
"""
import sys
from decimal import Decimal

import harness
from harness import Java, Regtest, Results

SATS = Decimal(100_000_000)


def sats(btc):
    """BTC to integer satoshis, exactly. signer.py parses with Decimal, so no
    binary float ever enters the comparison."""
    return int((Decimal(str(btc)) * SATS).to_integral_value())


def review_matches(info, marks):
    """(ok, note) for Core Signer's review against Sparrow's numbers.

    The M1 gate (PLAN.md:377) is "fee and outputs on screen match
    Sparrow", so this compares the review with the fee and outputs
    Sparrow computed for the same transaction.
    """
    coresigner = (sats(info["fee_btc"]),
                  sorted((o["address"], sats(o["amount_btc"]))
                         for o in info["outputs"]))
    sparrow = (int(marks["FEE"][0]),
               sorted((a, int(v)) for a, v in
                      (m.split("\t") for m in marks["VOUT"])))
    if coresigner == sparrow:
        return True, f"fee {coresigner[0]} sat, {len(coresigner[1])} outputs"
    return False, f"coresigner {coresigner} | sparrow {sparrow}"


def main():
    java = Java()
    R = Results()

    with Regtest() as net:
        print(f"Core Signer session open, {len(net.pubs)} public descriptors\n")

        for script_type, _ in harness.SCRIPT_TYPES:
            fp, path, xpub = net.account(script_type)
            print(f"=== {script_type}   {path} ===")

            # 1. derivation agreement with Core, both branches
            derived = {}
            for branch, purpose in ((0, "RECEIVE"), (1, "CHANGE")):
                mine = [line.split("\t")[1] for line in
                        java("SparrowGen", "addresses", "REGTEST", script_type,
                             xpub, fp, path, 12, purpose)]
                core = list(net.rpc.call("deriveaddresses",
                                         net.descriptor(script_type, branch),
                                         [0, 11]))
                derived[purpose] = mine
                R.record(f"{script_type} {purpose} derivation matches Core "
                         f"(12 addrs)", mine == core,
                         "" if mine == core else f"{mine[:1]} vs {core[:1]}")

            # 2. fund two batches so no case reuses another's UTXOs
            def batch(spec, derived=derived):
                out = {}
                for purpose, index in spec:
                    txid, vout, raw = net.fund(derived[purpose][index])
                    out[(purpose, index)] = (purpose, index, txid, vout, raw)
                net.mine()
                h = net.height()
                return {k: f"u={v[0]},{v[1]},{v[2]},{v[3]},1000000,{h},{v[4]}"
                        for k, v in out.items()}

            first = batch([("RECEIVE", i) for i in range(12)]
                          + [("CHANGE", i) for i in range(4)])
            second = batch([("CHANGE", i) for i in range(3)]
                           + [("RECEIVE", i + 9) for i in range(3)])
            print(f"     funded {len(first) + len(second)} outputs")

            d1, d2 = net.new_address(), net.new_address()
            def Rx(*ix, first=first):
                return [first[("RECEIVE", i)] for i in ix]

            def Cx(*ix, first=first):
                return [first[("CHANGE", i)] for i in ix]

            def S(purpose, i, second=second):
                return second[(purpose, i)]

            cases = [
                ("1 receive input, payment + change",   Rx(0),        [f"p={d1},500000,false"]),
                ("2 receive inputs, payment + change",  Rx(1, 2),     [f"p={d1},1500000,false"]),
                ("3 receive inputs, payment + change",  Rx(3, 4, 5),  [f"p={d1},2500000,false"]),
                ("10 receive inputs, payment + change", Rx(*range(6, 12)) + Cx(0, 1, 2, 3),
                                                                      [f"p={d1},9000000,false"]),
                ("1 change-branch input (internal desc)", [S("CHANGE", 0)],
                                                                      [f"p={d1},500000,false"]),
                ("mixed receive + change inputs", [S("CHANGE", 1), S("RECEIVE", 9)],
                                                                      [f"p={d1},1200000,false"]),
                ("2 payments + change", [S("CHANGE", 2), S("RECEIVE", 10)],
                                                    [f"p={d1},600000,false", f"p={d2},700000,false"]),
                ("send max, no change output", [S("RECEIVE", 11)],    [f"p={d1},999000,true"]),
            ]

            for name, utxos, payments in cases:
                full = f"{script_type} {name}"
                try:
                    java.chain_height = net.height()
                    marks = java("SparrowGen", "psbt", "REGTEST", script_type,
                                 xpub, fp, path, 2.0, *payments, *utxos,
                                 tags=("OUT", "FEE", "VOUT"))
                    psbt = marks["OUT"][0]
                    info = harness.signer.describe_psbt(net.rpc, psbt)
                    R.record(f"{full} :: review matches Sparrow",
                             *review_matches(info, marks))

                    signed = harness.signer.sign_psbt(net.rpc, psbt)
                    if not signed["complete"]:
                        R.record(full, False, "Core Signer returned an incomplete PSBT")
                        continue
                    final = net.rpc.call("finalizepsbt", signed["psbt"])
                    txid = net.rpc.call("sendrawtransaction", final["hex"])
                    net.mine()
                    conf = net.rpc.call("getrawtransaction", txid, True)["confirmations"]
                    R.record(full, conf >= 1,
                             f"{info['input_count']} in, {len(info['outputs'])} out, "
                             f"fee {info['fee_btc']}, tx {txid[:10]}")
                except Exception as exc:
                    R.record(full, False, str(exc).replace("\n", " ")[:170])

            # Sparrow's own PSBTv2, before its export downgrade. Core 31
            # refused every v2. Core 32 decodes it, and its decode of a v2
            # has no `tx`, so this is the check that the review reads that
            # second shape with the numbers Sparrow shows.
            full = f"{script_type} PSBTv2 :: review matches Sparrow"
            try:
                java.chain_height = net.height()
                marks = java("SparrowGen", "psbt", "REGTEST", script_type,
                             xpub, fp, path, 2.0, f"p={d1},400000,false",
                             S("CHANGE", 0), raw=True,
                             tags=("OUT", "FEE", "VOUT"))
                v2 = marks["OUT"][0]
                version = net.rpc.call("decodepsbt", v2).get("psbt_version")
                if version != 2:
                    R.record(full, False, f"Sparrow gave psbt_version {version}")
                else:
                    info = harness.signer.describe_psbt(net.rpc, v2)
                    R.record(full, *review_matches(info, marks))
            except Exception as exc:
                R.record(full, False, str(exc).replace("\n", " ")[:170])
            print()

    return R.summary()


if __name__ == "__main__":
    sys.exit(main())
