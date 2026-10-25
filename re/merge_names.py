"""Merge re/notes/*_names.tsv into re/names.tsv.

Existing names.tsv entries win unless listed in OVERRIDE (names an agent
showed to be wrong). Conflicts between agents are printed; the first area in
AREAS order wins. Duplicate names get an address suffix so Ghidra accepts them.
"""
import os, re, sys

RE = os.path.dirname(os.path.abspath(__file__))
AREAS = ["graphics", "layers", "pages", "settings"]
# names in names.tsv that the layers agent showed to be wrong
OVERRIDE = {"3009aa6c", "3009a4c4"}


def load(path):
    rows = []
    for line in open(path, encoding="utf-8"):
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.rstrip("\n").split("\t")
        if not re.fullmatch(r"[0-9a-fA-F]{8}", parts[0]) or len(parts) < 2 or not parts[1].strip():
            continue
        rows.append((parts[0].lower(), parts[1].strip(), parts[2].strip() if len(parts) > 2 else ""))
    return rows


base_lines = open(os.path.join(RE, "names.tsv"), encoding="utf-8").read().splitlines()
base = {a: (n, c) for a, n, c in load(os.path.join(RE, "names.tsv"))}
added, conflicts, merged = {}, [], {}
for area in AREAS:
    p = os.path.join(RE, "notes", area + "_names.tsv")
    for a, n, c in load(p):
        if a in base and a not in OVERRIDE:
            if base[a][0] != n:
                conflicts.append("%s: kept %s, %s says %s" % (a, base[a][0], area, n))
            continue
        if a in merged:
            if merged[a][0] != n:
                conflicts.append("%s: %s=%s vs %s=%s" % (a, merged[a][2], merged[a][0], area, n))
            continue
        merged[a] = (n, c, area)

# drop overridden base lines, then append new names by area
out = [l for l in base_lines if not (l[:8].lower() in OVERRIDE and l[:8].lower() in merged)]
used = {n for n, _ in base.values()}
for area in AREAS:
    out.append("# --- %s (re/notes/%s.md) ---" % (area, area))
    for a in sorted(k for k, v in merged.items() if v[2] == area):
        n, c, _ = merged[a]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", n):
            n = re.sub(r"\W", "_", n)
        if n in used:
            n = "%s_%s" % (n, a)
        used.add(n)
        out.append("%s\t%s\t%s" % (a, n, c.replace("\t", " ")))
open(os.path.join(RE, "names.tsv"), "w", encoding="utf-8", newline="\n").write("\n".join(out) + "\n")
print("added %d names; %d conflicts" % (len(merged), len(conflicts)))
for c in conflicts:
    print("  " + c)
