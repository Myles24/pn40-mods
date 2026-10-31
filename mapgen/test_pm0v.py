r"""Round-trip every DeLorme vector map: pm0.read() then serialize() must give the
same bytes, with every TV page parsed into features, texts and their tables.

    python test_pm0v.py [folder ...]
        default: ..\dumps\internal0_files\basedata (the stock base maps) and
                 ..\dumps\sdcard_files_2026-10-30\maps (Topo 10 / Topo 8)
"""
import glob, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pm0

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT = [os.path.join(HERE, "..", "dumps", "internal0_files", "basedata"),
           os.path.join(HERE, "..", "dumps", "sdcard_files_2026-10-30", "maps")]


def is_vector(path):
    with open(path, "rb") as f:
        b = f.read(0x52)
        f.seek(int.from_bytes(b[0x4E:0x52], "big") + 0x1B6 + 0x12)
        return f.read(2) == b"TV"


def main():
    folders = sys.argv[1:] or DEFAULT
    files = [f for d in folders for f in sorted(glob.glob(os.path.join(d, "*.pm0"))) if is_vector(f)]
    if not files:
        sys.exit("no vector maps in %s" % ", ".join(folders))
    bad = 0
    totals = [0, 0, 0]
    t0 = time.time()
    for f in files:
        b = open(f, "rb").read()
        try:
            m = pm0.Map.parse(b)
            out = m.serialize()
            if out != b:
                i = next((k for k in range(min(len(b), len(out))) if b[k] != out[k]), min(len(b), len(out)))
                raise pm0.FormatError("serialize differs at 0x%X (len %d vs %d)" % (i, len(out), len(b)))
            nf = sum(len(v.features) for v in m.vpages.values())
            nt = sum(len(v.texts) for v in m.vpages.values())
            totals[0] += len(m.vpages)
            totals[1] += nf
            totals[2] += nt
            print("ok   %s (%d pages, %d features, %d texts)" % (os.path.basename(f), len(m.vpages), nf, nt))
        except pm0.FormatError as e:
            bad += 1
            print("FAIL %s: %s" % (os.path.basename(f), e))
    print("%d of %d vector maps round-trip: %d TV pages, %d features, %d texts (%.0f s)" % (
        len(files) - bad, len(files), totals[0], totals[1], totals[2], time.time() - t0))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
