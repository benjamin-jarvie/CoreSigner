"""The image carries the program and nothing else.

`prepare-sd.sh` used to run `git archive HEAD`, which put the whole
repository on the signer: every test, every ticket, the articles and the
art, and 38 Python files that never execute there. A signer should carry
what it runs. Every extra file is one more thing a reader has to audit
before they can believe the device.

This suite is the pin. It builds the archive the way prepare-sd.sh does,
then asserts two things that must both stay true: everything provision.sh
reaches for is inside it, and nothing that only belongs to development is.

Run: python3 tests/test_image_contents.py (no bitcoind needed)
"""
import re
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

fails = []
def ok(m): print("ok  ", m)
def bad(m): fails.append(m); print("FAIL", m)


def archive_paths():
    """Exactly what prepare-sd.sh puts in coresigner.tar.gz, read out of the
    script itself so the two cannot drift apart."""
    script = (ROOT / "image" / "prepare-sd.sh").read_text()
    m = re.search(r'archive --format=tar\.gz -o "\$BOOT/coresigner\.tar\.gz" HEAD \\?\s*\n?\s*([^\n]*)',
                  script)
    if not m or not m.group(1).strip():
        bad("prepare-sd.sh no longer names the paths it ships")
        return None, []
    spec = m.group(1).split("#")[0].split()
    out = subprocess.run(["git", "archive", "--format=tar", "HEAD", *spec],
                         cwd=ROOT, capture_output=True)
    if out.returncode:
        bad(f"git archive refused {spec}: {out.stderr[-200:]!r}")
        return spec, []
    import io
    with tarfile.open(fileobj=io.BytesIO(out.stdout)) as tar:
        return spec, [m.name for m in tar.getmembers() if m.isfile()]


def main():
    spec, names = archive_paths()
    if not names:
        print("FAILED 1")
        sys.exit(1)
    ok(f"prepare-sd.sh ships {len(spec)} paths, {len(names)} files")

    # 1. Everything provision.sh installs from /opt/coresigner must be present,
    #    or the first real flash dies partway through provisioning.
    provision = (ROOT / "image" / "provision.sh").read_text()
    needed = set()
    for line in provision.splitlines():
        for hit in re.findall(r"/opt/coresigner/([A-Za-z0-9_@./-]+)", line):
            # A path at the end of a cp or install line is where the file
            # GOES, not a file the image must already carry. PINS.installed
            # is written by provisioning itself.
            if line.rstrip().endswith("/opt/coresigner/" + hit):
                continue
            # Nor does a redirection target: `> /opt/coresigner/BUILD`
            # creates the file, it does not require one.
            if re.search(r">\s*/opt/coresigner/" + re.escape(hit), line):
                continue
            # Nor a directory the tarball unpacks INTO. `names` lists
            # files, so a directory never appears in it; what has to
            # exist is a file underneath, and the archive is checked for
            # that rather than for the directory itself.
            if any(n.startswith(hit.rstrip("/") + "/") for n in names):
                continue
            needed.add(hit)
    needed = sorted(needed)
    missing = [n for n in needed if n not in names]
    if not missing:
        ok(f"every path provision.sh installs is in the image ({len(needed)})")
    else:
        bad(f"provision.sh would not find: {missing}")

    # 2. The program itself, the vendored code it imports, and the three
    #    scripts a tester runs ON the device. verify-install.sh is the one
    #    that matters most and shipped in nothing until audit A7: it is how
    #    anybody, including a tester who trusts neither of us, checks that
    #    the card in their hand matches the repository they can read.
    required = ("coresigner/main.py", "coresigner/signer.py", "coresigner/screens.py",
                "coresigner/qrchannel.py", "coresigner/filechannel.py",
                "coresigner/hal.py", "coresigner/splash.py",
                "image/leak-check.sh", "image/harden.sh", "image/unharden.sh",
                "image/verify-install.sh", "image/PINS",
                "image/requirements.txt",
                "hw/vendor/st7789.py", "hw/vendor/ur2/__init__.py",
                "hw/vendor/fonts/fa-solid-subset.ttf", "LICENSE")
    absent = [r for r in required if r not in names]
    if not absent:
        ok("the program, the vendored drivers, the font and the licence ship")
    else:
        bad(f"the image is missing {absent}")

    # 3. And nothing that belongs only to development.
    unwanted = {
        "tests/": "the suites",
        "docs/": "the wayfinder maps and tickets",
        "art/": "the artwork",
        "tools/": "the dev scripts",
        "PLAN.md": "the planning record",
        "TESTING.md": "the testing rules",
        "ISSUES.md": "the defect list",
        "ORDER.md": "the parts list",
        "CONTEXT.md": "the glossary",
        "run_tests.sh": "the test runner",
        "ruff.toml": "the linter config",
        "requirements-dev.txt": "the dev tools",
    }
    leaked = sorted({why for prefix, why in unwanted.items()
                     for n in names if n.startswith(prefix)})
    if not leaked:
        ok(f"none of the {len(unwanted)} development-only paths ship")
    else:
        bad(f"the signer would carry: {leaked}")

    # 4. No Python on the device except the program and what it imports.
    stray = [n for n in names if n.endswith(".py")
             and not n.startswith(("coresigner/", "hw/vendor/"))]
    if not stray:
        ok("no Python ships that the device does not run")
    else:
        bad(f"Python that never runs on the device: {stray}")

    # 5. The pins a device can be checked against. The tarball hash can
    #    only be used at install time, which audit A7 pointed out is the
    #    one moment nobody watches; the binary hashes make an installed
    #    device re-checkable for ever.
    pins = (ROOT / "image" / "PINS").read_text()
    for key in ("CORE_BIN_SHA256", "CLI_BIN_SHA256"):
        m = re.search(rf'{key}="([0-9a-f]*)"', pins)
        if m and len(m.group(1)) == 64:
            ok(f"{key} is pinned, so an installed device can be re-checked")
        else:
            bad(f"{key} is missing or not a sha256 in image/PINS, so "
                "verify-install.sh cannot check the binary on the board")

    # 6. The signer's own payload must be verified before it is unpacked
    #    as root. Bitcoin Core is checked against a sha256 and eight GPG
    #    signatures; coresigner.tar.gz was taken on trust, unpacked as root and
    #    then run as root at every boot (audit of image/, 2026-09-08).
    prov = (ROOT / "image" / "provision.sh").read_text()
    prep = (ROOT / "image" / "prepare-sd.sh").read_text()
    if "CORESIGNER_TARBALL_SHA256" not in prep:
        bad("prepare-sd.sh records no hash for coresigner.tar.gz, so the "
            "device has nothing to check the payload against")
    elif "CORESIGNER_TARBALL_SHA256" not in prov:
        bad("provision.sh does not check coresigner.tar.gz against a hash "
            "before unpacking it as root")
    elif "sha256sum -c" not in prov:
        bad("provision.sh names the hash but never verifies it")
    else:
        ok("the payload is hashed when the card is written and verified "
           "before it is unpacked")
    if "--no-same-owner" not in prov:
        bad("tar unpacks with the tarball's own ownership; everything in "
            "/opt/coresigner should belong to root whatever the archive says")
    else:
        ok("the payload unpacks as root, whatever the archive claims")

    # 6b. pip must be pinned by CONTENT. A version number stops an
    #     accidental upgrade and stops nothing else: a compromised index
    #     serves a different wheel under the same version.
    req = ROOT / "image" / "requirements.txt"
    if not req.exists():
        bad("image/requirements.txt is missing, so pip has no hash lock")
    elif "--require-hashes" not in prov:
        bad("provision.sh installs pip packages without --require-hashes, "
            "so the hashes in requirements.txt are decoration")
    else:
        lines = req.read_text().splitlines()
        pkgs = [ln for ln in lines if "==" in ln and not ln.startswith("#")]
        hashes = [ln for ln in lines if "--hash=sha256:" in ln]
        if not pkgs:
            bad("requirements.txt pins no packages")
        elif len(hashes) < len(pkgs):
            bad(f"{len(pkgs)} packages but only {len(hashes)} hashes; "
                "--require-hashes needs one for every package, transitive "
                "ones included")
        else:
            ok(f"all {len(pkgs)} pip packages are pinned by sha256 and "
               "installed with --require-hashes")
        # The lock ships inside the payload, so it can only be trusted
        # after the payload has been verified. Order matters.
        if prov.index("--require-hashes") < prov.index("tar xzf \"$BOOT"):
            bad("pip installs from the lock file BEFORE the payload "
                "carrying it is unpacked and verified")
        else:
            ok("pip installs only after the payload carrying the lock is "
               "verified and unpacked")

    # 7. A card must say which Core Signer is on it. CORESIGNER_COMMIT="HEAD" told a
    #    tester nothing and made two cards a week apart indistinguishable
    #    (audit A7).
    if 'CORESIGNER_COMMIT=' not in prep or "rev-parse HEAD" not in prep:
        bad("prepare-sd.sh does not pin the commit it packed into the "
            "card's PINS, so a tester cannot say which Core Signer they have")
    else:
        ok("the card records the exact commit it was written from")

    # 8. git archive packs HEAD, so uncommitted work silently does not
    #    reach the card.
    if "diff-index --quiet HEAD" not in prep:
        bad("prepare-sd.sh writes a card from HEAD without checking for "
            "uncommitted changes, so edited code can silently not ship")
    else:
        ok("a dirty working tree stops the card being written")

    print()
    print("FAILED %d" % len(fails) if fails else "ALL PASS")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
