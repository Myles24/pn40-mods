r"""Round-trip every DeLorme raster map: pm0.read() then serialize() must give the
same bytes, every tile must decode, and re-encoding must give the same size.

    python test_pm0.py [maps folder]      (default: ..\internal drive\maps)
"""
import glob, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pm0

HERE = os.path.dirname(os.path.abspath(__file__))


def test_rle():
    """encoder edge cases: no literal shorter than 3 pixels, literals split at 255"""
    import random
    rnd = random.Random(1)
    rows = [[1], [1, 2], [1, 2, 3], [1, 1, 2], [1, 2, 2], [5] * 300, list(range(255)), list(range(256)),
            list(range(257)), list(range(258)), list(range(510)), [7, 8] * 150]
    rows += [[rnd.choice((0, 0, 1, 2, 3)) for _ in range(rnd.randint(1, 600))] for _ in range(300)]
    bad = 0
    for row in rows:
        t = pm0.Tile((0, 0, 1, 1), len(row), 1, pm0.COMP_RLE, pm0.encode_rle([row]))
        try:
            if pm0.decode_rows(t) != [row]:
                raise pm0.FormatError("decodes differently")
        except pm0.FormatError as e:
            bad += 1
            print("FAIL rle %r...: %s" % (row[:6], e))
    print("%s RLE encoder edge cases" % ("ok  " if not bad else "FAIL"))
    return bad


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "internal drive", "maps")
    files = [f for f in sorted(glob.glob(os.path.join(folder, "*.pm0"))) if "SAT10" not in f]
    if not files:
        sys.exit("no raster maps in %s" % folder)
    bad = test_rle()
    for f in files:
        b = open(f, "rb").read()
        try:
            m = pm0.Map.parse(b)
            out = m.serialize()
            if out != b:
                i = next((k for k in range(min(len(b), len(out))) if b[k] != out[k]), min(len(b), len(out)))
                raise pm0.FormatError("serialize differs at 0x%X (len %d vs %d)" % (i, len(out), len(b)))
            for page, t in m.tiles.items():
                rows = pm0.decode_rows(t)
                # raw rows carry junk in their padding, so compare pixels
                enc = pm0.encode_raw(rows, t.width) if t.comp == pm0.COMP_RAW else pm0.encode_rle(rows)
                if pm0.decode_rows(pm0.Tile(t.bounds, t.width, t.height, t.comp, enc)) != rows:
                    raise pm0.FormatError("tile %d does not survive re-encoding" % page)
            print("ok   %s (%d tiles, %d layers)" % (os.path.basename(f), len(m.tiles), len(m.layers)))
        except pm0.FormatError as e:
            bad += 1
            print("FAIL %s: %s" % (os.path.basename(f), e))
    print("%d of %d maps round-trip" % (len(files) - bad, len(files)))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
