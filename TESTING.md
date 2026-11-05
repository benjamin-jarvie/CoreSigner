# How Core Signer is tested, and the rules that came from being wrong

`./run_tests.sh` runs the fast suites. `RUN_NODE=1 ./run_tests.sh` adds the
suites that need a real `bitcoind`. Both must be green before a commit.

This file exists because on 2026-09-02 a two-axis review found a feature that
had shipped in a state where it could not work, past a full green suite. The
rules below are what that failure taught. They are not style preferences.

## Rule 1: every input surface needs a real-data round-trip test

**A test that renders a screen proves nothing about whether the screen can
express real data.**

Typed key entry (audit item S3) shipped with a 64-cell character grid that
omitted base58's `B` and omitted `(`, `)` and `*`. About 85% of real xprvs
contain a `B`, and every descriptor needs brackets, so neither typed mode
could ever have worked. The screen rendered correctly, fit both panels, and
passed every suite, because no test ever asked it to type an actual key.

The paths that did NOT have this bug are the ones that already followed
this rule. Both were codex32 and BIP39 entry, and PLAN A-22 has since
deleted both screens, so the example survives only as the reason for the
rule and not as code you can go and read.

So, for any surface that accepts input:

1. Take **real** domain data. Ask Bitcoin Core for it where possible
   (`listdescriptors`, a derived xprv, a funded PSBT), rather than writing a
   plausible-looking literal. A literal encodes the author's assumptions,
   which are exactly what is under test.
2. Compute the key sequence programmatically from the navigation rules.
   `text_keys` in `tests/e2e_keys.py` is the live one; `keys_press`,
   `key_menu_press`, `home_press` and `tools_press` beside it do the same
   for the menus, which is rule 11's half of this.
3. Assert the device reconstructs the input **exactly**, and where the input
   is key material, that it opens a key which signs a real transaction.

Session T in `tests/e2e_session.py` is the reference shape: a real xprv,
typed on the real grid, opening a key that signs.

## Rule 2: the helper must not share the code's assumptions

A key-sequence helper written from the same mental model as the navigation
loop will agree with it and still be wrong. When `text_keys` and
`_text_entry` were both written by the same author in the same hour, they
desynchronised the moment paging entered the picture, and only a round-trip
assertion (`got == want`) exposed it.

Assert the round trip, never the key count.

## Rule 3: test the path that ships, not the path that is convenient

`_show_qr_loop` guards its paced, repeating animation behind
`if not self.animate:`. Every scripted session takes the one-pass dev branch,
so for the whole of its life the loop that actually reaches a coordinator's
scanner had never executed in a test.

If a branch exists only for the device, a test must set the flag and run it.
`tests/test_qr_out.py` does this with a threaded display and a blocking
button source.

## Rule 4: a metric that counts wrongly is worse than no metric

`test_readme_claims` counted scripted device sessions with a pattern that
matched only single-letter labels, so it ignored the multi-character ones
and undercounted by a third while reporting a confident number. Prefer
counting a marker that cannot drift (`# ---- Session `) over inferring
from prose.

**It then failed the same way a second time.** The marker was made
drift-proof and the SEARCH was not: it read `e2e_session.py` and no other
file, so the eleven sessions in `e2e_keys.py` were invisible from the day
that file was created. The README said 9. There were 21 (audit A6,
2026-09-06). Counting the right thing in one of the two places it lives
is still counting wrongly.

Audit A5 found the same failure in the coverage instrument, twice, and the
instrument is the thing you would otherwise trust to find the others:

- **A subprocess is invisible by default.** Most of Core Signer runs as
  `python3 coresigner/main.py --dev` under `subprocess.run`, and a plain
  `coverage run` sees none of it. Without the `COVERAGE_PROCESS_START`
  hook the figure is about twenty points low.
- **A clean environment drops the hook.** `test_splash.py` builds its
  child's env from scratch, on purpose, so that check 1 proves splash
  needs almost nothing. That also dropped `COVERAGE_PROCESS_START`, so the
  report called the first program the device runs dead code while the
  suite was running it end to end.
- **An architecture the suite cannot run is not an architecture without
  code.** `libzbar` on the dev Mac is x86_64 only, so `pyzbar` will not
  load under arm64 and the entire QR decode path looks unexecuted. It is
  executed, by `tests/m1`, under Rosetta.

`tools/coverage_run.sh` handles all three and prints one number, so it can
be reproduced. `tools/coverage_piles.py` sorts what is left into A5's
three piles and **exits non-zero when a line falls in no pile**, because a
classification with a hole in it is an opinion.

It also exits non-zero when a pile holds no line, which is the same rule
read backwards and was missing until 2026-09-08. The classifier pinned
LINE numbers, and by the time a reviewer read it two of them had slid:
one onto a comment, one onto a blank line. A pinned line that has slid
claims nothing, quietly, and the totals shrink with no alarm. **A pin
that can only lose its subject in silence is not a pin.** It keys on
FUNCTION names now, which survive every edit inside the function, and
`run_tests.sh` checks on every run that each name still exists.

`tests/test_readme_claims.py --update` rewrites the numbers it computes
instead of reporting them, because four of them move on any commit that
touches a line of code or a line of test, and hand-editing four figures
per commit is how a suite starts being run with one check already
known-red. It refuses to write anything if any OTHER check failed, so it
can correct a figure and can never make a broken claim pass.

## Rule 5: run the two-axis review, because the suite cannot find these

The suite is written by whoever wrote the code, so it inherits their blind
spots. Two of the three most serious defects found in the 2026-09-02 review
were invisible to a green suite:

- a charset that could not express its own domain (Spec axis);
- a backup tool that used a passphrase it never asked for, and so could
  encode a backup for a different wallet than the words open (Standards
  axis).

Run `/mp-code-review <fixed-point>` before each milestone gate and after any
change to an input path, a screen's meaning, or `signer.py`. It runs two
independent sub-agents:

- **Standards** — does the diff obey the repo's documented laws (PLAN A-11's
  opaque-bytes rule, the three-layer model, the no-device-RNG doctrine) plus
  the Fowler smell baseline?
- **Spec** — does the diff implement what the originating audit item or PLAN
  amendment actually asked for, and nothing more?

Report the two axes separately. Merging or reranking them lets one mask the
other, which is the whole reason they are separate.

**Verify the findings before acting on them.** In this project, three
separate claims from automated reviewers did not survive checking: a "17/17
suites pass" that matched no version of the repo, a set of fixes reported as
landed that were never in the tree, and an S5 "bug" that was correct code.
Reproduce a finding against the source before you change anything for it.

## Rule 6: a cost claim must come from a measurement, not an estimate

When the letter grid replaced the dial (audit D4) the design note claimed
"about 180 presses for a 24-word seed". The measured figure is **352**,
against the dial's 546. The estimate was never wrong on the direction, only
on the size, and it sat in a docstring as if it were a fact.

`tests/test_ui_cost.py` measures entry cost by replaying the real navigation
rules, and fails if the cost drifts. When a UI changes, that model must
change with it: after D4 it still modelled the dial for a while, so it
reported a confident 546 for a device that no longer worked that way.

## Rule 7: "needs hardware" is a claim, and it needs checking

I-1 (a cropped QR) and I-2 (POWER OFF that did not power off) were
deferred with "wait for M1, neither can be proven without hardware".
Neither needed hardware. I-1 is panel geometry, provable with two integers.
I-2 is a teardown sequence where only the final `systemctl poweroff` touches
the board, and that one call fakes cleanly.

Before you write "no test without hardware", name the exact line that needs
the board. If the answer is one syscall, fake that syscall and test the
rest. Fake it as the device would fail, not as the fake is convenient: the
first version of the I-2 test faked a missing `systemctl` as exit code 1,
so it passed over a `FileNotFoundError` that made the whole fallback dead
code.

## Rule 8: test against the other implementation's decoder, not your own

Every QR test Core Signer had decoded with `pyzbar`, because that is what the device
runs. That felt right and it hid a defect for as long as it existed.

Core Signer renders 244-character UR frames as a 49x49 QR. With the quiet zone that
is 53 modules, and the 320x240 panel allows `box_size = 240 // 53 = 4`. So
Core Signer renders at exactly **4.0 pixels per module** and cannot go higher without
fewer modules. Measured over 375 frames, three of them (0.8%) cannot be decoded
by **zxing**, which is the library Sparrow uses. `pyzbar` reads the same three
without trouble.

It was not intermittent. The same image failed five attempts out of five. And
`psbt_to_frames` emitted one pure cycle which the display looped, so the
scanner saw the identical unreadable image forever. At 13 to 21 frames per
PSBT, roughly one transfer in seven could never complete.

**Re-measured 2026-09-06: 8 of 750 frames, 1.1%.** Same order, and it
matters more than it did: on the pocket build the card is the boot device
and USB needs an adapter and a hole in the case, so QR is the only channel
that closes a signing loop. Run `tests/m1/outbound_margin.py` after
anything that touches frame size.

No suite could have found this, because every suite asked Core Signer's own decoder.
The fix is `tests/sparrow`, which runs Sparrow 2.5.4's real library out of the
sha256-verified release, and `tests/m1/outbound_margin.py`, which keeps
measuring the rate so the day it gets worse is a day somebody notices.

The rule: **an interop claim tested with your own tools is not an interop
claim.** Where a counterpart is named, run the counterpart.

The same pass produced Rule 8's twin, which is cheaper to state. When a test
starts failing one run in five, find out why before making it pass. Adding a
retry pass here would have gone green and buried the defect; it recovered
exactly zero frames, and that zero is what exposed the cause.

## Rule 9: some defects are invisible on the dev machine, by construction

Rule 7 says "needs hardware" is a claim that needs checking, and it stands.
This is its opposite number: sometimes the hardware really is the only place
a defect can appear, and no amount of care on the Mac will surface it.

`coresigner/signer.py` passed the base64 PSBT to `bitcoin-cli` as one argv entry.
Linux caps a **single** argument at `MAX_ARG_STRLEN`, 32 pages, 128KB, and
that cap is separate from the 2MB `ARG_MAX` total. A PSBT carries a whole
previous transaction per input, so the M0 stress case at 250 inputs went past
it and `execve` returned `E2BIG`.

**macOS applies no per-argument cap at all.** The 250-input path was tested,
repeatedly, and passed every time. It even produced the "RSS about 99MB"
reference figure that `m0/FLASH.md` quoted for a fortnight. The test was not
missing and it was not weak. The dev machine cannot fail this way.

What catches this class is not a better test on the Mac. It is running the
real thing on the real target early, which is what M0 exists to do. The gate
paid for itself the first time it ran.

What a test on the Mac **can** do is stop the fix regressing. `FakeRpc` in
`tests/test_property.py` asserts `stdin=True` on any PSBT-carrying method
rather than merely accepting the argument, so the rule is enforced where the
limit is unreachable.

The rule: **when a limit belongs to the target's kernel, the dev machine is
not evidence.** Name the limit, encode it as an assertion, and get on real
hardware sooner.

## Rule 12: a flake is a defect that has not been read yet

`tests/sparrow` failed twice, months apart, and passed on every retry.
Both times the runner sent its output to `/dev/null`, so the failure was
a name and nothing else, and there was nothing to do but shrug.

The cause was one line: `self.port = random.randint(20000, 60000)`, with
nothing checking whether anything was already listening there. On a
collision bitcoind exited at once, the wait loop spun the full forty
seconds, and the suite died somewhere else entirely with no mention of a
port. The kernel will hand out a free port if asked, and the wait loop
now notices a daemon that has already exited instead of waiting out the
clock on a corpse.

Two rules came out of it, and Rule 8's twin already said the first:

- **Keep the output.** A suite whose failure prints only its own name
  cannot be diagnosed, and the next person to see it will retry and move
  on, exactly as happened here twice.
- **Randomness in a harness needs a check.** A random port, a random
  temp name or a random key is a collision waiting for a busy machine.
  Ask the operating system instead, and where the window cannot be
  closed, make the remaining case say so.

## Rule 11: a menu is two lists, and nothing joins them

`screens.py` holds the labels a menu draws. `main.py` holds the
`if selected == N` cascade that runs them. Nothing connected the two, so
they drifted: the BACKUP menu drew "On paper" as row 0 and ran the FILE
backup for it. Choosing paper asked for an encryption passphrase, which is
the "back button does nothing on backup to paper" Ben reported off the
board on 2026-09-05.

Every suite was green through all of it. The screen test rendered the right
labels. The end-to-end session made a real paper backup and checked the
three xprv pages, because its script had been written by counting presses
against the code rather than against the screen, so it pressed DOWN then A
and called that "On paper (2nd)". The comment even said so. Both halves
agreed with the bug.

`tests/test_menu_wiring.py` drives the real `_pick` loop with a scripted
keypad and asserts, for every row of every menu, that the handler which
runs is the one the label names. It needs no bitcoind and no panel. It
takes under a second, and it fails on the exact defect above.

The rule: **assert the label, not the index.** A scripted session that
counts presses proves the code agrees with itself. Where a list screen
dispatches, one test must read the row's words and name the function it is
supposed to call.

## Rule 12a: a mutation run must clear `__pycache__`

Mutation testing is how every fix in this repo is accepted: break the
code on purpose, and the check that was supposed to catch it must fail.
That depends on the edit actually reaching the interpreter.

CPython caches by source size and mtime. A mutation script that writes a
mutant, runs a suite, and writes the original back can produce a `.pyc`
the interpreter then reuses for the ORIGINAL file, because the restored
source is the same length and the mtime moved within the same coarse
tick. On 2026-09-10 that left `coresigner/main.py` executing a mutant for
three suite runs after the script printed "restored", and
`test_menu_wiring.py` failed with the mutation's exact symptoms against
a source file that was correct on disk.

It reads as a flake, which rule 12 says is a defect not yet read. The
danger is the other direction: the same staleness can run the ORIGINAL
against a mutant's result and report a mutation as CAUGHT that nothing
caught.

**Clear `__pycache__` before trusting a mutation run**, and before
believing any failure whose source looks right:

    find . -name __pycache__ -type d -not -path "*/.build/*" -exec rm -rf {} +

**And judge the run by its EXIT CODE, not by counting "FAIL" lines.** A
mutation that makes a suite CRASH prints no FAIL line at all, and a
runner that greps for one calls that "survived". On 2026-09-11 three
mutations were reported as survivors that way; one had killed the suite
outright. A survivor you did not earn is worse than a missing test,
because it is written down as proof.

The predicate is `returncode != 0 or a FAIL line`, and a survivor is
worth a second look before it is believed.

## What is still thin

`ISSUES.md` is the open list and nothing else since audit A9: what is
fixed lives in git, and what it taught lives here as a numbered rule.
D17 and D18 are both closed. The standing milestone work (M1 to M3)
genuinely does need the board; M0 passed on 2026-09-03.

Coverage across both architectures is 86% of statements in `coresigner/`; the
arm64 suites alone report 84%. One statement is unreachable in any
configuration, `main.py:113`, and it is a deliberate abstract-method
guard. See `docs/wayfinder/beta-audit/tickets/A5-never-run.md`.

`tests/sparrow` runs from `run_tests.sh` whenever its one-time `setup.sh`
has built it, and the run says so when it has not (since 2026-09-05).
`tests/m1` still needs Rosetta on Apple Silicon and its own `setup.sh`;
run `tests/m1/run test_scan_loop.py` by hand after a change to the QR
channel, and remember that it pins the camera-source contract too: it is
where a stale assertion about `scan_key` survived ticket 09 for a day.

## Rule 10: a tool that does not ship is not a dependency of the signer

`ruff`, `vulture` and `mypy` run first in `run_tests.sh` when they are
installed from `requirements-dev.txt`, and the run says so when they are
not. They are the cheapest checks in the repo: `F821` finds an undefined
name in one second where the end-to-end suites took five minutes, and
`vulture` found four pieces of dead shipped code the day it was tried. None
of them goes on the device. The README section "What runs on the signer" is
the whole list of what does, and `tests/test_integrity.py` enforces it as an
import allowlist, so the two cannot drift apart without a red suite.

## Rule 13: a safety argument that says Core refuses a shape needs a test that gives that shape to Core

`signer.master_xprv` read the key after the last `(`. Its docstring said
that was safe because "Core 31.1 refuses every such shape with a private
key, tested 2026-09-08". No suite gave Core such a shape, so nothing could
show that the claim was wrong. Core 31.1 and 32.1 accept a scanned
`tr(k,pk(other))`, and Backup key showed the other party's tpub as the
paper backup (ISSUES E-9, 2026-11-05).

When code is safe only because Core refuses an input, a suite gives that
input to the real Core and asserts the refusal. A new Core version then
breaks the test, not the backup. `tests/test_backup_key.py` does this for
each descriptor shape the key read must handle.
