"""MULTISIG REVIEW. What the screen says before you sign one share.

Map multisig-cosigner, ticket M9, which builds M3's three decisions.
`tests/sparrow/test_cosigner.py` proves Sparrow takes our cosigner key
and signs beside it. This proves the DEVICE can do it: the review screen
states the threshold, the path and the cosigners, and a partial
signature is delivered rather than refused.

Run: python3 tests/test_multisig_review.py (needs bitcoind)
"""
import base64
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "coresigner"))
import signer  # noqa: E402

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


PATH = "48h/1h/0h/2h"


def cosigner(rpc, name):
    """One member of the quorum: a Core wallet, and its cosigner key."""
    rpc.call("createwallet", name)
    return signer.cosigner_key(rpc, name, PATH)


XPRV = ("tprv8ZgxMBicQKsPe5YMU9gHen4Ez3ApihUfykaqUorj9t6FDqy3nP6eoXiAo2ss"
        "vpAjoLroQxHqr3R5nE3a5dU3DHTjTgJDd7zrbniJr6nrCzd")

_built = [0]


def build_quorum(rpc, threshold=2, total=3, extra_key=None):
    """A real 2-of-3, funded, with a spend to review.

    Returns (psbt, keys, miner wallet, quorum wallet).

    `extra_key` puts an xprv WE hold into the quorum, so the spend is one
    this device can actually sign a share of.
    """
    tag = f"q{_built[0]}"
    _built[0] += 1
    keys = [cosigner(rpc, f"m9c{tag}{i}")
            for i in range(total - (1 if extra_key else 0))]
    if extra_key:
        rpc.call("createwallet", f"m9x{tag}", False, True, "", False, True)
        raw = f"wpkh({extra_key}/{PATH}/0/*)"
        c = rpc.call("getdescriptorinfo", raw, stdin=True)["checksum"]
        rpc.call("importdescriptors",
                 [{"desc": f"{raw}#{c}", "active": True, "timestamp": "now",
                   "range": [0, 20]}], wallet=f"m9x{tag}", stdin=True)
        keys.append(signer.cosigner_key(rpc, f"m9x{tag}", PATH))
    qw = f"m9quorum{tag}"
    rpc.call("createwallet", qw, True, True, "", False, True)
    for change in (0, 1):
        inner = (f"sortedmulti({threshold},"
                 + ",".join(f"{k}/{change}/*" for k in keys) + ")")
        info = rpc.call("getdescriptorinfo", f"wsh({inner})", stdin=True)
        rpc.call("importdescriptors",
                 [{"desc": f"wsh({inner})#{info['checksum']}",
                   "active": True, "internal": bool(change),
                   "timestamp": "now", "range": [0, 10]}],
                 wallet=qw, stdin=True)
    mw = f"m9miner{tag}"
    rpc.call("createwallet", mw)
    mine = rpc.call("getnewaddress", wallet=mw)
    rpc.call("generatetoaddress", 101, mine, wallet=mw)
    # TWO utxos at DIFFERENT addresses, and a spend that needs both. One
    # input cannot tell a cosigner list that dedupes from one that
    # appends, and the quorum must read "2 of 3" either way.
    for _ in range(2):
        addr = rpc.call("getnewaddress", wallet=qw)
        rpc.call("sendtoaddress", addr, 1.0, wallet=mw)
    rpc.call("generatetoaddress", 1, mine, wallet=mw)
    psbt = rpc.call("walletcreatefundedpsbt", [], [{mine: 1.5}], 0,
                    {"fee_rate": 5}, True, wallet=qw)["psbt"]
    return psbt, keys, mw, qw


def build_decay(rpc):
    """A three-tier decaying quorum, funded, with a spend per tier.

    Our key sits at a DIFFERENT account in each tier, which is not a
    style choice: Core refuses the policy as "contains duplicate public
    keys" when a key repeats at one path, which is Liana's
    DuplicateOriginSamePath seen from the other side.

    Returns (spends, xfp) where spends is [(label, sequence, psbt), ...].
    """
    rpc.call("createwallet", "dours", False, True, "", False, True)
    ours = []
    for acct in (0, 1, 2):
        path = f"48h/1h/{acct}h/2h"
        raw = f"wpkh({XPRV}/{path}/0/*)"
        c = rpc.call("getdescriptorinfo", raw, stdin=True)["checksum"]
        rpc.call("importdescriptors",
                 [{"desc": f"{raw}#{c}", "active": acct == 0,
                   "timestamp": "now", "range": [0, 3]}],
                 wallet="dours", stdin=True)
        ours.append(signer.cosigner_key(rpc, "dours", path) + "/0/0")
    strangers = {}
    for i in (0, 1):
        rpc.call("createwallet", f"ds{i}")
        for acct in (0, 1):
            strangers[(i, acct)] = signer.cosigner_key(
                rpc, f"ds{i}", f"48h/1h/{acct}h/2h") + "/0/0"
    a, b, c = ours
    desc = (f"wsh(or_d(multi(2,{a},{strangers[(0, 0)]},{strangers[(1, 0)]}),"
            f"or_i(and_v(v:multi(2,{b},{strangers[(0, 1)]}),older(10)),"
            f"and_v(v:pk({c}),older(20)))))")
    desc += "#" + rpc.call("getdescriptorinfo", desc, stdin=True)["checksum"]
    rpc.call("createwallet", "dq", True, True, "", False, True)
    rpc.call("importdescriptors", [{"desc": desc, "timestamp": "now"}],
             wallet="dq", stdin=True)
    addr = rpc.call("deriveaddresses", desc, stdin=True)[0]
    rpc.call("createwallet", "dmn")
    mine = rpc.call("getnewaddress", wallet="dmn")
    rpc.call("generatetoaddress", 101, mine, wallet="dmn")
    rpc.call("sendtoaddress", addr, 1.0, wallet="dmn")
    rpc.call("generatetoaddress", 25, mine, wallet="dmn")
    u = rpc.call("listunspent", 1, 9999, [addr], wallet="dq")[0]
    spends = []
    for label, seq in (("normal", 0xfffffffd), ("tier 2", 10),
                       ("tier 3", 20)):
        spends.append((label, seq, rpc.call(
            "walletcreatefundedpsbt",
            [{"txid": u["txid"], "vout": u["vout"], "sequence": seq}],
            [{mine: float(u["amount"])}], 0,
            {"fee_rate": 2, "subtractFeeFromOutputs": [0]}, True,
            wallet="dq", stdin=True)["psbt"]))
    return spends, signer.master_fingerprint(rpc, "dours")


def both_versions(rpc, wallet, ins, outs):
    """ONE transaction, as a PSBT v0 and as a PSBT v2. Returns (v0, v2).

    Built from one raw transaction and filled by the wallet that owns
    the inputs, rather than funded twice, so the two cannot differ in
    coin choice, change address or output order. The version is the only
    thing left to differ, and the version is what is under test.
    """
    raw = rpc.call("createrawtransaction", ins, outs, stdin=True)
    pair = []
    for version in (0, 2):
        bare = rpc.call("converttopsbt", raw, False, True, version,
                        stdin=True)
        filled = rpc.call("walletprocesspsbt", bare, False, "ALL", True,
                          False, wallet=wallet, stdin=True)["psbt"]
        got = rpc.call("decodepsbt", filled, stdin=True)["psbt_version"]
        if got != version:
            raise AssertionError(f"asked Core for PSBT v{version}, got v{got}")
        pair.append(filled)
    return tuple(pair)


def _compact(data, at):
    """A Bitcoin compact size at `at`. Returns (value, next offset)."""
    first = data[at]
    if first < 0xfd:
        return first, at + 1
    width = {0xfd: 2, 0xfe: 4, 0xff: 8}[first]
    return int.from_bytes(data[at + 1:at + 1 + width], "little"), at + 1 + width


def _as_compact(n):
    if n < 0xfd:
        return bytes([n])
    return b"\xfd" + n.to_bytes(2, "little")


def without_sequence(psbt_b64, index):
    """The same PSBT v2 with PSBT_IN_SEQUENCE removed from one input.

    BIP 370 makes the field optional and reads an absent one as
    0xffffffff. Core always writes it, measured on 32.1 (2026-11-04), so
    no Core call makes this case. Another coordinator may, so it is cut
    out of a real PSBT here, byte for byte, and nothing else moves.
    """
    data = base64.b64decode(psbt_b64)
    maps, at = [], 5
    while at < len(data):
        pairs = []
        while True:
            klen, at = _compact(data, at)
            if klen == 0:
                break
            key = data[at:at + klen]
            vlen, at = _compact(data, at + klen)
            pairs.append((key, data[at:at + vlen]))
            at += vlen
        maps.append(pairs)
    # maps[0] is the global map, so input `index` is maps[1 + index].
    maps[1 + index] = [(k, v) for k, v in maps[1 + index] if k != b"\x10"]
    body = b"".join(
        b"".join(_as_compact(len(k)) + k + _as_compact(len(v)) + v
                 for k, v in pairs) + b"\x00"
        for pairs in maps)
    return base64.b64encode(data[:5] + body).decode()


def main():
    daemon, rpc, datadir = start_node("m9-")
    try:
        psbt, keys, mw, qw = build_quorum(rpc)

        # 1. THE THRESHOLD. M3 decision 2: review says "2 of 3". Core
        #    already reports the script type and the threshold, so Core Signer
        #    reads them and parses no script itself (PLAN A-11).
        info = signer.describe_psbt(rpc, psbt)
        if info.get("quorum") == (2, 3):
            ok(f"review knows the quorum: {info.get('quorum')}")
        else:
            bad(f"1: quorum should be (2, 3), got {info.get('quorum')!r}")

        # 2. A SINGLE-SIG PSBT MUST NOT CLAIM ONE. The screen showing
        #    "1 of 1" on an ordinary spend would be a new lie, which is
        #    the thing A6 was about.
        rpc.call("createwallet", "m9solo")
        solo_addr = rpc.call("getnewaddress", wallet="m9solo")
        rpc.call("sendtoaddress", solo_addr, 1.0, wallet=mw)
        mine = rpc.call("getnewaddress", wallet=mw)
        rpc.call("generatetoaddress", 1, mine, wallet=mw)
        solo = rpc.call("walletcreatefundedpsbt", [], [{mine: 0.5}], 0,
                        {"fee_rate": 5}, True, wallet="m9solo")["psbt"]
        solo_info = signer.describe_psbt(rpc, solo)
        if solo_info.get("quorum") is None:
            ok("a single-sig spend reports no quorum")
        else:
            bad(f"2: single-sig reported quorum {solo_info['quorum']!r}")

        # 3. MIXED INPUTS MUST NOT BECOME ONE CONFIDENT CLAIM. Reading
        #    the first input and printing "2 of 3" over a transaction
        #    that also spends a single-sig UTXO is exactly the kind of
        #    true-looking screen audit A6 removed.
        #
        #    joinpsbts takes only v0 (Core 32.1: "joinpsbts only operates
        #    on version 0 PSBTs"), and 32.x funds v2 by default, so the
        #    two halves are funded again as v0. The arguments after
        #    `True` are the transaction version and the PSBT version.
        joined = rpc.call("joinpsbts", [
            rpc.call("walletcreatefundedpsbt", [], [{mine: 1.5}], 0,
                     {"fee_rate": 5}, True, 2, 0, wallet=qw)["psbt"],
            rpc.call("walletcreatefundedpsbt", [], [{mine: 0.5}], 0,
                     {"fee_rate": 5}, True, 2, 0,
                     wallet="m9solo")["psbt"]], stdin=True)
        mixed = signer.describe_psbt(rpc, joined)
        if mixed.get("quorum") == "mixed":
            ok("a transaction spending both reports a mixed quorum")
        else:
            bad(f"3: mixed inputs reported {mixed.get('quorum')!r}, and the "
                "screen would state one quorum for all of them")

        # 4. THE COSIGNERS. M3 decision 3: the review screen names every
        #    fingerprint on the transaction. signer.owners() already
        #    reads them; describe_psbt now carries them WITH the path,
        #    because M1 decision 2 puts the path on the same screen.
        if info["input_count"] != 2:
            bad(f"fixture: wanted a 2-input spend, got "
                f"{info['input_count']}; checks 4 and 5 are weaker")
        cos = info.get("cosigners") or []
        want = signer.owners(rpc, psbt)
        if len(cos) == 3 and {x for x, _ in cos} == want:
            ok(f"review names all three cosigners: "
               f"{', '.join(sorted(x for x, _ in cos))}")
        else:
            bad(f"4: cosigners {cos!r} should be 3 entries matching {want!r}")

        # 5. THE ACCOUNT PATH, NOT THE LEAF. Core reports
        #    m/48h/1h/0h/2h/0/0, whose last two steps are the change
        #    branch and the address index. Those change every address
        #    and say nothing about which wallet this is, which is the
        #    thing M1 decision 2 wants the path to say.
        paths = {pth for _, pth in cos}
        if paths == {"m/48h/1h/0h/2h"}:
            ok("the path shown is the account, with no change/index tail")
        else:
            bad(f"5: paths {paths!r} should all be m/48h/1h/0h/2h")

        # 6. A SINGLE-SIG SPEND STILL SHOWS ITS PATH. M1: with arbitrary
        #    paths allowed, the path is the only thing telling a wallet
        #    you set up from one you did not, so it is not multisig-only.
        solo_cos = solo_info.get("cosigners") or []
        if len(solo_cos) == 1 and solo_cos[0][1].startswith("m/"):
            ok(f"a single-sig spend shows its path: {solo_cos[0][1]}")
        else:
            bad(f"6: single-sig cosigners {solo_cos!r} should be one entry "
                "carrying its account path")

        # 7. EVERY INPUT, NOT THE FIRST. A cosigner reader that stops at
        #    input 0 passes checks 4 to 6 and still hides a key from the
        #    screen: this transaction spends the quorum AND a single-sig
        #    utxo, so its second input carries a wallet the first does
        #    not mention.
        mixed_cos = mixed.get("cosigners") or []
        want_mixed = set(want) | {x for x, _ in solo_cos}
        if {x for x, _ in mixed_cos} == want_mixed and len(mixed_cos) == 4:
            ok(f"a mixed transaction names every input's key "
               f"({len(mixed_cos)} of them)")
        else:
            bad(f"7: mixed cosigners {mixed_cos!r} should be 4 entries "
                f"covering {want_mixed!r}")

        # 8. SIGNING NOTHING IS NOT SIGNING. M3 decision 1 delivers a
        #    partial signature instead of refusing it, and `complete`
        #    alone cannot carry that: it is False for one signature of
        #    two AND for none at all. A device that showed SIGNED over an
        #    untouched PSBT would be lying on its most important screen,
        #    which is the exact shape of the bug M4 nearly shipped.
        #
        #    This is a real case and not a contrived one: a loaded Core Signer
        #    key holds the four standard policies, so it has no key at a
        #    BIP48 path and Core signs nothing at all.
        solo_w = signer.open_session_xprv(rpc, XPRV)
        blind = signer.sign_psbt(rpc, psbt, wallet=solo_w)
        if blind.get("added") is False and blind["complete"] is False:
            ok("a wallet with no key at that path reports nothing added")
        else:
            bad(f"8: added={blind.get('added')!r} for a wallet that signed "
                "nothing, so SIGNED would show over an untouched PSBT")

        # 9. AND A REAL SHARE IS REPORTED AS ONE.
        rpc.call("createwallet", "m9ours", False, True, "", False, True)
        for change in (0, 1):
            raw = f"wpkh({XPRV}/{PATH}/{change}/*)"
            c = rpc.call("getdescriptorinfo", raw, stdin=True)["checksum"]
            rpc.call("importdescriptors",
                     [{"desc": f"{raw}#{c}", "active": True,
                       "internal": bool(change), "timestamp": "now",
                       "range": [0, 20]}], wallet="m9ours", stdin=True)
        quorum_psbt, _, _, _ = build_quorum(rpc, extra_key=XPRV)
        share = signer.sign_psbt(rpc, quorum_psbt, wallet="m9ours")
        if share.get("added") is True and share["complete"] is False:
            ok("one share of a quorum reports a signature added, not done")
        else:
            bad(f"9: added={share.get('added')!r} complete="
                f"{share['complete']!r} for one share of a 2-of-3")

        # 10. THE DEVICE SIGNS WHERE THE PSBT SAYS. M1 decision 2:
        #     signing takes its path from the PSBT. A loaded key holds
        #     the four standard policies and nothing at a BIP48 path, so
        #     without this the device cannot sign a share at all, which
        #     is the gap between what M4 proved possible and what Core Signer
        #     does. Same PSBT and same wallet as check 8, which signed
        #     nothing.
        told = signer.sign_psbt(rpc, quorum_psbt, wallet=solo_w,
                                xfp=signer.master_fingerprint(rpc, solo_w))
        if told.get("added") is True and told["complete"] is False:
            ok("a loaded key signs its share at the path the PSBT names")
        else:
            bad(f"10: added={told.get('added')!r} complete="
                f"{told['complete']!r}; the device cannot sign a share")

        # 11. AND IT LEAVES NOTHING BEHIND. The fallback imports the
        #     branch to sign it. PLAN A-24 and the whole persistence
        #     argument say what it imports must not outlive the signing.
        # The scratch wallet BY NAME. This matched `"sign" in w`, which
        # also matches the session wallet now that it is called
        # coresigner: a substring filter reads as a filter and is a
        # coincidence (rename, 2026-09-11).
        after = {w for w in rpc.call("listwallets")
                 if w == signer._SIGN_SCRATCH}
        if not after:
            ok("the branch it imported to sign with is gone again")
        else:
            bad(f"11: {after!r} left loaded after signing")

        # 12. A DECAYING QUORUM. Core types a miniscript witness script
        #     as "nonstandard", so `quorum` is None and the review screen
        #     says no threshold. M3 decision 1 traded SIGNED-over-partial
        #     against decision 2 stating the threshold, and for this
        #     class of wallet the screen never had one. So the screen
        #     says the TIER instead, out of the same asm Core hands over.
        spends, dxfp = build_decay(rpc)
        by_label = {}
        for label, _seq, dpsbt in spends:
            by_label[label] = signer.describe_psbt(rpc, dpsbt)
        if by_label["tier 3"].get("timelocks") == (10, 20):
            ok("review reads both timelocks out of the policy: (10, 20)")
        else:
            bad(f"12: timelocks {by_label['tier 3'].get('timelocks')!r} "
                "should be (10, 20)")

        # 13. AND WHICH TIER THIS SPEND USES. The coordinator chooses it
        #     with nSequence at build time, which M6 measured. The device
        #     cannot change it and the person can refuse it, so the
        #     screen has to show it.
        got = {k: v.get("spend_lock") for k, v in by_label.items()}
        if got == {"normal": None, "tier 2": 10, "tier 3": 20}:
            ok(f"review names the tier each spend enables: {got}")
        else:
            bad(f"13: spend_lock {got!r} should be "
                "{'normal': None, 'tier 2': 10, 'tier 3': 20}")

        # 13b. BIP68 PUTS THE VALUE IN THE LOW 16 BITS. A sequence of
        #      0x00010005 means five blocks; read as a whole word it is
        #      65541, which clears both tiers and would name one the
        #      spend does not reach. Nothing else in this file separates
        #      the two readings, because 10 and 20 survive the mask.
        u = rpc.call("listunspent", 1, 9999, wallet="dq")[0]
        odd = rpc.call(
            "walletcreatefundedpsbt",
            [{"txid": u["txid"], "vout": u["vout"], "sequence": 0x00010005}],
            [{rpc.call("getnewaddress", wallet="dmn"): float(u["amount"])}],
            0, {"fee_rate": 2, "subtractFeeFromOutputs": [0]}, True,
            wallet="dq", stdin=True)["psbt"]
        odd_lock = signer.describe_psbt(rpc, odd).get("spend_lock")
        if odd_lock is None:
            ok("a sequence of 0x00010005 reaches no tier, as BIP68 reads it")
        else:
            bad(f"13b: spend_lock {odd_lock!r} for a sequence whose low 16 "
                "bits are 5; the screen would name a tier not reached")

        # 14. A PLAIN QUORUM CLAIMS NO TIMELOCK. A screen that said
        #     "AFTER 20 BLOCKS" over an ordinary 2-of-3 would be the
        #     same lie in the other direction.
        if not info.get("timelocks") and info.get("spend_lock") is None:
            ok("a plain 2-of-3 reports no timelock and no tier")
        else:
            bad(f"14: a plain quorum reported timelocks "
                f"{info.get('timelocks')!r} lock={info.get('spend_lock')!r}")

        # 15. AND CORESIGNER SIGNS EVERY TIER IT IS IN, IN ONE PASS. Our key
        #     sits at three accounts here. M9's _branches reads all of
        #     them out of the PSBT, so nothing has to be set up first.
        t3 = signer.sign_psbt(rpc, spends[2][2], wallet="dours", xfp=dxfp)
        if t3["added"] and t3["complete"]:
            ok("past its timelock, Core Signer alone finishes the spend")
        else:
            bad(f"15: tier 3 added={t3['added']} complete={t3['complete']}; "
                "the one-key tier must finish on this device alone")

        # 16. PSBT V2 GIVES THE SAME REVIEW AS V0. Core 32.0 accepts v2
        #     and walletcreatefundedpsbt makes it by default, so a Core
        #     coordinator sends it. A v2 decode has no `tx`: the outputs
        #     carry `amount` and `script`, the inputs carry `sequence`.
        #     One transaction in both versions must give the same review,
        #     field for field, for each thing the screen says: a plain
        #     spend, a quorum, and every tier of a decaying policy.
        mine = rpc.call("getnewaddress", wallet="dmn")
        solo_u = rpc.call("listunspent", 1, 9999, wallet="m9solo")[0]
        q_us = rpc.call("listunspent", 1, 9999, wallet=qw)
        d_u = rpc.call("listunspent", 1, 9999, wallet="dq")[0]
        pairs = [
            ("single-sig", "m9solo",
             [{"txid": solo_u["txid"], "vout": solo_u["vout"]}],
             [{mine: 0.4},
              {rpc.call("getrawchangeaddress", wallet="m9solo"): 0.5999}]),
            ("2 of 3", qw,
             [{"txid": u["txid"], "vout": u["vout"]} for u in q_us],
             [{mine: 1.5},
              {rpc.call("getrawchangeaddress", wallet=qw): 0.4999}]),
        ]
        for label, seq in (("normal", 0xfffffffd), ("tier 2", 10),
                           ("tier 3", 20), ("final", 0xffffffff)):
            pairs.append((
                f"decay {label}", "dq",
                [{"txid": d_u["txid"], "vout": d_u["vout"], "sequence": seq}],
                [{mine: float(d_u["amount"]) - 0.0001}]))
        for name, wallet, ins, outs in pairs:
            v0, v2 = both_versions(rpc, wallet, ins, outs)
            r0 = signer.describe_psbt(rpc, v0)
            r2 = signer.describe_psbt(rpc, v2)
            differ = sorted(k for k in set(r0) | set(r2)
                            if r0.get(k) != r2.get(k))
            if not r0["outputs"] or r0["fee_btc"] is None:
                bad(f"16 {name}: the v0 review is empty, so equality "
                    "proves nothing")
            elif differ:
                bad(f"16 {name}: v0 and v2 reviews differ in {differ}: "
                    f"{ {k: (r0.get(k), r2.get(k)) for k in differ} }")
            else:
                ok(f"{name}: v0 and v2 give the same review, "
                   f"{len(r0)} fields, lock={r2['spend_lock']}")

        # 17. AN ABSENT SEQUENCE IS 0xffffffff, NOT "SKIP THIS INPUT".
        #     BIP 370 makes PSBT_IN_SEQUENCE optional, and Core then
        #     leaves `sequence` out of the decode. Two inputs: one at
        #     tier 3, one with no sequence. Read as 0xffffffff the
        #     inputs disagree and no tier is named, exactly as v0 with
        #     that sequence says. A reader that skipped the input would
        #     say AFTER 20 BLOCKS over a spend whose other input has no
        #     relative lock at all.
        rpc.call("sendtoaddress", d_u["address"], 1.0, wallet="dmn")
        rpc.call("generatetoaddress", 1, mine, wallet="dmn")
        d_us = rpc.call("listunspent", 1, 9999, [d_u["address"]], wallet="dq")
        two = [{"txid": u["txid"], "vout": u["vout"], "sequence": s}
               for u, s in zip(d_us[:2], (20, 0xffffffff), strict=True)]
        total = sum(float(u["amount"]) for u in d_us[:2])
        v0, v2 = both_versions(rpc, "dq", two, [{mine: total - 0.0002}])
        bare = without_sequence(v2, 1)
        seqs = [i.get("sequence") for i in
                rpc.call("decodepsbt", bare, stdin=True)["inputs"]]
        r0 = signer.describe_psbt(rpc, v0)
        rb = signer.describe_psbt(rpc, bare)
        if len(d_us) < 2 or seqs != [20, None]:
            bad(f"17: fixture: wanted inputs with sequences [20, absent], "
                f"Core decodes {seqs}")
        elif r0 != rb or rb["spend_lock"] is not None:
            bad(f"17: absent sequence gave lock={rb['spend_lock']!r}; v0 "
                f"with 0xffffffff gives {r0['spend_lock']!r}")
        else:
            ok("an input with no sequence reads as 0xffffffff: the same "
               "review as v0, and no tier named")
    finally:
        daemon.terminate(); daemon.wait()
    print("\n" + ("=" * 60))
    print(f"MULTISIG REVIEW: {'FAIL ' + str(len(fails)) if fails else 'PASS'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
