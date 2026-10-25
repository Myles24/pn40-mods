"""Tests for pnhex delta updates (pnhex.delta / pnhex.check_delta) on
synthetic hex files: records that cross 64 KB blocks, blocks only one side
touches, and a delta that must be refused.

usage: python test_pnhex.py
"""
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pnhex

BASE = 0x30100000


def hexfile(recs):
    out = []
    for a, v in sorted(recs):
        out.append(b"%08X " % a + b"".join(b"%02X " % c for c in v) + b"\r\n")
    return b"".join(out)


def image_recs(img, start, step=16, skip=()):
    """records covering img (bytes) from start, `step` bytes each, except skipped offsets"""
    return [(start + o, img[o:o + step]) for o in range(0, len(img), step) if o not in skip]


def run_tests():
    ok = 0
    rnd = random.Random(40)

    # --- identical files: nothing to send -------------------------------------
    img = bytes(rnd.randrange(256) for _ in range(0x30000))
    a = hexfile(image_recs(img, BASE))
    d, blocks = pnhex.delta(a, a)
    assert d == b"" and blocks == []
    ok += 1; print("ok  same build: empty delta")

    # --- one byte changed: exactly its whole block, verbatim lines --------------
    img2 = bytearray(img)
    img2[0x18005] ^= 0xFF
    b = hexfile(image_recs(bytes(img2), BASE))
    d, blocks = pnhex.delta(a, b)
    assert blocks == [(BASE + 0x10000) // 0x10000] and d.count(b"\n") == 0x10000 // 16
    assert all(line in b for line in d.split(b"\r\n") if line)
    pnhex.check_delta(a, b, d)
    pnhex.validate(d)
    ok += 1; print("ok  one byte changed: its whole 64 KB block (4096 lines), check passes")

    # --- records crossing a block boundary pull in the neighbour -----------------
    recs = image_recs(img, BASE + 8)                    # every record straddles 16-byte phase -> one crosses each block edge
    c = hexfile(recs)
    recs2 = list(recs)
    k = next(i for i, (addr, v) in enumerate(recs2) if addr < BASE + 0x20000 <= addr + len(v) - 1)
    addr, v = recs2[k]
    recs2[k] = (addr, bytes([v[0] ^ 1]) + v[1:])        # change the byte before the edge only
    e = hexfile(recs2)
    d, blocks = pnhex.delta(c, e)
    # every block edge in this file has a straddling record, so the whole run of 4 blocks is pulled in
    assert blocks == [x // 0x10000 for x in (BASE, BASE + 0x10000, BASE + 0x20000, BASE + 0x30000)], \
        [hex(x) for x in blocks]
    pnhex.check_delta(c, e, d)
    ok += 1; print("ok  a changed record across a block edge: neighbours sent whole (transitively)")

    # --- a hole the new file doesn't cover becomes 0xEE: still matches ------------
    f = hexfile(image_recs(img, BASE, skip={0x100, 0x110}))
    d, blocks = pnhex.delta(a, f)
    assert blocks == [BASE // 0x10000]
    pnhex.check_delta(a, f, d)
    ok += 1; print("ok  bytes the new file leaves out (0xEE after flashing) count as a difference")

    # --- new code beyond the old end; old-only blocks are left alone ---------------
    g = hexfile(image_recs(img, BASE) + [(BASE + 0x50000, b"\x01\x02\x03")])
    h = hexfile(image_recs(img[:0x20000], BASE))
    d, blocks = pnhex.delta(h, g)
    assert blocks == [(BASE + 0x20000) // 0x10000, (BASE + 0x50000) // 0x10000]
    pnhex.check_delta(h, g, d)
    d, blocks = pnhex.delta(g, h)                       # the reverse: only ever writes, never "un-writes"
    assert blocks == [] and d == b""
    pnhex.check_delta(g, h, d)
    ok += 1; print("ok  new blocks are sent; blocks only the old file had are left alone (as a full install would)")

    # --- a wrong delta is refused --------------------------------------------------
    d, blocks = pnhex.delta(a, b)
    lines = d.split(b"\r\n")
    for bad in (b"\r\n".join(lines[:-3] + [b""]),                          # truncated block
                d.replace(lines[5], lines[5][:-3] + b"00 ", 1)):          # a byte changed
        try:
            pnhex.check_delta(a, b, bad)
        except pnhex.HexError:
            continue
        raise AssertionError("check_delta accepted a wrong delta")
    try:
        pnhex.check_delta(c, b, pnhex.delta(a, b)[0])                       # built against another base
    except pnhex.HexError:
        pass
    else:
        raise AssertionError("delta against the wrong base accepted")
    ok += 1; print("ok  truncated, altered or wrong-base deltas are refused")

    print("\nall %d tests passed" % ok)


if __name__ == "__main__":
    run_tests()
