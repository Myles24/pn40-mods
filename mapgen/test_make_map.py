r"""Offline tests for make_map.py: build maps from synthetic EPSG:4326 GeoTIFFs and
check them against the rules taken from the firmware and DeLorme's maps.

    python test_make_map.py
"""
import os, struct, subprocess, sys, tempfile
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pm0
import make_map

PY = sys.executable
failures = 0


def check(cond, what):
    global failures
    if not cond:
        failures += 1
        print("FAIL", what)


def write_geotiff(path, w, s, e, n, width, height):
    import tifffile
    yy, xx = np.mgrid[0:height, 0:width]
    img = np.stack([xx * 255 // width, yy * 255 // height, (xx // 16 + yy // 16) % 2 * 255], -1).astype(np.uint8)
    img[height // 3:height // 2, width // 4:width // 2] = (40, 90, 200)       # flat area: RLE
    geokeys = [1, 1, 0, 3, 1024, 0, 1, 2, 1025, 0, 1, 1, 2048, 0, 1, 4326]
    tifffile.imwrite(path, img, photometric="rgb", extratags=[
        (33550, "d", 3, ((e - w) / width, (n - s) / height, 0.0)),
        (33922, "d", 6, (0.0, 0.0, 0.0, w, n, 0.0)),
        (34735, "H", len(geokeys), geokeys)])
    return img


def build(tmp, name, bbox, kind):
    tif = os.path.join(tmp, name + ".tif")
    write_geotiff(tif, *bbox, 1200, 900)
    r = subprocess.run([PY, "-I", os.path.join(HERE, "make_map.py"), name, "--bbox", *map(str, bbox),
                        "--source", tif, "--kind", kind, "--out", tmp], capture_output=True, text=True)
    check(r.returncode == 0, "%s: make_map failed\n%s%s" % (name, r.stdout, r.stderr))
    path = os.path.join(tmp, name + make_map.KINDS[kind]["suffix"] + ".pm0")
    return open(path, "rb").read() if os.path.exists(path) else None


def check_map(name, b, bbox, kind):
    k = make_map.KINDS[kind]
    m = pm0.Map.parse(b)
    check(m.serialize() == b, "%s: round-trip" % name)
    check(m.name.rstrip(b"\0") == name.encode(), "%s: name" % name)
    check([L.level for L in m.layers] == sorted(k["levels"]), "%s: levels coarsest first" % name)
    check(all(L.zoom == k["zoom"] for L in m.layers), "%s: zoom range" % name)
    check(all(L.label.startswith(k["label"].encode() + b"\0") for L in m.layers), "%s: layer label" % name)
    check([t for t, _ in m.directory] == [b"IG", b"IA", b"IR", b"VB"], "%s: directory (no licence record)" % name)
    p0 = b[struct.unpack_from(">I", b, 0x4E)[0]:]
    check(p0[0x32] == k["maptype"], "%s: map type byte" % name)
    n = struct.unpack_from(">H", b, 0x4C)[0]
    last = struct.unpack_from(">I", b, 0x4E + 8 * (n - 1))[0]
    check(b[last:] == b"RN", "%s: trailing RN page" % name)
    check(sorted(m.pages) == [33, 49, 305], "%s: index pages" % name)
    check(min(m.tiles) == 321 and sorted(m.tiles) == list(range(321, 321 + len(m.tiles))),
          "%s: tile pages consecutive from 321" % name)
    w, s, e, nn = bbox
    U = make_map.UNIT
    for L in m.layers:
        g = L.grid
        check(set(g.pages()) == set(range(g.first_page, g.first_page + len(set(g.pages())))),
              "%s L%d: first_page and consecutive pages" % (name, L.level))
        sy = make_map.pixel_y(L.level)
        check(g.ystep == 256 * sy and all(r[1] == g.ystep for r in g.rows), "%s L%d: steps" % (name, L.level))
        check(L.bounds[0] <= (w + 256) * U and L.bounds[2] >= (e + 256) * U and
              L.bounds[1] <= (256 - nn) * U and L.bounds[3] >= (256 - s) * U, "%s L%d: covers bbox" % (name, L.level))
        for r, (x0, xstep, cells) in enumerate(g.rows):
            for c, cell in enumerate(cells):
                entries = cell if isinstance(cell, list) else [(m.tiles[cell].bounds, cell)]
                for bounds, p in entries:
                    t = m.tiles[p]
                    check(t.bounds == bounds, "%s: list bounds = tile bounds" % name)
                    cx, cy = x0 + c * xstep, g.y0 + r * g.ystep
                    check(cx <= t.bounds[0] and t.bounds[2] <= cx + xstep and
                          cy <= t.bounds[1] and t.bounds[3] <= cy + g.ystep, "%s: tile inside its cell" % name)
                    check(t.height <= 256 and ((2 * t.width + 3) & ~3) * t.height <= make_map.MAX_RAW,
                          "%s: tile size" % name)
                    check(t.bounds[3] - t.bounds[1] == t.height * sy, "%s: tile y scale" % name)
                    check(48 + len(t.data) <= pm0.LAST_PAGE_MAX, "%s: page size" % name)
    comps = {t.comp for t in m.tiles.values()}
    check(pm0.COMP_RLE in comps, "%s: RLE used for flat areas" % name)
    # georeferencing: the blue box (source rows 300-450, cols 300-600 of 900x1200)
    # must be at its lon/lat in every layer
    for qx, qy, blue in ((0.375, 0.4167, True), (0.20, 0.4167, False), (0.375, 0.30, False), (0.375, 0.53, False)):
        lon, lat = w + (e - w) * qx, nn - (nn - s) * qy
        x, y = (lon + 256) * U, (256 - lat) * U
        for L in m.layers:
            for p in L.grid.pages():
                t = m.tiles[p]
                if t.bounds[0] <= x < t.bounds[2] and t.bounds[1] <= y < t.bounds[3]:
                    rows = pm0.decode_rows(t)[::-1]
                    px = rows[int((y - t.bounds[1]) / (t.bounds[3] - t.bounds[1]) * t.height)][
                        int((x - t.bounds[0]) / (t.bounds[2] - t.bounds[0]) * t.width)]
                    is_blue = px == make_map.to565(np.array([40, 90, 200], np.uint8)).item()
                    check(is_blue == blue, "%s L%d: georeference at %.4f,%.4f" % (name, L.level, lon, lat))
    return m


def main():
    with tempfile.TemporaryDirectory() as tmp:
        cases = [("Test_Aerial", (-75.65, 39.433333, -75.616667, 39.45), "aerial"),
                 ("Test_Topo", (-105.30, 40.00, -105.25, 40.04), "topo"),
                 ("Test_South", (151.20, -33.88, 151.22, -33.86), "aerial")]   # southern/eastern hemisphere
        for name, bbox, kind in cases:
            b = build(tmp, name, bbox, kind)
            if b:
                check_map(name, b, bbox, kind)
                print("ok   %s (%d bytes)" % (name, len(b)))
    # unit checks on the lattice
    L = make_map.make_lattice((-75.65, 39.433333, -75.616667, 39.45), 15, 198, 16)
    check(L.k0 % 16 == 0 and L.k1 % 16 == 0 and L.j0 % 16 == 0 and L.j1 % 16 == 0, "lattice alignment")
    C = L.coarser(1)
    check(C.level == 13 and C.x(C.k0) == L.x(L.k0) and C.y(C.j1) == L.y(L.j1), "coarser lattice edges")
    check(all(make_map.x_of(k * 198, 0x5A00, 198) == k * 0x5A00 for k in range(100)), "cell edges exact")
    print("%d failures" % failures)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
