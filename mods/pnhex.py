"""Read, patch, extend and check PN-40 firmware .hex files.

The .hex is the bootloader's plain-text format, one record per line:
    "AAAAAAAA XX XX ... XX \\r\\n"     (address, 1-16 bytes, trailing space, CRLF)

Rules enforced here come from the bootloader's parser (RAM 0x842C, see
nor\\BOOTLOADER.md), plus our own safety limits:
  * the bootloader accepts 0x30000000-0x3007FFFF (itself!) - we never write there
  * we never write at or above SAFE_END: the last 64 KB block holds the device
    identity record (0x307FC000) and the update-finished marker (0x307FFFFC),
    and touching any byte of a block makes the bootloader erase all of it
"""
import hashlib

FW_START = 0x30080000
SAFE_END = 0x307F0000          # first byte we refuse to write
MAX_LINE = 60                  # bootloader rejects longer lines (CRLF not counted)


class HexError(Exception):
    pass


class HexFile:
    def __init__(self, path):
        self.lines = open(path, "rb").read().split(b"\n")
        self.index = {}
        for n, line in enumerate(self.lines):
            parts = line.split()
            if parts:
                self.index[int(parts[0], 16)] = n
        self.appended = []      # (addr, bytes) records added after the last line

    # -- in-place byte patches --------------------------------------------
    def _locate(self, addr):
        # records hold 1-16 bytes and are not always 16-byte aligned
        for k in range(16):
            n = self.index.get(addr - k)
            if n is not None and len(self.lines[n].split()) - 1 > k:
                return n, k
        raise HexError("address %08X is not in the hex file" % addr)

    def get(self, addr, n):
        out = bytearray()
        for a in range(addr, addr + n):
            line, i = self._locate(a)
            out.append(int(self.lines[line].split()[1 + i], 16))
        return bytes(out)

    def patch(self, name, addr, old, new):
        """Replace bytes at addr; refuses unless the current bytes equal old."""
        old, new = bytes(old), bytes(new)
        if len(old) != len(new):
            raise HexError("%s: old/new length differ" % name)
        check_range(name, addr, len(new))
        have = self.get(addr, len(old))
        if have != old:
            raise HexError("%s: bytes at %08X are %s, expected %s" % (name, addr, have.hex(), old.hex()))
        for k, b in enumerate(new):
            line, i = self._locate(addr + k)
            pos = 9 + 3 * i
            ln = self.lines[line]
            assert ln[pos - 1:pos] == b" "
            self.lines[line] = ln[:pos] + b"%02X" % b + ln[pos + 2:]

    # -- new records past the end of the stock image -------------------------
    def last_address(self):
        a = max(self.index)
        return a + len(self.lines[self.index[a]].split()) - 1 - 1

    def append(self, name, addr, data):
        check_range(name, addr, len(data))
        prev_end = self.appended[-1][0] + len(self.appended[-1][1]) - 1 if self.appended else self.last_address()
        if addr <= prev_end:
            raise HexError("%s: %08X is not above the previous record end %08X" % (name, addr, prev_end))
        self.appended.append((addr, bytes(data)))

    def render(self):
        body = b"\n".join(self.lines)
        if not self.appended:
            return body
        if not body.endswith(b"\r\n"):
            raise HexError("stock file does not end with CRLF")
        out = [body]
        for addr, data in self.appended:
            for o in range(0, len(data), 16):
                chunk = data[o:o + 16]
                out.append(b"%08X " % (addr + o) + b"".join(b"%02X " % c for c in chunk) + b"\r\n")
        return b"".join(out)

    def write(self, path):
        data = self.render()
        validate(data)
        open(path, "wb").write(data)
        return hashlib.sha256(data).hexdigest()


def check_range(name, addr, n):
    if addr < FW_START or addr + n > SAFE_END:
        raise HexError("%s: %08X+%d is outside the writable firmware area %08X-%08X"
                       % (name, addr, n, FW_START, SAFE_END - 1))


def parse(data):
    """Yield (line_no, addr, bytes) for each record, enforcing the format."""
    for n, raw in enumerate(data.split(b"\n"), 1):
        line = raw[:-1] if raw.endswith(b"\r") else raw
        if not line.strip():
            continue
        if len(line) > MAX_LINE:
            raise HexError("line %d: %d chars (bootloader limit %d)" % (n, len(line), MAX_LINE))
        parts = line.split()
        try:
            addr = int(parts[0], 16)
            vals = [int(p, 16) for p in parts[1:]]
        except ValueError:
            raise HexError("line %d: not hex: %r" % (n, line))
        if not 1 <= len(vals) <= 16:
            raise HexError("line %d: %d data bytes (must be 1-16)" % (n, len(vals)))
        if any(v > 0xFF for v in vals):
            raise HexError("line %d: byte value above 0xFF" % n)
        yield n, addr, bytes(vals)


def validate(data):
    """Bootloader rules + our safety limits. Returns (records, lowest, highest)."""
    prev_end = -1
    count = 0
    lo, hi = None, None
    for n, addr, vals in parse(data):
        end = addr + len(vals) - 1
        if addr < FW_START:
            raise HexError("line %d: %08X is in the bootloader area - refusing" % (n, addr))
        if end >= SAFE_END:
            raise HexError("line %d: %08X reaches the identity/marker block - refusing" % (n, end))
        if addr <= prev_end:
            raise HexError("line %d: %08X does not ascend (previous record ended at %08X)" % (n, addr, prev_end))
        prev_end = end
        lo = addr if lo is None else lo
        hi = end
        count += 1
    if count == 0:
        raise HexError("no records")
    return count, lo, hi


def image(data):
    """{addr: byte} is too big; return (base, bytearray) with 0xEE fill like the bootloader."""
    recs = [(a, v) for _, a, v in parse(data)]
    base = recs[0][0] & ~0xFFFF
    end = recs[-1][0] + len(recs[-1][1])
    img = bytearray(b"\xEE" * (end - base))
    for a, v in recs:
        img[a - base:a - base + len(v)] = v
    return base, img


# -- delta updates ------------------------------------------------------------
# The bootloader rewrites only the 64 KB blocks a file touches (each one
# 0xEE-filled, then the file's bytes) and leaves every other block alone. So a
# file holding just the blocks that differ from what is installed, each block
# complete, gives the same flash as the full file.

BLOCK = 0x10000


def lines_of(data):
    """[(addr, bytes, raw line)] for every record."""
    raws = [r for r in data.split(b"\n") if r.strip()]
    recs = list(parse(data))
    assert len(raws) == len(recs)
    return [(a, v, raw.rstrip(b"\r")) for (_, a, v), raw in zip(recs, raws)]


def blocks_of(addr, n):
    return range(addr // BLOCK, (addr + n - 1) // BLOCK + 1)


def flash(lines, nor):
    """Apply records to nor ({block: bytearray}) the way the bootloader does."""
    touched = {}
    for a, v, _ in lines:
        for b in blocks_of(a, len(v)):
            touched.setdefault(b, bytearray(b"\xEE" * BLOCK))
        for k, byte in enumerate(v):
            touched[(a + k) // BLOCK][(a + k) % BLOCK] = byte
    nor.update(touched)
    return set(touched)


def delta(base_data, new_data):
    """Records of new_data for every block whose flashed contents differ from
    base_data's (or that base_data doesn't touch). Returns (hex bytes, blocks)."""
    base_l, new_l = lines_of(base_data), lines_of(new_data)
    after_base, after_new = {}, {}
    flash(base_l, after_base)
    new_blocks = flash(new_l, after_new)
    want = {b for b in new_blocks if after_new[b] != after_base.get(b)}
    while True:                         # a record across a block boundary touches both blocks
        sel = [l for l in new_l if any(b in want for b in blocks_of(l[0], len(l[1])))]
        touched = {b for l in sel for b in blocks_of(l[0], len(l[1]))}
        if touched <= want:
            break
        want |= touched
    return b"".join(raw + b"\r\n" for _, _, raw in sel), sorted(want)


def check_delta(base_data, new_data, delta_data):
    """Raise unless base + delta flashes exactly like base + full new file."""
    full, part = {}, {}
    flash(lines_of(base_data), full)
    flash(lines_of(base_data), part)
    flash(lines_of(new_data), full)
    flash(lines_of(delta_data), part)
    if full.keys() != part.keys():
        raise HexError("delta touches different blocks than the full file would leave")
    bad = [b for b in full if full[b] != part[b]]
    if bad:
        raise HexError("delta gives different flash in blocks %s" % [hex(b * BLOCK) for b in bad[:8]])
