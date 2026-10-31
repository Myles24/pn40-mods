"""Read and write PN-40 map packages (.pm0): raster (3DTQ topo quads, CDOQQ aerials)
and vector (base map, Topo 9 regional, Topo 10).

Format notes: ..\\re\\notes\\pm0.md (container, raster) and ..\\re\\notes\\vector.md.
Field meanings come from the firmware's map engine (page-0 layer descriptors:
FUN_30340238; directory records: FUN_30343f98; file-size check: FUN_30344860;
vector pages: FUN_3031838c and the accessors at 0x30336878-0x3033713c) and from
DeLorme's maps, all of which read() / write() reproduce byte for byte
(test_pm0.py: 14 raster maps; test_pm0v.py: the base maps and Topo 10).

Coordinates are 8.23 fixed point, plate carree: x = (lon + 256) * 2^23,
y = (256 - lat) * 2^23, so y grows southwards.

The licence record (EL) is carried through as an opaque directory record and
never interpreted. Maps written by make_map.py have none: they are meant for
MSYS0:/basedata, which the firmware loads without a licence check (0x300AA338).
"""
import struct

VERSION = 0x019F                # page 0 starts with it; the engine accepts 0x19B-0x19F
DESC_SIZE = 0x9E                # page-0 layer descriptor
DESC_START = 0x1B6              # page 0: u16 layer count at 0x1B4, descriptors from here
LAST_PAGE_MAX = 0x10000         # FUN_30344860: file size <= last page offset + 64 KB
TILE_MAGIC = b"TR\x01\x00\x06\x01\x00\x00\x00\x00"
TILE_MID = b"\x03\x00\x00\x00\x00\x00\x01\x00\x00\x00"
COMP_RAW, COMP_RLE = 1, 4
RLE_END = b"\x00\x01"           # after the last RLE row, then a pad byte to even length
MIN_LITERAL = 3                 # 00 00/01/02 are escapes (end of row / end of tile / BMP's delta)


class FormatError(Exception):
    pass


def u16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


# -- tiles ------------------------------------------------------------------

class Tile:
    """One TR page: an RGB565 raster covering bounds (x0, y0, x1, y1)."""

    def __init__(self, bounds, width, height, comp, data):
        self.bounds, self.width, self.height, self.comp, self.data = bounds, width, height, comp, data

    @classmethod
    def parse(cls, page):
        if page[:10] != TILE_MAGIC or page[26:36] != TILE_MID:
            raise FormatError("not a raster tile page")
        bounds = struct.unpack_from("<4I", page, 10)
        w, h, bpp, stride, comp, size = struct.unpack_from("<6H", page, 36)
        if bpp != 16 or stride != (2 * w + 3) & ~3 or 48 + size != len(page):
            raise FormatError("unexpected tile header")
        return cls(bounds, w, h, comp, page[48:])

    def serialize(self):
        stride = (2 * self.width + 3) & ~3
        return (TILE_MAGIC + struct.pack("<4I", *self.bounds) + TILE_MID +
                struct.pack("<6H", self.width, self.height, 16, stride, self.comp, len(self.data)) +
                self.data)


def decode_rows(tile):
    """-> list of rows, each a list of RGB565 ints, in stored order"""
    d, rows = tile.data, []
    if tile.comp == COMP_RAW:
        stride = (2 * tile.width + 3) & ~3
        for r in range(tile.height):
            rows.append(list(struct.unpack_from("<%dH" % tile.width, d, r * stride)))
        return rows
    if tile.comp != COMP_RLE:
        raise FormatError("compression %d" % tile.comp)
    o = 0
    for r in range(tile.height):
        end = o + u16(d, o)
        o += 2
        row = []
        while True:
            c, n = d[o], d[o + 1]
            if c == 0 and n == 0:
                o += 2
                break
            if c == 0:
                if n < MIN_LITERAL:
                    raise FormatError("RLE row %d: escape 00 %02x inside a row" % (r, n))
                row.extend(struct.unpack_from("<%dH" % n, d, o + 2))
                o += 2 + 2 * n
            else:
                row.extend([u16(d, o + 1)] * c)
                o += 3
        if o != end or len(row) != tile.width:
            raise FormatError("bad RLE row %d" % r)
        rows.append(row)
    if d[o:] != RLE_END + b"\0" * (o & 1):
        raise FormatError("RLE data ends %s" % d[o:].hex())
    return rows


def encode_raw(rows, width):
    pad = b"\0" * (((2 * width + 3) & ~3) - 2 * width)
    return b"".join(struct.pack("<%dH" % width, *row) + pad for row in rows)


def encode_rle(rows):
    """Row = u16 length (including itself) + runs + 00 00. Run: c >= 1 followed by
    one pixel = that pixel c times; 00 n (n >= 3) followed by n pixels = literals.
    After the last row: 00 01, padded to an even length.

    As in BMP RLE, 00 00 / 00 01 / 00 02 are escapes, so literals are never shorter
    than 3 pixels (DeLorme's never are). One or two stray pixels become 1-runs. The
    firmware's full-size decoder tolerates short literals but its reduced-size
    drawing does not (smeared rows on the device, 2026-10-25)."""
    out = []
    for row in rows:
        runs, lit, i, n = [], [], 0, len(row)

        def flush():
            while lit:
                k = len(lit) if len(lit) <= 255 else (255 if len(lit) - 255 >= MIN_LITERAL else 252)
                part = lit[:k]
                del lit[:k]
                if len(part) >= MIN_LITERAL:
                    runs.append(part)
                else:
                    runs.extend((1, p) for p in part)

        while i < n:
            j = i
            while j < n and j - i < 255 and row[j] == row[i]:
                j += 1
            if j - i >= 2:                      # a 2-run costs 3 bytes, 2 literals cost 4
                flush()
                runs.append((j - i, row[i]))
                i = j
            else:
                lit.append(row[i])
                i += 1
        flush()
        body = bytearray()
        for r in runs:
            if isinstance(r, tuple):
                body += struct.pack("<BH", *r)
            else:
                body += struct.pack("<BB%dH" % len(r), 0, len(r), *r)
        body += b"\0\0"
        out.append(struct.pack("<H", 2 + len(body)) + body)
    data = b"".join(out)
    return data + RLE_END + b"\0" * (len(data) & 1)


# -- raster index (RI page) --------------------------------------------------

class Grid:
    """A layer's index: rows of cells, each cell a tile page or a list of
    (bounds, page) entries.

    Serialized (all little-endian), offsets relative to the index start:
        u16 size, u16 nrows, u16 first_page, u32 y0, u32 ystep
        nrows x { u16 ncols, u16 offset, u16 mask bytes (2 per 16 cells), u32 x0, u32 xstep }
        per row at offset: bitmask (MSB first, 1 = cell is a list offset), u16 cells[ncols]
        lists: u16 n, n x { u32 x0, y0, x1, y1, u16 page }
    """

    def __init__(self, first_page, y0, ystep, rows):
        self.first_page, self.y0, self.ystep, self.rows = first_page, y0, ystep, rows

    @staticmethod
    def mask_bytes(ncols):
        return (ncols + 15) // 16 * 2

    @classmethod
    def parse(cls, b):
        size, nrows, first_page, y0, ystep = struct.unpack_from("<HHHII", b, 0)
        if size != len(b):
            raise FormatError("index size")
        rows, used = [], 14 + 14 * nrows
        for r in range(nrows):
            ncols, off, kind, x0, xstep = struct.unpack_from("<HHHII", b, 14 + 14 * r)
            mb = cls.mask_bytes(ncols)
            if kind != mb:
                raise FormatError("row with %d cells has a %d-byte mask" % (ncols, kind))
            mask = int.from_bytes(b[off:off + mb], "big")
            cells = []
            for c in range(ncols):
                v = u16(b, off + mb + 2 * c)
                if mask >> (8 * mb - 1 - c) & 1:
                    n = u16(b, v)
                    cells.append([(struct.unpack_from("<4I", b, v + 2 + 18 * k), u16(b, v + 18 + 18 * k))
                                  for k in range(n)])
                    used += 2 + 18 * n
                else:
                    cells.append(v)
            used += mb + 2 * ncols
            rows.append((x0, xstep, cells))
        if used != size:
            raise FormatError("index has %d unexplained bytes" % (size - used))
        return cls(first_page, y0, ystep, rows)

    def serialize(self):
        head = 14 + 14 * len(self.rows)
        rowdata = []
        off = head
        for x0, xstep, cells in self.rows:
            rowdata.append(off)
            off += self.mask_bytes(len(cells)) + 2 * len(cells)
        lists = bytearray()
        out_rows = bytearray()
        for (x0, xstep, cells), roff in zip(self.rows, rowdata):
            mb = self.mask_bytes(len(cells))
            mask, vals = 0, []
            for c, cell in enumerate(cells):
                if isinstance(cell, list):
                    mask |= 1 << (8 * mb - 1 - c)
                    vals.append(off + len(lists))
                    lists += struct.pack("<H", len(cell))
                    for bounds, page in cell:
                        lists += struct.pack("<4IH", *bounds, page)
                else:
                    vals.append(cell)
            out_rows += mask.to_bytes(mb, "big") + struct.pack("<%dH" % len(vals), *vals)
        size = off + len(lists)
        out = bytearray(struct.pack("<HHHII", size, len(self.rows), self.first_page, self.y0, self.ystep))
        for (x0, xstep, cells), roff in zip(self.rows, rowdata):
            out += struct.pack("<HHHII", len(cells), roff, self.mask_bytes(len(cells)), x0, xstep)
        return bytes(out + out_rows + lists)

    def pages(self):
        for x0, xstep, cells in self.rows:
            for cell in cells:
                if isinstance(cell, list):
                    for bounds, page in cell:
                        yield page
                else:
                    yield cell


# -- vector pages (TV) --------------------------------------------------------

LINE_TYPES = (3, 10)            # 3 = one part, 10 = several (routing data per part)
AREA_TYPE = 4


class Feature:
    """A line or area record of a TV page.

    u8 type, u16 class (index into the TS record), u8 bbox x0, y0, x1, y1 (0-255),
    u16 label (offset of a text record, 0 = none); lines then u8 flags; u16 n,
    n x (u8 x, u8 y). Lines only, by flag: 0x80 u16 shield (text record offset);
    0x10 / 0x20 routing data (type 3: 4 x u16 / 4 x u32; type 10: u16 count, count x
    (those 4 values, u16 first point)); 0x08 u16 (unknown). Flag 0x40 adds nothing.
    label and shield are Text objects or None; route is raw bytes."""

    __slots__ = ("type", "cls", "bbox", "label", "flags", "points", "shield", "route", "extra")

    def __init__(self, type, cls, bbox, points, label=None, flags=0, shield=None, route=b"", extra=0):
        self.type, self.cls, self.bbox, self.points = type, cls, bbox, points
        self.label, self.flags, self.shield, self.route, self.extra = label, flags, shield, route, extra

    @classmethod
    def parse(cls, p, o):
        """-> (Feature with label/shield still offsets, end offset)"""
        t = p[o]
        if t in LINE_TYPES:
            flags, n, a = p[o + 9], u16(p, o + 10), o + 12
        elif t == AREA_TYPE:
            flags, n, a = 0, u16(p, o + 9), o + 11
        else:
            raise FormatError("feature type %d" % t)
        e = a + 2 * n
        f = cls(t, u16(p, o + 1), p[o + 3:o + 7], p[a:e], u16(p, o + 7), flags)
        if flags & 0x80:
            f.shield = u16(p, e)
            e += 2
        if flags & 0x30:
            w = 16 if flags & 0x20 else 8
            ln = w if t == 3 else 2 + u16(p, e) * (w + 2)
            f.route = p[e:e + ln]
            e += ln
        if flags & 0x08:
            f.extra = u16(p, e)
            e += 2
        return f, e

    def size(self):
        return ((12 if self.type in LINE_TYPES else 11) + len(self.points) + (2 if self.flags & 0x80 else 0) +
                len(self.route) + (2 if self.flags & 0x08 else 0))

    def serialize(self, off):
        out = bytearray(struct.pack("<BH", self.type, self.cls) + self.bbox + struct.pack("<H", off(self.label)))
        if self.type in LINE_TYPES:
            out.append(self.flags)
        out += struct.pack("<H", len(self.points) // 2) + self.points
        if self.flags & 0x80:
            out += struct.pack("<H", off(self.shield))
        out += self.route
        if self.flags & 0x08:
            out += struct.pack("<H", self.extra)
        return bytes(out)


class Text:
    """A text or point record of a TV page (types 0x80-0x83: point, ?, label, shield).

    u8 type, u16 class, u8 x, y (points; 0 for labels), u8 kind (high 3 bits) | number
    of refs, u8 number of names, refs x u16 (feature record offsets), names (NUL ended).
    refs are Feature objects; names are bytes."""

    __slots__ = ("type", "cls", "x", "y", "kind", "refs", "names")

    def __init__(self, type, cls, x, y, kind, refs, names):
        self.type, self.cls, self.x, self.y, self.kind, self.refs, self.names = type, cls, x, y, kind, refs, names

    @classmethod
    def parse(cls, p, o):
        t = p[o]
        if t < 0x80:
            raise FormatError("text type %d" % t)
        nref, nname = p[o + 5] & 0x1F, p[o + 6]
        refs = list(struct.unpack_from("<%dH" % nref, p, o + 7))
        e, names = o + 7 + 2 * nref, []
        for _ in range(nname):
            z = p.index(b"\0", e)
            names.append(p[e:z])
            e = z + 1
        return cls(t, u16(p, o + 1), p[o + 3], p[o + 4], p[o + 5] & 0xE0, refs, names), e

    def size(self):
        return 7 + 2 * len(self.refs) + sum(len(n) + 1 for n in self.names)

    def serialize(self, off):
        return (struct.pack("<BHBBBB", self.type, self.cls, self.x, self.y, self.kind | len(self.refs),
                            len(self.names)) +
                b"".join(struct.pack("<H", off(r)) for r in self.refs) + b"".join(n + b"\0" for n in self.names))


class VPage:
    """One TV page: a box, line/area features, text/point records and their tables.

        "TV", u32 x0, y0, x1, y1          box: point bytes b map to x0 + b (x1 - x0) / 255
        +0x12 u32, +0x16 u32, +0x1A u16    unknown (head)
        +0x1C u16 land colour, +0x1E u16 flags (bit 0: fill the box with it first)
        +0x20 u16 nf, nl, nt, ng, groups offset; +0x2A 4 x u16 unknown (counts)
        +0x32 u16 offsets[nf + nt]         features, then texts
        nl x (u16 offset, u8)              label draw list
        pad to even; ng x 5 u16            groups: region id, first/last feature, first/last text
        u16 n, n x u16                     every record (the item list search refers to)
        records, back to back to the end of the page, in `order`

    Coordinates: Feature.points / Text.x, y are bytes across the box (FUN_3033472c)."""

    def __init__(self):
        self.bounds = (0, 0, 0, 0)
        self.head = b""             # 0x12..0x1C
        self.fill, self.flags = 0xF77A, 1
        self.counts = (0, 0, 0, 0)  # 0x2A..0x31
        self.features, self.texts = [], []
        self.drawlist = []          # (Text, u8)
        self.pad = 0
        self.groups = []            # (region, first feature, last feature, first text, last text)
        self.items = []
        self.order = []             # records in stored order

    @classmethod
    def parse(cls, p):
        v = cls()
        v.bounds = struct.unpack_from("<4I", p, 2)
        v.head = p[0x12:0x1C]
        v.fill, v.flags, nf, nl, nt, ng, gofs = struct.unpack_from("<7H", p, 0x1C)
        v.counts = struct.unpack_from("<4H", p, 0x2A)
        offs = struct.unpack_from("<%dH" % (nf + nt), p, 0x32)
        dl = 0x32 + 2 * (nf + nt)
        if gofs != dl + 3 * nl + ((dl + 3 * nl) & 1):
            raise FormatError("TV group table offset")
        if (dl + 3 * nl) & 1:
            v.pad = p[gofs - 1]
        at = {}
        for i, o in enumerate(offs):
            r, e = (Feature if i < nf else Text).parse(p, o)
            at[o] = (r, e)
        if len(at) != len(offs):
            raise FormatError("TV records share an offset")
        ip = gofs + 10 * ng
        items = struct.unpack_from("<%dH" % u16(p, ip), p, ip + 2)
        pos = ip + 2 + 2 * len(items)
        for o in sorted(at):
            if o != pos:
                raise FormatError("TV record gap at %#x" % pos)
            v.order.append(at[o][0])
            pos = at[o][1]
        if pos != len(p):
            raise FormatError("TV page tail")

        def rec(o, kind=None):
            if not o:
                return None
            if o not in at or (kind and not isinstance(at[o][0], kind)):
                raise FormatError("TV reference %#x is not a record" % o)
            return at[o][0]

        v.features = [at[o][0] for o in offs[:nf]]
        v.texts = [at[o][0] for o in offs[nf:]]
        for f in v.features:
            f.label, f.shield = rec(f.label, Text), rec(f.shield, Text)
        for t in v.texts:
            t.refs = [rec(o, Feature) for o in t.refs]
        v.drawlist = [(rec(u16(p, dl + 3 * i)), p[dl + 3 * i + 2]) for i in range(nl)]
        v.groups = [(g[0], rec(g[1]), rec(g[2]), rec(g[3]), rec(g[4]))
                    for g in (struct.unpack_from("<5H", p, gofs + 10 * k) for k in range(ng))]
        v.items = [rec(o) for o in items]
        return v

    def serialize(self):
        nf, nt, nl, ng = len(self.features), len(self.texts), len(self.drawlist), len(self.groups)
        dl = 0x32 + 2 * (nf + nt)
        gofs = dl + 3 * nl + ((dl + 3 * nl) & 1)
        pos = gofs + 10 * ng + 2 + 2 * len(self.items)
        where = {}
        for r in self.order:
            where[id(r)] = pos
            pos += r.size()
        if pos > 0xFFFF:
            raise FormatError("TV page too large for 16-bit offsets")

        def off(r):
            return 0 if r is None else where[id(r)]

        out = bytearray(b"TV" + struct.pack("<4I", *self.bounds) + self.head +
                        struct.pack("<7H", self.fill, self.flags, nf, nl, nt, ng, gofs) +
                        struct.pack("<4H", *self.counts))
        out += b"".join(struct.pack("<H", off(r)) for r in self.features + self.texts)
        for r, b in self.drawlist:
            out += struct.pack("<HB", off(r), b)
        if len(out) & 1:
            out.append(self.pad)
        for g in self.groups:
            out += struct.pack("<5H", g[0], *(off(r) for r in g[1:]))
        out += struct.pack("<H", len(self.items)) + b"".join(struct.pack("<H", off(r)) for r in self.items)
        for r in self.order:
            out += r.serialize(off)
        return bytes(out)


def vpoints(page, f):
    """Feature points in map units"""
    x0, y0, x1, y1 = page.bounds
    p = f.points
    return [(x0 + p[k] * (x1 - x0) / 255, y0 + p[k + 1] * (y1 - y0) / 255) for k in range(0, len(p), 2)]


# -- layers (page-0 descriptors) ---------------------------------------------

class Layer:
    """A raster ('TR') or vector ('TV') layer: bounds, native level, zoom range shown,
    labels, and its index (the same grid for both kinds).

    Descriptor (0x9E bytes): +0 u32 x0, y0, x1, y1; +0x10 u16 level (1..19);
    +0x12 'TR' / 'TV'; +0x14 u16 index page; +0x16 u16 index offset; +0x18 u16 index size;
    +0x1A u32 flags (0x10 on vector layers); +0x1E name and description (NUL separated,
    0x40 bytes); +0x5E copyright (0x3C bytes); +0x9A u8 min zoom, max zoom, z, 0.
    z = level on raster layers; vector layers differ (Topo 9 regional: level 9, z 10).
    """

    def __init__(self, bounds, level, zoom, label, copyright, grid, flags=0, kind=b"TR", z=None):
        self.bounds, self.level, self.zoom = bounds, level, zoom
        self.label, self.copyright, self.grid, self.flags, self.kind = label, copyright, grid, flags, kind
        self.z = level if z is None else z

    @classmethod
    def parse(cls, d, ri):
        bounds = struct.unpack_from("<4I", d, 0)
        level, kind, page, off, size, flags = struct.unpack_from("<H2sHHHI", d, 0x10)
        if kind not in (b"TR", b"TV") or page != 1 or d[0x9D] != 0 or (kind == b"TR" and d[0x9C] != level):
            raise FormatError("unexpected layer descriptor")
        return cls(bounds, level, (d[0x9A], d[0x9B]), d[0x1E:0x5E], d[0x5E:0x9A],
                   Grid.parse(ri[off:off + size]), flags, kind, d[0x9C]), off, size

    def serialize(self, off, size):
        return (struct.pack("<4IH2sHHHI", *self.bounds, self.level, self.kind, 1, off, size, self.flags) +
                self.label + self.copyright + bytes([self.zoom[0], self.zoom[1], self.z, 0]))


def fixed_str(text, n):
    b = text.encode("ascii") if isinstance(text, str) else text
    if len(b) > n:
        raise FormatError("text too long: %r" % text)
    return b + b"\0" * (n - len(b))


# -- the whole file ----------------------------------------------------------

class Map:
    """pages: dict page number -> bytes for every page that isn't page 0, the RI
    page, a tile or an empty slot; tiles: dict page number -> Tile; vpages: dict
    page number -> VPage."""

    def __init__(self):
        self.name = b""
        self.stamp = 0              # seconds since 1904-01-01 (file header, 3 copies)
        self.p0_stamp = 0           # same clock, page 0 (DeLorme's are a few seconds earlier)
        self.product = b"CMapXMap"
        self.p0_head = b""          # page 0 bytes 2..0x1B4 except stamp and centre
        self.centre = (0, 0)
        self.layers = []
        self.directory = []         # (tag, data) after the descriptors
        self.dir_tail = b""
        self.npages = 0
        self.pages = {}
        self.tiles = {}
        self.vpages = {}

    @classmethod
    def parse(cls, b):
        m = cls()
        m.name = b[:0x20]
        if b[0x20:0x24] != b"\x80\x00\x00\x01" or b[0x30:0x3C] != b"\0\0\0\x01" + b"\0" * 8:
            raise FormatError("unexpected file header")
        m.stamp = struct.unpack_from(">I", b, 0x24)[0]
        if b[0x24:0x28] * 3 != b[0x24:0x30]:
            raise FormatError("header stamps differ")
        m.product = b[0x3C:0x44]
        if b[0x44:0x4C] != b"\0" * 8:
            raise FormatError("unexpected file header")
        n = m.npages = struct.unpack_from(">H", b, 0x4C)[0]
        offs = []
        for i in range(n):
            o, idx = struct.unpack_from(">II", b, 0x4E + 8 * i)
            if idx != i:
                raise FormatError("page table index")
            offs.append(o)
        if b[0x4E + 8 * n:offs[0]] != b"\0\0" or offs != sorted(offs):
            raise FormatError("page table layout")
        if len(b) - offs[-1] > LAST_PAGE_MAX:
            raise FormatError("last page too long")
        page = [b[offs[i]:offs[i + 1] if i + 1 < n else len(b)] for i in range(n)]

        p0 = page[0]
        if struct.unpack_from(">H", p0, 0)[0] != VERSION:
            raise FormatError("version")
        m.p0_stamp = u32(p0, 2)
        m.p0_head = p0[6:0x2A] + p0[0x32:0x1B4]
        m.centre = struct.unpack_from("<II", p0, 0x2A)

        ri = page[1]
        if ri[:2] != b"RI":
            raise FormatError("page 1 is not RI")
        nl = u16(p0, 0x1B4)
        spans = []
        for k in range(nl):
            layer, off, size = Layer.parse(p0[DESC_START + DESC_SIZE * k:DESC_START + DESC_SIZE * (k + 1)], ri)
            m.layers.append(layer)
            spans.append((off, off + size))
        # the indices tile the RI page after its tag, in some order
        pos = 2
        for a, e in sorted(spans):
            if a != pos:
                raise FormatError("RI page gap")
            pos = e
        if pos != len(ri):
            raise FormatError("RI page tail")
        m.ri_order = [k for k, _ in sorted(enumerate(spans), key=lambda t: t[1])]

        o = DESC_START + DESC_SIZE * nl
        cnt = u16(p0, o)
        o += 2
        for _ in range(cnt):
            tag, ln = p0[o:o + 2], u16(p0, o + 2)
            m.directory.append((tag, p0[o + 4:o + 4 + ln]))
            o += 4 + ln
        m.dir_tail = p0[o:]

        for i in range(2, n):
            if page[i][:2] == b"TR":
                m.tiles[i] = Tile.parse(page[i])
            elif page[i][:2] == b"TV":
                m.vpages[i] = VPage.parse(page[i])
            elif page[i] != b"RN":
                m.pages[i] = page[i]
        return m

    def serialize(self):
        nl = len(self.layers)
        grids = [L.grid.serialize() for L in self.layers]
        order = getattr(self, "ri_order", list(range(nl)))
        ri, place = bytearray(b"RI"), {}
        for k in order:
            place[k] = len(ri)
            ri += grids[k]
        p0 = bytearray(struct.pack(">H", VERSION) + struct.pack("<I", self.p0_stamp) + self.p0_head[:0x24] +
                       struct.pack("<II", *self.centre) + self.p0_head[0x24:])
        assert len(p0) == 0x1B4
        p0 += struct.pack("<H", nl)
        for k, L in enumerate(self.layers):
            p0 += L.serialize(place[k], len(grids[k]))
        p0 += struct.pack("<H", len(self.directory))
        for tag, data in self.directory:
            if len(data) & 1:
                raise FormatError("directory record lengths must be even")
            p0 += tag + struct.pack("<H", len(data)) + data
        p0 += self.dir_tail

        pages = [bytes(p0), bytes(ri)]
        for i in range(2, self.npages):
            if i in self.tiles:
                pages.append(self.tiles[i].serialize())
            elif i in self.vpages:
                pages.append(self.vpages[i].serialize())
            else:
                pages.append(self.pages.get(i, b"RN"))
        n = len(pages)
        head = bytearray(fixed_str(self.name, 0x20) + b"\x80\x00\x00\x01" + struct.pack(">I", self.stamp) * 3 +
                         b"\0\0\0\x01" + b"\0" * 8 + self.product + b"\0" * 8 + struct.pack(">H", n))
        pos = 0x4E + 8 * n + 2
        for i, p in enumerate(pages):
            head += struct.pack(">II", pos, i)
            pos += len(p)
        head += b"\0\0"
        if len(pages[-1]) > LAST_PAGE_MAX:
            raise FormatError("last page too long")
        return bytes(head) + b"".join(pages)


def read(path):
    return Map.parse(open(path, "rb").read())
