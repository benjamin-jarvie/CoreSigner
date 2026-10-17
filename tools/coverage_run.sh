#!/bin/bash
# Measure what coresigner/ has never executed, across the WHOLE suite.
#
# Most of Core Signer's code runs in a child process: every scripted device
# session is `python3 coresigner/main.py --dev ...` under subprocess.run. A
# plain `coverage run run_tests.sh` sees none of it and reports a figure
# that is wrong by about twenty points. The fix is coverage's documented
# subprocess hook: a sitecustomize.py that every interpreter imports at
# startup, which calls coverage.process_startup() when
# COVERAGE_PROCESS_START names a config file.
#
# Audit A5 (2026-09-06). This script exists because the first measurement
# was done by hand in a temp directory, so the number in the ticket could
# not be reproduced by anyone, which makes it a claim and not evidence.
set -e
cd "$(dirname "$0")/.."
ROOT=$PWD
if ! arch -arm64 python3 -m coverage --version >/dev/null 2>&1; then
  echo "coverage is not installed: python3 -m pip install --user coverage"
  exit 1
fi
HOOK=$(mktemp -d)
cat > "$HOOK/sitecustomize.py" <<'PY'
import coverage
coverage.process_startup()
PY
# SOURCE=tests measures the suites themselves, which is how audit A6
# finds an assertion that never executes: a check behind a guard that is
# never true is an uncovered line in the test file, and nothing else
# reports it.
SRC=${SOURCE:-coresigner}
cat > "$HOOK/.coveragerc" <<PY
[run]
branch = True
parallel = True
source = $ROOT/$SRC
data_file = $ROOT/.coverage
PY
rm -f "$ROOT"/.coverage "$ROOT"/.coverage.*
export COVERAGE_PROCESS_START="$HOOK/.coveragerc"
export PYTHONPATH="$HOOK${PYTHONPATH:+:$PYTHONPATH}"
RUN_NODE=1 ./run_tests.sh || echo "(suite reported failures; coverage below is still valid)"
# tests/m1 runs under Rosetta, because libzbar on this Mac is x86_64 only
# and pyzbar cannot load under arm64. Leaving it out understates the
# figure by six points: it is the ONLY suite that reaches
# qrchannel.decode_image and ImageQrSource.strings, which is every frame
# the camera ever produces. A measurement that silently skips the QR
# decoder is not a measurement of Core Signer.
M1="$ROOT/tests/m1"
if [ -d "$M1/.build/py-x86" ] && [ -f "$M1/.build/py-x86/coverage/__init__.py" ]; then
  env PYTHONDONTWRITEBYTECODE=1 COVERAGE_PROCESS_START="$HOOK/.coveragerc" \
      PYTHONPATH="$HOOK:$M1/.build/py-x86:$ROOT/coresigner:$ROOT/hw/vendor" \
      arch -x86_64 /usr/bin/python3 -m coverage run \
      --rcfile="$HOOK/.coveragerc" "$M1/test_scan_loop.py" >/dev/null 2>&1 \
    && echo "PASS tests/m1/test_scan_loop.py (x86_64)" \
    || echo "FAIL tests/m1/test_scan_loop.py (x86_64)"
else
  echo "(not run: tests/m1. Run tests/m1/setup.sh, then"
  echo "          arch -x86_64 /usr/bin/python3 -m pip install \\"
  echo "            --target tests/m1/.build/py-x86 coverage."
  echo "          Without it the QR decoder is unmeasured and the total"
  echo "          below is about six points low.)"
fi
arch -arm64 python3 -m coverage combine --rcfile="$HOOK/.coveragerc" >/dev/null
arch -arm64 python3 -m coverage report --rcfile="$HOOK/.coveragerc" -m \
  | tee "$HOOK/report.txt"
# The README states this percentage, and until 2026-10-16 nothing checked
# it: the page said 86% while this script measured 81%, because two e2e
# suites fail before the device sessions they drive can run. A figure a
# reader uses to judge how well the thing is tested is the last one that
# should be taken on trust.
MEASURED=$(grep -E "^TOTAL" "$HOOK/report.txt" | grep -oE "[0-9]+%" \
           | tr -d '%')
STATED=$(grep -oE '\*\*[0-9]+% of `coresigner/`\*\*' "$ROOT/README.md" \
         | grep -oE '[0-9]+')
if [ -z "$STATED" ]; then
  echo "FAIL the README no longer states a coverage percentage"
elif [ "$STATED" != "$MEASURED" ]; then
  echo "FAIL the README says ${STATED}% and this run measured ${MEASURED}%"
else
  echo "PASS the README's ${STATED}% is the figure this run measured"
fi
rm -rf "$HOOK"
