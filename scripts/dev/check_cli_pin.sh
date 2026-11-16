#!/bin/sh
# Usage: scripts/dev/check_cli_pin.sh <baseline-dump-dir> <work-dir>
# Rebuilds, re-dumps the CLI help tree into <work-dir>/dump, diffs it against
# the baseline, then records cargo test totals and the clippy verdict.
set -u
baseline=$1
work=$2
repo=$(cd "$(dirname "$0")/../.." && pwd)
cd "$repo" || exit 1
rm -rf "$work"
mkdir -p "$work"
cargo build >"$work/build.log" 2>&1 || { echo "build: FAILED"; tail -30 "$work/build.log"; exit 1; }
python3 scripts/dev/dump_cli_help.py target/debug/openkakao-cli "$work/dump" >/dev/null
if diff -r "$baseline" "$work/dump" >"$work/help.diff"; then
  echo "help dump: identical"
else
  echo "help dump: DIFFERS ($work/help.diff)"
fi
cargo test >"$work/test.log" 2>&1
echo "cargo test exit: $?"
grep -E '^test result:' "$work/test.log" |
  awk '{p += $4; f += $6} END {print "tests passed: " p ", failed: " f}'
awk '/Running unittests src\/main.rs/ {bin = 1} bin && /^test result:/ {print "bin unit tests: " $4 " passed"; exit}' "$work/test.log"
grep -E '^test .* FAILED$' "$work/test.log"
cargo clippy --all-targets -- -D warnings >"$work/clippy.log" 2>&1
echo "clippy exit: $?"
