# A9 Delete what is superseded

Type: `wayfinder:task`, AFK. Ben's call, 2026-09-06.

**Blocked by:** A8, which says what the old documents are still load-bearing for.

## Question

Ben: delete the superseded, git keeps it. The reasoning is A8's: a
document nobody has checked is worse than no document, and two false
claims reached him from stale ones in a single evening.

The candidates, with what each is:

- `docs/audit/ui-and-branding.md`, 390 lines from 2026-09-01. Its fifteen
  items are closed and it describes screens that no longer exist. It is
  the source of the D, S and I numbering that ISSUES.md still uses, so
  check what that numbering is still load-bearing for before deleting.
- `docs/wayfinder/m1-qr-without-optics/`, 9 tickets, all closed.
- `docs/wayfinder/zero2w-m0-fixes/`, 5 tickets, all closed.
- `ISSUES.md`'s two "Fixed" sections, 112 lines of history, and its "Open"
  section, which is empty.

What has to survive: anything a later document cites, and anything that
records WHY rather than WHAT. The A-19a amendment is only comprehensible
because the tradeoff screen it removed is described somewhere.

Decide per artefact, delete in one commit, and say in the commit message
what a reader should read instead.

---

> **Superseded on 2026-09-07.** A9 kept the four closed maps and Ben
> then had them archived out of the repository instead. They are in
> `coresigner-closed-wayfinder-maps-2026-09-07.tar.gz` and in git before
> `08b27b9`; paths to them below are ARCHIVED 2026-09-07.

## Answer (2026-09-06)

**One of the four candidates was deleted. Three were kept, and the reason
is the ticket's own test: anything a later document cites has to
survive.** Two of the ticket's own premises did not survive checking,
which is rule 5 applying to the map as much as to a reviewer.

### ISSUES.md: 298 lines to 97

The ticket said its Open section was empty. It was not: it held D17, D18
and six export items E-1 to E-6. Checked one at a time against the
source on 2026-09-06:

| item | claim | now |
|---|---|---|
| D17 | teardown failure is silent | **fixed** — `Session.run` reports "key not cleared" |
| D18 | load and review errors unwind the process | **fixed** — `Session.HANDLED` wraps every flow home dispatches |
| E-1 | four screens have no scroll bar | **fixed** — six draw one, `test_scroll.py` asserts it |
| E-2 | the export is four screens deep with no map | **fixed** on the board with Ben, 2026-09-05 |
| E-3 | "PUBLIC KEY" is not what the screen holds | **fixed** — `main.py:832` passes the policy name |
| E-5 | Core's file has nowhere to go | **fixed** — `--card-dir` exists, session K13 proves it |
| E-6 | two of Core's four policies | **fixed** — all four, session K12 |
| E-4 | five coordinators unproven on a device | **open**, and needs Ben, a phone and the laptop |

So the file's Open section was seven-eighths stale, and its Fixed
sections were 112 lines the file's own opening rule says do not belong
in it: "Fixed items leave this file and live in the git history
instead." It also claimed `CameraQrSource.scan_key` still raises; no
`scan_key` has existed since ticket 09 replaced it with the `strings()`
contract.

Rewritten. What is open is E-4, the undocumented board combination A8
measured, and the unpinned tester image. What was fixed is one line each
saying where the lesson went, because that is what a reader needs and the
git history is where the detail belongs.

### docs/audit/ui-and-branding.md: KEPT, with a header

The ticket flagged it for deletion and said to check the numbering
first. Checked: the D, S and I numbering is cited from **shipped code**
(twelve references in `coresigner/main.py` alone), from `tests/`, and from
TESTING.md rules 1, 6 and 7, which are only comprehensible with the
items they came from. Deleting it orphans all of that.

It does describe screens that no longer exist, which is the rot the map
is about, so it now opens with a header saying it is closed, that the
screens are superseded, what replaced them, and why it survives. That
turns "a document nobody has checked" into "a dated record with a stated
purpose", which is the thing Ben's rule is actually protecting against.
Its own dangling reference to `tasks/audit-ui-and-branding.md` is
explained rather than left hanging.

### The two closed maps: KEPT

`m1-qr-without-optics` is cited by `PLAN.md` and `docs/wayfinder/README.md`;
`zero2w-m0-fixes` is cited by `m0/m0_gate.py` and by two other maps.
Both record WHY. They are marked **closed** in `docs/wayfinder/README.md`,
which is the index a reader actually opens, and that index now lists
`beta-audit` as the open one, which it did not.

That index also described `e2e-before-testers` as covering "Core's own
encrypted file backup" — the feature PLAN A-24 deleted the next day. Same
class as A8's `CONTEXT.md` finding: the index of the maps had itself gone
stale.

### What a reader should read instead

- For what a fixed defect taught: **TESTING.md**, rules 1 to 11.
- For why a decision was made: the **PLAN amendment**, A-1 to A-24.
- For what is open: **ISSUES.md**, which is now only that.
- For which effort is live: **docs/wayfinder/README.md**.
- For the detail of any of it: **git**.
