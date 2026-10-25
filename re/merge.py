"""Join the per-slice export files into out/decomp.c and out/functions.tsv, by address."""
import os, re, sys

N = int(sys.argv[1])
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
chunks = []
for k in range(N):
    text = open(os.path.join(OUT, "decomp_%d.c" % k), encoding="utf-8", errors="replace").read()
    parts = re.split(r"(?=^/\* FUNC [0-9a-f]{8} )", text, flags=re.M)
    chunks += [p for p in parts if p.startswith("/* FUNC")]
chunks.sort(key=lambda p: int(p[8:16], 16))
# Jython wrote "\r\n" + Ghidra's own "\n": drop the doubled line breaks
chunks = [re.sub(r"\n{3,}", "\n\n", re.sub(r"\n\n(?=\s*\S)", "\n", c.replace("\r", ""))) for c in chunks]
with open(os.path.join(OUT, "decomp.c"), "w", encoding="utf-8") as f:
    f.writelines(chunks)

rows = []
for k in range(N):
    rows += open(os.path.join(OUT, "functions_%d.tsv" % k), encoding="utf-8", errors="replace").read().splitlines()
rows.sort(key=lambda r: int(r.split("\t", 1)[0], 16))
with open(os.path.join(OUT, "functions.tsv"), "w", encoding="utf-8") as f:
    f.write("addr\tname\tsize\tcallers\tcallees\tthumb\tstrings\n")
    f.write("\n".join(rows) + "\n")
failed = sum("decompile failed" in c for c in chunks)
print("merged %d functions (%d failed to decompile), %d rows" % (len(chunks), failed, len(rows)))
for k in range(N):
    os.remove(os.path.join(OUT, "decomp_%d.c" % k))
    os.remove(os.path.join(OUT, "functions_%d.tsv" % k))
