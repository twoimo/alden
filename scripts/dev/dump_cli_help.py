#!/usr/bin/env python3
"""Dump `--help`, `-h`, and `--version` output for every openkakao-cli subcommand.

Usage: scripts/dev/dump_cli_help.py <binary> <out-dir> [--source src/main.rs]

`diff -r` two dumps to prove a refactor left the CLI surface unchanged.
clap omits hidden subcommands from help, so top-level variant names are also
read from the `Commands` enum in the source file.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def run(binary, args):
    proc = subprocess.run(
        [binary, *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return f"exit: {proc.returncode}\n--- stdout\n{proc.stdout}--- stderr\n{proc.stderr}"


def visible_subcommands(help_text):
    names = []
    in_commands = False
    for line in help_text.splitlines():
        if line.strip() == "Commands:":
            in_commands = True
            continue
        if in_commands:
            match = re.match(r"^  ([a-z0-9][a-z0-9-]*)\b", line)
            if not match:
                if not line.startswith("   "):
                    in_commands = False
                continue
            if match.group(1) != "help":
                names.append(match.group(1))
    return names


def kebab(name):
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"-\1", name).lower()


def source_subcommands(source):
    lines = source.read_text().splitlines()
    start = lines.index("enum Commands {")
    names = []
    pending_name = None
    for line in lines[start + 1 :]:
        if line == "}":
            break
        attr = re.match(r"^    #\[command\(.*\bname = \"([^\"]+)\"", line)
        if attr:
            pending_name = attr.group(1)
            continue
        variant = re.match(r"^    ([A-Z][A-Za-z0-9]*)\b", line)
        if variant:
            names.append(pending_name or kebab(variant.group(1)))
            pending_name = None
    return names


def dump(binary, out, path, source_names):
    target = out.joinpath(*path) if path else out / "_root"
    target.mkdir(parents=True, exist_ok=True)
    long_help = run(binary, [*path, "--help"])
    (target / "help.txt").write_text(long_help)
    (target / "h.txt").write_text(run(binary, [*path, "-h"]))
    children = visible_subcommands(long_help.split("--- stderr")[0])
    if not path:
        (target / "help-subcommand.txt").write_text(run(binary, ["help"]))
        (target / "version.txt").write_text(run(binary, ["--version"]))
        (target / "V.txt").write_text(run(binary, ["-V"]))
        children = sorted(set(children) | set(source_names))
    dumped = [" ".join(path)]
    for child in children:
        dumped += dump(binary, out, [*path, child], source_names)
    return dumped


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("binary")
    parser.add_argument("out")
    parser.add_argument("--source", default=str(REPO_ROOT / "src" / "main.rs"))
    args = parser.parse_args()
    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        sys.exit(f"refusing to write into non-empty {out}")
    out.mkdir(parents=True, exist_ok=True)
    dumped = dump(args.binary, out, [], source_subcommands(Path(args.source)))
    (out / "commands.txt").write_text("\n".join(dumped) + "\n")
    print(f"dumped {len(dumped)} command paths to {out}")


if __name__ == "__main__":
    main()
