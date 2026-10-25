"""Build a patched PN-40.hex from the stock 2.9.6.70319 firmware dump.

usage: python make_patch.py <stock PN-40.hex> <output PN-40.hex>

The .hex file is a plain text dump ("AAAAAAAA XX XX ... XX \r\n", 16 bytes per
line). Only the hex tokens of patched bytes are rewritten; every other character
of the file is copied through unchanged. Each patch checks the original bytes
first and the script refuses to write anything if they don't match.
"""
import sys

PATCHES = [
    # GPS week-number rollover fix.
    #
    # Stock routine at 0x301C8CC8 expands the 10-bit broadcast week number:
    #     wn = (word >> 20) & 0x3FF;  if (wn < 1000) wn += 1024;
    # which maps every date onto 1999-03-07 .. 2018-10-20. Since 2018-10-21
    # the unit reports dates 1024 weeks (19.6 years) in the past.
    #
    # Replacement keeps the same size (7 instructions) and maps onto weeks
    # 1976..2999 (2017-11-19 .. 2037-07-04). 2999 is the highest week the
    # GPS library's own sanity checks accept (week - 1000 < 2000).
    #     ldr   r2, [r0, #8]
    #     lsl   r3, r2, #2
    #     lsr   r0, r3, #22          ; (word >> 20) & 0x3FF
    #     cmp   r0, #952
    #     add   r0, r0, #0x400
    #     addlt r0, r0, #0x400
    #     bx    lr
    (
        "GPS week rollover fix",
        0x301C8CC8,
        "082090e510309fe5033002e0230aa0e1fa0f50e3010b80b21eff2fe1",
        "082090e50231a0e1230ba0e1ee0f50e3010b80e2010b80b21eff2fe1",
    ),
]


def main(src, dst):
    lines = open(src, "rb").read().split(b"\n")
    index = {}
    for n, line in enumerate(lines):
        parts = line.split()
        if parts:
            index[int(parts[0], 16)] = n

    def locate(addr):
        base = addr & ~0xF
        if base not in index:
            sys.exit("address %08X is not in the hex file" % addr)
        return index[base], addr - base

    def get_byte(addr):
        n, i = locate(addr)
        return int(lines[n].split()[1 + i], 16)

    def set_byte(addr, value):
        n, i = locate(addr)
        line = lines[n]
        # token i starts after the 8-char address + separator, 3 chars per byte
        pos = 9 + 3 * i
        assert line[pos - 1:pos] == b" " and len(line) >= pos + 2
        lines[n] = line[:pos] + b"%02X" % value + line[pos + 2:]

    for name, addr, old, new in PATCHES:
        old, new = bytes.fromhex(old), bytes.fromhex(new)
        assert len(old) == len(new)
        # the bootloader would accept 0x30000000-0x3007FFFF and rewrite itself;
        # the last block holds the identity record and update marker
        if addr < 0x30080000 or addr + len(new) > 0x307F0000:
            sys.exit("%s: %08X is outside the firmware area 30080000-307EFFFF" % (name, addr))
        have = bytes(get_byte(addr + i) for i in range(len(old)))
        if have != old:
            sys.exit("%s: original bytes at %08X don't match (found %s). "
                     "Is this the stock 2.9.6.70319 firmware?" % (name, addr, have.hex()))

    for name, addr, old, new in PATCHES:
        for i, b in enumerate(bytes.fromhex(new)):
            set_byte(addr + i, b)
        print("applied: %s @ %08X (%d bytes)" % (name, addr, len(bytes.fromhex(new))))

    open(dst, "wb").write(b"\n".join(lines))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
