"""Core Signer's session flow: everything between loading a key and a signed PSBT.

This module performs no cryptography. It hands Core what the user supplied,
then drives Bitcoin Core over RPC. Core does all key derivation, all PSBT
parsing, all fee arithmetic and all signing. Every function here is plumbing.

The front end (screen/camera) calls exactly four things per session:
    open_session_xprv(xprv) / open_session_descriptors(descs)
    describe_psbt(psbt_b64)             -> dict for the review screen
    sign_psbt(psbt_b64)                 -> signed PSBT (base64)
    close_session()                     -> wallet unloaded (ramdisk wipe is
                                           the real teardown at power-off)
"""

import contextlib
import json
import re
import shutil
import time
from collections import namedtuple
from decimal import Decimal
import subprocess
from pathlib import Path


# Anything that looks like an extended private key, in any network's
# prefix. Core quotes the offending key back in its own error messages
# (verified against 31.1 on 2026-09-05: getdescriptorinfo answers
# "wpkh(): key 'tprv8Zgx...' is not valid"), and Core Signer puts Core's message
# on the panel. Unredacted, that message also reaches stderr, which systemd
# captures into the journal on the SD card. A key on the card is the one
# thing this device must never do.
#: Every prefix a BIP32 extended PRIVATE key can carry, across networks and
#: SLIP-132 script types. One list, so the redactor and the scan classifier
#: cannot disagree about what a private key looks like.
#:
#: The four UPPERCASE forms are SLIP-132's multisig prefixes. They were
#: missing until 2026-09-08, and the list is used for two different jobs,
#: so a Zprv escaped both of them: it was not redacted out of Core's
#: refusal, and it did not trip the stdin guard in Rpc.call, so it went
#: into argv where `ps` reads it. Core Signer does not accept a multisig key and
#: Core refuses one, but the refusal quotes the key back, and by then it
#: had already been on the process list.
XPRV_PREFIXES = ("xprv", "tprv", "yprv", "zprv", "vprv", "uprv",
                 "Yprv", "Zprv", "Uprv", "Vprv")

#: A WIF private key, which carries no word-shaped prefix at all: one
#: character for network and compression, then 50 or 51 base58 characters.
#: 5 and 9 are the uncompressed forms, K, L and c the compressed ones.
#:
#: Core Signer never asks for a WIF, but a person typing a key on a five-way pad
#: can paste or mistype one, and Core echoes what it refused: "key
#: 'cVjzvdHG…' is not valid" reached the panel and the journal in full
#: until audit A2 (2026-09-06). Redaction is defence in depth, so it
#: covers key forms this device does not accept as well as the ones it
#: does.
_WIF_RE = r"\b[59KLc][1-9A-HJ-NP-Za-km-z]{50,51}\b"

_SECRET_RE = re.compile(
    r"\b(?:%s)[1-9A-HJ-NP-Za-km-z]{20,}|%s"
    % ("|".join(XPRV_PREFIXES), _WIF_RE))


def redact(text: str) -> str:
    """Strip key material out of text bound for a screen, a log or the
    journal. This is string handling, not key handling: nothing here
    computes on a key, it only refuses to repeat one (PLAN A-22)."""
    return _SECRET_RE.sub("<key redacted>", text)


def _json_decimal(obj):
    if isinstance(obj, Decimal):
        return str(obj)  # Core accepts string amounts; never re-floated
    raise TypeError


WALLET = "coresigner"

# Several keys in one session (map e2e-before-testers, ticket 03): one Core
# wallet per key, up to MAX_KEYS. The first key keeps the historic wallet
# name; the rest take numbered slots. The fingerprint, not the slot, names a
# key on screen. Measured on the Zero 2 W: about 3MB of bitcoind RSS per key.
MAX_KEYS = 5
SLOTS = (WALLET,) + tuple(f"{WALLET}-{i}" for i in range(2, MAX_KEYS + 1))

#: A loaded key: the Core wallet that holds it, and the fingerprint that
#: names it to the user.
Key = namedtuple("Key", "name xfp")

# Account-level derivation, hardened, per BIP84/BIP86. Coin type 0' mainnet,
# 1' for test networks, per SLIP-44.
#: The BIP purpose and descriptor function for each of Core's four script
#: policies, in the order the panel walks them. A key that arrives by scan
#: or by typing is built with ALL of these, so it presents what a key Core
#: generated presents.
#:
#: It was (84, 86) until 2026-09-05, which meant restoring your own paper
#: backup gave you two of the four policies the key controls. Coins on a
#: legacy or nested address were still the key's and still spendable by
#: anyone who imported the right descriptor, and Core Signer showed neither the
#: address nor the balance. Measured on the board: four pairs is a 44kB
#: wallet against 20kB for two, and the node's RSS does not move. 24kB per
#: key against 512MB of RAM is not a reason to hide half a wallet.
PURPOSE_FUNCS = ((44, "pkh({key})"),
                 (49, "sh(wpkh({key}))"),
                 (84, "wpkh({key})"),
                 (86, "tr({key})"))

#: How long any one bitcoin-cli call may take before the device gives up.
#:
#: Measured on the Zero 2 W, 2026-09-06, with the M0 gate: opening a key
#: (importdescriptors, eight descriptors) takes **4.4s**, and building,
#: reviewing and signing a 60-input PSBT takes **1.5s**. This cap is 27x
#: the slowest of those, so it cannot fire on a healthy node doing real
#: work; it exists only to turn "frozen for ever" into a message.
RPC_TIMEOUT = 120.0


class Rpc:
    """Minimal bitcoin-cli wrapper. chain: 'main', 'test', 'regtest', 'signet'."""

    def __init__(self, datadir, chain="main", cli="bitcoin-cli"):
        flag = {"main": [], "test": ["-testnet"], "testnet4": ["-testnet4"],
                "regtest": ["-regtest"], "signet": ["-signet"]}[chain]
        self.base = [cli, f"-datadir={datadir}", *flag]
        self.chain = chain
        # Verified against Core 31.1: -testnet still writes testnet3/,
        # and -testnet4 writes testnet4/.
        subdir = {"main": "", "test": "testnet3", "testnet4": "testnet4",
                  "regtest": "regtest", "signet": "signet"}[chain]
        self.net_dir = Path(datadir) / subdir

    @property
    def wallet_dir(self):
        """Where Core keeps this node's wallets, decided the way Core decides
        it: the wallets/ directory when one exists, else the datadir itself.
        On the Zero 2 W's ramdisk datadir there is no wallets/ directory, so
        the board's wallets sit at /run/coresigner/<name>. A fixed wallets/ path
        would have left every wallet directory behind on close (seen
        2026-09-04)."""
        sub = self.net_dir / "wallets"
        return sub if sub.is_dir() else self.net_dir

    def call(self, method: str, *params, wallet: "str | None" = None,
             stdin: bool = False, drop: "frozenset[str] | tuple" = ()):
        """Run one bitcoin-cli command.

        stdin=True sends the parameters through bitcoin-cli's -stdin instead
        of argv, so key material never appears in a process listing.

        **Callers no longer have to remember.** This used to read "callers
        that pass an xprv or a private descriptor MUST set it (S4)", which
        put a correctness rule in the caller's head and enforced it
        nowhere. On 2026-09-05 a caller forgot and a master private key
        went into argv twice per paper check; a two-axis review found it,
        not the suite. An invariant a module can check for itself does not
        belong in its interface, so this one checks: any argument that
        `redact` would strip goes through stdin whether it was asked for
        or not. Passing stdin=True still works and is still right for a
        PSBT.

        Callers that pass a PSBT should set it, for a second reason.
        Linux caps any SINGLE argument at MAX_ARG_STRLEN, 32 pages, which
        is 128KB, separately from the 2MB ARG_MAX total. A PSBT carries a
        whole previous transaction per input, so a many-input PSBT passes
        that cap and execve fails with E2BIG. macOS has no per-argument
        cap, so this cannot reproduce on the dev machine (I-10).
        """
        cmd = list(self.base)
        if wallet:
            cmd.append(f"-rpcwallet={wallet}")
        args = [p if isinstance(p, str)
                else json.dumps(p, default=_json_decimal) for p in params]
        # The module knows what key material looks like; the caller should
        # not have to. An empty argument list stays on argv, because
        # -stdin with nothing to read is a blank line bitcoin-cli has no
        # use for.
        #
        # THE SAME MATCHER AS redact(). This used to substring-search
        # XPRV_PREFIXES, which is one of the two things _SECRET_RE is
        # built from, so it saw extended keys and never a WIF: a WIF has
        # no word-shaped prefix at all, and one typed on the pad went
        # straight into argv (two-axis review, 2026-09-08). Two readings
        # of the same question in one file will disagree eventually, so
        # there is now one reading.
        #
        # It over-matches. _WIF_RE finds a hit in most megabyte-sized
        # base64, so a large PSBT is pushed to stdin whether or not it
        # holds a key. That is the direction to be wrong in, and it is
        # where a PSBT belonged anyway: Linux caps a single argument at
        # 128KB. Measured on 1MB of base64: 2.3ms, faster than the six
        # substring scans it replaces.
        if args and any(_SECRET_RE.search(a) for a in args):
            stdin = True
        feed = None
        if stdin:
            # bitcoin-cli -stdin reads the EXTRA ARGUMENTS from stdin, one
            # per line; the method itself stays in argv. Verified against
            # bitcoin-cli 31.1's own -stdin help text.
            cmd.append("-stdin")
            cmd.append(method)
            feed = "\n".join(args) + "\n"
        else:
            cmd += [method, *args]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True,
                                 input=feed, timeout=RPC_TIMEOUT)
        except subprocess.TimeoutExpired:
            # A node that is SLOW rather than dead is the case the suites
            # never had: absent bitcoind fails fast, but one that holds the
            # socket and never answers used to block here for ever. There
            # was no timeout at all, so the panel froze on a busy screen
            # with no way out and no shell to fix it from (audit A4,
            # 2026-09-06, measured with SIGSTOP on a live node).
            #
            # RuntimeError, because that is what Session.HANDLED catches
            # and what every other Core failure raises. The panel gets an
            # error it can dismiss instead of nothing, for ever.
            raise RuntimeError(
                f"{method}: Bitcoin Core did not answer in "
                f"{RPC_TIMEOUT:.0f}s") from None
        if out.returncode != 0:
            raise RuntimeError(f"{method}: {redact(out.stderr.strip())}")
        text = out.stdout.strip()
        try:
            # parse_float=Decimal: BTC amounts must never pass through binary
            # floats. The review screen is the device's security boundary.
            #
            # `drop` names fields to throw away AS the answer is parsed,
            # never afterwards, because the cost being avoided is building
            # them at all. decodepsbt on the 250-input batch case answers
            # with 10.8MB of JSON that becomes a 21.1MB object tree, and
            # 20.7MB of that is previous transactions the caller never
            # reads (measured 2026-09-06). Dropping by NAME and not by
            # path is deliberate: a field this build has never heard of is
            # kept, so a new one cannot go missing quietly.
            hook = None
            if drop:
                dropped = frozenset(drop)

                def hook(pairs):
                    return {k: v for k, v in pairs if k not in dropped}

            return json.loads(text, parse_float=Decimal,
                              object_pairs_hook=hook)
        except json.JSONDecodeError:
            return text


#: The first 16 characters of a Bitcoin Core master private key, which
#: are the same for every key on a given network. A master key is depth
#: 0 with no parent fingerprint and no child number, so the leading
#: bytes of the serialisation are fixed and base58 turns them into a
#: fixed prefix. Four boxes of 28 that nobody has to type.
#:
#: MEASURED, not reasoned (TESTING.md rule 6): 12 freshly generated
#: regtest keys and 12 testnet keys agreed on `tprv8ZgxMBicQKsP` and
#: nothing longer; BIP32's three published test vectors, this repo's
#: fixture and the key on the dev board agreed on `xprv9s21ZrQH143K`
#: (2026-09-19). Nothing here derives or checks a key: these are two
#: string constants a screen puts in front of a person (PLAN A-22).
MAINNET_PREFIX = "xprv9s21ZrQH143K"
TESTNET_PREFIX = "tprv8ZgxMBicQKsP"
#: Both, for asking whether a key in hand starts with one of them.
MASTER_PREFIXES = (MAINNET_PREFIX, TESTNET_PREFIX)


def master_prefix(rpc: "Rpc") -> str:
    """The prefix every master key on THIS node's network starts with."""
    return MAINNET_PREFIX if rpc.chain == "main" else TESTNET_PREFIX


def build_descriptors(rpc: "Rpc", xprv: str) -> list[dict]:
    """All four policies, receive and change, checksummed by Core.

    BIP44 legacy, BIP49 nested segwit, BIP84 native segwit and BIP86
    taproot, which is what `createwallet` makes, so a key that arrives by
    scan or by typing presents the same wallet as one Core generated.
    """
    coin = 0 if rpc.chain == "main" else 1
    descs = []
    for purpose, shape in PURPOSE_FUNCS:
        for change in (0, 1):
            raw = shape.format(
                key=f"{xprv}/{purpose}h/{coin}h/0h/{change}/*")
            # getdescriptorinfo's "checksum" field covers the descriptor as
            # given (private form); its "descriptor" field is the public form.
            checksum = rpc.call("getdescriptorinfo", raw,
                                stdin=True)["checksum"]
            descs.append(_desc_entry(f"{raw}#{checksum}", internal=bool(change)))
    return descs


def _desc_entry(desc, internal):
    return {"desc": desc, "active": True, "internal": internal,
            "timestamp": "now", "range": [0, 200]}


def loaded_keys(rpc: "Rpc") -> list[Key]:
    """Every key in the session, in slot order, with its fingerprint."""
    loaded = set(rpc.call("listwallets"))
    return [Key(name, master_fingerprint(rpc, wallet=name))
            for name in SLOTS if name in loaded]


def _next_slot(rpc: "Rpc") -> str:
    """The first free wallet slot, or a refusal at the cap.

    Free means free on disk as well as unloaded. A slot whose directory
    survives without the wallet being loaded is invisible to `listwallets`,
    so picking it made `createwallet` fail with Core's raw "Database
    already exists", and no key could be loaded until the board rebooted
    (found 2026-09-05 by auditing every createwallet call site).
    """
    taken = set(_coresigner_wallets(rpc))
    for name in SLOTS:
        if name not in taken:
            return name
    raise RuntimeError(f"{MAX_KEYS} keys already loaded; discard one first")


def _import(rpc: "Rpc", descriptors: list[dict]) -> str:
    name = _next_slot(rpc)
    rpc.call("createwallet", name, False, True, "", False, True)
    result = rpc.call("importdescriptors", descriptors, wallet=name,
                      stdin=True)
    failures = [r for r in result if not r.get("success")]
    if failures:
        _drop_wallet(rpc, name)
        # redact, though measured not to be needed: six malformed private
        # descriptors were pushed through Core 31.1 on 2026-09-08 and none
        # came back with the key in the failure body. That is Core's
        # behaviour and not this module's guarantee, and `failures` is the
        # one Core answer here that is not stderr, so it is not covered by
        # Rpc.call's redaction.
        raise RuntimeError(f"importdescriptors failed: {redact(str(failures))}")
    # The fingerprint is only knowable once Core holds the key:
    # getdescriptorinfo's public form keeps hardened steps on the xpub and
    # carries no origin. So a duplicate is found after the import and the
    # new wallet is dropped again, leaving the session as it was.
    xfp = master_fingerprint(rpc, wallet=name)
    if any(k.xfp == xfp for k in loaded_keys(rpc) if k.name != name):
        _drop_wallet(rpc, name)
        raise RuntimeError(f"key {xfp} is already loaded")
    return name


def open_session_xprv(rpc: "Rpc", xprv: str) -> str:
    """Input mode 2: a raw BIP32 xprv (typed or from a static QR).
    Pure Core from the first byte; Core Signer applies the BIP84/86 paths.
    Returns the wallet name of the new key."""
    return _import(rpc, build_descriptors(rpc, xprv.strip()))


def open_session_descriptors(rpc: "Rpc", descriptors: list[str]) -> str:
    """Input mode 1: Core-native private descriptors (from a static QR).
    Fully self-describing; no assumed derivation paths.
    Accepts one or more descriptor strings; each becomes an active
    receive/change pair according to its own content."""
    imports = []
    for desc in descriptors:
        desc = desc.strip()
        if "multi" in desc:
            # v1 scope is frozen to single-sig (README); multisig descriptors
            # are refused here rather than silently imported.
            raise RuntimeError("multisig descriptors are out of v1 scope")
        # Re-checksum via Core (accepts descriptors with or without one).
        info = rpc.call("getdescriptorinfo", desc, stdin=True)
        bare = desc.split("#")[0]
        imports.append(_desc_entry(f"{bare}#{info['checksum']}",
                                   internal=_is_change(bare)))
    return _import(rpc, imports)


#: The change branch of a ranged descriptor: the last numeric path step
#: before the `/*`.
#:
#: This was `bare.endswith("/1/*)")`, which counts closing parens. Three
#: of the four policies end in exactly one, so it worked for wpkh, tr and
#: pkh and failed for the fourth: `sh(wpkh(.../1/*))` ends in TWO, so a
#: scanned nested-segwit pair arrived as two RECEIVE descriptors.
#:
#: Measured on Core 31.1, 2026-09-08, importing such a pair: Core does not
#: refuse it. It deactivates the first and keeps the second, so the wallet
#: ends up with the CHANGE chain active and marked external, and the
#: receive chain inactive. Nothing is lost, because `ismine` still answers
#: True on both. But `public_descriptors` filters on `active`, so the
#: public key handed to a coordinator would have described the change
#: chain as the receive chain, and Core Signer's address screen would have shown
#: the change chain.
#:
#: Core Signer's own keys were never affected: `build_descriptors` sets internal
#: explicitly. This is the scanned-descriptor path only.
#:
#: Multipath (`/<0;1>/*`) still returns False, as it did before. Core
#: expands one such descriptor into both chains itself, so the flag is not
#: the thing that decides it.
_CHANGE_BRANCH = re.compile(r"/(\d+)/\*")



def _is_change(bare: str) -> bool:
    m = _CHANGE_BRANCH.search(bare)
    return m is not None and m.group(1) == "1"


def public_descriptors(rpc: "Rpc", wallet: str = WALLET) -> list[str]:
    """What the coordinator needs: the watch-only (xpub) descriptors."""
    listed = rpc.call("listdescriptors", wallet=wallet)["descriptors"]
    return [d["desc"] for d in listed if d["active"]]


# What a coordinator may be given. Core creates all four of these per
# wallet and always has, verified against v31.1 on regtest 2026-09-05:
# pkh at 44h (legacy), sh(wpkh) at 49h (nested segwit), wpkh at 84h
# (native segwit) and tr at 86h (taproot). One master private key opens
# all four, so a paper backup covers them whatever the export shows.
#
# Core Signer offered two of the four until T0 of the export map, which meant
# coins sent to a legacy or nested address of this wallet were spendable
# by this key and invisible on the panel. Which of the four the SHIPPED
# flow offers is map ticket D1, decided once T1 to T3 say what each
# coordinator actually accepts. This table is what the device CAN emit.
#
# `sh` must be matched before `pkh` is read: the prefixes are distinct
# under startswith, but the order below is the order LEFT and RIGHT cycle
# on the panel, and native segwit stays first because it is the default.
EXPORT_KINDS = {"wpkh": "wpkh(", "tr": "tr(",
                "sh": "sh(wpkh(", "pkh": "pkh("}

#: The order LEFT and RIGHT walk on the export and address screens.
EXPORT_ORDER = ("wpkh", "tr", "sh", "pkh")


def export_descriptors(rpc: "Rpc", wallet: str = WALLET) -> list[str]:
    """Every public descriptor a coordinator may be given for this key:
    all four script policies, receive and change, as Core wrote them."""
    return [d for d in public_descriptors(rpc, wallet=wallet)
            if any(d.startswith(p) for p in EXPORT_KINDS.values())]


def origin_of(descriptor: str) -> "tuple[str, str]":
    """The master fingerprint and derivation path Core wrote into a
    descriptor, as `("73c5da0a", "m/84h/0h/0h")`.

    Read out of Core's own string rather than rebuilt from the script
    type, so the panel shows the path this key actually uses instead of
    the one BIP44 says it should. Returns empty strings if Core wrote no
    origin, which a bare descriptor can lack.
    """
    m = re.search(r"\[([0-9a-fA-F]{8})((?:/\d+[h']?)+)\]", descriptor)
    return (m.group(1).lower(), "m" + m.group(2)) if m else ("", "")


def account_key(descriptor: str) -> str:
    """The KEY-ORIGIN form of a descriptor: `[fingerprint/path]xpub`.

    Core hands us a whole descriptor, and a descriptor is not what
    coordinators read. Measured on 2026-09-29 against two of them:

    * BlueWallet 7.2.0 imports our `wpkh` descriptor and NOTHING else.
      `sh(wpkh(…))` throws inside a `setSecret` that nothing catches, and
      the import hangs (ISSUES.md E-8).
    * Bitcoin Keeper carries THIRTEEN vendor adapters and not one of them
      parses a descriptor. Its SeedSigner, Specter and Krux adapters all
      read this form, character for character the same way.

    SeedSigner ships exactly this string as its "Static" xpub QR, built
    in `models/encode_qr.py`. It is BIP380 key-origin syntax, so it is a
    published form and not one we invented.

    What is LEFT OUT is the point. No script wrapper, no `/0/*` and no
    `#checksum`: those three are what every coordinator either strips
    differently or fails to strip at all.

    Returns "" when Core wrote no origin, the way `origin_of` does. A
    descriptor with no origin has no account key to name.

    This reads a string CORE PRODUCED, not one that arrived from
    outside, so A-11 is untouched: Bitcoin Core is still the only thing
    here that parses untrusted bytes.
    """
    # The base58 run after the bracket. Excludes 0, O, I and l, so it
    # stops at the `/` that starts `/0/*` without having to know that is
    # what follows.
    m = re.search(r"\[([0-9a-fA-F]{8}(?:/\d+[h']?)+)\]"
                  r"([1-9A-HJ-NP-Za-km-z]+)", descriptor)
    return f"[{m.group(1).lower()}]{m.group(2)}" if m else ""


def available_kinds(rpc: "Rpc", wallet: str = WALLET) -> tuple[str, ...]:
    """The script policies THIS key actually has, in EXPORT_ORDER.

    Every key Core Signer opens has all four now: `createwallet` makes them and
    `build_descriptors` builds them, so a key restored from its own paper
    backup presents what the key that made it presented (map ticket D6).

    The function stays because a wallet is not obliged to hold four. One
    imported as a bare descriptor holds exactly what that descriptor
    described, and exporting a policy a wallet has not got should be
    something the panel cannot offer rather than an error the user reaches.

    SO IT CAN RETURN AN EMPTY TUPLE, and `order[0]` on one is an
    IndexError, which is not in Session.HANDLED and therefore ends the
    process rather than painting an error. That bug existed at three call
    sites at once and was found at one (2026-09-07). Every caller guards
    it now, with its own message because the three screens are answering
    different questions:

      main._next_kind      returns the current policy unchanged
      main._export         "this key has no policies to export"
      main._page_addresses "this key derives no addresses"
      signer.opens_wallet  "this key derives no addresses to check
                            against"

    A fourth caller needs a fourth answer. There is no shared one to
    inherit, which is why this note is here and not in a wrapper.

    The fourth was found by writing that sentence. `opens_wallet` was
    already doing `available_kinds(...)[0]` and had been since it was
    written; the note said a fourth caller would need an answer, and then
    the next line of the file was the fourth caller with no answer
    (2026-09-08).
    """
    have = export_descriptors(rpc, wallet=wallet)
    return tuple(k for k in EXPORT_ORDER
                 if any(d.startswith(EXPORT_KINDS[k]) for d in have))


def export_descriptor(rpc: "Rpc", wallet: str, kind: str, branch: int = 0) -> str:
    """One public descriptor, Core's own string with its checksum.

    Sparrow's own library parses this verbatim and derives the same
    addresses Core does, for wpkh and tr (proved 2026-09-04). BlueWallet,
    Green and Bull Bitcoin are desk research only (tickets 19, 20, 21);
    what any of them does with legacy or nested segwit is unproven, which
    is what map tickets T1 to T3 are for.
    """
    prefix = EXPORT_KINDS[kind]
    want = f"/{branch}/*"
    for desc in export_descriptors(rpc, wallet=wallet):
        if desc.startswith(prefix) and want in desc:
            return desc
    raise RuntimeError(f"this key has no {kind} descriptor")


def receive_addresses(rpc: "Rpc", wallet: str, kind: str, count: int, start: int = 0) -> list[str]:
    """The first `count` receive addresses, derived by Core.

    `deriveaddresses` is side-effect free; `getnewaddress` advances the
    wallet's address index every time it is called, which is wrong for a
    screen that may be redrawn (verified against 31.1: keypool unmoved).
    """
    desc = export_descriptor(rpc, wallet, kind, branch=0)
    return rpc.call("deriveaddresses", desc, [start, start + count - 1])


def cosigner_key(rpc: "Rpc", wallet: str, path: str) -> str:
    """This key as ONE COSIGNER of somebody else's quorum.

    Returns the key expression a coordinator needs, and nothing else:

        [8d427bd4/48h/1h/0h/2h]tpubDErVqwfZ8V8DiTQUWnbLScTFk2Sfar7uB…

    `path` is hardened steps only, no `m/` and no `/0/*`, as
    `48h/1h/0h/2h`. It is not checked against a list: map M1 settled that
    Core Signer derives where it is told, which is what makes a blinded path
    reachable at all.

    **Why the round trip.** `getdescriptorinfo`'s public form keeps
    hardened steps unexpanded and hands back the MASTER xpub with the
    path trailing it, which no coordinator can use watch-only: Core
    itself refuses to expand it. Importing into a scratch wallet and
    reading `listdescriptors` gives the xpub AT that depth with its
    origin, which is the thing Sparrow and Nunchuk parse. Same round trip
    `write_watch_only` uses, and for the same reason.

    **No `/0/*` suffix, and the fingerprint lowercased.** That is
    Coldcard's `key_expr.txt` exactly (`shared/export.py`,
    `make_key_expression_export`), and matching a shape three
    coordinators already parse beats agreeing a new one with three
    projects (map M7).
    """
    scratch = f"{wallet}-cosign"
    _drop_wallet(rpc, scratch)
    rpc.call("createwallet", scratch, False, True, "", False, True)
    try:
        raw = f"wpkh({master_xprv(rpc, wallet)}/{path}/0/*)"
        checksum = rpc.call("getdescriptorinfo", raw, stdin=True)["checksum"]
        result = rpc.call("importdescriptors",
                          [{"desc": f"{raw}#{checksum}", "active": True,
                            "internal": False, "timestamp": "now",
                            "range": [0, 1]}], wallet=scratch, stdin=True)
        failures = [r for r in result if not r.get("success")]
        if failures:
            raise RuntimeError(
                f"cosigner import failed: {redact(str(failures))}")
        pub = rpc.call("listdescriptors", wallet=scratch)["descriptors"][0]
        inner = pub["desc"][len("wpkh("):pub["desc"].index(")#")]
    finally:
        _drop_wallet(rpc, scratch)
    if not inner.endswith("/0/*"):
        raise RuntimeError(f"Core wrote a shape this cannot trim: {inner[:40]}")
    return inner[:-len("/0/*")]


#: What the watch-only wallet file is called. It carries no private key,
#: which is why it is the one file the private-key-on-paper rule (A-24)
#: still allows off this device.
WATCH_PREFIX = "coresigner-"


#: BIP48's script-type step. 2h is P2WSH, which is what a modern quorum
#: uses; 1h is P2SH-P2WSH, kept for a coordinator that still wants it.
COSIGNER_SCRIPTS = {"wsh": 2, "sh-wsh": 1}


def cosigner_path(rpc: "Rpc", script: str = "wsh", account: int = 0) -> str:
    """The BIP48 path for this chain, as `48h/1h/0h/2h`.

    Hardened steps only, no `m/` and no `/0/*`, which is the shape
    `cosigner_key` takes. The coin step follows the chain the way
    `build_descriptors` does, so a regtest key never offers a mainnet
    path and the person cannot pick the wrong one by reading it.

    M1 settled that Core Signer derives where it is TOLD, so this is a
    convenience for the common case and never a limit: the typed row
    reaches any path at all, which is the only route to a blinded xpub.
    """
    coin = 0 if rpc.chain == "main" else 1
    return f"48h/{coin}h/{account}h/{COSIGNER_SCRIPTS[script]}h"


def write_cosigner(rpc: "Rpc", wallet: str, path: str,
                   dest_dir: "str | Path") -> Path:
    """The cosigner record as the file Sparrow's importer reads.

    The BARE key expression on one line, which is M7's answer and M8's
    measurement: the file importer wants exactly this and refuses a
    wrapper, where the scan wants a whole descriptor and refuses this.
    `cosigner_qr` is the other half.

    Named by the fingerprint, as `write_watch_only` is. Two exports of
    DIFFERENT paths for one key overwrite each other, which is a real
    limit and a small one: a person exporting a second path is doing it
    because the first was wrong.
    """
    record = cosigner_key(rpc, wallet, path)
    xfp = master_fingerprint(rpc, wallet=wallet) or "unknown"
    out = Path(dest_dir) / f"coresigner-{xfp}-cosigner.txt"
    # NO TRAILING NEWLINE, and this is measured rather than tidy.
    # Sparrow's Specter DIY importer refuses the file with one: `\n`,
    # `\r\n` and two newlines all fail, where a LEADING space is
    # tolerated. M7 read Nunchuk allowing at most one trailing newline
    # and this was written to match; Sparrow is the stricter of the two
    # and the file has to satisfy both. Found by pointing the M8 suite
    # at what the device writes rather than at a string the test built.
    out.write_text(record)
    return out


def cosigner_qr(rpc: "Rpc", wallet: str, path: str) -> str:
    """The same cosigner key, as the QR a coordinator can scan.

    Returns a whole descriptor with Core's checksum on it:

        wsh(sortedmulti(1,[8d427bd4/48h/1h/0h/2h]tpubDErV…/0/*))#7asmw9jj

    **The QR and the file carry different text, and that is not a slip.**
    Map M8 measured both ends. Sparrow's file importer wants the BARE key
    expression `cosigner_key` returns and refuses a wrapper. Its scan
    path wants a whole descriptor and refuses the bare expression, because
    a key expression on its own is not a descriptor. One question is put
    to the person; the two payloads behind it are not their problem.

    **Why `sortedmulti(1, …)` and not `wsh(<key>)`.** Sparrow parses the
    second one, and Core refuses it: "A function is needed within P2WSH".
    Emitting a string this device's own brain calls invalid is how a
    format rots, so the wrapper is one both agree on. The `1` is a
    placeholder because Core Signer holds ONE key and the quorum belongs to
    the coordinator, which is the scope Ben set for this map.

    It also keeps the SCRIPT TYPE honest. Sparrow hands the scan result
    to `getScannedKeystore(ScriptType)` for the wallet being built, so a
    `wsh(…)` payload matches a P2WSH quorum where a `wpkh(…)` one, which
    Core and Sparrow would both otherwise accept, claims single-sig
    native segwit about a key that is neither.
    """
    raw = f"wsh(sortedmulti(1,{cosigner_key(rpc, wallet, path)}/0/*))"
    return f"{raw}#{rpc.call('getdescriptorinfo', raw, stdin=True)['checksum']}"


def write_watch_only(rpc: "Rpc", wallet: str, dest_dir: "str | Path") -> Path:
    """A watch-only wallet file for a laptop running Bitcoin Core.

    Core has no QR reader, so its half of the air gap is a file. Core's own
    `backupwallet` writes it, from a wallet made with `disable_private_keys`
    that holds nothing but the public descriptors, so no code of ours shapes
    the format and no secret can be in it. The scratch wallet is deleted
    again, so the session is left exactly as it was found.

    The file is named by the key's fingerprint. Returns its path.
    """
    xfp = master_fingerprint(rpc, wallet=wallet) or "unknown"
    # _is_change, not a second reading of the same question. This said
    # `"/1/*" in d`, which happens to agree on every shape Core writes,
    # and that is the whole trouble with a second reading: it agrees
    # until it does not, and nothing says which one is the definition
    # (two-axis review, 2026-09-08).
    descs = [_desc_entry(d, internal=_is_change(d))
             for d in export_descriptors(rpc, wallet=wallet)]
    scratch = f"{wallet}-watch"
    _drop_wallet(rpc, scratch)
    # disable_private_keys=True, blank=True: a wallet that CANNOT hold a key.
    rpc.call("createwallet", scratch, True, True, "", False, True)
    try:
        result = rpc.call("importdescriptors", descs, wallet=scratch)
        failures = [r for r in result if not r.get("success")]
        if failures:
            raise RuntimeError(
                f"watch-only import failed: {redact(str(failures))}")
        out = Path(dest_dir) / f"{WATCH_PREFIX}{xfp}-watch.dat"
        rpc.call("backupwallet", str(out), wallet=scratch)
        return out
    finally:
        _drop_wallet(rpc, scratch)


#: What decodepsbt says that the review screen never reads.
#:
#: `non_witness_utxo` is the whole previous transaction for every input,
#: and at 250 batch-funded inputs that is 25,000 output objects nobody
#: looks at: 10.8MB of JSON becoming a 21.1MB tree, where 20.7MB is this.
#: The rest are signing and derivation material Core needs and the screen
#: does not.
_REVIEW_DROPS = frozenset((
    "non_witness_utxo", "witness_utxo",
    "taproot_bip32_derivs", "redeem_script",
    "final_scriptSig", "final_scriptwitness", "scriptSig", "txinwitness",
    "partial_signatures", "hex", "desc",
))

#: Kept out of `_REVIEW_DROPS` since M9, and each one is read:
#: `witness_script` and its `asm` carry the threshold, `bip32_derivs`
#: carries every cosigner's fingerprint and path.
#:
#: `non_witness_utxo` STAYS dropped, and it was the expensive one: 20.7MB
#: of the 21.1MB tree at 250 batch inputs. What comes back is a witness
#: script of about 210 hex characters and three derivation entries of
#: about 150 per input. M3 recorded the consequence for M5 as "150 more
#: scripts in the tree" and that was an estimate; M9 measures it, and
#: M5 confirms the number on the board.


def _quorum_of(script: dict) -> "tuple[int, int] | None":
    """The threshold Core already decoded, read and not parsed.

    PLAN A-11: Core is the only thing that parses a descriptor or a
    script, and this reads Core's answer rather than the script. Core
    reports
    `type: "multisig"` and renders the script as `asm`, which for a
    bare multisig begins with the threshold and ends with the total
    before `OP_CHECKMULTISIG`. Reading two of Core's own tokens is not
    parsing a script, and it is what M3 sanctioned when it asked for a
    threshold on the review screen.

    Anything that is not exactly that shape returns None, so a script
    this build has never seen shows no quorum rather than a wrong one.
    """
    if script.get("type") != "multisig":
        return None
    parts = str(script.get("asm", "")).split()
    if len(parts) < 3 or parts[-1] != "OP_CHECKMULTISIG":
        return None
    try:
        return int(parts[0]), int(parts[-2])
    except ValueError:
        return None

#: The same idea for `owners`, which reads the fingerprints and nothing
#: else. It must NOT drop the two bip32 lists that carry them.
_OWNER_DROPS = frozenset((
    "non_witness_utxo", "witness_utxo", "redeem_script", "witness_script",
    "final_scriptSig", "final_scriptwitness", "scriptSig", "txinwitness",
    "partial_signatures", "asm", "hex", "desc", "tx",
))


def describe_psbt(rpc: "Rpc", psbt_b64: str) -> dict:
    """Everything the review screen shows. All numbers are Core's.

    The fee is computed by Core from coordinator-supplied input amounts;
    an air-gapped signer cannot verify those amounts against the chain.
    The screen must say so.

    **The total going in is Core's fee plus the outputs, not a sum Core Signer
    makes.** Core Signer used to add up every input's own amount, which meant
    reading the whole previous transaction for every legacy input. Those
    previous transactions are 20.7MB of the 21.1MB this call used to
    build at the 250-input batch case, and they exist in the answer only
    to be added up, which Core has already done. `fee` IS that sum minus
    the outputs.

    It costs nothing in trust. Core computed the fee from exactly the
    amounts Core Signer was summing, so the two were never independent; this
    only stops pretending they were. What checks it is
    `tests/test_export.py` 1c, which compares the total against the
    node's own UTXO set with `gettxout`, and that is a genuinely separate
    source.

    A fee of None means Core could not value every input, which is what
    `input_total_btc` of None used to mean and what makes the device
    refuse the transaction. One condition now instead of two that could
    disagree.
    """
    decoded = rpc.call("decodepsbt", psbt_b64, stdin=True,
                       drop=_REVIEW_DROPS)
    analysis = rpc.call("analyzepsbt", psbt_b64, stdin=True)
    outputs = [
        {"address": vout["scriptPubKey"].get("address", "(non-standard)"),
         "amount_btc": vout["value"]}
        for vout in decoded["tx"]["vout"]
    ]
    locks = _timelocks(decoded["inputs"])
    fee = decoded.get("fee")
    input_total = None
    if fee is not None:
        input_total = Decimal(str(fee)) + sum(
            Decimal(str(o["amount_btc"])) for o in outputs)
    return {
        "outputs": outputs,
        "fee_btc": fee,                         # None if inputs incomplete
        "input_total_btc": input_total,
        "input_count": len(decoded["inputs"]),
        "next_role": analysis.get("next"),
        "fee_note": "fee computed from coordinator-supplied input amounts",
        "quorum": _quorum(decoded["inputs"]),
        "cosigners": _cosigners(decoded["inputs"]),
        "timelocks": locks,
        "spend_lock": _spend_lock(decoded["tx"].get("vin", []), locks),
    }


#: The change branch and address index at the end of a derivation.
#: `_account_path` strips it, and `_branches` reads the two numbers out
#: of it. One pattern, because two readings of the same question
#: disagree eventually and nothing then says which is the definition.
_CHANGE_LEAF = re.compile(r"/(\d+)/(\d+)$")


def _account_path(path: str) -> str:
    """The wallet, with the address stripped off.

    Core reports `m/48h/1h/0h/2h/0/0`. The last two steps are the change
    branch and the address index: they differ for every address in one
    wallet, and they say nothing about WHICH wallet, which is the only
    thing M1 decision 2 wants this path to say. Two non-hardened steps
    at the end are removed and nothing else is, so a path of an unusual
    shape is shown whole rather than trimmed by a rule it does not obey.
    """
    return _CHANGE_LEAF.sub("", path)


def _cosigners(inputs: list) -> "list[tuple[str, str]]":
    """Every fingerprint on the transaction, with the wallet it derives at.

    One entry per (fingerprint, account path), in the order Core reports
    them, so a quorum reads the same way twice. `owners()` stays as it
    is: it answers "whose transaction is this" for key selection, which
    is a different question asked before this screen exists.
    """
    found = []
    for txin in inputs:
        for deriv in txin.get("bip32_derivs", []):
            xfp = deriv.get("master_fingerprint")
            path = deriv.get("path")
            if not xfp or not path:
                continue
            entry = (xfp.lower(), _account_path(path))
            if entry not in found:
                found.append(entry)
    return found


#: BIP68 packs a relative timelock into nSequence. Bit 31 set disables
#: it; bit 22 set counts 512-second units instead of blocks. Core Signer reads
#: BLOCK units only and says nothing about the rest, because comparing a
#: block count to a duration is a consensus rule and not a reading.
_SEQUENCE_UNITS = 1 << 22

_CSV = re.compile(r"(\d+) OP_CHECKSEQUENCEVERIFY")


def _timelocks(inputs: list) -> "tuple[int, ...]":
    """Every relative timelock in the policy, as Core printed it.

    Core types a miniscript witness script as `nonstandard`, so
    `_quorum_of` finds no threshold and the review screen would say
    nothing at all about a Liana policy or a decaying quorum. The same
    `asm` that carries the threshold for a bare multisig carries the
    timelocks here, in front of `OP_CHECKSEQUENCEVERIFY`. Measured
    2026-09-10 against a three-tier decay: `('10', '20')`.
    """
    found: "set[int]" = set()
    for txin in inputs:
        asm = str(txin.get("witness_script", {}).get("asm", ""))
        found.update(int(n) for n in _CSV.findall(asm))
    return tuple(sorted(found))


def _spend_lock(vin: list, locks: "tuple[int, ...]") -> "int | None":
    """Which tier THIS spend enables, or None for the ordinary path.

    M6 measured that the tier belongs to the coordinator: it sets
    `nSequence` when it builds, and the same policy finalises on one key
    or refuses to, depending only on that. Core Signer cannot change it. A
    person can refuse it, which is the whole reason the screen has to
    show it: a spend at a tier nobody asked for looks exactly like a
    normal one today.

    None whenever the answer is not plainly readable: no locks, inputs
    that disagree, the disable bit set, or 512-second units. A screen
    that says nothing is right more often than a screen that guesses.
    """
    if not locks:
        return None
    seqs = {v.get("sequence") for v in vin}
    if len(seqs) != 1:
        return None
    seq = seqs.pop()
    if not isinstance(seq, int) or seq >= _SEQUENCE_UNITS:
        return None
    # BIP68 puts the VALUE in the low 16 bits and ignores bits 16 to 21.
    # Comparing the whole word read a sequence of 0x00010005 as 65541
    # where consensus reads it as 5, so the screen could name a tier the
    # spend does not reach (two-axis review, 2026-09-10).
    seq &= 0xffff
    enabled = [n for n in locks if n < _SEQUENCE_UNITS and n <= seq]
    return max(enabled) if enabled else None


def _quorum(inputs: list) -> "tuple[int, int] | str | None":
    """One quorum for the whole transaction, or an honest refusal to say.

    `None` when nothing being spent is multisig, `(threshold, total)` when
    every input agrees, and the string `"mixed"` when they do not.

    The third case earns its place. A screen that reads the first input
    and prints "2 of 3" over a transaction whose other inputs are
    single-sig, or a different quorum, states something untrue on the one
    screen this device exists to make truthful. That is audit A6, and the
    cheapest guard against it is to notice rather than to assume.
    """
    seen = {_quorum_of(txin.get("witness_script", {})) for txin in inputs}
    if seen == {None} or not seen:
        return None
    return seen.pop() if len(seen) == 1 else "mixed"


def owners(rpc: "Rpc", psbt_b64: str) -> set[str]:
    """The master fingerprints Core finds on the transaction's inputs.

    decodepsbt lists bip32_derivs on every input a coordinator described,
    and taproot_bip32_derivs on taproot inputs. Each carries the master
    fingerprint of the key that owns it. That is how a transaction names
    its key (ticket 03); Core Signer matches, Core decides.
    """
    decoded = rpc.call("decodepsbt", psbt_b64, stdin=True,
                       drop=_OWNER_DROPS)
    found = set()
    for txin in decoded["inputs"]:
        for field in ("bip32_derivs", "taproot_bip32_derivs"):
            for deriv in txin.get(field, []):
                xfp = deriv.get("master_fingerprint")
                if xfp:
                    found.add(xfp.lower())
    return found


def _missing_signatures(rpc: "Rpc", psbt_b64: str) -> int:
    """How many signatures Core still wants, across every input.

    `analyzepsbt` reports `missing.signatures` per input as the list of
    key hashes it has not seen. Its `next` field cannot tell a finished
    PSBT from an unfinished one, which the map recorded; this field is a
    different question and it does answer.
    """
    analysis = rpc.call("analyzepsbt", psbt_b64, stdin=True)
    return sum(len(i.get("missing", {}).get("signatures", []))
               for i in analysis.get("inputs", []))


_SIGN_SCRATCH = "coresigner-sign-branch"


def _branches(inputs: list, xfp: str) -> "dict[tuple[str, str], int]":
    """Where this key is used, as (account, change) -> highest index.

    Read out of the PSBT's own derivations, so the device is TOLD where
    to sign rather than assuming a policy (map M1 decision 2). A path
    whose last two steps are not a change branch and an index is carried
    whole, with change and index empty, and imported exactly.
    """
    found: "dict[tuple[str, str], int]" = {}
    for txin in inputs:
        for deriv in txin.get("bip32_derivs", []):
            if (deriv.get("master_fingerprint") or "").lower() != xfp:
                continue
            path = deriv.get("path") or ""
            m = _CHANGE_LEAF.search(path)
            if m:
                key = (path[:m.start()], m.group(1))
                found[key] = max(found.get(key, 0), int(m.group(2)))
            else:
                found[(path, "")] = 0
    return found


def sign_at_told_paths(rpc: "Rpc", wallet: str, psbt_b64: str,
                       xfp: str) -> dict:
    """Sign the shares this PSBT asks this key for, at ITS paths.

    A loaded key holds the four standard policies. A quorum, a tier of a
    decaying policy or a blinded path is none of them, so Core signs nothing and
    the device that M4 proved a coordinator would accept could not
    actually produce the signature. M1 decision 2 settled the answer:
    the PSBT names the path, and the device derives where it is told.

    **The master key is read, and that is the cost.** Building a
    descriptor is the only way Core imports a derivation, and a
    descriptor needs the key, so this pulls the master xprv into Core Signer
    for the length of one signature. `generate_wallet` refuses to hand it
    back for exactly this reason, so the exposure is kept as narrow as it
    can be: this runs ONLY when a plain sign added nothing, the branch
    goes into a scratch wallet and not the session's, and the scratch
    wallet is dropped in a `finally` whether or not the signing worked.
    A-24 is untouched, because nothing here writes a key anywhere: the
    datadir is tmpfs and the wallet is gone before this returns.
    """
    decoded = rpc.call("decodepsbt", psbt_b64, stdin=True,
                       drop=_OWNER_DROPS)
    branches = _branches(decoded["inputs"], xfp.lower())
    if not branches:
        return {"psbt": psbt_b64, "complete": False, "added": False}
    _drop_wallet(rpc, _SIGN_SCRATCH)
    rpc.call("createwallet", _SIGN_SCRATCH, False, True, "", False, True)
    try:
        xprv = master_xprv(rpc, wallet)
        imports = []
        for (account, change), top in branches.items():
            stem = account.removeprefix("m/")
            raw = (f"wpkh({xprv}/{stem}/{change}/*)" if change
                   else f"wpkh({xprv}/{stem})")
            # Checksummed here rather than in a second pass over the
            # list. The second pass had to read `desc` back out of a dict
            # holding a range as well, which is a str and a list in one
            # value, and mypy was right to refuse it.
            checksum = rpc.call("getdescriptorinfo", raw,
                                stdin=True)["checksum"]
            # A ranged descriptor needs its range and a flat one must NOT
            # carry it, so the shape differs per branch.
            entry: "dict[str, object]" = {"desc": f"{raw}#{checksum}",
                                          "timestamp": "now"}
            if change:
                entry["range"] = [0, top]
            imports.append(entry)
        result = rpc.call("importdescriptors", imports,
                          wallet=_SIGN_SCRATCH, stdin=True)
        failed = [r for r in result if not r.get("success")]
        if failed:
            raise RuntimeError(
                f"could not derive where the PSBT asks: {redact(str(failed))}")
        return sign_psbt(rpc, psbt_b64, wallet=_SIGN_SCRATCH)
    finally:
        _drop_wallet(rpc, _SIGN_SCRATCH)


def sign_psbt(rpc: "Rpc", psbt_b64: str, wallet: str = WALLET,
              xfp: "str | None" = None) -> dict:
    """Sign, and say whether this device actually signed anything.

    `added` exists because `complete` cannot carry the question. M3
    decision 1 delivers a partial signature rather than refusing it, and
    `complete: False` is true both for one signature of two AND for none
    at all. Without `added`, the SIGNED screen would appear over a PSBT
    this device never touched, which is a lie on the one screen the
    project exists to keep truthful (audit A6).

    That case is ordinary, not contrived. A loaded key holds the four
    standard policies, so a BIP48 quorum PSBT arriving at it produces no
    signature whatsoever. It is also the exact bug M4's test nearly
    shipped, where `complete is False` passed while nothing was signed.

    Measured 2026-09-10: when Core signs nothing, `walletprocesspsbt`
    returns the IDENTICAL base64 string. That is a second, free reading
    of the same fact, and the count below is the one relied on, because
    it stays right if a later Core adds metadata without signing.
    """
    before = _missing_signatures(rpc, psbt_b64)
    result = rpc.call("walletprocesspsbt", psbt_b64, wallet=wallet,
                      stdin=True)
    after = _missing_signatures(rpc, result["psbt"])
    if after >= before and xfp:
        # Nothing signed with the policies this wallet holds. The PSBT
        # may still be asking this key for a share at a path it was
        # never told about until now, which is every quorum, every
        # tier of a decaying policy and every blinded path.
        return sign_at_told_paths(rpc, wallet, psbt_b64, xfp)
    return {"psbt": result["psbt"], "complete": result["complete"],
            "added": after < before}


def generate_wallet(rpc: "Rpc") -> str:
    """A-19: seed generation and usage EXACTLY as a Bitcoin Core wallet.

    `createwallet` makes Core generate its master key with its own RNG
    (GetStrongRandBytes) and derive the standard descriptor set, exactly
    as any Core wallet is born. Core Signer then simply USES that wallet, and
    the backup shown to the user is Core's own master xprv, read verbatim
    out of the descriptors Core wrote. Nothing of ours sits between
    Core's RNG and the backup: no extraction, no hashing, no reshaping.

    Returns the wallet name, and NOTHING ELSE (Ben, 2026-09-05). It used to
    return the master xprv too, so a key Core had just made was pulled back
    out into Core Signer's memory at the moment of birth, whether or not anyone
    ever asked to see it. A key generated here and backed up to an
    encrypted file is now never read out of Core at all. `master_xprv` is
    still there for the paper backup, which asks for it when the user
    chooses to look at it.

    The new key takes the next free slot beside the keys already loaded
    (ticket 03); at the cap the slot lookup refuses with a message the home
    screen can show. Raises if the descriptors do not all share one master
    key (they always do for Core-generated wallets; the check is a sanity
    assertion, not entropy verification).
    """
    name = _next_slot(rpc)
    rpc.call("createwallet", name)
    try:
        # One read, discarded immediately: it is the sanity check that all
        # the descriptors share a master, not a value anyone keeps.
        master_xprv(rpc, name)
    except RuntimeError:
        _drop_wallet(rpc, name)
        raise
    return name


def master_xprv(rpc: "Rpc", wallet: str = WALLET) -> str:
    """The wallet's master xprv, read verbatim from the private descriptors
    Core wrote. This is the paper backup (ticket 07): Core's own string,
    nothing of ours between Core and the page.

    THE ONE-MASTER CHECK IS WHAT MAKES THE PARSE SAFE. "After the last
    `(`" finds the innermost key, which is right for `wpkh(k/...)` and for
    `sh(wpkh(k/...))`, and would find the wrong key in a descriptor whose
    innermost `(` belonged to a script leaf rather than to the key, such
    as `tr(k,pk(other))`. Core 31.1 refuses every such shape with a
    private key, tested 2026-09-08, so Core Signer cannot hold one. If a future
    Core accepted one, the eight descriptors would disagree about the
    master and this raises. It shows the right key or no key; there is no
    shape that makes it show a wrong one silently.
    """
    descs = rpc.call("listdescriptors", True, wallet=wallet)["descriptors"]
    masters = set()
    for d in descs:
        text = d["desc"]
        # innermost key expression: text after the LAST '(' up to '/' or ')'
        key = text[text.rindex("(") + 1:]
        for stop in "/)":
            if stop in key:
                key = key[: key.index(stop)]
        masters.add(key)
    if len(masters) != 1:
        raise RuntimeError(
            f"expected one master key across descriptors, got {len(masters)}")
    return masters.pop()


def opens_wallet(rpc: "Rpc", wallet: str, key: str, count: int = 2) -> bool:
    """Does this private key derive the addresses this wallet hands out?

    The question "does my paper open this wallet" asked the only way that
    can answer it: Core derives receive addresses from the TYPED key, Core
    reports the addresses the LOADED wallet gives, and Core Signer compares two
    lists of strings Core returned (PLAN A-11).

    Audit A6 (2026-09-06) found the check it replaces could not fail.
    `_check_typed` only accepts what was typed when it matches character
    for character, so by the time the flow asked Core to confirm, it was
    asking whether a string equalled itself. The screen said "your paper
    opens key X" on the strength of that. Deleting the whole comparison
    left every suite green, which is how it was found.

    The derivation path is read out of the wallet's OWN descriptor rather
    than rebuilt from the script type, so this follows the key wherever
    Core actually put it, including regtest's coin type 1.

    Nothing is created and nothing is written: `getdescriptorinfo` and
    `deriveaddresses` are both side-effect free. The key goes through
    `-stdin`, never argv, which is Rpc.call's standing rule for anything
    carrying an xprv. Core refuses a mistyped key at base58's own
    checksum, long before anything cryptographic happens, and Rpc.call
    redacts the key out of that error before it reaches a screen.
    """
    # The fourth caller of available_kinds, and the one that did not
    # guard the empty tuple. `[0]` on one is an IndexError, which is not
    # in Session.HANDLED, so a bare-descriptor wallet with no recognised
    # policy ended the process on the backup-check screen instead of
    # saying it could not check (found reading signer.py, 2026-09-08,
    # after writing the note above that says a fourth caller needs a
    # fourth answer).
    kinds = available_kinds(rpc, wallet)
    if not kinds:
        raise RuntimeError("this key derives no addresses to check against")
    kind = kinds[0]
    shapes = {shape.split("(")[0]: shape for _purpose, shape in PURPOSE_FUNCS}
    if kind not in shapes:
        raise RuntimeError(f"no descriptor shape for {kind}")
    _xfp, path = origin_of(export_descriptor(rpc, wallet, kind))
    if not path:
        raise RuntimeError("this wallet's descriptor carries no origin")
    want = receive_addresses(rpc, wallet, kind, count)
    raw = shapes[kind].format(key=f"{key}{path[1:]}/0/*")
    checksum = rpc.call("getdescriptorinfo", raw, stdin=True)["checksum"]
    got = rpc.call("deriveaddresses", f"{raw}#{checksum}",
                   [0, count - 1], stdin=True)
    return got == want


def _drop_wallet(rpc: "Rpc", name: str) -> None:
    """Unload and delete a wallet, ignoring the not-loaded case."""
    with contextlib.suppress(RuntimeError):
        rpc.call("unloadwallet", name)
    shutil.rmtree(rpc.wallet_dir / name, ignore_errors=True)


def master_fingerprint(rpc: "Rpc", wallet: str = WALLET) -> "str | None":
    """The wallet's master key fingerprint (XFP), or None if no wallet.

    Read from the PUBLIC descriptors: listdescriptors without the private
    flag, so nothing secret is fetched to draw a header. Core writes the
    origin as [XXXXXXXX/84h/...] at the front of every descriptor, and all
    of a wallet's descriptors share one master key.
    """
    try:
        descs = rpc.call("listdescriptors", wallet=wallet)["descriptors"]
    except (RuntimeError, KeyError, TypeError):
        return None                      # no wallet loaded: no fingerprint
    for d in descs:
        m = re.search(r"\[([0-9a-fA-F]{8})/", d.get("desc", ""))
        if m:
            return m.group(1).lower()
    return None


def _coresigner_wallets(rpc: "Rpc") -> list[str]:
    """Every wallet on this node that belongs to Core Signer, loaded or not.

    The five key slots, and also the scratch wallets the export and the
    file backup build (`coresigner-watch`, `coresigner-2-backup`, ...). A scratch
    holds the PRIVATE descriptors between `createwallet` and the `finally`
    that deletes it, so a crash in that window used to leave a plaintext
    key on the ramdisk that nothing ever dropped: not close_session, not
    the next session's clear_on_start, because both walked SLOTS only.
    Found by the two-axis review, 2026-09-05, and reproduced.

    Ownership is by name: `coresigner`, or anything beginning `coresigner-`. On the
    device Core Signer owns the whole node; in the test harness the
    coordinator's wallets are named otherwise and must survive.
    """
    names = set()
    with contextlib.suppress(RuntimeError):
        names.update(rpc.call("listwallets"))
    with contextlib.suppress(RuntimeError, KeyError, TypeError):
        names.update(w["name"] for w in rpc.call("listwalletdir")["wallets"])
    return sorted(n for n in names
                  if n == WALLET or n.startswith(WALLET + "-"))


def clear_on_start(rpc: "Rpc") -> list[str]:
    """Drop every key Core Signer did not load in THIS session, before the first
    screen is drawn. Returns the slot names it dropped.

    bitcoind runs under its own systemd unit (`coresigner-bitcoind.service`) and
    keeps running when `coresigner.service` restarts. `Restart=on-failure` means
    a crashed session comes straight back, and the ramdisk and the node
    both survive it, so without this the new session would adopt a key its
    user never entered and show it as loaded on the home screen.

    Only Core Signer's own wallets are dropped, slots and scratches alike. On the
    device the datadir is a fresh tmpfs at every boot, so no other wallet
    can be there; in the test harness the coordinator's wallets share the
    datadir and must survive.
    """
    dropped = _coresigner_wallets(rpc)
    for name in dropped:
        _drop_wallet(rpc, name)
    return dropped


def close_key(rpc: "Rpc", name: str) -> None:
    """Discard one key: unload its wallet and delete its directory."""
    _drop_wallet(rpc, name)


def close_session(rpc: "Rpc") -> None:
    """Unload AND delete every wallet Core Signer owns: the key slots and any
    scratch left by an export or a backup. On the device the datadir is a
    ramdisk and power-off is the real teardown; deleting here keeps every
    environment (and every test) as stateless as the hardware."""
    for name in _coresigner_wallets(rpc):
        _drop_wallet(rpc, name)


def stop_node(rpc: "Rpc", timeout: float = 30.0, poll: float = 0.2) -> bool:
    """Ask bitcoind to shut down, and wait until it stops answering.

    POWER OFF must leave nothing running (I-2). bitcoind holds the ramdisk
    datadir open, so a halt during a write can tear wallet.dat on any build
    that is not fully RAM-resident. Returns True if the node has gone, False
    if it outlived the timeout. The caller must show the False case: a
    device that says it is off while its node runs is audit defect D16.
    """
    try:
        rpc.call("stop")
    except RuntimeError:
        return True                    # already down, or never came up
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            rpc.call("uptime")
        except RuntimeError:
            return True                # RPC refused: the node has gone
        time.sleep(poll)
    return False
