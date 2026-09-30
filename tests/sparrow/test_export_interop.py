"""Core Signer's exported public key, read by Sparrow's own library.

TESTING.md rule 8: an interop claim tested with your own tools is not an
interop claim. Core Signer's own decoder reads Core Signer's own QR by construction.
This suite renders the export exactly as the panel shows it, decodes it
with the zxing reader Sparrow uses, hands the decoded string to Sparrow's
OutputDescriptor, and compares the addresses with Core's.

Run: ./setup.sh once, then python3 test_export_interop.py
"""
import sys
import tempfile
from pathlib import Path

import harness
from harness import Java, Regtest, Results

sys.path.insert(0, str(harness.REPO / "coresigner"))
import qrchannel
import screens  # noqa: E402
import signer  # noqa: E402

#: How many of the eight masks must decode. ISSUES.md E-6.
#:
#: This was 6, chosen by eye, and it failed about one run in forty
#: because the key is GENERATED fresh each run and about one descriptor
#: in three hundred masks awkwardly. Measured 2026-09-11 over 720
#: renders, 45 keys twice, four policies, both panels:
#:
#:     8 of 8   ~94%        7 of 8   ~5%       5 of 8   0.3%
#:
#: Nothing read 6, and nothing read below 5. `wpkh` never dropped below
#: 8 at all; the low outlier was `tr` both times, on BOTH panels for the
#: same key, so it is the descriptor's content and not the panel.
#:
#: **What the number has to protect is time to scan, not a tidy score.**
#: The export cycles all eight masks at EXPORT_MASK_DELAY, 0.3s each, so
#: with k of 8 readable a coordinator's first readable frame arrives in
#: about (8/k) x 0.3s: 0.3s at k=8, 0.5s at k=5, 0.8s at k=3. The
#: failure that matters is k=0, a key whose export can never be read,
#: and 720 renders produced none. Three bounds the wait under a second
#: and sits well clear of the measured floor of 5.
MIN_MASKS = 3

#: Descriptors measured at 7 of 8, kept so every run meets awkward
#: content instead of drawing for it. A generated key still runs beside
#: them, because what Core really writes is the thing under test and a
#: frozen corpus cannot notice Core changing.
HARD_DESCRIPTORS = [
    ("pkh", "pkh([4abbcc4c/44h/1h/0h]tpubDCSALTaVF5mgfGbPu2Hn8PQkAomNX1Zvwg"
            "JJYn7FHFjfUZcnESg4hR53UMZCaDX5UgNXKKyDNC4bksPLegrUdmi5narMjs7a"
            "CBe5c4UBfPF/0/*)#arkj9h0u"),
    ("tr", "tr([2e249786/86h/1h/0h]tpubDChTwweq5BgQwMnbGmKMUNgNodLn8YkkyZXcH"
           "mcgLLVfd5SjAmz4hF6VJVGUzML7hqdTepLne9pDRPEJ7GZMrJ6MBYXyKFGGq348d"
           "5fmfCu/0/*)#n3czq29y"),
    ("tr", "tr([b3e01921/86h/1h/0h]tpubDChTREaE9NLFZe7kevKgFkNKG9FcQPYSi4mvx"
           "osNu3BpGw1zFXcCio6Yev39VrV8iGGZdFnRiorabWtgRMss3JbASgVo4DtriLch3"
           "qq1ccK/0/*)#mulrnnv4"),
    ("wpkh", "wpkh([f592f2a3/84h/1h/0h]tpubDDqZvAmGV9hWgnkAJ5M6auHVjCBWS1sbE"
             "CVPZF1EvT6TonAgWGqEYndWfkw6kbfJXgirmNNvwns1YD1pPHfTqEqdxhnY65q"
             "6HiWymrw8hNK/0/*)#7k5tw96g"),
]

PANELS = [("primary 320x240", (320, 240)), ("pocket 240x240", (240, 240))]


def main():
    java = Java()
    r = Results()
    work = Path(tempfile.mkdtemp(prefix="coresigner-export-interop-"))
    with Regtest(mine=1) as net:
        # Every policy this key HAS. The harness key arrives by import,
        # and since map ticket D6 an imported key is built with all four,
        # like a generated one, so restoring a paper backup gives back the
        # whole wallet rather than half of it.
        kinds = signer.available_kinds(net.rpc, net.wallet)
        r.record("an imported key presents all four policies, like a "
                 "generated one",
                 kinds == signer.EXPORT_ORDER, str(kinds))
        for kind in kinds:
            desc = signer.export_descriptor(net.rpc, net.wallet, kind)
            core_addrs = signer.receive_addresses(net.rpc, net.wallet, kind, 5)

            for panel_name, panel in PANELS:
                img = qrchannel.text_to_image(
                    desc, panel=(panel[0], min(panel[1], screens.QR_MAX_PX)))
                r.record(f"{kind} QR fits the {panel_name} panel",
                         img.width <= panel[0] and img.height <= panel[1],
                         f"{img.width}x{img.height}")
                png = work / f"{kind}-{panel[0]}.png"
                # Render the screen the DEVICE draws, not a bare code: the
                # panel is dark, the code sits on a light card, and the
                # identity runs underneath. The card is what supplies the
                # quiet zone once the ground is no longer white, so this is
                # the check that it supplies enough of one.
                xfp, path = signer.origin_of(desc)
                screens.qr_export(panel[0], panel[1], img,
                                  xfp, kind, path).save(png)
                decoded = java("SparrowQr", "qrdecode", str(png))[0]
                r.record(f"{kind}: Sparrow's zxing reads the export screen "
                         f"on the {panel_name} panel",
                         decoded == desc,
                         "byte-identical" if decoded == desc
                         else f"got {decoded[:60]!r}")

            # Sparrow's own library must accept the string Core wrote.
            out = java("SparrowDesc", "REGTEST", desc, 5, tags=("INFO", "OUT"))
            info = "\n".join(out["INFO"])
            sparrow_addrs = [line.split("\t")[1] for line in out["OUT"]]
            r.record(f"{kind}: Sparrow parses Core's descriptor verbatim",
                     "policy=Single Signature HD" in info,
                     info.replace("\n", " | "))
            xfp = signer.master_fingerprint(net.rpc, wallet=net.wallet)
            r.record(f"{kind}: Sparrow reads the same fingerprint",
                     f"fp={xfp}" in info, xfp)
            r.record(f"{kind}: Sparrow derives Core's first five addresses",
                     sparrow_addrs == core_addrs,
                     f"{core_addrs[0][:18]}… x5" if sparrow_addrs == core_addrs
                     else f"{sparrow_addrs[:1]} != {core_addrs[:1]}")

            # --- the ACCOUNT KEY row, `[fingerprint/path]xpub` ----------
            # Added 2026-09-29. The descriptor above is what Sparrow and
            # Core read. Measured the same week: BlueWallet 7.2.0 imports
            # one of our four descriptors and Bitcoin Keeper's thirteen
            # vendor adapters import none of them (ISSUES.md E-8). Three
            # of Keeper's adapters read this form instead, and so does
            # SeedSigner's own "Static" export.
            key = signer.account_key(desc)

            # What the form is FOR is what it leaves out. Checked against
            # the descriptor's own characters rather than against the
            # function that built it (rule 2).
            r.record(f"{kind}: the account key drops the wrapper, the "
                     f"branch and the checksum",
                     ("(" not in key and ")" not in key
                      and "#" not in key and not key.endswith("*")),
                     key[:34] + "…")

            # The fingerprint and the path Core itself wrote, pulled out
            # of Core's string here by a plain split, so this assertion
            # does not lean on `origin_of` or on `account_key`.
            inside = desc[desc.index("[") + 1:desc.index("]")]
            core_fp, core_path = inside.split("/", 1)
            r.record(f"{kind}: the account key carries Core's own "
                     f"fingerprint and path",
                     key.startswith(f"[{core_fp.lower()}/{core_path}]"),
                     f"[{core_fp}/{core_path}]")

            # Rule 8: ask the counterpart. SparrowScan drives the two
            # drongo parsers QRScanDialog hands a scanned payload to.
            #
            # SPARROW REFUSES THIS FORM, and that is the point of the
            # assertion. Asked on 2026-09-29 on mainnet, testnet and
            # regtest, in both the `h` and the `'` spelling: "Unrecognised
            # extended key header". It reads a BARE xpub and it reads a
            # descriptor, and the key-origin string between them is
            # neither. I told Ben it accepted this form. It does not.
            #
            # So the two QR rows are not two spellings of one thing. The
            # descriptor row is the only one Sparrow scans, and the
            # account key row is the only one Keeper's adapters and
            # BlueWallet read. Neither row can be dropped. If a future
            # Sparrow starts accepting this, this check fails and tells
            # us, which is worth more than asserting what we hope for.
            f = work / f"{kind}-accountkey.txt"
            f.write_text(key)
            scanned = java("SparrowScan", "REGTEST", str(f), tags=("OUT",))
            said = "\t".join(scanned) if scanned else "nothing"
            r.record(f"{kind}: Sparrow's scan parsers still refuse the "
                     f"account key, so the descriptor row has to stay",
                     said.startswith("REFUSED"), said[:70])

            # The two coordinators whose code I cannot run here, each
            # transcribed from its own source. Keeper's three adapters
            # that read this form are character-identical, so one stands
            # for all three.
            bw_fp = key[1:9]                       # BlueWallet, line 236
            bw_path = "m/" + key[10:key.index("]")].replace("h", "'")
            bw_key = key[key.index("]") + 1:]
            keeper_key = key[key.index("]") + 1:]  # Keeper seedsigner/index.ts
            keeper_fp = key[1:9]
            r.record(f"{kind}: BlueWallet 7.2.0's parser recovers it",
                     bw_fp == core_fp.lower()
                     and bw_path.startswith("m/" + core_path.split("/")[0]
                                            .replace("h", "'"))
                     and bw_key.startswith(("xpub", "tpub")),
                     f"{bw_fp} {bw_path} {bw_key[:8]}")
            r.record(f"{kind}: Bitcoin Keeper's adapter recovers it",
                     keeper_fp == core_fp.lower()
                     and keeper_key.startswith(("xpub", "tpub")),
                     f"{keeper_fp} {keeper_key[:8]}")

        # The KEY scan, with real key material and a real decoder.
        # TESTING.md rule 1 wants real domain data through the surface, and
        # rule 8 wants the counterpart's decoder. test_keyscan.py drives the
        # stopping rules with stand-ins; this drives the bytes.
        sys.path.insert(0, str(harness.REPO / "coresigner"))
        import main as coresigner_main  # noqa: E402
        priv = [d["desc"] for d in
                net.rpc.call("listdescriptors", True,
                             wallet=net.wallet)["descriptors"]
                if d["desc"].startswith("wpkh(") and not d["internal"]][0]
        xprv = signer.master_xprv(net.rpc, wallet=net.wallet)
        session = coresigner_main.Session.__new__(coresigner_main.Session)

        for name, payload, want_kind in (("private descriptor", priv, "descriptor"),
                                         ("master xprv", xprv, "xprv")):
            png = work / f"key-{want_kind}.png"
            qrchannel.text_to_image(payload, panel=(320, 240)).save(png)
            decoded = java("SparrowQr", "qrdecode", str(png))[0]
            r.record(f"a real {name} survives render and zxing decode",
                     decoded == payload,
                     "byte-identical" if decoded == payload
                     else f"got {decoded[:40]!r}")
            r.record(f"the scan classifies a real {name} as {want_kind}",
                     coresigner_main._classify_qr(decoded) == want_kind,
                     coresigner_main._classify_qr(decoded))
            guarded = coresigner_main.Session._guard_key_payload(session, decoded)
            r.record(f"the A-11 guards pass a real {name} through unchanged",
                     guarded == payload, "unchanged")

        # And the guarded xprv really opens the key it came from: same
        # fingerprint, same addresses, from a wallet built out of the
        # string that came back through the camera path.
        before_xfp = signer.master_fingerprint(net.rpc, wallet=net.wallet)
        before_addrs = signer.receive_addresses(net.rpc, net.wallet, "wpkh", 3)
        signer.close_session(net.rpc)
        slot = signer.open_session_xprv(net.rpc, xprv)
        after_xfp = signer.master_fingerprint(net.rpc, wallet=slot)
        after_addrs = signer.receive_addresses(net.rpc, slot, "wpkh", 3)
        r.record("a scanned xprv opens the key it came from",
                 after_xfp == before_xfp and after_addrs == before_addrs,
                 f"{after_xfp}, {after_addrs[0][:16]}…")

        # The export must never carry a private key, whatever the panel.
        for kind in signer.available_kinds(net.rpc, slot):
            desc = signer.export_descriptor(net.rpc, slot, kind)
            r.record(f"{kind}: the exported string holds no private key",
                     "prv" not in desc, desc[:28] + "…")

        # A key CORE generated carries all four policies, and Sparrow's
        # zxing has to read every one of them captioned, because that is
        # what Ben puts in front of a coordinator. Nested segwit is the
        # awkward one: 61 modules against the others' 57, so its caption
        # turns sideways into the margin instead of sitting under the
        # code, and a caption drawn over the code fails here.
        signer.close_session(net.rpc)
        made = signer.generate_wallet(net.rpc)
        four = signer.available_kinds(net.rpc, made)
        r.record("a key Core generated carries all four policies",
                 four == ("wpkh", "tr", "sh", "pkh"), str(four))
        for kind in four:
            desc = signer.export_descriptor(net.rpc, made, kind)
            core_addrs = signer.receive_addresses(net.rpc, made, kind, 3)
            for panel_name, panel in PANELS:
                # EVERY mask, because the screen shows every mask. One
                # static render fails for about one descriptor in a
                # hundred (ISSUES.md E-5) and this suite used to render
                # exactly that one, so it failed roughly one run in
                # sixty and read as a flake. What matters now is not
                # whether a particular render decodes; it is how many of
                # the eight do, because a scanner reads whichever frame
                # it catches.
                xfp, path = signer.origin_of(desc)
                read = 0
                for m, img in enumerate(qrchannel.text_to_images(
                        desc, panel=(panel[0],
                                     min(panel[1], screens.QR_MAX_PX)))):
                    png = work / f"gen-{kind}-{panel[0]}-m{m}.png"
                    screens.qr_export(panel[0], panel[1], img,
                                      xfp, kind, path).save(png)
                    try:
                        if java("SparrowQr", "qrdecode", str(png))[0] == desc:
                            read += 1
                    except Exception:
                        pass
                r.record(f"{kind}: zxing reads the {panel_name} export screen "
                         "of a Core-generated key",
                         read >= MIN_MASKS,
                         f"{read} of 8 masks readable"
                         + ("" if read >= MIN_MASKS else
                            "  TOO FEW: a scanner can miss a whole cycle"))
            out = java("SparrowDesc", "REGTEST", desc, 3, tags=("INFO", "OUT"))
            sparrow_addrs = [line.split("\t")[1] for line in out["OUT"]]
            r.record(f"{kind}: Sparrow derives Core's first three addresses",
                     sparrow_addrs == core_addrs,
                     f"{core_addrs[0][:18]}… x3" if sparrow_addrs == core_addrs
                     else f"{sparrow_addrs[:1]} != {core_addrs[:1]}")
        signer.close_session(net.rpc)

        # THE PINNED AWKWARD ONES (E-6). Every run meets these, so the
        # suite stops depending on which key Core happened to make.
        totals = []
        for kind, desc in HARD_DESCRIPTORS:
            xfp, path = signer.origin_of(desc)
            for panel_name, panel in PANELS:
                read = 0
                pngs = []
                for m, img in enumerate(qrchannel.text_to_images(
                        desc, panel=(panel[0],
                                     min(panel[1], screens.QR_MAX_PX)))):
                    png = work / f"hard-{xfp}-{kind}-{panel[0]}-m{m}.png"
                    screens.qr_export(panel[0], panel[1], img,
                                      xfp, kind, path).save(png)
                    pngs.append(str(png))
                for line in java("SparrowQr", "qrcount", *pngs, tags=("OUT",)):
                    if line.startswith("HIT\t") and line[4:] == desc:
                        read += 1
                totals.append(read)
                r.record(f"{kind} {xfp}: the {panel_name} screen of a "
                         "descriptor measured awkward",
                         read >= MIN_MASKS, f"{read} of 8 masks readable")
        # One awkward key is ordinary; all of them dropping together is a
        # regression in the renderer, and a per-render floor of 3 would
        # not notice it.
        mean = sum(totals) / len(totals)
        r.record("the awkward corpus still averages near eight masks",
                 mean >= 6.5, f"mean {mean:.2f} of 8 over {len(totals)} renders")

    return r.summary()


if __name__ == "__main__":
    sys.exit(main())
