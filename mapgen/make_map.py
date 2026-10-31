r"""Make a PN-40 raster map (.pm0) from public-domain data.

    python make_map.py NAME --bbox WEST SOUTH EAST NORTH --source naip|topo|FILE.tif

  naip      USDA NAIP / high-res orthoimagery, USGS The National Map (NAIP Plus)
  topo      USGS Topo base map, The National Map
  FILE.tif  a GeoTIFF in EPSG:4326 (lon/lat), or any EPSG code if pyproj is installed

Writes out\NAME-CDOQQ-MAP.pm0 (aerial) or out\NAME-3DTQ-Map.pm0 (topo) and a
preview PNG of the finest layer. Downloads are cached in out\cache\.

The map has no licence record, so it only loads from MSYS0:/basedata, the
built-in base map folder, which the firmware never licence-checks. Install it
with the bootloader's partition update: copy the .pm0 to
SD:\update\0\basedata\, power-cycle with the card in, and the bootloader copies
it into MSYS0:/basedata (the copy stays on the card). To remove it later,
put an EMPTY file with the same name there and power-cycle. MSYS0 has about
85 MB free.

Layout follows DeLorme's own maps (see pm0.py): aerials get levels 15/13/11
(1.2 / 4.8 / 19 m pixels), topo levels 14/12 (2.4 / 9.5 m). Every grid cell is
256 pixels tall and round(256 cos(lat)) wide, so pixels are square on the ground.
"""
import argparse, datetime, hashlib, io, json, math, os, sys, time, urllib.parse, urllib.request
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pm0

Image.MAX_IMAGE_PIXELS = None
UNIT = 1 << 23                  # map units per degree
MAX_RAW = 51200                 # stride * rows per tile: two 99-px tiles per full cell, as DeLorme (max 63488)
OVERSAMPLE = 2                  # fetch at 2x and average down
MAX_FETCH = 1500                # px per request side; the services allow 4000 but time out on big ones
MSYS0_FREE = 85 << 20

KINDS = {
    "aerial": dict(levels=(15, 13, 11), zoom=(10, 17), maptype=0x0F, suffix="-CDOQQ-MAP",
                   label="COLOR DOQQ", what="NAIP Aerial Imagery",
                   copyright="Public domain: USDA NAIP, USGS The National Map"),
    "topo": dict(levels=(14, 12), zoom=(10, 16), maptype=0x0E, suffix="-3DTQ-Map",
                 label="3DTQ", what="USGS Topo (The National Map)",
                 copyright="Public domain: USGS The National Map"),
}
SERVICES = {
    "naip": ("aerial", "https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPPlus/ImageServer/exportImage",
             dict(format="png", bandIds="0,1,2", interpolation="RSP_BilinearInterpolation")),
    "topo": ("topo", "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/export",
             dict(format="png24", transparent="false", dpi=str(96 * OVERSAMPLE))),
}

# Pages DeLorme's cutter reserves for the (empty) search indices; tiles follow.
SG_PAGE, IA_PAGE, IR_PAGE, FIRST_TILE = 33, 49, 305, 321
SG_DATA = b"SG\x0c\x00" + b"\0" * 14
IA_DATA = b"IA\x08\x00" + b"\0" * 6 + b"\x05\x00\x0e\x00"
IR_DATA = b"IR\x08\x00" + b"\0" * 6 + b"\x0f\x00\x00\x00"


def pixel_y(level):
    return 90 << (15 - level)           # map units per pixel row (level 14 = 180)


def x_of(k, xstep, wpx):
    """left edge of global pixel column k (cells are wpx columns, xstep units wide)"""
    return (k * xstep + wpx // 2) // wpx


class Lattice:
    """Pixel grid of one level: global columns [k0, k1), rows [j0, j1)."""

    def __init__(self, level, wpx, k0, k1, j0, j1):
        self.level, self.wpx = level, wpx
        self.sy = pixel_y(level)
        self.step = 256 * self.sy
        self.k0, self.k1, self.j0, self.j1 = k0, k1, j0, j1

    def x(self, k):
        return x_of(k, self.step, self.wpx)

    def y(self, j):
        return j * self.sy

    def coarser(self, steps):
        """the lattice `steps` layers coarser: each layer is 2 levels, 4x the pixel size"""
        f = 4 ** steps
        return Lattice(self.level - 2 * steps, self.wpx,
                       self.k0 // f, self.k1 // f, self.j0 // f, self.j1 // f)

    def lonlat(self):
        """west, south, east, north of the lattice in degrees"""
        return (self.x(self.k0) / UNIT - 256, 256 - self.y(self.j1) / UNIT,
                self.x(self.k1) / UNIT - 256, 256 - self.y(self.j0) / UNIT)


def make_lattice(bbox, level, wpx, align):
    w, s, e, n = bbox
    sy = pixel_y(level)
    step = 256 * sy
    xw, xe = (w + 256) * UNIT, (e + 256) * UNIT
    yn, ys = (256 - n) * UNIT, (256 - s) * UNIT
    k0 = int(math.floor(xw * wpx / step)) // align * align
    k1 = -(-int(math.ceil(xe * wpx / step)) // align) * align
    j0 = int(math.floor(yn / sy)) // align * align
    j1 = -(-int(math.ceil(ys / sy)) // align) * align
    return Lattice(level, wpx, k0, k1, j0, j1)


# -- sources -------------------------------------------------------------------

def http_get(url, tries=6):
    for t in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "pn40-mapgen"}),
                                        timeout=120) as r:
                return r.read()
        except Exception as e:
            if t == tries - 1:
                raise
            print("  retry (%s)" % e)
            time.sleep(3 * (t + 1))


def fetch_service(source, bbox, width, height, cache):
    """RGB array of exactly bbox (w, s, e, n) at width x height square-degree pixels."""
    _, url, extra = SERVICES[source]
    params = dict(extra, bbox="%.12f,%.12f,%.12f,%.12f" % bbox, bboxSR="4326", imageSR="4326",
                  size="%d,%d" % (width, height), f="json")
    query = url + "?" + urllib.parse.urlencode(params)
    path = os.path.join(cache, hashlib.sha1(query.encode()).hexdigest()[:16] + ".png")
    if not os.path.exists(path):
        meta = json.loads(http_get(query))
        if "href" not in meta:
            raise RuntimeError("service error: %s" % meta)
        ext = meta["extent"]
        got = (ext["xmin"], ext["ymin"], ext["xmax"], ext["ymax"])
        tol = (bbox[2] - bbox[0]) / width * 0.01
        if meta["width"] != width or meta["height"] != height or max(abs(a - b) for a, b in zip(got, bbox)) > tol:
            raise RuntimeError("service changed the request: %s" % meta)
        data = http_get(meta["href"])
        with open(path + ".tmp", "wb") as f:
            f.write(data)
        os.replace(path + ".tmp", path)
    return np.asarray(Image.open(path).convert("RGB"))


def from_service(source, lat, cache):
    """-> function(lattice) giving the lattice's RGB image, fetched in chunks"""
    def get(L):
        w, s, e, n = L.lonlat()
        d = L.sy / UNIT / OVERSAMPLE                      # square source pixel, degrees
        H = (L.j1 - L.j0) * OVERSAMPLE
        W = int(round((e - w) / d))
        rows = []
        for r0 in range(0, H, MAX_FETCH):
            r1 = min(H, r0 + MAX_FETCH)
            row = []
            for c0 in range(0, W, MAX_FETCH):
                c1 = min(W, c0 + MAX_FETCH)
                print("  fetch %s rows %d-%d cols %d-%d of %dx%d" % (source, r0, r1, c0, c1, W, H))
                row.append(fetch_service(source, (w + c0 * d, n - r1 * d, w + c1 * d, n - r0 * d),
                                         c1 - c0, r1 - r0, cache))
            rows.append(np.concatenate(row, 1))
        src = Image.fromarray(np.concatenate(rows, 0))
        # square-degree pixels -> lattice pixels (1/cos(lat) wider), area average
        return np.asarray(src.resize((L.k1 - L.k0, L.j1 - L.j0), Image.BOX))
    return get


def from_geotiff(path):
    """-> function(lattice) sampling a GeoTIFF onto the lattice (bilinear)"""
    import tifffile
    with tifffile.TiffFile(path) as t:
        page = t.pages[0]
        tags = {tag.code: tag.value for tag in page.tags}
        img = page.asarray()
        geo = t.geotiff_metadata or {}
    if img.ndim == 2:
        img = np.stack([img] * 3, -1)
    img = img[..., :3]
    if img.dtype != np.uint8:
        img = (img / max(1, img.max()) * 255).astype(np.uint8)
    if 33922 not in tags or 33550 not in tags:
        sys.exit("%s: needs ModelTiepoint and ModelPixelScale tags" % path)
    tie, scale = tags[33922], tags[33550]
    i0, j0, x0, y0 = tie[0], tie[1], tie[3], tie[4]
    sx, sy = scale[0], scale[1]
    epsg = geo.get("ProjectedCSTypeGeoKey") or geo.get("GeographicTypeGeoKey")
    epsg = int(getattr(epsg, "value", epsg)) if epsg else 4326
    area = geo.get("GTRasterTypeGeoKey")
    half = 0.5 if (getattr(area, "value", area) or 1) == 1 else 0.0   # PixelIsArea: tie at the corner
    if epsg == 4326:
        to_src = None
    else:
        from pyproj import Transformer
        to_src = Transformer.from_crs(4326, epsg, always_xy=True)
    print("  %s: %dx%d, EPSG:%d" % (os.path.basename(path), img.shape[1], img.shape[0], epsg))

    def get(L):
        ks = np.arange(L.k0, L.k1) + 0.5
        js = np.arange(L.j0, L.j1) + 0.5
        lon = (ks * L.step / L.wpx) / UNIT - 256
        lat = 256 - js * L.sy / UNIT
        LON, LAT = np.meshgrid(lon, lat)
        X, Y = (LON, LAT) if to_src is None else to_src.transform(LON, LAT)
        fi = (X - x0) / sx + i0 - half
        fj = (y0 - Y) / sy + j0 - half
        out = np.zeros(LON.shape + (3,), np.float32)
        i, j = np.floor(fi).astype(int), np.floor(fj).astype(int)
        a, b = (fi - i)[..., None], (fj - j)[..., None]
        h, w = img.shape[:2]
        ok = (i >= 0) & (j >= 0) & (i + 1 < w) & (j + 1 < h)
        i, j = np.clip(i, 0, w - 2), np.clip(j, 0, h - 2)
        out = ((1 - a) * (1 - b) * img[j, i] + a * (1 - b) * img[j, i + 1] +
               (1 - a) * b * img[j + 1, i] + a * b * img[j + 1, i + 1])
        out[~ok] = 255
        return out.round().astype(np.uint8)
    return get


# -- building the map -----------------------------------------------------------

def to565(rgb):
    r = (rgb[..., 0].astype(np.uint32) * 31 + 127) // 255
    g = (rgb[..., 1].astype(np.uint32) * 63 + 127) // 255
    b = (rgb[..., 2].astype(np.uint32) * 31 + 127) // 255
    return (r << 11 | g << 5 | b).astype(np.uint16)


def make_tile(L, px, ka, kb, ja, jb):
    """tile for global columns [ka, kb), rows [ja, jb); px is the lattice's RGB565 array"""
    rows = px[ja - L.j0:jb - L.j0, ka - L.k0:kb - L.k0][::-1].tolist()   # stored south first
    raw = pm0.encode_raw(rows, kb - ka)
    rle = pm0.encode_rle(rows)
    comp, data = (pm0.COMP_RLE, rle) if len(rle) < len(raw) else (pm0.COMP_RAW, raw)
    return pm0.Tile((L.x(ka), L.y(ja), L.x(kb), L.y(jb)), kb - ka, jb - ja, comp, data)


def build_layer(L, rgb, kind, next_page, tiles):
    px = to565(rgb)
    R0, R1 = L.j0 // 256, (L.j1 - 1) // 256
    C0, C1 = L.k0 // L.wpx, (L.k1 - 1) // L.wpx
    first = next_page
    rows = []
    for R in range(R0, R1 + 1):
        ja, jb = max(R * 256, L.j0), min((R + 1) * 256, L.j1)
        cells = []
        for C in range(C0, C1 + 1):
            ka, kb = max(C * L.wpx, L.k0), min((C + 1) * L.wpx, L.k1)
            stride = lambda w: (2 * w + 3) & ~3
            n = 1
            while stride(-(-(kb - ka) // n)) * (jb - ja) > MAX_RAW:
                n += 1
            cuts = [ka + (kb - ka) * p // n for p in range(n + 1)]
            entries = []
            for a, b in zip(cuts, cuts[1:]):
                t = make_tile(L, px, a, b, ja, jb)
                tiles[next_page] = t
                entries.append((t.bounds, next_page))
                next_page += 1
            cells.append(entries[0][1] if n == 1 else entries)
        rows.append((C0 * L.step, L.step, cells))
    grid = pm0.Grid(first, R0 * L.step, L.step, rows)
    k = KINDS[kind]
    label = pm0.fixed_str(k["label"].encode() + b"\0" + k["what"].encode(), 0x40)
    layer = pm0.Layer((L.x(L.k0), L.y(L.j0), L.x(L.k1), L.y(L.j1)), L.level, k["zoom"], label,
                      pm0.fixed_str(k["copyright"], 0x3C), grid)
    return layer, next_page


def mac_seconds(dt):
    return int((dt - datetime.datetime(1904, 1, 1, tzinfo=datetime.timezone.utc)).total_seconds())


def build_map(name, kind, lattices, images):
    k = KINDS[kind]
    m = pm0.Map()
    m.name = pm0.fixed_str(name, 0x20)
    m.stamp = m.p0_stamp = mac_seconds(datetime.datetime.now(datetime.timezone.utc))
    creator = pm0.fixed_str("pn40-mods mapgen", 0x1C)
    m.p0_head = (b"\xb6\x0d\x00\x00XMap" + creator + bytes([k["maptype"], 0x04]) + b"\0" * (0x1B4 - 0x34))
    fine = lattices[0]
    m.centre = ((fine.y(fine.j0) + fine.y(fine.j1)) // 2, (fine.x(fine.k0) + fine.x(fine.k1)) // 2)
    # DeLorme order: page-0 descriptors and tile pages coarsest first, RI indices finest first
    page = FIRST_TILE
    for L, rgb in reversed(list(zip(lattices, images))):
        layer, page = build_layer(L, rgb, kind, page, m.tiles)
        m.layers.append(layer)
    m.ri_order = list(range(len(m.layers)))[::-1]
    m.directory = [(b"IG", pm0.struct.pack("<III", SG_PAGE, 12, 64000)),
                   (b"IA", pm0.struct.pack("<III", IA_PAGE, 8, 64000)),
                   (b"IR", pm0.struct.pack("<III", IR_PAGE, 8, 64000)),
                   (b"VB", b"\x01\x00\x00\x00")]
    m.pages = {SG_PAGE: SG_DATA, IA_PAGE: IA_DATA, IR_PAGE: IR_DATA}
    m.npages = page + 1                 # trailing 2-byte RN page, as DeLorme's
    return m


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("name", help="map name (letters, digits, _), shown in the map list")
    ap.add_argument("--bbox", nargs=4, type=float, required=True, metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    ap.add_argument("--source", required=True, help="naip, topo or a GeoTIFF path")
    ap.add_argument("--kind", choices=sorted(KINDS), help="for a GeoTIFF: aerial (default) or topo")
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    a = ap.parse_args()

    if a.source in SERVICES:
        kind = SERVICES[a.source][0]
    else:
        kind = a.kind or "aerial"
    k = KINDS[kind]
    stem = a.name + k["suffix"]
    if len(a.name) > 31 or not all(c.isalnum() or c in "_-" for c in a.name):
        sys.exit("name: up to 31 letters, digits, _ or -")
    w, s, e, n = a.bbox
    if not (w < e and s < n and -180 <= w and e <= 180 and -90 <= s and n <= 90):
        sys.exit("bbox must be WEST SOUTH EAST NORTH in degrees")
    lat = (s + n) / 2
    wpx = int(round(256 * math.cos(math.radians(lat))))
    levels = k["levels"]
    f = 4 ** (len(levels) - 1)
    fine = make_lattice(a.bbox, levels[0], wpx, f)
    lattices = [fine] + [fine.coarser(i) for i in range(1, len(levels))]
    mpx = (fine.k1 - fine.k0) * (fine.j1 - fine.j0) / 1e6
    print("%s: level %d grid %dx%d px (%.1f Mpx), %d px per cell row, %.2f m pixels" % (
        stem, fine.level, fine.k1 - fine.k0, fine.j1 - fine.j0, mpx, wpx, fine.sy / UNIT * 111320))
    if mpx > 400:
        sys.exit("area too large for one map (%.0f Mpx at the finest level)" % mpx)

    cache = os.path.join(a.out, "cache")
    os.makedirs(cache, exist_ok=True)
    get = from_service(a.source, lat, cache) if a.source in SERVICES else from_geotiff(a.source)
    img = get(fine)
    images = [img]
    for L in lattices[1:]:
        g = (fine.k1 - fine.k0) // (L.k1 - L.k0)      # 4 per two levels
        h, w_ = L.j1 - L.j0, L.k1 - L.k0
        images.append(img.reshape(h, g, w_, g, 3).mean((1, 3)).round().astype(np.uint8))

    m = build_map(a.name, kind, lattices, images)
    data = m.serialize()
    # self-check: parse it back and compare every pixel with what went in
    back = pm0.Map.parse(data)
    if back.serialize() != data:
        sys.exit("internal error: map does not round-trip")
    for L, rgb, layer in zip(lattices, images, reversed(back.layers)):
        px = to565(rgb)
        for p in layer.grid.pages():
            t = back.tiles[p]
            ka = (t.bounds[0] * L.wpx + L.step // 2) // L.step
            ja = t.bounds[1] // L.sy
            got = np.array(pm0.decode_rows(t), np.uint16)[::-1]
            if not np.array_equal(got, px[ja - L.j0:ja - L.j0 + t.height, ka - L.k0:ka - L.k0 + t.width]):
                sys.exit("internal error: tile %d pixels differ" % p)

    os.makedirs(a.out, exist_ok=True)
    out = os.path.join(a.out, stem + ".pm0")
    with open(out, "wb") as fh:
        fh.write(data)
    Image.fromarray(img).save(os.path.join(a.out, stem + "_preview.png"))
    print("wrote %s: %.1f MB, %d tiles, %d pages, layers %s" % (
        out, len(data) / 1e6, len(m.tiles), m.npages, [L.level for L in m.layers]))
    if len(data) > MSYS0_FREE:
        print("WARNING: larger than the ~85 MB free on MSYS0")
    print("install: copy it to SD:\\update\\0\\basedata\\ and power-cycle with the card in")


if __name__ == "__main__":
    main()
