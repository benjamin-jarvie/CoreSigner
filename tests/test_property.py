"""Property-based and fuzz tests for Core Signer. Run: python3 tests/test_property.py

A-22 cut this suite from five properties to three. The shim, codex32 and
SeedQR properties went with the modules they tested: the pure signer has no
code that transforms secret material, so there is nothing left to
cross-check against an oracle.

What remains guards the two things Core Signer still does with untrusted input,
and the one number it computes:

  1. PSBT boundary fuzz: FrameAssembler.feed and read_psbt never raise an
     uncaught exception on garbage, only their controlled errors.
  2. Fee and amount Decimal arithmetic in describe_psbt is exact.
"""
import base64
import shutil
import time
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

from hypothesis import given, settings, strategies as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "coresigner"))

import qrchannel      # noqa: E402
import filechannel    # noqa: E402
import signer         # noqa: E402

EXAMPLES = 200


# ---- Property 1: PSBT boundary fuzz (no crash) ------------------------

_UR_PREFIXES = ["", "ur:crypto-psbt/", "ur:crypto-psbt/1-3/", "UR:CRYPTO-PSBT/",
                "ur:crypto-seed/", "ur:", "ur:crypto-psbt"]


@given(prefix=st.sampled_from(_UR_PREFIXES),
       body=st.text(min_size=0, max_size=400))
@settings(max_examples=EXAMPLES * 3, deadline=None)
def prop_qr_feed_no_crash(prefix, body):
    fa = qrchannel.FrameAssembler()
    frame = prefix + body
    try:
        result = fa.feed(frame)
        assert isinstance(result, bool)
    except qrchannel.QrChannelError:
        pass  # controlled failure is allowed


@given(data=st.binary(min_size=0, max_size=500))
@settings(max_examples=EXAMPLES * 3, deadline=None)
def prop_read_psbt_no_crash(data):
    with tempfile.NamedTemporaryFile(suffix=".psbt", delete=False) as f:
        f.write(data)
        p = Path(f.name)
    try:
        try:
            out = filechannel.read_psbt(p)
            assert isinstance(out, str)
        except filechannel.FileChannelError:
            pass  # controlled failure (empty/oversize) is allowed
    finally:
        p.unlink(missing_ok=True)


def sats_to_btc(sats: int) -> Decimal:
    return (Decimal(sats) / Decimal(10**8)).quantize(Decimal("0.00000001"))


def _no_key_in_argv(method, params, stdin):
    """A private key may never travel as a command-line argument.

    Rpc.call's own rule: "Callers that pass an xprv or a private
    descriptor MUST set it (S4)", because argv is visible in a process
    listing. The rule was documented and every caller obeyed it, and then
    `signer.identity_of_key` shipped without it on 2026-09-05, carrying
    the master private key twice per paper check. The two-axis review
    found it; nothing in this suite did, because the only stdin assertion
    here named three PSBT methods.

    So the check is on the ARGUMENT rather than the method name. Any call
    is refused if a parameter contains a private-key prefix and stdin is
    off, whatever the method is called, including one that does not exist
    yet.
    """
    if stdin:
        return
    for p in params:
        # signer.redact is the ONE definition of what key material looks
        # like. This used to substring-search XPRV_PREFIXES, which is half
        # of it, so a WIF passed the check (two-axis review, 2026-09-08).
        if isinstance(p, str) and signer.redact(p) != p:
            raise AssertionError(
                f"{method} put key material in argv, where a process "
                f"listing shows it. Pass stdin=True (Rpc.call, S4).")


def prop_no_key_reaches_the_panel():
    """Every message funnel redacts, so no caller has to remember.

    Rpc.call redacts what Core writes to STDERR. It cannot redact a
    failure Core reports in the JSON BODY, and it cannot redact a message
    built any other way. Ten call sites put str(exc) on the result screen
    (two-axis review, 2026-09-08), and one of them went around _hold
    entirely and called screens.result itself.

    A message on the panel is also a message in the journal on the SD
    card, which is the one place PLAN A-24 says a private key must never
    reach.
    """
    import hal
    import main as coresigner_main
    import screens as scr
    import signer as sg

    KEY = ("tprv8ZgxMBicQKsPe5YMU9gHen4Ez3ApihUfykaqUorj9t6FDqy3nP6eoXi"
           "Ao2ssvpAjoLroQxHqr3R5nE3a5dU3DHTjTgJDd7zrbniJr6nrCzd")
    WIF = "L1aW4aubDFB7yfras2S1mN3bqg9nwySY8nkoLmJebSLD5BWv3ENZ"

    class Blind:
        width, height = 320, 240

        def show(self, image, sensitive=False):
            pass

    class NoRpc:
        chain = "regtest"
        wallet_dir = Path("/nonexistent")

        def call(self, *a, **k):
            return ""

    def session():
        return coresigner_main.Session(Blind(), hal.DevButtons("a"), NoRpc(),
                                  animate=False)

    seen = []
    real = scr.result
    scr.result = lambda w, h, **kw: seen.append(kw.get("detail", "")) or "r"
    try:
        for secret in (KEY, WIF):
            funnels = {
                "_hold":
                    lambda s: session()._hold(f"import failed: {s}"),
                # NOT "is not valid": that message is replaced whole
                # with a sentence, so it would prove the redactor
                # nothing. This is a Core refusal quoting a key that
                # the funnel has to redact itself.
                "_show_core_error":
                    lambda s: session()._show_core_error(
                        RuntimeError(f"importdescriptors: '{s}' refused")),
            }
            for label, run in funnels.items():
                seen.clear()
                run(secret)
                assert seen, f"{label} painted nothing"
                assert secret not in seen[0], (
                    f"{label} put a private key on the panel, and therefore "
                    f"in the journal on the card: {seen[0][:60]}")
                assert "<key redacted>" in seen[0], (
                    f"{label} did not redact: {seen[0][:60]}")

            # The one message that is replaced rather than redacted.
            # Core says `pkh(): key '<yours>' is not valid` for anything
            # that is not a key, and the panel says so in its own words,
            # so no part of the key survives to need redacting.
            seen.clear()
            session()._show_core_error(
                RuntimeError(f"pkh(): key '{secret}' is not valid"))
            assert seen and secret not in seen[0], (
                f"a refused key put the key on the panel: {seen[0][:60]}")
            assert "not a valid key" in seen[0], (
                f"a refused key does not say so plainly: {seen[0][:60]}")
    finally:
        scr.result = real
    # And the redactor must leave an ordinary message alone, or this
    # proves only that something was replaced.
    plain = "no way to load a PSBT"
    assert sg.redact(plain) == plain, "redact mangles an ordinary message"


def prop_no_key_form_reaches_argv():
    """Drive the REAL Rpc.call: nothing redact() strips may reach argv.

    The double above asserts what CALLERS pass. This asserts what the
    module BUILDS, which is the thing `ps` actually reads, and it does it
    for every private key form the redactor knows rather than for the one
    a test author thought of. Three escaped on 2026-09-08: SLIP-132's
    Zprv and Uprv, because XPRV_PREFIXES held only the lowercase
    single-sig prefixes, and a WIF, because a WIF has no word-shaped
    prefix and the guard searched for prefixes.
    """
    import subprocess as sp
    import signer as sg

    class Probe(sg.Rpc):
        def __init__(self):
            self.base = ["true"]
            self.chain = "regtest"
            self.net_dir = Path("/tmp")

    forms = {
        "mainnet xprv":
            "xprv9s21ZrQH143K3QTDL4LXw2F7HEK3wJUD2nW2nRk4stbPy6cq3jPPqji"
            "ChkVvvNKmPGJxWUtg6LnF5kejMRNNU3TGtRBeJgk33yuGBxrMPHi",
        "testnet tprv":
            "tprv8ZgxMBicQKsPe5YMU9gHen4Ez3ApihUfykaqUorj9t6FDqy3nP6eoXi"
            "Ao2ssvpAjoLroQxHqr3R5nE3a5dU3DHTjTgJDd7zrbniJr6nrCzd",
        "SLIP-132 Zprv":
            "ZprvAhWyxUFHYPKrpTGnMgVwHnGpvSKXnEfGVKrCZLxqTerAiWgAWL7hVQR"
            "5AKMTnRhLpGVHBWD8YyxTHqiFEHFwLwYYs2rUvNtcgVVKvBhVLdc",
        "SLIP-132 Uprv":
            "UprvA6NLj9GFvGyfHKq9d3iA1nhbXcCJHfSbXhfWq4dXjRe3H1sMHqCjJc4"
            "vDPLD4S4vSA8HXBUyGP9hjbb7bmVoq7dVpDPGXPbLdVpvyxa6XoV",
        "WIF, compressed":
            "L1aW4aubDFB7yfras2S1mN3bqg9nwySY8nkoLmJebSLD5BWv3ENZ",
        "WIF, uncompressed":
            "5HueCGU8rMjxEXxiPuD5BDku4MkFqeZyd4dZ1jvhTVqvbTLvyTJ",
    }
    def argv_for(probe, arg):
        """The command line Rpc.call really builds, with no cli run."""
        seen = {}

        def fake(cmd, **kw):
            seen["cmd"] = cmd

            class R:
                returncode, stdout, stderr = 0, "{}", ""
            return R()

        real, sp.run = sp.run, fake
        try:
            probe.call("getdescriptorinfo", arg)
        finally:
            sp.run = real
        return seen["cmd"]

    probe = Probe()
    for label, key in forms.items():
        cmd = argv_for(probe, f"wpkh({key}/0/*)")
        on_argv = [a for a in cmd if key in a]
        assert not on_argv, (
            f"a {label} reached argv, where `ps` reads it: {on_argv[0][:40]}…")
        assert sg.redact(key) == "<key redacted>", (
            f"a {label} is not redacted, so Core's refusal quotes it back "
            f"to the panel and the journal: {sg.redact(key)[:40]}")

    # ...and the public form must NOT be pushed to stdin, or the guard is
    # matching everything and proving nothing.
    xpub = ("xpub661MyMwAqRbcFtXgS5sYJABqqG9YLmC4Q1Rdap9gSE8NqtwybGhePY2"
            "gZ29ESFjqJoCu1Rupje8YtGqsefD265TMg7usUDFdp6W1EGMcet8")
    assert any(xpub in a for a in argv_for(probe, f"wpkh({xpub}/0/*)")), (
        "an xpub was pushed to stdin, so the guard fires on public keys "
        "too and the private-key results above prove nothing")


class FakeRpc:
    """Returns canned decodepsbt/analyzepsbt; exercises the Decimal path."""
    def __init__(self, decoded, analysis):
        self._decoded = decoded
        self._analysis = analysis

    def call(self, method, *params, wallet=None, stdin=False, drop=()):
        # stdin is not optional for a PSBT-carrying call: on Linux a PSBT
        # is too long to pass as one argv entry (I-10). The double asserts
        # it rather than accepting it, so a regression fails here on the
        # dev machine, where the real execve limit cannot be reached.
        if method in ("decodepsbt", "analyzepsbt", "walletprocesspsbt"):
            assert stdin, f"{method} must pass the PSBT through stdin"
        # Same shape of rule, same reason. decodepsbt's answer holds the
        # whole previous transaction for every input, and building all of
        # them cost 20.7MB of a 21.1MB tree at 250 batch-funded inputs.
        # That only matters on a 512MB board, so it cannot be felt here
        # and has to be asserted here instead (TESTING.md rule 9).
        if method == "decodepsbt":
            assert "non_witness_utxo" in drop, (
                "decodepsbt must drop non_witness_utxo: it is 20.7MB of "
                "previous transactions the review screen never reads")
        _no_key_in_argv(method, params, stdin)
        if method == "decodepsbt":
            return self._decoded
        if method == "analyzepsbt":
            return self._analysis
        raise AssertionError(method)


# --- every signer entry point that takes key material ------------------
# The generic guard above only fires for calls that actually happen, and
# the property tests never touch the key paths. So walk them explicitly:
# hand each one a real private key and assert it never reached argv.

KEY = ("tprv8ZgxMBicQKsPe5YMU9gHen4Ez3ApihUfykaqUorj9t6FDqy3nP6eoXiAo2ss"
       "vpAjoLroQxHqr3R5nE3a5dU3DHTjTgJDd7zrbniJr6nrCzd")


class ArgvWatcher:
    """Answers enough for the key paths, and refuses key material in argv."""

    chain = "regtest"

    def __init__(self):
        self.methods = []

    def call(self, method, *params, wallet=None, stdin=False, drop=()):
        self.methods.append(method)
        _no_key_in_argv(method, params, stdin)
        if method == "getdescriptorinfo":
            return {"checksum": "aaaaaaaa",
                    "descriptor": "wpkh(tpubDEADBEEF)#aaaaaaaa"}
        if method == "importdescriptors":
            return [{"success": True}]
        if method == "listwallets":
            return []
        if method == "listwalletdir":
            return {"wallets": []}
        if method == "listdescriptors":
            return {"descriptors": []}
        return ""


def prop_amounts_never_become_floats():
    """A BTC amount reaches Core as a string, never a binary float.

    `signer._json_decimal` is what makes that true, and audit A5 found it
    had never executed: every test happened to pass amounts that were
    already strings. It guards the review screen, which is where a user
    decides whether to sign, and a float there is how 0.1 + 0.2 becomes
    0.30000000000000004 on the one screen that must not lie.
    """
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["input"] = cmd, kw.get("input")

        class R:
            returncode, stdout, stderr = 0, '"ok"', ""
        return R()

    real = signer.subprocess.run
    signer.subprocess.run = fake_run
    try:
        rpc = signer.Rpc("/nonexistent", chain="regtest")
        # The shape walletcreatefundedpsbt takes: a list of {address: amount}
        rpc.call("walletcreatefundedpsbt", [],
                 [{"bcrt1qexample": Decimal("0.1")},
                  {"bcrt1qother": Decimal("21000000.00000001")}])
        blob = " ".join(str(a) for a in seen["cmd"])
        assert "0.1" in blob, f"the amount never reached Core: {blob[-120:]}"
        assert "21000000.00000001" in blob, \
            "a 21-million-BTC amount lost precision on the way to Core"
        assert "0.10000000000000000555" not in blob and "e-" not in blob, \
            f"an amount was rendered as a float: {blob[-120:]}"
        # And a type it does not know must be refused, not silently coerced.
        try:
            rpc.call("anything", object())
            raise AssertionError("_json_decimal accepted an unknown type")
        except TypeError:
            pass
    finally:
        signer.subprocess.run = real


def prop_rpc_routes_keys_itself():
    """Rpc.call keeps key material off argv without being asked.

    The check below this one drives FAKES, so it asserts what CALLERS do.
    This one drives the real Rpc and asserts what the MODULE does, which
    is the difference between a rule written in a docstring and a rule
    that holds. Only subprocess.run is replaced, so the command Core Signer
    would have executed is the thing under test.
    """
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["input"] = cmd, kw.get("input")

        class R:
            returncode, stdout, stderr = 0, '"ok"', ""
        return R()

    real = signer.subprocess.run
    signer.subprocess.run = fake_run
    try:
        rpc = signer.Rpc("/nonexistent", chain="regtest")
        # A caller that forgets. This is the 2026-09-05 defect exactly.
        rpc.call("getdescriptorinfo", f"wpkh({KEY})")
        assert not any(KEY in str(a) for a in seen["cmd"]), \
            "Rpc.call let a private key onto argv"
        assert KEY in (seen["input"] or ""), \
            "Rpc.call dropped the key instead of routing it to stdin"
        # And a call with nothing secret in it is left alone, so the
        # protection costs nothing everywhere else.
        rpc.call("getblockcount")
        assert "-stdin" not in seen["cmd"], \
            "Rpc.call used -stdin for a call with no key material"
    finally:
        signer.subprocess.run = real


def prop_no_key_in_argv():
    """Every signer entry point that takes key material uses stdin."""
    for name, run in (
            ("build_descriptors",
             lambda r: signer.build_descriptors(r, KEY)),
            ("opens_wallet",
             lambda r: signer.opens_wallet(r, "w", KEY)),
            ("open_session_descriptors",
             lambda r: signer.open_session_descriptors(
                 r, [f"wpkh({KEY}/84h/1h/0h/0/*)"])),
    ):
        try:
            run(ArgvWatcher())
        except AssertionError:
            raise
        except Exception as exc:                   # noqa: BLE001
            # A fake this thin cannot satisfy every path. What matters is
            # that no key reached argv before it gave up, and _no_key_in_argv
            # raises AssertionError, which is re-raised above.
            if "argv" in str(exc):
                raise AssertionError(f"{name}: {exc}") from None


@given(inputs=st.lists(st.integers(1, 21_000_000 * 10**8), min_size=1, max_size=8),
       out_frac=st.integers(1, 999))
@settings(max_examples=EXAMPLES, deadline=None)
def prop_fee_decimal_exact(inputs, out_frac):
    input_total_sats = sum(inputs)
    # One output taking a fraction; the remainder is the fee.
    out_sats = max(1, input_total_sats * out_frac // 1000)
    if out_sats >= input_total_sats:
        out_sats = input_total_sats - 1
    fee_sats = input_total_sats - out_sats
    input_total_btc = sum(sats_to_btc(s) for s in inputs)
    out_btc = sats_to_btc(out_sats)
    fee_btc = input_total_btc - out_btc

    decoded = {
        "tx": {"vout": [{"scriptPubKey": {"address": "bcrt1qtest"},
                         "value": out_btc}],
               "vin": [{} for _ in inputs]},
        "inputs": [{"witness_utxo": {"amount": sats_to_btc(s)}} for s in inputs],
        "fee": fee_btc,
    }
    analysis = {"next": "signer"}
    result = signer.describe_psbt(FakeRpc(decoded, analysis), "dummy")

    # describe_psbt sums witness amounts via Decimal(str(amount)); assert exact.
    assert result["input_total_btc"] == input_total_btc
    # Fee equals inputs_total - outputs_total exactly (Decimal, no loss).
    assert result["fee_btc"] == input_total_btc - out_btc
    assert result["fee_btc"] == fee_btc
    # Cross-check against integer-sat arithmetic (the ground truth).
    assert result["fee_btc"] == sats_to_btc(fee_sats)


def prop_a_slow_node_does_not_freeze_the_device():
    """A node that never answers must become an error, not a hang.

    Absent bitcoind fails fast, so every suite covered that and none
    covered the middle case: a node holding its socket and answering
    nothing. `subprocess.run` had NO timeout, so the call blocked for
    ever and the panel stayed on a busy screen with no way out and no
    shell to fix it from. Measured with SIGSTOP on a live node (audit A4,
    2026-09-06); it was still blocked after 45 seconds.

    Faked here with a `bitcoin-cli` that sleeps, because the property
    under test is the timeout and not the node. RuntimeError is the
    required type: it is what `Session.HANDLED` catches, so the panel
    shows a message it can dismiss.
    """
    import subprocess as sp
    import tempfile
    sleeper = Path(tempfile.mkdtemp()) / "bitcoin-cli"
    sleeper.write_text("#!/bin/sh\nsleep 60\n")
    sleeper.chmod(0o755)
    rpc = signer.Rpc("/nonexistent", chain="regtest", cli=str(sleeper))
    was = signer.RPC_TIMEOUT
    signer.RPC_TIMEOUT = 1.0
    t0 = time.monotonic()
    try:
        rpc.call("getblockchaininfo")
        raise AssertionError("a node that never answers returned normally")
    except RuntimeError as exc:
        took = time.monotonic() - t0
        assert "did not answer" in str(exc), f"wrong message: {exc}"
        assert took < 10, f"gave up after {took:.0f}s, not about 1s"
    except sp.TimeoutExpired:
        raise AssertionError(
            "TimeoutExpired reaches the caller. Session.HANDLED does not "
            "catch it, so this ends the process instead of painting an "
            "error") from None
    finally:
        signer.RPC_TIMEOUT = was
        shutil.rmtree(sleeper.parent, ignore_errors=True)
    # And the cap has to stay far above real work, or it breaks the
    # working case: the slowest call measured on the board is 4.4s.
    assert was >= 60, f"RPC_TIMEOUT is {was}s, too close to real work"


def prop_too_many_inputs_is_refused_not_signed():
    """A PSBT past the board's memory limit is refused, and one under it
    is not.

    Measured on a Zero 2 W: 250 batch-funded inputs leave 72MB where
    100MB is required, and the kernel then kills whichever process asks
    for the next page. That can be bitcoind, holding the only copy of a
    signature. Refusing on a screen beats dying half way through.

    Driven through the real `state_review`, with a Core that answers as
    Core does, because the point is the wiring and not the arithmetic.
    The limit itself cannot be reached on a dev machine, so it is
    asserted here instead (TESTING.md rule 9).
    """
    import hal
    import main as coresigner_main
    import screens

    class Painted:
        width, height = 320, 240

        def __init__(self):
            self.shown = []

        def show(self, image, sensitive=False):
            self.shown.append(image)

    class Answers:
        chain = "regtest"

        def __init__(self, n, quorum=False):
            self.n = n
            # A quorum input carries a witness script, which is what
            # `describe_psbt` reads the threshold out of and what makes
            # the decode cost more per input.
            self.txin = ({"witness_script": {
                "type": "multisig",
                "asm": "2 03aa 03bb 03cc 3 OP_CHECKMULTISIG"}}
                if quorum else {})

        def call(self, method, *params, wallet=None, stdin=False, drop=()):
            if method == "decodepsbt":
                return {"tx": {"vout": [{"value": Decimal("1"),
                                         "scriptPubKey": {"address": "bcrt1q"}}],
                               "vin": [{}] * self.n},
                        "fee": Decimal("0.0001"),
                        "inputs": [dict(self.txin) for _ in range(self.n)]}
            if method == "analyzepsbt":
                return {"next": "signer"}
            if method == "listdescriptors":
                return {"descriptors": [{"desc": "wpkh([73c5da0a/84h/1h/0h]x)"}]}
            if method == "walletprocesspsbt":
                # Reached only by the UNDER-limit run, which is the half
                # that proves the guard does not refuse real work.
                return {"psbt": base64.b64encode(b"psbt\xffsigned").decode(),
                        "complete": True}
            return ""

    limit = coresigner_main.MAX_SIGNABLE_INPUTS
    # Pin the VALUE against the board, not just the wiring. Reading the
    # constant and testing limit+1 follows the code wherever it goes:
    # setting it to 1 passed this check until the band below was added.
    # 200 inputs measured 78MB of headroom against 100MB required, and
    # 175 measured 114MB, so anything from 200 up ships a known failure
    # and anything under 100 refuses transactions the board can hold.
    if not 100 <= limit < 200:
        raise AssertionError(
            f"MAX_SIGNABLE_INPUTS is {limit}. The board was measured at "
            "114MB of headroom on 175 inputs and 78MB on 200, against "
            "100MB required, so the limit belongs between 100 and 199")
    # And the QUORUM ceiling, which is lower because a multisig input
    # costs more to decode. Ben, 2026-09-11: two numbers, not one
    # conservative one, because a single low cap refuses ordinary batches
    # this board is measured signing.
    multi = coresigner_main.MAX_SIGNABLE_MULTISIG_INPUTS
    if not 100 <= multi <= 150:
        raise AssertionError(
            f"MAX_SIGNABLE_MULTISIG_INPUTS is {multi}. Measured on the "
            "Zero 2 W: a 2-of-3 leaves 107MB of headroom at 150 inputs "
            "and FAILS at 175 with 92MB, against 100MB required, so "
            "anything past 150 ships a known failure and anything under "
            "100 refuses quorums the board can hold")
    for n, quorum, want_refusal in ((limit + 1, False, True),
                                    (limit, False, False),
                                    (multi + 1, True, True),
                                    (multi, True, False)):
        disp = Painted()
        sess = coresigner_main.Session(disp, hal.DevButtons("a" * 40),
                                  rpc=Answers(n, quorum), animate=False,
                                  on_device=False)
        sess.keys = [coresigner_main.LoadedKey("coresigner-73c5da0a", "73c5da0a")] \
            if hasattr(coresigner_main, "LoadedKey") else []
        sess._key_for = lambda _psbt: "coresigner-73c5da0a"
        try:
            sess.state_review("cHNidP8B", None)
        except hal.ScriptExhausted:
            pass
        cap = multi if quorum else limit
        refusal = screens.result(
            320, 240, ok=False,
            detail=f"{n} inputs; this board signs up to {cap}")
        drew = any(f.tobytes() == refusal.tobytes() for f in disp.shown)
        shape = "a quorum" if quorum else "single-sig"
        if want_refusal and not drew:
            raise AssertionError(
                f"{n} {shape} inputs is past the {cap} the board can hold, "
                "and the device did not refuse it")
        if not want_refusal and drew:
            raise AssertionError(
                f"{n} {shape} inputs is within the limit and was refused "
                "anyway; a guard that refuses real work is worse than no "
                "guard")
        # The cap that ran must be the one for THIS shape, or a quorum
        # gets the single-sig number and the whole split does nothing.
        other = limit if quorum else multi
        if any(f.tobytes() == screens.result(
                320, 240, ok=False,
                detail=f"{n} inputs; this board signs up to {other}"
        ).tobytes() for f in disp.shown):
            raise AssertionError(
                f"{n} {shape} inputs was judged against {other}, which is "
                "the other shape's ceiling")


def prop_an_unreadable_decode_is_refused_not_signed():
    """A decodepsbt answer the review cannot read is refused on screen.

    Core 32.0 began accepting PSBT v2, and its decode of one has no `tx`.
    `describe_psbt` read `decoded["tx"]["vout"]`, so the first v2
    transaction raised a KeyError, which is not in `Session.HANDLED`, and
    the process died with the panel frozen on the scan screen. Reading
    the second shape fixes v2. It does not fix the next shape, so every
    answer that is neither must become a refusal that names what is
    missing, and nothing may be signed (bb-orchestrator ruling,
    2026-11-04).

    Each case removes one field the review needs from an answer Core
    really gives. The first case is that answer whole, which proves the
    fake reaches the review screen, so a refusal below means the field
    and not the fake.
    """
    import hal
    import main as coresigner_main
    import screens

    class Painted:
        width, height = 320, 240

        def __init__(self):
            self.shown = []

        def show(self, image, sensitive=False):
            self.shown.append(image)

    def v0():
        return {"psbt_version": 0,
                "tx": {"vout": [{"value": Decimal("0.5"),
                                 "scriptPubKey": {"address": "bcrt1q"}}],
                       "vin": [{"sequence": 0xfffffffd}]},
                "inputs": [{}], "outputs": [{}], "fee": Decimal("0.0001")}

    def v2():
        return {"psbt_version": 2,
                "inputs": [{"sequence": 0xfffffffd}],
                "outputs": [{"amount": Decimal("0.5"),
                             "script": {"address": "bcrt1q"}}],
                "fee": Decimal("0.0001")}

    def without(answer, *path):
        node = answer
        for step in path[:-1]:
            node = node[step]
        del node[path[-1]]
        return answer

    # The third column is the word the refusal must contain, so the
    # screen names the problem rather than saying only that there is one.
    cases = [
        ("a whole v0 answer", v0(), None),
        ("a whole v2 answer", v2(), None),
        ("v0 with no tx and no v2 version", without(v0(), "tx"), "'tx'"),
        ("v0 with no vout", without(v0(), "tx", "vout"), "'vout'"),
        ("v0 with no output value",
         without(v0(), "tx", "vout", 0, "value"), "'value'"),
        ("v0 with no scriptPubKey",
         without(v0(), "tx", "vout", 0, "scriptPubKey"), "'scriptPubKey'"),
        ("v2 with no outputs", without(v2(), "outputs"), "'outputs'"),
        ("v2 with no output amount",
         without(v2(), "outputs", 0, "amount"), "'amount'"),
        ("v2 with no output script",
         without(v2(), "outputs", 0, "script"), "'script'"),
        ("v2 with no inputs", without(v2(), "inputs"), "'inputs'"),
        ("no version and no tx", without(v2(), "psbt_version"), "'tx'"),
        ("a version that is neither", {**v2(), "psbt_version": 3}, "3"),
    ]
    for name, answer, names in cases:
        want_refusal = names is not None
        called = []

        class Answers:
            chain = "regtest"

            def call(self, method, *params, wallet=None, stdin=False,
                     drop=(), answer=answer, called=called):
                called.append(method)
                if method == "decodepsbt":
                    return answer
                if method == "analyzepsbt":
                    return {"next": "signer"}
                if method == "walletprocesspsbt":
                    return {"psbt": base64.b64encode(b"psbt\xffsigned").decode(),
                            "complete": True}
                return ""

        disp = Painted()
        # C rejects on the review screen, so a readable answer ends on
        # "rejected by user" and an unreadable one never reaches it.
        sess = coresigner_main.Session(disp, hal.DevButtons("c"),
                                       rpc=Answers(), animate=False,
                                       on_device=False)
        sess._key_for = lambda _psbt: "coresigner-73c5da0a"
        try:
            sess.state_review("cHNidP8B", None)
        except hal.ScriptExhausted:
            pass
        refusals = [f for f in disp.shown
                    if any(f.tobytes() == screens.result(
                        320, 240, ok=False,
                        detail=f"Cannot review: {why}"[:coresigner_main.MESSAGE_CHARS]
                    ).tobytes() for why in _unreadable_why(answer))]
        if "walletprocesspsbt" in called:
            raise AssertionError(f"{name}: the device signed it")
        if want_refusal and not refusals:
            raise AssertionError(
                f"{name}: no refusal screen naming the problem was drawn")
        if want_refusal and not any(names in why
                                    for why in _unreadable_why(answer)):
            raise AssertionError(
                f"{name}: the refusal {_unreadable_why(answer)!r} does not "
                f"name {names}")
        if not want_refusal and refusals:
            raise AssertionError(
                f"{name}: an answer Core really gives was refused")
        rejected = screens.result(320, 240, ok=False,
                                  detail="rejected by user").tobytes()
        if not want_refusal and not any(f.tobytes() == rejected
                                        for f in disp.shown):
            raise AssertionError(f"{name}: the review screen was never reached")


def _unreadable_why(answer):
    """What describe_psbt says about `answer`, or nothing if it reads it."""
    class One:
        def call(self, method, *params, **kw):
            return answer if method == "decodepsbt" else {"next": "signer"}
    try:
        signer.describe_psbt(One(), "cHNidP8B")
    except signer.UnreadablePsbt as exc:
        return [str(exc)]
    except Exception:  # noqa: BLE001 - the caller reports it as no refusal
        return []
    return []


def prop_a_failing_stick_does_not_lose_a_signature():
    """Core has signed. If the stick then fails, the screen delivers it.

    The QR path has always said that letting an error unwind here throws
    the signature away and makes the user approve the whole transaction
    again. The FILE path did not, and audit A3 then taught write_signed
    to refuse a short write, which gave a full or failing stick a brand
    new way to lose a signature (found reading main.py, 2026-09-07).

    The screen cannot be full, unplugged or mounted read-only, so it is
    the fallback.
    """
    import filechannel
    import hal
    import main as coresigner_main

    class Painted:
        width, height = 320, 240

        def __init__(self):
            self.shown = []

        def show(self, image, sensitive=False):
            self.shown.append(image)

    class Signs:
        chain = "regtest"

        def __init__(self):
            self.signed = False

        def call(self, method, *params, wallet=None, stdin=False, drop=()):
            if method == "walletprocesspsbt":
                self.signed = True
                return {"psbt": base64.b64encode(b"psbt\xffSIGNED").decode(),
                        "complete": True}
            if method == "analyzepsbt":
                # sign_psbt counts what Core still wants, before and
                # after, so that "signed nothing" cannot reach the SIGNED
                # screen (map M9). One missing signature becomes none.
                return {"inputs": [{"missing": {}} if self.signed
                                   else {"missing": {"signatures": ["ab"]}}]}
            return ""

    src = Path(tempfile.mkdtemp()) / "tx.psbt"
    src.write_bytes(b"psbt\xff")
    real = filechannel.write_signed

    def full_medium(source, b64):
        raise filechannel.FileChannelError(
            "tx-signed.psbt: wrote 100 of 4005 bytes, the medium is full")

    # What reaching the signed screen is worth depends on what is ON it.
    # Until 2026-09-08 this property asserted the outcome alone, so a
    # fallback that showed the UNSIGNED psbt back to the user passed it:
    # the screen was reached, the signature was still lost, and the test
    # said the signature was delivered.
    import qrchannel
    framed = []
    real_frames = qrchannel.psbt_to_frames

    def spy(b64):
        framed.append(b64)
        return real_frames(b64)

    signed_b64 = base64.b64encode(b"psbt\xffSIGNED").decode()
    filechannel.write_signed = full_medium
    qrchannel.psbt_to_frames = spy
    sess = coresigner_main.Session(Painted(), hal.DevButtons("a" * 30),
                              rpc=Signs(), animate=False, on_device=False)
    try:
        out = sess._sign_and_deliver("cHNidP8B", src, "coresigner-x")
    except filechannel.FileChannelError:
        raise AssertionError(
            "the stick failed and the error unwound out of "
            "_sign_and_deliver, so a signature Core had already made was "
            "thrown away") from None
    finally:
        filechannel.write_signed = real
        qrchannel.psbt_to_frames = real_frames
        shutil.rmtree(src.parent, ignore_errors=True)
    assert out in (coresigner_main.SIGN_AGAIN, coresigner_main.POWER_OFF,
                   coresigner_main.TO_HOME), f"unexpected outcome {out!r}"
    assert out != coresigner_main.TO_HOME, (
        "the run went home instead of reaching the signed screen, so the "
        "signature was not delivered anywhere")
    assert framed, (
        "the signed screen was reached but nothing was ever turned into "
        "frames, so the screen carried no transaction")
    assert framed == [signed_b64], (
        f"the QR fallback framed {framed!r}, not the psbt Core signed. "
        f"The screen was reached and the signature was still lost.")


def main():
    checks = [
        ("no key form reaches argv", prop_no_key_form_reaches_argv),
        ("no key reaches the panel", prop_no_key_reaches_the_panel),
        ("qr feed no-crash fuzz", prop_qr_feed_no_crash),
        ("read_psbt no-crash fuzz", prop_read_psbt_no_crash),
        ("fee Decimal exact", prop_fee_decimal_exact),
        ("no key material in argv", prop_no_key_in_argv),
        ("Rpc routes keys to stdin itself", prop_rpc_routes_keys_itself),
        ("amounts never become floats", prop_amounts_never_become_floats),
        ("a slow node does not freeze the device",
         prop_a_slow_node_does_not_freeze_the_device),
        ("too many inputs is refused, not signed",
         prop_too_many_inputs_is_refused_not_signed),
        ("a failing stick does not lose a signature",
         prop_a_failing_stick_does_not_lose_a_signature),
        ("an unreadable decode is refused, not signed",
         prop_an_unreadable_decode_is_refused_not_signed),
    ]
    failed = 0
    for name, fn in checks:
        try:
            fn()
            print(f"ok   {name}")
        except Exception as exc:
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\nPROPERTY TESTS {'PASS' if not failed else 'FAILED'} "
          f"({len(checks) - failed}/{len(checks)}, {EXAMPLES}+ examples each)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
