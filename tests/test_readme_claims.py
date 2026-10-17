"""README truth test: fails when the README's numeric claims drift.

The frozen-hash discipline is already mechanical (test_integrity.py), but
the README's line counts and campaign figures were prose — and prose rots.
This asserts every counted claim against the tree itself, so a
test-writing burst can no longer leave the README quietly wrong.
Run: python3 tests/test_readme_claims.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
fails = []

def ok(m): print("ok  ", m)
def bad(m): fails.append(m); print("FAIL", m)

def code_lines(path):
    """Functional lines: no blanks, no comments, no docstrings."""
    n, indoc = 0, False
    for ln in Path(path).read_text().splitlines():
        s = ln.strip()
        if not s:
            continue
        if indoc:
            if s.endswith('"""') or s.endswith("'''"):
                indoc = False
            continue
        if s.startswith('"""') or s.startswith("'''"):
            if not ((s.endswith('"""') or s.endswith("'''")) and len(s) > 3):
                indoc = True
            continue
        if s.startswith("#"):
            continue
        n += 1
    return n

README = (ROOT / "README.md").read_text()

# A-22: Layer 1 is empty on main. tests/test_integrity.py is the guard
# that keeps it empty; this file only has to stop claiming otherwise.
LAYER1 = []
# signer.py moved from layer 3 to layer 2 on 2026-09-05, after the two-axis
# review pointed out that it takes an xprv (open_session_xprv,
# build_descriptors, master_xprv, identity_of_key) as parameters. The
# passphrase pair went with PLAN A-24. CONTEXT.md: "Layer 2 sees secrets and
# carries them as strings. Layer 3 is opaque to secrets." It always carried
# the xprv; the README said otherwise for longer than it should have.
# qrsource.py is LAYER 2, not 3. DevQrSource.strings() yields whatever is
# in --qr-key, and on the device ImageQrSource.strings() yields whatever
# the camera decoded, which on the Scan-a-key flow is a private key. It
# carries the key as a string and computes nothing on it, which is the
# definition. qrchannel.py stays layer 3 because it only ever handles
# crypto-psbt frames (extracted 2026-09-08).
LAYER2 = ["coresigner/main.py", "coresigner/screens.py", "coresigner/splash.py",
          "coresigner/hal.py", "coresigner/signer.py", "coresigner/qrsource.py"]
LAYER3 = ["coresigner/filechannel.py", "coresigner/qrchannel.py"]

#: Rewrite the numbers instead of reporting them. Every commit that
#: touches a line of code or a line of test moves four of these, and
#: hand-editing four numbers per commit is how a suite starts getting run
#: with one of them already known-red. tests/test_vendor_pinned.py has the
#: same switch for the same reason.
#:
#: It rewrites only a number this file computes from the tree, and only
#: when the pattern already matches, so it can correct a figure and never
#: invent a claim. Every non-numeric check stays a check: --update cannot
#: make the apt table, the vendored split or a broken link pass.
UPDATE = "--update" in sys.argv
rewrites = []


def claimed(pattern, label):
    m = re.search(pattern, README)
    if not m:
        bad(f"README no longer states {label} (pattern missing)")
        return None
    return int(m.group(1).replace(",", ""))


def pin(pattern, label, actual, fmt="{:,}"):
    """Compare, and under --update rewrite the README to match."""
    c = claimed(pattern, label)
    if c is None or c == actual:
        if c is not None:
            ok(f"{label}: {c} == {actual}")
        return
    if not UPDATE:
        bad(f"{label}: README {c}, actual {actual}")
        return
    m = re.search(pattern, README)
    was = m.group(1)
    now = fmt.format(actual) if "," in was else str(actual)
    rewrites.append((label, was, now,
                     m.group(0).replace(was, now), m.group(0)))
    ok(f"{label}: {c} -> {actual} (rewritten)")

# Layer totals
for files, pat, label in [
    (LAYER1, r"transforms secret material\. ([\d,]+) lines", "layer 1 total"),
    (LAYER2, r"sees secrets, computes nothing with them\. ([\d,]+) lines", "layer 2 total"),
    (LAYER3, r"never touches secrets at all\. ([\d,]+) lines", "layer 3 total"),
]:
    actual = sum(code_lines(ROOT / f) for f in files)
    pin(pat, label, actual)

# Per-file counts were dropped from the README on 2026-09-07: seven
# numbers that had to be corrected on almost every commit and that no
# reader was checking. The three LAYER TOTALS stay, because "layer 1 is
# zero lines" is the security claim this project rests on, and a total
# that drifts is the claim quietly becoming false. This test's job is to
# keep stated claims true, not to force claims to be stated.

# Total functional
# The raw total was unpinned until 2026-09-02 and had drifted by 95 lines
# while the functional total stayed exact (TESTING.md rule 4).
raw = sum(len((ROOT / f).read_text().splitlines())
          for f in LAYER1 + LAYER2 + LAYER3)
pin(r"\(([\d,]+) with blanks/comments\)", "raw total", raw)

total = sum(code_lines(ROOT / f) for f in LAYER1 + LAYER2 + LAYER3)
pin(r"\*\*Total functional code: ([\d,]+) lines\*\*", "total functional",
    total)

# Test lines
tests = sorted((ROOT / "tests").glob("*.py"))
tl = sum(code_lines(f) for f in tests)
pin(r"\*\*Test code: ([\d,]+) lines", "test code", tl)

# Vendored lines (total incl. comments, as the README states them)
vend = sum(len(f.read_text().splitlines())
           for f in (ROOT / "hw" / "vendor").rglob("*.py"))
pin(r"Vendored, not ours: ([\d,]+) lines", "vendored", vend)

# The same four figures again, in the audit paragraph at the top of "What
# you are trusting". 2026-10-16: that paragraph is new, and the sentence
# beside it had said 2,407 lines since before the multisig work. It was
# 712 lines stale, and nothing failed, because the only pinned totals
# were the ones further down the page. A number a reader uses to decide
# how much code to read is the last one that should be allowed to drift.
pin(r"What an auditor reads: ([\d,]+) lines, in one directory",
    "auditor's total", total)
pin(r"and Core Signer's ([\d,]+) lines do not", "lines-not-read", total)
pin(r"holds ([\d,]+) lines that are not ours either", "vendored, again",
    vend)
pin(r"holds ([\d,]+) lines that never ship", "test code, again", tl)

# Every shipped file must be NAMED on the page, not just counted in it.
# hal.py and splash.py were in LAYER2's total and in no list a reader
# could see, so the page described six files as four and an auditor
# reading only the README would have missed 124 lines that ship.
shipped_files = sorted((ROOT / "coresigner").glob("*.py"))
unnamed = [f.name for f in shipped_files if f.name not in README]
if unnamed:
    bad(f"{len(unnamed)} shipped file(s) the README never names: "
        + ", ".join(unnamed))
else:
    ok(f"the README names all {len(shipped_files)} files in coresigner/")

# ...and the split between what runs and what does not. The README said
# "every one of these lines runs on the device" while 383 of them were a
# driver nothing imports (two-axis review, 2026-09-08). A count nobody
# recomputes goes stale in one direction only: the flattering one.
#
# A vendored top-level name counts as reached when coresigner/ imports it. ur2
# is a package, so every file in it rides on the one import. That rule is
# coarse, and it is coarse in the safe direction: it can call a file
# reached that is not, never the reverse, so the README can only ever
# understate what is dead.
VEND = ROOT / "hw" / "vendor"
coresigner_src = "\n".join(f.read_text() for f in sorted((ROOT / "coresigner").glob("*.py")))
imported = {n for n in (p.name for p in VEND.iterdir())
            if re.search(rf"^\s*(from|import) {re.escape(n.removesuffix('.py'))}\b",
                         coresigner_src, re.M)}
dead = [f for f in sorted(VEND.rglob("*.py"))
        if f.relative_to(VEND).parts[0] not in imported]
live = vend - sum(len(f.read_text().splitlines()) for f in dead)
# The rule is stronger than a count now: NOTHING vendored may be
# unreachable. The README used to state how many of the lines ran and how
# many did not, which made carrying dead third-party code a thing you
# could declare and then keep. Core Signer vendors only what it runs, so an
# unreachable vendored file is a defect and not a footnote (2026-09-08,
# when SeedSigner's second display driver went: 383 lines, imported by
# nothing, kept for a 2.4" board Core Signer does not ship).
if dead:
    bad(f"{len(dead)} vendored file(s) that no shipped module imports: "
        f"{[str(f.relative_to(ROOT)) for f in dead]}. Core Signer vendors only "
        f"what it runs; delete them or import them.")
elif live != vend:
    bad(f"the reachability sum does not add up: {live} live of {vend}")
else:
    ok(f"all {vend} vendored lines are reachable from a shipped module")

# The README calls the drivers modified and the codec unmodified. That
# split is a claim about what a reader must audit, and nothing checked it
# against the files (two-axis review, 2026-09-08). It cannot be checked
# against upstream from here, and it does not need to be: detecting a
# change to a vendored file is test_vendor_pinned.py's job. What is
# checked here is that the README and the tree tell the same story.
drivers = sorted(VEND.glob("*.py"))
undeclared = [f.name for f in drivers if "Modified:" not in f.read_text()[:400]]
if undeclared:
    bad(f"{undeclared} carry no 'Modified:' line, but the README calls "
        "SeedSigner's two display drivers modified")
else:
    ok(f"all {len(drivers)} display drivers say how they were modified")
vendored_md = VEND / "ur2" / "VENDORED.md"
if not vendored_md.exists():
    bad("hw/vendor/ur2/VENDORED.md is gone; the codec has no provenance")
elif "Unmodified" not in vendored_md.read_text():
    bad("VENDORED.md no longer says the BC-UR codec is unmodified, but "
        "the README does")
else:
    ok("the BC-UR codec's provenance says unmodified, as the README does")


def table_cell(source_word):
    """The first cell of the dependency row whose source cell starts with
    `source_word`.

    Scoped to the ROW on purpose. The first version of this check asked
    whether the whole README contained the string "pip", which the pip
    row's own source cell satisfies, so a missing apt package passed.
    """
    for line in README.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 2 and cells[1].startswith(source_word):
            return cells[0]
    return None


def packages_row():
    """The dependency row that LISTS the six packages.

    Found by a package name and not by the source cell, because the
    source stopped being the word "apt" on 2026-09-23 when they moved to
    pinned files, and a check keyed on how they arrive goes blind the
    next time that changes. What it has to find is the row a reader
    audits the package list on.
    """
    for line in README.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 2 and "picamera2" in cells[0]:
            return cells[0]
    return None


# What provision.sh really installs, against the table on this page. The
# table named five packages and provision.sh asked for six.
prov = (ROOT / "image" / "provision.sh").read_text()
# From the PINS block, not from provision.sh: since 2026-09-23 the six
# are installed from pinned files and provision.sh names no package at
# all. The pins are the list now, and a package added there without a
# line on the README still fails this.
pins_text = (ROOT / "image" / "PINS").read_text()
m = re.search(r'PINNED_DEBS="\n(.*?)\n"', pins_text, re.S)
apt_cell = packages_row()
if not m:
    bad("image/PINS no longer defines PINNED_DEBS, so this check is blind")
elif apt_cell is None:
    bad("no row in the README's dependency table lists the packages")
else:
    # apt package name -> the word the README uses for it. A new package
    # with no entry fails, which is the point: it must be named on the
    # page a reader audits, or deliberately excused here.
    APT_NAMES = {"python3-pil": "Pillow", "python3-rpi.gpio": "RPi.GPIO",
                 "python3-spidev": "spidev", "python3-picamera2": "picamera2",
                 # libzbar0 is a transitional package; the real one on
                 # Debian 13 is libzbar0t64, which is what apt resolves
                 # to and therefore what is pinned.
                 "libzbar0t64": "libzbar0", "python3-pip": "pip"}
    pkgs = sorted({line.split("/")[-1].split("_")[0]
                   for line in m.group(1).splitlines() if line.strip()})
    unknown = [q for q in pkgs if q not in APT_NAMES]
    absent = [APT_NAMES[q] for q in pkgs
              if q in APT_NAMES and APT_NAMES[q] not in apt_cell]
    if unknown:
        bad(f"image/PINS pins {unknown} which this check has never "
            "heard of; name them in the README and add them here")
    elif absent:
        bad(f"image/PINS pins {absent}, which the README's apt row "
            f"does not mention: {apt_cell!r}")
    else:
        ok(f"the README's apt row names all {len(pkgs)} packages "
           "image/PINS pins")

# ...and the pip row against requirements.txt, by name and by count.
reqs = sorted(ln.split("==")[0] for ln in
              (ROOT / "image" / "requirements.txt").read_text().splitlines()
              if ln[:1].isalpha())
pip_cell = table_cell("pip")
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
if pip_cell is None:
    bad("the README has no pip row in the dependency table")
else:
    named = [r for r in reqs if r in pip_cell]
    m2 = re.search(r"their (\w+) dependencies", pip_cell)
    rest = WORDS.get(m2.group(1)) if m2 else None
    if rest is None:
        bad(f"the pip row does not say how many unnamed dependencies come "
            f"with it: {pip_cell!r}")
    elif len(named) + rest != len(reqs):
        bad(f"the README's pip row names {named} plus {rest} more, which "
            f"is {len(named) + rest}; requirements.txt pins {len(reqs)}: "
            f"{reqs}")
    else:
        ok(f"the README's pip row accounts for all {len(reqs)} pins in "
           "requirements.txt")

# Every file the README links must exist
broken = [link for link in set(re.findall(r"\]\((?!http)([^)#]+)\)", README))
          if not (ROOT / link).exists()]
for link in broken:
    bad(f"README links a missing path: {link}")
if not broken:
    ok("every relative README link resolves")

# Prose figures the README asserts about the test campaign.
# Count the session markers themselves. The old rule matched only
# `print("ok   X:` with a single-letter label, so it silently ignored
# sessions named D3, H3/H4, R3 or T2 and undercounted by a third.
# Across EVERY suite that holds sessions, not one named file. The marker
# was made drift-proof and the search was not: e2e_keys.py was added with
# eleven more sessions and this counter never saw one of them, so the
# README said 9 while the tree held 20 (audit A6, 2026-09-06). That is
# the same defect as the label pattern, one level up.
sess = sum(len(re.findall(r"^\s*# ---- Session ", p.read_text(), re.M))
           for p in sorted((ROOT / "tests").glob("*.py")))
pin(r"([\d]+) scripted device sessions", "device sessions", sess)
# The attacks, counted from the one marker that cannot drift: their own
# def line. The previous pattern looked for "# 1." or "ATTACK" headings
# that no longer exist, matched exactly one thing, and reported the
# mismatch as "informational", so it could never fail. A metric nobody
# can fail is decoration (TESTING.md rule 4).
adv = (ROOT / "tests" / "test_adversarial.py").read_text()
n_attacks = len(re.findall(r"^def attack_", adv, re.M))
pin(r"([\d]+) adversarial attack scenarios", "adversarial attacks",
    n_attacks)
# The warning at the top of the README is the first thing anybody reads,
# and it counts gates. A count in prose goes stale the moment a gate
# passes, and this one goes stale in the flattering direction: nobody
# edits a warning down to be more alarming (2026-09-08).
#
# The gate table is the source. A row is measured when its last column
# does not begin "no".
gates = re.findall(r"^\| (M\d) \|[^|]+\|([^|]*)\|\s*$", README, re.M)
if len(gates) < 3:
    bad(f"the gate table no longer parses; found {len(gates)} rows, so the "
        "warning at the top of the page is unchecked")
else:
    measured = [g for g, state in gates if not state.strip().startswith("no")]
    unproven = [g for g, state in gates if state.strip().startswith("no")]
    WORDS = {"none": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
    m = re.search(r"(\w+) of the five gates below are measured", README)
    said = WORDS.get(m.group(1).lower()) if m else None
    if said is None:
        bad("the top warning no longer states how many gates are measured")
    elif said != len(measured):
        bad(f"the top warning says {m.group(1)} of five gates are measured; "
            f"the table says {len(measured)}: {measured} measured, "
            f"{unproven} not")
    elif len(gates) != 5:
        bad(f"the warning says five gates and the table has {len(gates)}")
    else:
        ok(f"the top warning and the gate table agree: {len(measured)} "
           f"measured ({', '.join(measured)}), "
           f"{len(unproven)} not ({', '.join(unproven)})")

# The backup section makes four checkable claims. It got the ARGUMENT
# wrong for a long time (it attacked seed phrases for losing the
# derivation path, which Core Signer's own xprv backup loses in exactly the
# same way), so the facts under the new argument are pinned rather than
# trusted (2026-09-09).
sys.path.insert(0, str(ROOT / "coresigner"))
import screens                                          # noqa: E402
import signer as _signer                                # noqa: E402

# 1. the four purposes the page lists are the four the code rebuilds
listed = {int(m) for m in re.findall(r"`m/(\d+)'/0'/0'`", README)}
built = {purpose for purpose, _shape in _signer.PURPOSE_FUNCS}
if listed != built:
    bad(f"the README lists derivation purposes {sorted(listed)} and "
        f"signer.PURPOSE_FUNCS rebuilds {sorted(built)}")
else:
    ok(f"the README's {len(listed)} derivation paths are the ones "
       "build_descriptors actually rebuilds")

# 2. the account really is hardcoded, which is the stated edge
_src = (ROOT / "coresigner" / "signer.py").read_text()
if not re.search(r'f"\{xprv\}/\{purpose\}h/\{coin\}h/0h/', _src):
    bad("build_descriptors no longer hardcodes account 0h, so the "
        "README's stated recovery edge is wrong")
elif "hardcoded" not in README and "hardcode" not in README:
    bad("the README stopped saying the account is hardcoded, which is "
        "the one thing a paper backup cannot recover from")
else:
    ok("the account is hardcoded 0h and the README says so")

# 3. the backup really is that long
c = claimed(r"(\d+) characters, on three screens", "backup length")
KEY = ("tprv8ZgxMBicQKsPe5YMU9gHen4Ez3ApihUfykaqUorj9t6FDqy3nP6eoXiAo2ssv"
       "pAjoLroQxHqr3R5nE3a5dU3DHTjTgJDd7zrbniJr6nrCzd")
if c is not None:
    if c != len(KEY):
        bad(f"the README says the backup is {c} characters; a real master "
            f"key is {len(KEY)}")
    elif len(screens.text_pages(KEY)) != 3:
        bad(f"the backup is {len(screens.text_pages(KEY))} screens, "
            "not the three the README describes")
    else:
        ok(f"the backup is {c} characters across three screens")

# Every file and every module.symbol the documents NAME must exist.
#
# Audit A8 (2026-09-06) found the security argument citing
# `radio-check.sh` as the proof that "the OS is silent". No such file has
# ever existed; the tool is `leak-check.sh`, named correctly eleven lines
# above. A table of claims whose evidence column points at nothing is the
# exact mechanism this project has already been bitten by twice, so it is
# mechanical from here.
#
# PLAN.md is a dated LOG of amendments and a forward spec, so it is the
# one document that legitimately names things that do not exist: what an
# amendment deleted, and what a post-v1 note proposes. It may do that,
# but it has to SAY so within three lines, in one of the words below. An
# entry that quietly names a deleted module reads as current, which is
# how the shim looked alive for two days after A-22 removed it.
import ast                                            # noqa: E402

# ISSUES.md joined this list on 2026-09-07: it named a path into a map
# that had just been archived, and nothing noticed.
DOCS = ["README.md", "TESTING.md", "CONTEXT.md", "hw/HARDWARE.md",
        "ISSUES.md"]
SHIPPED = ("signer", "screens", "hal", "qrchannel", "filechannel",
           "main", "splash")

defined = set()
for py in sorted(ROOT.rglob("*.py")):
    if any(part in (".git", "vendor", ".build") for part in py.parts):
        continue
    try:
        tree = ast.parse(py.read_text())
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id)
        elif isinstance(node, ast.Attribute):
            defined.add(node.attr)

EXCUSED = ("superseded", "post-v1", "not written", "deleted", "removed")
SYM = re.compile(r"`([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)`")
# Markdown was NOT in this list until 2026-09-07, so six dead .md paths
# sat green across PLAN.md, ISSUES.md, m0_gate.py, harden.sh and
# leak-check.sh when the closed maps were archived. A path is a path
# whatever it points at.
FILE = re.compile(
    r"`([\w./-]+\.(?:py|sh|md|service|rules|conf|json|txt|toml|dat))`")
ghost_paths, ghost_syms = [], []
for doc in DOCS + ["PLAN.md"]:
    lines = (ROOT / doc).read_text().splitlines()
    for n, line in enumerate(lines, 1):
        window = " ".join(lines[n - 1:n + 2]).lower()
        if doc == "PLAN.md" and any(w in window for w in EXCUSED):
            continue
        # A line that names SeedSigner is citing THEIR source, not ours.
        # Core Signer vendors their drivers and steals their ideas, and a
        # reader has to be able to tell which tree a path lives in.
        #
        # 2026-10-16: the same rule for Bitcoin Core's own tree. The
        # README now cites `doc/JSON-RPC-interface.md` on bitcoin/bitcoin
        # for the rule that Core's other programs have to be trusted, and
        # that path is real in Core and absent here. The exemption needs
        # the line to carry the repository name, so a bare path still
        # fails and a cited one does not.
        external = ("SeedSigner" in line or "seedsigner" in line
                    or "bitcoin/bitcoin" in line)
        for m in FILE.finditer(line):
            named = m.group(1)
            if external:
                continue
            if not (ROOT / named).exists() and \
                    not any(ROOT.rglob(named.split("/")[-1])):
                ghost_paths.append(f"{doc}:{n} {named}")
        if doc == "PLAN.md":
            continue                     # a log may name what it removed
        for m in SYM.finditer(line):
            mod, _, attr = m.group(1).rpartition(".")
            if mod in SHIPPED and attr not in defined:
                ghost_syms.append(f"{doc}:{n} {m.group(1)}")

# config.txt is the Pi's boot file on the card, not a file in this repo.
ghost_paths = [g for g in ghost_paths if "config.txt" not in g]
if ghost_paths:
    bad(f"documents name {len(ghost_paths)} file(s) that do not exist: "
        + "; ".join(ghost_paths))
else:
    ok("every file the documents name exists")
# The same rule for paths named in CODE. Three of the six dead paths
# found on 2026-09-07 were in comments and docstrings, not in documents:
# m0/m0_gate.py, image/harden.sh and image/leak-check.sh all pointed at a
# map that had been archived. A comment that names a file is a promise
# about the tree exactly like a document's is.
CODE = [f for f in sorted(ROOT.rglob("*.py")) + sorted(ROOT.rglob("*.sh"))
        if not any(part in {".git", "vendor", ".build", "tmp"}
                   for part in f.relative_to(ROOT).parts)]
IN_CODE = re.compile(
    r"(?<![\w/.-])((?:docs|tests|image|coresigner|hw|m0|tools)"
    r"/[\w./@-]+\.(?:py|sh|md|service|rules|conf|json|txt|toml|dat))")
# A line that says a path is GONE is allowed to name it: test_integrity
# asserts exactly that, file by file, and it is the check that keeps
# Layer 1 empty. Same excusing words the PLAN uses.
ABSENT_WORDS = ("absent", "gone", "removed", "deleted", "no longer",
                "used to", "went with", "must not exist")
ghost_code = []
for f in CODE:
    rel = f.relative_to(ROOT)
    lines = f.read_text().splitlines()
    for n, line in enumerate(lines, 1):
        window = " ".join(lines[max(0, n - 3):n + 2]).lower()
        if any(w in window for w in ABSENT_WORDS):
            continue
        for m in IN_CODE.finditer(line):
            named = m.group(1)
            if not (ROOT / named).exists():
                ghost_code.append(f"{rel}:{n} {named}")
if ghost_code:
    bad(f"code names {len(ghost_code)} path(s) that do not exist: "
        + "; ".join(ghost_code[:6]))
else:
    ok(f"every path named in {len(CODE)} code files exists")

if ghost_syms:
    bad(f"documents name {len(ghost_syms)} symbol(s) that do not exist: "
        + "; ".join(ghost_syms))
else:
    ok("every module.symbol the documents name exists")

# The icon count, in the three places it is written. It was six in the
# README, seven in the README's own supply-chain table two dozen lines
# later, and seven in the font's NOTICE. The sign tile changed from a QR
# to a signature on 2026-09-05 and one of the three was not updated
# (audit A8, 2026-09-06).
sys.path.insert(0, str(ROOT / "coresigner"))
import screens                                        # noqa: E402
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
         "seven": 7, "eight": 8, "nine": 9, "ten": 10}
real_icons = len(screens.ICON)
said = []
for doc in ("README.md", "hw/vendor/fonts/NOTICE.md"):
    for n, line in enumerate((ROOT / doc).read_text().splitlines(), 1):
        for m in re.finditer(r"\b(\w+)[- ]glyph\b", line):
            word = m.group(1).lower()
            if word in WORDS:
                said.append((f"{doc}:{n}", WORDS[word]))
        for m in re.finditer(r"\b(\w+) home and settings glyphs\b", line):
            word = m.group(1).lower()
            if word in WORDS:
                said.append((f"{doc}:{n}", WORDS[word]))
wrong = [w for w in said if w[1] != real_icons]
if not said:
    bad("no document states the icon count any more; the check is blind")
elif wrong:
    bad(f"screens.ICON holds {real_icons} glyphs, documents say "
        + ", ".join(f"{n} at {w}" for w, n in wrong))
else:
    ok(f"all {len(said)} statements of the icon count say {real_icons}")

if rewrites:
    # Written once, at the end, and only if every OTHER check passed.
    # A --update run that also broke the apt table or a link should not
    # quietly hand back a README that now reads as correct.
    if fails:
        print("\n--update made no change: the run has failures that "
              "rewriting a number cannot fix.")
    else:
        text = (ROOT / "README.md").read_text()
        for label, _was, _now, new_span, old_span in rewrites:
            if text.count(old_span) != 1:
                bad(f"--update found {text.count(old_span)} places to "
                    f"rewrite {label}; refusing to guess")
                break
            text = text.replace(old_span, new_span, 1)
        else:
            (ROOT / "README.md").write_text(text)
            print(f"\nupdated {len(rewrites)} number(s) in README.md: "
                  + ", ".join(f"{lbl} {was}->{now}"
                              for lbl, was, now, _, _ in rewrites))

# --- the tester pack states numbers the CODE owns -----------------------
# A pack that rots is worse than no pack: it is a limit a person was told
# and then relied on. These two come straight out of main.py, one of them
# measured on the board on 2026-09-11.
PACK = (ROOT / "docs" / "TESTER-PACK.md")
if not PACK.exists():
    bad("docs/TESTER-PACK.md is missing; it is what a tester is handed")
else:
    pack = PACK.read_text()
    main_py = (ROOT / "coresigner" / "main.py").read_text()
    for const, label in (("MAX_SIGNABLE_INPUTS", "ordinary payments"),
                         ("MAX_SIGNABLE_MULTISIG_INPUTS", "multisig")):
        m = re.search(rf"^{const} = (\d+)$", main_py, re.M)
        if not m:
            bad(f"{const} is gone from main.py; the pack still quotes it")
            continue
        want = m.group(1)
        row = re.search(rf"\| {label} \| \*\*(\d+) inputs\*\*", pack)
        if not row:
            bad(f"the tester pack no longer states the {label} ceiling")
        elif row.group(1) != want:
            bad(f"tester pack says {label} is {row.group(1)} inputs; "
                f"{const} is {want}. A tester was told a limit that moved")
        else:
            ok(f"the tester pack's {label} ceiling matches {const} ({want})")

if fails:
    print("\n" + "\n".join(fails))
    sys.exit(1)
print("\nREADME CLAIMS PASS")
