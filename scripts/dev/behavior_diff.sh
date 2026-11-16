#!/bin/sh
# Usage: behavior_diff.sh <base-bin> <new-bin> <out-dir>
# Runs commands that stop before any network, AX, send, or state write,
# each under a fresh empty HOME, and diffs base vs new output. Both binaries
# must be named openkakao-cli, because clap prints argv[0] in usage errors.
# Every case below was checked in source to fail or finish before touching
# KakaoTalk; keep it that way when adding cases.
base=$1
new=$2
out=$3
rm -rf "$out"
mkdir -p "$out/base" "$out/new"
i=0
while IFS= read -r line; do
  [ -z "$line" ] && continue
  i=$((i + 1))
  for side in base new; do
    if [ "$side" = base ]; then bin=$base; else bin=$new; fi
    home=$(mktemp -d)
    # shellcheck disable=SC2086
    (cd "$home" && eval "env -i HOME=\"$home\" PATH=/usr/bin:/bin \"$bin\" $line" >"$out/$side/$i.out" 2>"$out/$side/$i.err" </dev/null)
    echo "exit=$?" >>"$out/$side/$i.err"
    sed "s#$home#<HOME>#g" "$out/$side/$i.err" >"$out/$side/$i.err.n" && mv "$out/$side/$i.err.n" "$out/$side/$i.err"
    sed "s#$home#<HOME>#g" "$out/$side/$i.out" >"$out/$side/$i.out.n" && mv "$out/$side/$i.out.n" "$out/$side/$i.out"
    rm -rf "$home"
  done
  echo "$i: $line" >>"$out/cases.txt"
done <<'EOF'
completions bash
completions zsh
completions fish
completions powershell
completions elvish
context-sync-local --chat-id 1 --chat ''
--json context-sync-local --chat-id 1 --chat ' '
context-repair-self-sends --chat-id 1 --chat ''
context-reply-bundle q --chat c --chat-id 1
context-reply-bundle q --chat c --exclude-log-id 5
local-chats
--json local-chats --groups
local-read 1
local-search keyword
local-schema
--json local-schema
send
local-read
context-sync-local --chat-id 0 --chat c
EOF
if diff -r "$out/base" "$out/new" >"$out/behavior.diff"; then
  echo "behavior: identical across $i cases"
else
  echo "behavior: DIFFERS ($out/behavior.diff)"
fi
