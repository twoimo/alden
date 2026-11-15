#!/usr/bin/env python3
"""Move whole top-level Rust items between files verbatim.

  list <file> [--mod tests]        print each item's span, key, and the
                                   other items in the same scope it uses
  move <src> <dst> KEY... [--src-mod tests] [--dst-mod tests]
                                   cut the items and append them to <dst>
  compare <base> <file>... [--base-mod tests] [--mod tests] [--show]
                                   report which items in <file> match the
                                   same-named item in <base> verbatim

An item spans from the line after the previous item ends through its own
closing `}` or `;`, so doc comments and attributes travel with it. Moving a
type also moves every `impl` block for it. Items that share a name (for
example `#[cfg]` variants of one function) move together. A `--*-mod tests`
flag selects the items inside `mod tests { ... }` instead of the file's top
level; a destination gets a `mod tests` block if it lacks one. Moved text is
not re-indented, so run rustfmt on the destination afterwards.
"""

import argparse
import difflib
import re
import sys
from pathlib import Path

HEADER = re.compile(
    r'^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+|unsafe\s+|extern\s+"[^"]*"\s+|const\s+(?=fn))*'
    r"(?:(fn|struct|enum|trait|mod|type|static|const|union)\s+([A-Za-z_][A-Za-z0-9_]*)"
    r"|(impl)\b(.*)|(use)\s+(.*)|(macro_rules!)\s*([A-Za-z_][A-Za-z0-9_]*))"
)


RAW = re.compile(r'r(#*)"')


def raw_string_start(text, i):
    prev = text[i - 1] if i > 0 else ""
    if prev == "b":
        prev = text[i - 2] if i > 1 else ""
    return not (prev.isalnum() or prev == "_")


def scan(text):
    """Return code text with strings and comments blanked, same length."""
    out = list(text)
    i, n = 0, len(text)

    def blank(a, b):
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        c = text[i]
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
        elif text.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if text.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif text.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            blank(i, j)
            i = j
        elif c == "r" and raw_string_start(text, i) and RAW.match(text, i):
            hashes = RAW.match(text, i).group(1)
            start = i + 2 + len(hashes)
            end = text.find('"' + hashes, start)
            blank(start, end)
            i = end + 1 + len(hashes)
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            blank(i + 1, j)
            i = j + 1
        elif c == "'":
            if i + 1 < n and text[i + 1] == "\\":
                j = text.find("'", i + 3)
                blank(i + 1, j)
                i = j + 1
            elif i + 2 < n and text[i + 2] == "'":
                blank(i + 1, i + 2)
                i += 3
            else:
                i += 1
        else:
            i += 1
    return "".join(out)


def items(lines, first, last):
    """Split lines[first:last] into top-level items of that region."""
    code = scan("".join(lines[first:last])).splitlines(keepends=True)
    result, depth, start, header = [], 0, 0, None
    for offset, line in enumerate(code):
        stripped = line.strip()
        if depth == 0 and header is None and stripped and not stripped.startswith("#"):
            header = offset
        for ch in line:
            if ch in "{([":
                depth += 1
            elif ch in "})]":
                depth -= 1
        if depth == 0 and header is not None and (stripped.endswith("}") or stripped.endswith(";")):
            result.append((first + start, first + header, first + offset + 1))
            start, header = offset + 1, None
    return result


def key_of(header_line):
    m = HEADER.match(header_line)
    if not m:
        return header_line.strip()
    if m.group(1):
        return m.group(2)
    if m.group(3):
        sig = re.sub(r"\s*\{\s*$", "", m.group(4)).strip()
        sig = re.sub(r"^<[^>]*>\s*", "", sig)
        return "impl " + sig
    if m.group(5):
        return "use " + m.group(6).rstrip(";").strip()
    return m.group(8)


def impl_target(key):
    if not key.startswith("impl "):
        return None
    sig = key[5:]
    target = sig.split(" for ", 1)[1] if " for " in sig else sig
    return re.match(r"[A-Za-z_][A-Za-z0-9_]*", target).group(0)


def region(lines, mod):
    if mod is None:
        return 0, len(lines)
    for i, line in enumerate(lines):
        if re.match(rf"^mod {re.escape(mod)} \{{\s*$", line):
            for j in range(len(lines) - 1, i, -1):
                if lines[j].rstrip() == "}":
                    return i + 1, j
    sys.exit(f"no `mod {mod} {{` block found")


def parsed(path, mod):
    lines = Path(path).read_text().splitlines(keepends=True)
    first, last = region(lines, mod)
    spans = [(s, h, e, key_of(lines[h])) for s, h, e in items(lines, first, last)]
    return lines, first, last, spans


def cmd_list(args):
    lines, _, _, spans = parsed(args.file, args.mod)
    names = {k for *_, k in spans if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k)}
    for s, h, e, key in spans:
        idents = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", scan("".join(lines[h:e]))))
        uses = sorted((idents & names) - {key, impl_target(key)})
        print(f"{h + 1}-{e}\t{e - h}\t{key}\t{' '.join(uses)}")


def insert_into(dst, block, mod):
    existing = dst.read_text() if dst.exists() else ""
    lines = existing.splitlines(keepends=True)
    if mod is None:
        at = next((i for i, l in enumerate(lines) if l.startswith("mod tests {")), len(lines))
        while at > 0 and lines[at - 1].startswith("#["):
            at -= 1
    else:
        if not any(re.match(rf"^mod {re.escape(mod)} \{{\s*$", l) for l in lines):
            lines += ["\n", "#[cfg(test)]\n", f"mod {mod} {{\n", "    use super::*;\n", "}\n"]
        at = region(lines, mod)[1]
    head = "".join(lines[:at]).rstrip("\n")
    tail = "".join(lines[at:])
    dst.write_text((head + "\n\n" if head.strip() else "") + block + ("\n" + tail if tail else ""))


def cmd_move(args):
    lines, _, _, spans = parsed(args.src, args.src_mod)
    wanted = set(args.keys)
    chosen = [sp for sp in spans if sp[3] in wanted or impl_target(sp[3]) in wanted]
    missing = wanted - ({sp[3] for sp in chosen} | {impl_target(sp[3]) for sp in chosen})
    if missing:
        sys.exit(f"not found in {args.src}: {' '.join(sorted(missing))}")
    taken, moved = set(), []
    for s, _, e, _ in chosen:
        body = lines[s:e]
        while body and not body[0].strip():
            body = body[1:]
        moved.append("".join(body))
        taken.update(range(s, e))
    Path(args.src).write_text("".join(l for i, l in enumerate(lines) if i not in taken))
    insert_into(Path(args.dst), "\n".join(moved), args.dst_mod)
    print(f"moved {len(chosen)} items ({len(taken)} lines) to {args.dst}")


def normalized(text):
    text = re.sub(r"\bpub(\([^)]*\))?\s+", "", text)
    text = re.sub(r"\s+", "", text)
    return re.sub(r",([)\]}])", r"\1", text)


def chunk_texts(path, mod):
    lines, _, _, spans = parsed(path, mod)
    texts = {}
    for s, _, e, key in spans:
        texts.setdefault(key, []).append("".join(lines[s:e]).strip("\n"))
    return texts


def cmd_compare(args):
    base = chunk_texts(args.base, args.base_mod)
    same, changed, new = 0, [], []
    for path in args.files:
        for key, texts in chunk_texts(path, args.mod).items():
            if key not in base:
                new.append(f"{path}: {key}")
            elif sorted(map(normalized, texts)) == sorted(map(normalized, base[key])):
                same += len(texts)
            else:
                changed.append(f"{path}: {key}")
                if args.show:
                    diff = difflib.unified_diff(
                        "\n".join(base[key]).splitlines(), "\n".join(texts).splitlines(), lineterm=""
                    )
                    print("\n".join(diff))
    print(f"verbatim (modulo whitespace, visibility, trailing commas): {same}")
    print(f"changed: {len(changed)}")
    for item in changed:
        print(f"  {item}")
    print(f"not in base: {len(new)}")
    for item in new:
        print(f"  {item}")


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_list = sub.add_parser("list")
    p_list.add_argument("file")
    p_list.add_argument("--mod")
    p_move = sub.add_parser("move")
    p_move.add_argument("src")
    p_move.add_argument("dst")
    p_move.add_argument("keys", nargs="+")
    p_move.add_argument("--src-mod")
    p_move.add_argument("--dst-mod")
    p_cmp = sub.add_parser("compare")
    p_cmp.add_argument("base")
    p_cmp.add_argument("files", nargs="+")
    p_cmp.add_argument("--base-mod")
    p_cmp.add_argument("--mod")
    p_cmp.add_argument("--show", action="store_true")
    args = parser.parse_args()
    {"list": cmd_list, "move": cmd_move, "compare": cmd_compare}[args.cmd](args)


if __name__ == "__main__":
    main()
