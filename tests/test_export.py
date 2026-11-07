"""Export the public key (map e2e-before-testers, ticket 12).

What a coordinator needs, in the forms it can read, with nothing secret in
any of them. Run: python3 tests/test_export.py (needs bitcoind)
"""
import random
import shutil
import subprocess
import sys
from decimal import Decimal
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "coresigner"))
sys.path.insert(0, str(ROOT / "tests"))
import signer  # noqa: E402
import main as coresigner_main  # noqa: E402

XPRV_A = "tprv8ZgxMBicQKsPe5YMU9gHen4Ez3ApihUfykaqUorj9t6FDqy3nP6eoXiAo2ssvpAjoLroQxHqr3R5nE3a5dU3DHTjTgJDd7zrbniJr6nrCzd"

fails = []
def ok(m): print("ok  ", m)
def bad(m): fails.append(m); print("FAIL", m)


def start_node(prefix):
    datadir = tempfile.mkdtemp(prefix=prefix)
    (Path(datadir) / "bitcoin.conf").write_text(
        "regtest=1\n[regtest]\nrpcport=%d\n" % random.randint(20000, 60000))
    daemon = subprocess.Popen(
        ["bitcoind", f"-datadir={datadir}", "-regtest", "-networkactive=0",
         "-listen=0", "-server=1", "-fallbackfee=0.0001"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rpc = signer.Rpc(datadir, chain="regtest")
    for _ in range(120):
        try:
            rpc.call("getblockcount"); break
        except RuntimeError:
            time.sleep(0.5)
    return daemon, rpc, datadir


def main():
    daemon, rpc, datadir = start_node("export-")
    other, orpc, odir = start_node("laptop-")   # "a laptop running Core"
    work = Path(tempfile.mkdtemp(prefix="export-work-"))
    try:
        name = signer.open_session_xprv(rpc, XPRV_A)

        # 1. Every policy a key HAS can be exported, and a key does not
        #    always have four. Core's own createwallet makes all four;
        #    build_descriptors makes BIP84 and BIP86, so a key that
        #    arrived by scan or by typing has two. The panel offers what
        #    the key presents (map ticket T0); the asymmetry itself is D6.
        gen_name = signer.generate_wallet(rpc)
        if signer.available_kinds(rpc, gen_name) == signer.EXPORT_ORDER:
            ok("a key Core generated presents all four script policies")
        else:
            bad(f"a generated key presented "
                f"{signer.available_kinds(rpc, gen_name)}")
        # A key that arrives by xprv must present what a key Core
        # generated presents, or restoring your own paper backup shows you
        # half your wallet. Assert the ROUND TRIP, not the list: generate,
        # take the paper backup, restore it, and every policy must derive
        # the same addresses (TESTING.md rule 1).
        gen_kinds = signer.available_kinds(rpc, gen_name)
        gen_addrs = {k: signer.receive_addresses(rpc, gen_name, k, 3)
                     for k in gen_kinds}
        paper = signer.master_xprv(rpc, wallet=gen_name)
        signer.close_key(rpc, gen_name)
        back = signer.open_session_xprv(rpc, paper)
        back_kinds = signer.available_kinds(rpc, back)
        back_addrs = {k: signer.receive_addresses(rpc, back, k, 3)
                      for k in back_kinds}
        if back_kinds != gen_kinds:
            bad(f"a restored key presents {back_kinds}, not {gen_kinds}")
        elif back_addrs != gen_addrs:
            differ = [k for k in gen_addrs if gen_addrs[k] != back_addrs[k]]
            bad(f"a restored key derives different addresses for {differ}")
        else:
            ok("a key restored from its own paper backup presents all four "
               "policies and derives the same addresses")
        signer.close_key(rpc, back)

        # 1b. Check an address must accept every address Core Signer can hand
        #     out. _classify_qr decides that on shape alone, and its only
        #     address case was one bech32 v0 literal, so the three other
        #     policies were never put to it. A rejected address does not
        #     say "wrong policy", it silently counts the code as stray and
        #     keeps scanning, which reads as a dead camera.
        #     (Devil's advocate on A5, 2026-09-06. Rule 1: Core's own
        #     addresses, not shapes typed from memory.)
        misread = {k: a[0] for k, a in gen_addrs.items()
                   if coresigner_main._classify_qr(a[0]) != "address"}
        if misread:
            bad(f"the scan does not recognise these as addresses: {misread}")
        else:
            shapes = ", ".join(f"{k}:{a[0][:4]}" for k, a in
                               sorted(gen_addrs.items()))
            ok(f"every policy's address is recognised by the scan ({shapes})")
        gen_name = signer.generate_wallet(rpc)

        # 1c. A LEGACY INPUT, whose amount lives only inside the whole
        #     previous transaction, so Core has to read that to value it.
        #
        #     describe_psbt no longer reads it: those previous transactions
        #     were 20.7MB of a 21.1MB tree at 250 batch inputs, and the
        #     total going in is now Core's own fee plus the outputs. This
        #     check is what says the two agree, and it asks the CHAIN
        #     rather than Core, so it is a genuinely separate source.
        legacy_addr = signer.receive_addresses(rpc, gen_name, "pkh", 1)[0]
        rpc.call("createwallet", "miner_legacy")
        m_addr = rpc.call("getnewaddress", wallet="miner_legacy")
        rpc.call("generatetoaddress", 101, m_addr, wallet="miner_legacy")
        rpc.call("sendtoaddress", legacy_addr, 2.0, wallet="miner_legacy")
        rpc.call("generatetoaddress", 1, m_addr, wallet="miner_legacy")
        leg = rpc.call("walletcreatefundedpsbt", [],
                       [{m_addr: 1.0}], 0, {"fee_rate": 5}, True,
                       wallet=gen_name)["psbt"]
        decoded = rpc.call("decodepsbt", leg)
        uses_legacy = any("witness_utxo" not in i for i in decoded["inputs"])
        if not uses_legacy:
            bad("1c: Core built a witness PSBT, so this is not the legacy "
                "case it claims to be")
        else:
            review = signer.describe_psbt(rpc, leg)
            total = review["input_total_btc"]
            out_sum = sum(Decimal(str(o["amount_btc"]))
                          for o in review["outputs"])
            # The independent source of truth is the node's UTXO SET, not
            # the PSBT. The review's total is derived from Core's fee, so
            # comparing it to that fee would compare Core to Core and
            # could not fail. gettxout answers from the chain instead, so
            # a coordinator that lies about an input amount shows up
            # here. (Devil's advocate on A5, 2026-09-06: the first version
            # of this check was exactly that tautology.)
            # Core 32 funds a PSBT v2, whose decode has no `tx`: each
            # input names its own outpoint instead.
            outpoints = ([(t["txid"], t["vout"]) for t in decoded["tx"]["vin"]]
                         if "tx" in decoded else
                         [(i["previous_txid"], i["previous_vout"])
                          for i in decoded["inputs"]])
            chain_in = Decimal(0)
            for txid, vout in outpoints:
                utxo = rpc.call("gettxout", txid, vout)
                chain_in += Decimal(str(utxo["value"]))
            chain_fee = chain_in - out_sum
            if total is None:
                bad("1c: the review shows no input total for a legacy "
                    "input, so the screen cannot state what is being spent")
            elif total != chain_in:
                bad(f"1c: the review says {total} BTC goes in, the chain "
                    f"says {chain_in} BTC")
            elif Decimal(str(review["fee_btc"])) != chain_fee:
                bad(f"1c: the review's fee {review['fee_btc']} is not "
                    f"chain inputs minus outputs, {chain_fee}")
            else:
                ok(f"1c: a legacy input's total agrees with the chain, "
                   f"without reading the previous transaction: {total} in, "
                   f"{out_sum} out, {chain_fee} fee")

        # 1d. THE REFUSAL. describe_psbt reports no input total when any
        #     input carries neither a witness_utxo nor the previous
        #     transaction, and main.py turns that into "PSBT lacks input
        #     data; fee unknown; refused". It is the one branch that stops
        #     the device signing a transaction whose fee it cannot state,
        #     and audit A5 measured it as never executed (2026-09-06).
        #     createpsbt builds that shape: raw inputs, empty input maps.
        funded = rpc.call("listunspent", 1, 9999999, [], True,
                          wallet=gen_name)
        if not funded:
            bad("1d: no UTXO to build a bare PSBT from")
        else:
            u = funded[0]
            bare = rpc.call("createpsbt", [{"txid": u["txid"],
                                            "vout": u["vout"]}],
                            [{m_addr: 0.5}])
            bare_review = signer.describe_psbt(rpc, bare)
            if bare_review["input_total_btc"] is not None:
                bad(f"1d: a PSBT with no input data still claimed an input "
                    f"total of {bare_review['input_total_btc']}")
            elif bare_review["fee_btc"] is not None:
                bad(f"1d: a PSBT with no input data still claimed a fee of "
                    f"{bare_review['fee_btc']}")
            else:
                ok("1d: a PSBT that carries no input amounts reports no "
                   "total and no fee, which is what makes the device "
                   "refuse it")

        # 1e. HOW FAR "Check an address" can actually see. Core answers
        #     ismine from the addresses it has DERIVED, not from the key,
        #     so an address the wallet really owns comes back False once
        #     the index passes Core's keypool. The screen used to say "no
        #     loaded key owns that address", which is a claim the device
        #     cannot support (found reading main.py, 2026-09-07).
        #
        #     This pins the boundary against Core itself, so the day a
        #     Core release moves it, the number on the screen is wrong
        #     and this fails rather than the user being misled.
        depth = coresigner_main.ADDRESS_CHECK_DEPTH
        inside = signer.receive_addresses(rpc, gen_name, "wpkh", 1,
                                          depth - 1)[0]
        beyond = signer.receive_addresses(rpc, gen_name, "wpkh", 1, depth)[0]
        in_mine = rpc.call("getaddressinfo", inside,
                           wallet=gen_name).get("ismine")
        out_mine = rpc.call("getaddressinfo", beyond,
                            wallet=gen_name).get("ismine")
        if not in_mine:
            bad(f"1e: Core does not recognise its own address at index "
                f"{depth - 1}, so ADDRESS_CHECK_DEPTH is too high")
        elif out_mine:
            bad(f"1e: Core still recognises index {depth}, so "
                f"ADDRESS_CHECK_DEPTH is lower than it needs to be and "
                f"the screen understates what was checked")
        else:
            ok(f"1e: ismine reaches exactly the first {depth} addresses, "
               f"which is the number the screen quotes")

        # Whatever it presents, nothing exported may carry a private key.
        leaked = [d for d in signer.export_descriptors(rpc, gen_name)
                  if any(p in d for p in signer.XPRV_PREFIXES)]
        if leaked:
            bad(f"an exported descriptor carried a private key: {leaked[0][:40]}")
        else:
            ok("no exported descriptor carries a private key, all four kinds")
        signer.close_key(rpc, gen_name)

        # 2. The descriptor Core Signer exports is Core's own string, byte for
        #    byte, with its checksum.
        desc = signer.export_descriptor(rpc, name, "wpkh")
        core = [d["desc"] for d in
                rpc.call("listdescriptors", wallet=name)["descriptors"]
                if d["desc"].startswith("wpkh(") and not d["internal"]][0]
        if desc == core:
            ok("the exported descriptor is Core's own string, checksum included")
        else:
            bad(f"export rewrote the descriptor:\n  {desc}\n  {core}")
        if signer.export_descriptor(rpc, name, "tr").startswith("tr("):
            ok("taproot exports too")
        else:
            bad("the taproot export is not a tr() descriptor")

        # 3. Addresses shown for comparison come from deriveaddresses, which
        #    is side-effect free. getnewaddress would advance the wallet's
        #    index every time the screen was drawn.
        before = rpc.call("getwalletinfo", wallet=name)["keypoolsize"]
        addrs = signer.receive_addresses(rpc, name, "wpkh", 3)
        after = rpc.call("getwalletinfo", wallet=name)["keypoolsize"]
        want = rpc.call("deriveaddresses", core, [0, 2])
        if addrs == want and len(addrs) == 3:
            ok(f"the first three addresses match Core: {addrs[0][:14]}…")
        else:
            bad(f"addresses {addrs} != Core's {want}")
        if before == after:
            ok(f"drawing the address screen does not move the keypool ({after})")
        else:
            bad(f"the keypool moved from {before} to {after}")

        # 3b. Browsing past the first block keeps deriving, and never
        #     repeats an address. Receive branch only, by decision.
        first = signer.receive_addresses(rpc, name, "wpkh", 10, 0)
        second = signer.receive_addresses(rpc, name, "wpkh", 10, 10)
        if len(set(first + second)) == 20 and first[:3] == addrs:
            ok("address browsing pages on without repeating (20 derived)")
        else:
            bad("address paging repeats or does not continue")
        change_desc = signer.export_descriptor(rpc, name, "wpkh", branch=1)
        change = rpc.call("deriveaddresses", change_desc, [0, 2])
        if not set(change) & set(first):
            ok("the change branch is a different set, and is not browsed")
        else:
            bad("a change address appeared in the receive list")

        # 4. Bitcoin Core has no QR reader, so its export is a watch-only
        #    wallet file made by Core's own backupwallet. A second Core
        #    restores it, owns the same addresses, and holds no private key.
        out = signer.write_watch_only(rpc, name, work)
        if out.exists() and out.stat().st_size > 0:
            ok(f"watch-only wallet file written: {out.name}")
        else:
            bad("no watch-only wallet file was written")
        orpc.call("restorewallet", "fromcoresigner", str(out))
        info = orpc.call("getwalletinfo", wallet="fromcoresigner")
        if info.get("private_keys_enabled") is False:
            ok("the restored wallet has no private keys")
        else:
            bad(f"the restored wallet reports private_keys_enabled="
                f"{info.get('private_keys_enabled')}")
        mine = orpc.call("getaddressinfo", addrs[0], wallet="fromcoresigner")
        if mine.get("ismine") and not mine.get("isscript", False):
            ok("the restored wallet owns Core Signer's first receive address")
        else:
            bad("the restored wallet does not own Core Signer's first address")
        # And the file itself carries no secret.
        from test_no_persistence import _key_bytes
        blob = out.read_bytes()
        if any(n in blob for n in _key_bytes(XPRV_A).values()):
            bad("the exported wallet file contains key material")
        else:
            ok("the exported wallet file contains no key material")

        # 4b. The watch-only file names the key it watches, so a person
        #     with two of them on a stick can tell which is which. The
        #     probe is a filename signer actually WROTE, not one built
        #     from the same constant, or drift could never show.
        xfp = signer.master_fingerprint(rpc, wallet=name)
        if xfp in out.name and out.name.endswith("-watch.dat"):
            ok(f"the watch-only file names its key: {out.name}")
        else:
            bad(f"{out.name} does not name the key {xfp}")

        # 6. A SCANNED pair, all four policies, exports the receive chain
        #    as the receive chain.
        #
        #    open_session_descriptors decides internal= from the
        #    descriptor's own text, and it used to do it by counting
        #    closing parens: `endswith("/1/*)")`. Three policies end in one
        #    paren and sh(wpkh(...)) ends in two, so a nested-segwit pair
        #    arrived as two RECEIVE descriptors. Core does not refuse that.
        #    It deactivates the first and keeps the second, so the wallet
        #    kept the CHANGE chain active and marked external, and the
        #    public key a coordinator was handed described the change
        #    chain as the receive chain (2026-09-08).
        #    A key of its own, because XPRV_A is loaded above and _import
        #    refuses a duplicate by fingerprint.
        donor = signer.generate_wallet(rpc)
        scanned_key = signer.master_xprv(rpc, wallet=donor)
        signer._drop_wallet(rpc, donor)
        for label, shape in (("wpkh", "wpkh({k}/84h/1h/0h/{c}/*)"),
                             ("tr", "tr({k}/86h/1h/0h/{c}/*)"),
                             ("pkh", "pkh({k}/44h/1h/0h/{c}/*)"),
                             ("sh(wpkh)", "sh(wpkh({k}/49h/1h/0h/{c}/*))")):
            pair = [shape.format(k=scanned_key, c=c) for c in (0, 1)]
            slot = signer.open_session_descriptors(rpc, pair)
            listed = rpc.call("listdescriptors", wallet=slot)["descriptors"]
            chains = {bool(x.get("internal")): x for x in listed
                      if x["active"]}
            exported = signer.export_descriptors(rpc, wallet=slot)
            recv = [d for d in exported if "/0/*" in d]
            if len(chains) != 2:
                bad(f"a scanned {label} pair left {len(chains)} active "
                    f"chain(s), not a receive and a change: "
                    f"{[(x.get('internal'), x['active']) for x in listed]}")
            elif not recv:
                bad(f"a scanned {label} pair exports no receive chain, so "
                    f"a coordinator would watch the change chain: "
                    f"{[d[:40] for d in exported]}")
            else:
                ok(f"a scanned {label} pair keeps both chains and exports "
                   f"the receive one")
            signer._drop_wallet(rpc, slot)

        # 5. Exporting must not disturb the session: the key still signs and
        #    no extra wallet is left behind.
        left = [w for w in rpc.call("listwallets") if w in signer.SLOTS]
        if left == [name]:
            ok("export leaves exactly the loaded key, no scratch wallet")
        else:
            bad(f"after export the loaded keys are {left}")
    finally:
        for r, d in ((rpc, daemon), (orpc, other)):
            try:
                r.call("stop")
            except Exception:
                pass
            d.wait(timeout=30)
        shutil.rmtree(datadir, ignore_errors=True)
        shutil.rmtree(odir, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)
    print()
    print("FAILED %d" % len(fails) if fails else "ALL PASS")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
