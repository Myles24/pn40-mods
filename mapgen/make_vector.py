r"""Make a PN-40 vector map (.pm0) from OpenStreetMap data: roads, trails, railways,
power lines, streams and rivers, water, woods, wetlands and parks, with road, water
and park names, route shields, towns and points of interest. No search data yet.

    python make_vector.py NAME --geofabrik REGION [REGION ...] [--bbox WEST SOUTH EAST NORTH]
    python make_vector.py NAME --bbox WEST SOUTH EAST NORTH        (Overpass API, small areas)

  REGION   a Geofabrik extract, e.g. north-america/us/delaware: its .osm.pbf and .poly
           are downloaded once into out\cache\ (delete them there to refresh). Several
           extracts are merged (features in more than one count once) into one region,
           so state lines don't matter. Without --bbox the whole region is mapped.

Writes out\NAME-Vector-Map.pm0 and a preview PNG. Data (c) OpenStreetMap
contributors, ODbL.

Like make_map.py's maps it has no licence record, so it only loads from the base
map folder: SD:\basedata with the mods build (BASEDATA_ON_SD), MSYS0:/basedata
on stock firmware.

Layout follows DeLorme's Topo 10 (see ..\re\notes\vector.md): level 12 (shown at
zooms 11-17) and level 10 (zooms 9-11, major features only), one TV page per grid
cell, points as bytes across the cell. Classes and colours are DeLorme's own TS
entries. The layer label "Custom Vectors" draws above Topo 10 and below aerials.
The area is widened to whole level-10 cells (0.088 deg). Every page paints its whole
box with the land colour first (page flag 1, as all of DeLorme's 126,442 pages), so a
cell only gets a page if the data covers all of it: cells the extracts' outlines cut
are left to the maps below. (Pages that painted only their covered part, flag 0,
drew nothing at all on the device, 2026-10-30.) A cell too full for one page (64 KB)
is halved, as DeLorme does, until each part fits. A region too large for one file is
cut into strips NAME_1, NAME_2, ...
"""
import argparse, datetime, hashlib, json, math, os, re, struct, sys, urllib.parse
import numpy as np
import shapely
from PIL import Image, ImageDraw
from shapely import STRtree
from shapely.geometry import LineString, MultiPolygon, Polygon, box
from shapely.ops import linemerge, polygonize, unary_union

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pm0
from make_map import (FIRST_TILE, IA_DATA, IA_PAGE, IR_DATA, IR_PAGE, SG_DATA, SG_PAGE, UNIT, http_get,
                      mac_seconds)

OVERPASS = "https://overpass-api.de/api/interpreter"
GEOFABRIK = "https://download.geofabrik.de/"
PAGE_LIMIT = 60000                      # bytes; TV offsets are 16-bit, DeLorme's pages stay under ~61 KB
MAX_PAGES = 65000                       # the page table index is 16-bit
# All grid indices of a map share the RI page and its 16-bit offsets: at most ~30,000
# cells (2 bytes each) per file. A level-10 cell holds 16 level-12 cells, so 17 in all.
MAX_CELLS10 = 1500
LABEL = pm0.fixed_str(b"Custom Vectors\0OpenStreetMap", 0x40)
COPYRIGHT = pm0.fixed_str("(c) OpenStreetMap contributors, ODbL", 0x3C)
MAPTYPE = (0x0C, 0x08)                  # page 0 +0x32, as Topo 10
LAND = 0xF77A                           # page fill colour, as DeLorme
PAGE_HEAD = b"\x96\x96\x16\x00\x96\x96\x16\x00"     # TV +0x12..+0x19 on most Topo 10 pages

# Classes: DeLorme TS entries (code, colour 1, colour 2, width/style), from Topo 10
# MD DE DC. Order = draw order within a page (areas, then lines bottom to top).
# Label colour: DeLorme's most used name-label entry for the code (None = no names).
CLASSES = [
    # name           TS entry (10 bytes)        type  level 10?  label colour
    ("wood",        "30088a11 53cf 53cf 0100", 4, True, None),
    ("wetland",     "40088a11 d2be d2be 0100", 4, False, None),
    ("park",        "d0088611 79cf 79cf 0100", 4, True, 0x0000),
    ("water",       "60108411 7cb6 7cb6 0100", 4, True, 0x0000),
    ("island",      "20148311 7af7 7af7 0100", 4, True, 0x0000),
    ("power",       "20040911 8a52 ffff 2802", 3, False, None),
    ("stream",      "21080411 5b65 ffff 4002", 3, False, 0x655B),
    ("canal",       "70080411 5b65 ffff 4002", 3, False, 0x655B),
    ("river",       "30080411 5b65 ffff 4002", 3, True, 0x655B),
    ("rail",        "20040811 2c63 2c63 6010", 3, True, 0x0000),
    ("trail",       "301c0711 2c63 ffff 4802", 3, False, 0x632C),
    ("track",       "30180711 2c63 ffff c040", 3, False, 0xFFFF),
    ("local",       "10100711 2c63 ffff c040", 3, False, 0xFFFF),
    ("tertiary",    "300c0711 c1a3 ffff 2002", 3, True, 0xFF56),
    ("secondary",   "100c0711 c1a3 ffff 4002", 3, True, 0xFEB2),
    ("primary",     "70080711 45f9 75fd a030", 3, True, 0xFD75),
    ("trunk",       "60040711 1200 1abd a030", 3, True, 0xBD1A),
    ("ramp",        "b00c0711 1200 ffff 2002", 3, False, None),
    ("motorway",    "30040711 1200 1abd c040", 3, True, 0xBD1A),
]
MAX_REFS = 31                           # text record: ref count in 5 bits
MAX_AREA_POINTS = 148                   # the most any of DeLorme's area records has

# Points (0x80 records): DeLorme class codes from Topo 10 MD DE DC. The firmware picks
# the icon from the code (FUN_303324f0); TS bytes 4-9 as DeLorme's for that code.
BIZ, BIG, PLACE = "0cdd 0cdd 0904", "0000 0000 0906", "ffff ffff 0000"
POIS = {
    "city": (0x088150C0, PLACE), "village": (0x08815070, PLACE), "hamlet": (0x08815080, PLACE),
    "locality": (0x08810410, PLACE),
    "fast_food": (0x08813CC1, BIZ), "restaurant": (0x08813CBC, BIZ), "pizza": (0x08813CC2, BIZ),
    "cafe": (0x08813CC4, BIZ), "ice_cream": (0x08813CC3, BIZ), "bar": (0x08813CFC, BIZ),
    "convenience": (0x08813C94, BIZ), "supermarket": (0x08813C91, BIZ), "pharmacy": (0x08813C64, BIZ),
    "bank": (0x08813D21, BIZ), "worship": (0x08814C20, BIZ), "school": (0x08814810, BIZ),
    "college": (0x08814820, BIZ), "library": (0x08814840, BIZ), "childcare": (0x08813C41, BIZ),
    "fire": (0x08814450, BIZ), "police": (0x08814440, BIZ), "post": (0x08814410, BIZ),
    "government": (0x08814420, BIZ), "hospital": (0x08813D08, BIG), "cemetery": (0x08812440, BIG),
    "hotel": (0x08813CE1, BIZ), "car_repair": (0x08813C24, BIZ), "car_dealer": (0x08813C21, BIZ),
    "car_wash": (0x08813C27, BIZ), "car_parts": (0x08813C22, BIZ), "hardware": (0x08813C53, BIZ),
    "department": (0x08813E02, BIZ), "variety": (0x08813C61, BIZ), "clothes": (0x08813C31, BIZ),
    "shoes": (0x08813C34, BIZ), "hair": (0x08813C43, BIZ), "laundry": (0x08813C42, BIZ),
    "pet": (0x08813C44, BIZ), "phone": (0x08813D75, BIZ), "storage": (0x08813C19, BIZ),
    "alcohol": (0x08813C63, BIZ), "florist": (0x08813C65, BIZ), "books": (0x08813C6D, BIZ),
    "furniture": (0x08813C59, BIZ), "jewelry": (0x08813C35, BIZ), "sports": (0x08813C62, BIZ),
    "funeral": (0x08813C45, BIZ), "fitness": (0x08813D01, BIZ), "museum": (0x08813CF5, BIZ),
    "mall": (0x08813C6E, BIZ), "marina": (0x08813C13, BIZ), "golf": (0x08813DE3, BIZ),
    "camp": (0x08813C85, BIG), "bakery": (0x08813C92, BIZ), "shop": (0x08813C6F, BIZ),
}
AMENITY = {"fast_food": "fast_food", "restaurant": "restaurant", "cafe": "cafe", "ice_cream": "ice_cream",
           "bar": "bar", "pub": "bar", "fuel": "convenience", "pharmacy": "pharmacy", "bank": "bank",
           "place_of_worship": "worship", "school": "school", "college": "college", "university": "college",
           "library": "library", "kindergarten": "childcare", "childcare": "childcare", "fire_station": "fire",
           "police": "police", "post_office": "post", "townhall": "government", "courthouse": "government",
           "hospital": "hospital", "grave_yard": "cemetery", "car_wash": "car_wash", "veterinary": "pet"}
SHOP = {"convenience": "convenience", "supermarket": "supermarket", "chemist": "pharmacy", "car_repair": "car_repair",
        "tyres": "car_repair", "car": "car_dealer", "car_parts": "car_parts", "hardware": "hardware",
        "doityourself": "hardware", "department_store": "department", "variety_store": "variety",
        "clothes": "clothes", "shoes": "shoes", "hairdresser": "hair", "beauty": "hair", "dry_cleaning": "laundry",
        "laundry": "laundry", "pet": "pet", "mobile_phone": "phone", "electronics": "phone",
        "storage_rental": "storage", "alcohol": "alcohol", "florist": "florist", "books": "books",
        "furniture": "furniture", "jewelry": "jewelry", "sports": "sports", "funeral_directors": "funeral",
        "mall": "mall", "bakery": "bakery"}
PLACES = {"city": "city", "town": "city", "village": "village", "hamlet": "hamlet", "suburb": "hamlet",
          "neighbourhood": "hamlet", "locality": "locality"}
LEVEL10_POIS = {"city", "village"}
SHIELD_STATE, SHIELD_US, SHIELD_INTERSTATE = 1, 2, 3     # TS byte 8 of a shield (0x83) entry
CLS = {name: i for i, (name, *_) in enumerate(CLASSES)}

HIGHWAY = {"motorway": "motorway", "motorway_link": "ramp", "trunk": "trunk", "trunk_link": "trunk",
           "primary": "primary", "primary_link": "primary", "secondary": "secondary", "secondary_link": "secondary",
           "tertiary": "tertiary", "tertiary_link": "tertiary", "residential": "local", "unclassified": "local",
           "living_street": "local", "road": "local", "service": "track", "track": "track", "footway": "trail",
           "path": "trail", "cycleway": "trail", "bridleway": "trail", "steps": "trail", "pedestrian": "trail"}
WATERWAY = {"stream": "stream", "ditch": "stream", "drain": "stream", "canal": "canal", "river": "river"}

# levels: (level, zoom range, z byte, classes kept)
LEVELS = [(12, (11, 17), 11, None), (10, (9, 11), 9, {c[0] for c in CLASSES if c[3]})]
STEP10 = 256 * (90 << 5)                # level-10 cell, map units


class Styles:
    """The TS record: CLASSES' entries, then label and shield entries as needed.
    A label (0x82) entry is the feature's code, 0, the text colour, 0; a shield (0x83)
    entry is the code, 0, the road colour, the shield style, 0 (as DeLorme's)."""

    def __init__(self):
        self.entries = [bytes.fromhex(c[1].replace(" ", "")) for c in CLASSES]
        self.index = {e: i for i, e in enumerate(self.entries)}

    def get(self, e):
        if e not in self.index:
            self.index[e] = len(self.entries)
            self.entries.append(e)
        return self.index[e]

    def label(self, cls):
        return self.get(self.entries[cls][:4] + struct.pack("<HHH", 0, CLASSES[cls][4], 0))

    def shield(self, cls, style):
        e = self.entries[cls]
        return self.get(e[:4] + b"\0\0" + e[4:6] + bytes((style, 0)))

    def poi(self, c):
        code, tail = POIS[c]
        return self.get(struct.pack("<I", code) + bytes.fromhex(tail.replace(" ", "")))

    def record(self):
        return struct.pack("<H", len(self.entries)) + b"".join(self.entries)


def shield_of(refs):
    """OSM refs -> (style, number) of the most important route, or None"""
    best = None
    for r in refs:
        mt = re.match(r"^(I|US|[A-Z]{2})[ -]?(\d+[A-Z]?)\b", r.strip())      # "DE 1 Toll" too
        if not mt or mt.group(1) == "CR":
            continue
        style = SHIELD_INTERSTATE if mt.group(1) == "I" else SHIELD_US if mt.group(1) == "US" else SHIELD_STATE
        if best is None or style > best[0]:
            best = (style, mt.group(2))
    return best


def text(s):
    return s.encode("latin-1", "replace")


def poi_class(tags):
    if "place" in tags:
        return PLACES.get(tags["place"])
    a = tags.get("amenity")
    if a == "restaurant" and "pizza" in tags.get("cuisine", ""):
        return "pizza"
    if a in AMENITY:
        return AMENITY[a]
    if "shop" in tags:
        return SHOP.get(tags["shop"], "shop")
    if tags.get("tourism") in ("hotel", "motel"):
        return "hotel"
    if tags.get("tourism") in ("museum", "camp_site"):
        return {"museum": "museum", "camp_site": "camp"}[tags["tourism"]]
    if tags.get("landuse") == "cemetery":
        return "cemetery"
    return {"fitness_centre": "fitness", "golf_course": "golf", "marina": "marina"}.get(tags.get("leisure"))


def fetch_pois(bbox, cache):
    """-> list of (poi class, x, y in map units, name)"""
    w, s, e, n = bbox
    bb = "%.6f,%.6f,%.6f,%.6f" % (s, w, n, e)
    sel = ['nwr["amenity"]["name"]', 'nwr["shop"]["name"]', 'nwr["tourism"~"^(hotel|motel|museum|camp_site)$"]["name"]',
           'node["place"]["name"]', 'nwr["landuse"="cemetery"]["name"]',
           'nwr["leisure"~"^(fitness_centre|golf_course|marina)$"]["name"]']
    q = "[out:json][timeout:180];(%s);out center tags;" % "".join("%s(%s);" % (x, bb) for x in sel)
    path = os.path.join(cache, "poi_%s.json" % hashlib.sha1(q.encode()).hexdigest()[:12])
    if not os.path.exists(path):
        print("downloading OpenStreetMap points of interest (Overpass API)...")
        data = http_get(OVERPASS + "?" + urllib.parse.urlencode({"data": q}))
        with open(path, "wb") as f:
            f.write(data)
    with open(path, "rb") as f:
        elements = json.load(f)["elements"]
    out = []
    for el in elements:
        tags = el.get("tags", {})
        c = poi_class(tags)
        pos = el if "lat" in el else el.get("center")
        if c and pos:
            x, y = to_units(pos["lon"], pos["lat"])
            out.append((c, x, y, text(tags["name"])))
    return out


def classify(tags):
    if "highway" in tags:
        return HIGHWAY.get(tags["highway"])
    if "waterway" in tags:
        return "water" if tags["waterway"] == "riverbank" else WATERWAY.get(tags["waterway"])
    if tags.get("natural") == "water" or tags.get("landuse") in ("reservoir", "basin"):
        return "water"
    if tags.get("natural") == "wood" or tags.get("landuse") == "forest":
        return "wood"
    if tags.get("natural") == "wetland":
        return "wetland"
    if tags.get("leisure") in ("park", "nature_reserve") or tags.get("boundary") == "protected_area":
        return "park"
    if tags.get("railway") == "rail":
        return "rail"
    if tags.get("power") == "line":
        return "power"
    return None


def to_units(lon, lat):
    return ((lon + 256) * UNIT, (256 - lat) * UNIT)


# -- OpenStreetMap ---------------------------------------------------------------

def fetch_osm(bbox, cache):
    w, s, e, n = bbox
    bb = "%.6f,%.6f,%.6f,%.6f" % (s, w, n, e)
    sel = ['way["highway"]', 'way["waterway"]', 'nwr["natural"~"^(water|wood|wetland)$"]',
           'nwr["landuse"~"^(forest|reservoir|basin)$"]', 'nwr["leisure"~"^(park|nature_reserve)$"]',
           'nwr["boundary"="protected_area"]', 'way["railway"="rail"]', 'way["power"="line"]',
           'way["natural"="coastline"]']
    q = "[out:json][timeout:180];(%s);out geom;" % "".join("%s(%s);" % (x, bb) for x in sel)
    path = os.path.join(cache, "osm_%s.json" % hashlib.sha1(q.encode()).hexdigest()[:12])
    if not os.path.exists(path):
        print("downloading OpenStreetMap data (Overpass API)...")
        data = http_get(OVERPASS + "?" + urllib.parse.urlencode({"data": q}))
        with open(path, "wb") as f:
            f.write(data)
    with open(path, "rb") as f:
        return json.load(f)["elements"]


def meta_of(tags):
    """-> (names: route refs then the name, shield)"""
    refs = [r.strip() for r in tags.get("ref", "").split(";") if r.strip()] if "highway" in tags else []
    return tuple(text(x) for x in refs + ([tags["name"]] if "name" in tags else [])), shield_of(refs)


def geometries(elements):
    """-> list of (class name, shapely geometry in map units, names, shield)"""
    out = []
    for el in elements:
        tags = el.get("tags", {})
        c = classify(tags)
        if not c:
            continue
        meta = meta_of(tags)
        area = CLASSES[CLS[c]][2] == 4
        if el["type"] == "way" and "geometry" in el:
            pts = [to_units(p["lon"], p["lat"]) for p in el["geometry"]]
            if area:
                if len(pts) >= 4 and pts[0] == pts[-1]:
                    out.append((c, Polygon(pts).buffer(0)) + meta)
            elif len(pts) >= 2:
                out.append((c, LineString(pts)) + meta)
        elif el["type"] == "relation" and area:
            rings = {"outer": [], "inner": []}
            for mem in el.get("members", []):
                if mem.get("type") == "way" and mem.get("role") in rings and "geometry" in mem:
                    rings[mem["role"]].append(LineString([to_units(p["lon"], p["lat"]) for p in mem["geometry"]]))
            outer = list(polygonize(linemerge(rings["outer"]))) if rings["outer"] else []
            inner = list(polygonize(linemerge(rings["inner"]))) if rings["inner"] else []
            if outer:
                g = unary_union(outer)
                if inner:
                    g = g.difference(unary_union(inner))
                out.append((c, g) + meta)
    return out


def coast_lines(elements):
    return [LineString([to_units(p["lon"], p["lat"]) for p in el["geometry"]]) for el in elements
            if el["type"] == "way" and el.get("tags", {}).get("natural") == "coastline" and "geometry" in el]


# -- Geofabrik extracts ------------------------------------------------------------

def fetch_geofabrik(region, cache):
    """-> (path of the .osm.pbf, coverage polygon in map units from the .poly)"""
    region = region.strip("/")
    stem = os.path.join(cache, region.replace("/", "_"))
    for url, path in ((GEOFABRIK + region + "-latest.osm.pbf", stem + "-latest.osm.pbf"),
                      (GEOFABRIK + region + ".poly", stem + ".poly")):
        if not os.path.exists(path):
            print("downloading %s ..." % url)
            data = http_get(url)
            with open(path, "wb") as f:
                f.write(data)
    return stem + "-latest.osm.pbf", read_poly(stem + ".poly")


def read_poly(path):
    """Osmosis .poly: name, then rings (a "!" before the ring number marks a hole), END"""
    lines = [ln.strip() for ln in open(path, encoding="utf-8") if ln.strip()]
    outer, holes, i = [], [], 1
    while lines[i] != "END":
        hole = lines[i].startswith("!")
        i += 1
        pts = []
        while lines[i] != "END":
            lon, lat = map(float, lines[i].split()[:2])
            pts.append(to_units(lon, lat))
            i += 1
        i += 1
        (holes if hole else outer).append(Polygon(pts))
    g = unary_union(outer)
    return g.difference(unary_union(holes)) if holes else g


def to_units_array(a):
    return np.column_stack(((a[:, 0] + 256) * UNIT, (256 - a[:, 1]) * UNIT))


def load_pbf(paths, area):
    """Read .osm.pbf files -> (geometries as geometries() makes them, points of interest
    as fetch_pois() makes them, coastline LineStrings), all within `area` (map units).
    Objects in more than one file (the extracts overlap at their edges) count once."""
    import osmium
    wkb = osmium.geom.WKBFactory()
    x0, y0, x1, y1 = area.bounds

    def geom(make, obj):
        try:
            return shapely.transform(shapely.from_wkb(make(obj)), to_units_array)
        except Exception:                       # missing node locations at the extract edge
            return None

    geoms, pois, coast = {}, {}, {}             # keyed by OSM object, for the merge

    class Handler(osmium.SimpleHandler):
        def node(self, n):
            if "name" not in n.tags or ("n", n.id) in pois:
                return
            tags = {t.k: t.v for t in n.tags}
            c = poi_class(tags)
            if c and n.location.valid():
                x, y = to_units(n.location.lon, n.location.lat)
                if x0 <= x <= x1 and y0 <= y <= y1:
                    pois[("n", n.id)] = (c, x, y, text(tags["name"]))

        def way(self, w):
            if ("w", w.id) in geoms or w.id in coast:
                return
            tags = {t.k: t.v for t in w.tags}
            if tags.get("natural") == "coastline":
                g = geom(wkb.create_linestring, w)
                if g is not None:
                    coast[w.id] = g
                return
            c = classify(tags)
            if c and CLASSES[CLS[c]][2] == 3:
                g = geom(wkb.create_linestring, w)
                if g is not None and g.intersects(area):
                    geoms[("w", w.id)] = (c, g) + meta_of(tags)

        def area(self, a):
            if ("a", a.id) in geoms or ("a", a.id) in pois:
                return
            tags = {t.k: t.v for t in a.tags}
            c = classify(tags)
            is_area = c is not None and CLASSES[CLS[c]][2] == 4
            pc = poi_class(tags) if "name" in tags and "place" not in tags else None
            if not is_area and not pc:
                return
            g = geom(wkb.create_multipolygon, a)
            if g is None or not g.intersects(area):
                return
            if is_area:
                geoms[("a", a.id)] = (c, g if g.is_valid else g.buffer(0)) + meta_of(tags)
            if pc:
                r = g.representative_point()
                pois[("a", a.id)] = (pc, r.x, r.y, text(tags["name"]))

    for path in paths:
        print("reading %s ..." % os.path.basename(path))
        Handler().apply_file(path, locations=True, idx="flex_mem")
    return list(geoms.values()), list(pois.values()), list(coast.values())


def sea(coast, area):
    """Water polygons from the OSM coastline (land is left of each way, water right)
    within `area` (map units: the box, or the extracts' outline within it). The tidal
    Delaware is mapped this way."""
    coast = [c for c in coast if c.intersects(area)]
    if not coast:
        return []
    faces = polygonize(unary_union([unary_union(coast).intersection(area), area.boundary]))
    out = []
    for face in faces:
        r = face.representative_point()
        line = min(coast, key=lambda c: c.distance(r))
        d, acc = line.project(r), 0.0
        cs = list(line.coords)
        for a, b in zip(cs, cs[1:]):
            acc += math.dist(a, b)
            if acc >= d:
                break
        # y grows southwards, so "right of the way" is a positive cross product here
        if (b[0] - a[0]) * (r.y - a[1]) - (b[1] - a[1]) * (r.x - a[0]) > 0:
            out.append(face)
    return out


# -- pages -----------------------------------------------------------------------

def parts(g):
    if g.is_empty:
        return []
    if hasattr(g, "geoms"):
        return [p for x in g.geoms for p in parts(x)]
    return [g]


def quantize(coords, b, closed):
    x0, y0, x1, y1 = b
    w, h = x1 - x0, y1 - y0
    q = []
    for x, y in coords:
        p = (min(255, max(0, round((x - x0) * 255 / w))), min(255, max(0, round((y - y0) * 255 / h))))
        if not q or p != q[-1]:
            q.append(p)
    if closed and len(q) > 1 and q[0] == q[-1]:
        q.pop()
    return q


def area_rings(poly, b, depth=0):
    """Quantized rings for one hole-free polygon, halved (longer side first) until each
    has at most MAX_AREA_POINTS points: DeLorme's areas never have more."""
    q = quantize(poly.exterior.coords, b, True)
    if len(q) <= MAX_AREA_POINTS or depth >= 12:
        return [q] if len(q) >= 3 else []
    x0, y0, x1, y1 = poly.bounds
    if x1 - x0 >= y1 - y0:
        halves = [box(x0, y0, (x0 + x1) / 2, y1), box((x0 + x1) / 2, y0, x1, y1)]
    else:
        halves = [box(x0, y0, x1, (y0 + y1) / 2), box(x0, (y0 + y1) / 2, x1, y1)]
    return [r for h in halves for p in parts(poly.intersection(h)) if p.geom_type == "Polygon"
            for r in area_rings(Polygon(p.exterior), b, depth + 1)]


def make_feature(cls, q):
    t = CLASSES[cls][2]
    xs, ys = [p[0] for p in q], [p[1] for p in q]
    return pm0.Feature(t, cls, bytes((min(xs), min(ys), max(xs), max(ys))), bytes(b for p in q for b in p))


def make_texts(out, styles):
    """Label (0x82) and shield (0x83) records for the page's named features, one per
    name and style, each referring to up to 31 feature pieces. -> (shields, line
    labels, area labels): DeLorme's order of a page's texts."""
    labels, shields = {}, {}
    for cls, f, names, shield in out:
        if names and CLASSES[cls][4] is not None:
            labels.setdefault((f.type == 4, styles.label(cls), names), []).append(f)
        if shield and f.type != 4:
            shields.setdefault((styles.shield(cls, shield[0]), text(shield[1])), []).append(f)
    sh, ll, al = [], [], []
    for (ts, num), fs in shields.items():
        for i in range(0, len(fs), MAX_REFS):
            t = pm0.Text(0x83, ts, 0, 0, 0xC0, fs[i:i + MAX_REFS], [num])
            for f in t.refs:
                f.shield, f.flags = t, f.flags | 0x80
            sh.append(t)
    for (area, ts, names), fs in labels.items():
        for i in range(0, len(fs), MAX_REFS):
            t = pm0.Text(0x82, ts, 0, 0, 0x80 if area else 0x40, fs[i:i + MAX_REFS], list(names))
            for f in t.refs:
                f.label = t
            (al if area else ll).append(t)
    return sh, ll, al


def build_page(b, z, feats, pois, styles):
    """One TV page for box b = (x0, y0, x1, y1). feats: (class index, geometry, names,
    shield) intersecting it; pois: (poi class, x, y, name)."""
    clip = box(*b)
    pois = [p for p in pois if b[0] <= p[1] < b[2] and b[1] <= p[2] < b[3]]
    out = []
    for cls, g, names, shield in feats:
        for part in parts(g.intersection(clip)):
            if CLASSES[cls][2] == 4:
                if part.geom_type != "Polygon":
                    continue
                rings = [(part.exterior, cls, names)] + [(r, CLS["island"], ()) for r in part.interiors]
                for ring, k, nm in rings:
                    for q in area_rings(Polygon(ring), b):
                        out.append((k, make_feature(k, q), nm, None))
            elif part.geom_type == "LineString":
                q = quantize(part.coords, b, False)
                if len(q) >= 2:
                    out.append((cls, make_feature(cls, q), names, shield))
    if not out and not pois:
        return None
    out.sort(key=lambda t: t[0])                # stable: draw order by class, areas first
    v = pm0.VPage()
    v.bounds = b
    v.head = PAGE_HEAD + struct.pack("<H", z)
    v.fill, v.flags = LAND, 1
    v.features = [t[1] for t in out]
    sh, ll, al = make_texts(out, styles)
    pts = [pm0.Text(0x80, styles.poi(c), *quantize([(x, y)], b, False)[0], 0xC0, [], [name])
           for c, x, y, name in pois]
    sh += pts                                   # kind 0xC0 texts first: shields, then points
    v.texts = sh + ll + al
    nareas = sum(1 for f in v.features if f.type == 4)
    # +0x2A: last area index; +0x2C/+0x2E: first line label; +0x30: first area label
    v.counts = (max(nareas - 1, 0), len(sh), len(sh), len(sh) + len(ll))
    names = [(n, t, sum(len(x) + 1 for x in t.names[:i])) for t in v.texts for i, n in enumerate(t.names)]
    v.drawlist = [(t, o) for n, t, o in sorted(names, key=lambda e: (e[0].upper(), e[0])) if o < 256]
    v.groups = [(0, v.features[0] if v.features else None, v.features[-1] if v.features else None,
                 v.texts[0] if v.texts else None, v.texts[-1] if v.texts else None)]
    v.items = v.features + v.texts
    v.order = v.features + v.texts
    return v


def build_cell(b, z, feats, pois, styles, depth=0):
    """-> [(box, VPage)]: one page, or the box halved (longer side first) until each fits"""
    v = build_page(b, z, feats, pois, styles)
    if v is None:
        return []
    try:
        fits = len(v.serialize()) <= PAGE_LIMIT
    except pm0.FormatError:                     # over 64 KB: its offsets don't even fit
        fits = False
    if fits:
        return [(b, v)]
    if depth >= 8:
        raise SystemExit("a page is still over %d bytes after 8 splits" % PAGE_LIMIT)
    x0, y0, x1, y1 = b
    if x1 - x0 >= y1 - y0:
        m = x0 + (x1 - x0) // 2
        halves = [(x0, y0, m, y1), (m, y0, x1, y1)]
    else:
        m = y0 + (y1 - y0) // 2
        halves = [(x0, y0, x1, m), (x0, m, x1, y1)]
    return [e for hb in halves for e in build_cell(hb, z, feats, pois, styles, depth + 1)]


def build_layer(level, zoom, z, keep, geoms, pois, bbox, cover, next_page, vpages, styles):
    step = 256 * (90 << (15 - level))
    xw, yn = to_units(bbox[0], bbox[3])
    xe, ys = to_units(bbox[2], bbox[1])
    gx0, gy0 = int(xw // step) * step, int(yn // step) * step
    ncols, nrows = math.ceil((xe - gx0) / step), math.ceil((ys - gy0) / step)
    tol = step / 255 / (2 if level == 12 else 1)
    sel = []
    for c, g, names, shield in geoms:
        if keep is not None and c not in keep:
            continue
        g = g.simplify(tol)
        if CLASSES[CLS[c]][2] == 4 and g.area < (4 * tol) ** 2:
            continue
        sel.append((CLS[c], g, names, shield))
    tree = STRtree([e[1] for e in sel])
    bycell = {}
    for p in pois:
        if keep is None or p[0] in LEVEL10_POIS:
            bycell.setdefault((int((p[1] - gx0) // step), int((p[2] - gy0) // step)), []).append(p)
    first, rows, split = next_page, [], 0
    for r in range(nrows):
        cells = []
        for k in range(ncols):
            cx, cy = gx0 + k * step, gy0 + r * step
            cb = box(cx, cy, cx + step, cy + step)
            if not cover.covers(cb):            # data missing in part of it: leave it to other maps
                cells.append(0)
                continue
            feats = [sel[i] for i in sorted(tree.query(cb, predicate="intersects"))]   # keep input order
            entries = build_cell((cx, cy, cx + step, cy + step), z, feats, bycell.get((k, r), []), styles)
            pages = []
            for b, v in entries:
                vpages[next_page] = v
                pages.append((b, next_page))
                next_page += 1
            split += len(pages) > 1
            cells.append(0 if not pages else pages[0][1] if len(pages) == 1 else pages)
        rows.append((gx0, step, cells))
        if next_page > MAX_PAGES:
            raise SystemExit("more than %d pages: the area is too large for one map file" % MAX_PAGES)
    if split:
        print("  level %d: %d crowded cells split into several pages" % (level, split))
    grid = pm0.Grid(first, gy0, step, rows)
    layer = pm0.Layer((int(xw), int(yn), int(xe), int(ys)), level, zoom, LABEL, COPYRIGHT, grid,
                      flags=0x10, kind=b"TV", z=z)
    return layer, next_page


def build_map(name, geoms, pois, bbox, cover):
    m = pm0.Map()
    m.name = pm0.fixed_str(name, 0x20)
    m.stamp = m.p0_stamp = mac_seconds(datetime.datetime.now(datetime.timezone.utc))
    creator = pm0.fixed_str("pn40-mods mapgen", 0x1C)
    m.p0_head = b"\xb6\x0d\x00\x00XMap" + creator + bytes(MAPTYPE) + b"\0" * (0x1B4 - 0x34)
    xw, yn = to_units(bbox[0], bbox[3])
    xe, ys = to_units(bbox[2], bbox[1])
    m.centre = (int((yn + ys) // 2), int((xw + xe) // 2))
    page = FIRST_TILE
    styles = Styles()
    for level, zoom, z, keep in reversed(LEVELS):        # descriptors and pages coarsest first
        layer, page = build_layer(level, zoom, z, keep, geoms, pois, bbox, cover, page, m.vpages, styles)
        m.layers.append(layer)
    m.ri_order = list(range(len(m.layers)))[::-1]
    ts = styles.record()
    m.directory = [(b"IG", struct.pack("<III", SG_PAGE, 12, 64000)),
                   (b"IA", struct.pack("<III", IA_PAGE, 8, 64000)),
                   (b"IR", struct.pack("<III", IR_PAGE, 8, 64000)),
                   (b"TS", ts)]
    m.pages = {SG_PAGE: SG_DATA, IA_PAGE: IA_DATA, IR_PAGE: IR_DATA}
    m.npages = page + 1
    return m


def rgb565(c):
    return ((c >> 11) * 255 // 31, ((c >> 5) & 63) * 255 // 63, (c & 31) * 255 // 31)


def preview(m, layer, path, width=1400):
    x0, y0, x1, y1 = layer.bounds
    lat = 256 - (y0 + y1) / 2 / UNIT
    h = int(width * (y1 - y0) / (x1 - x0) / math.cos(math.radians(lat)))
    img = Image.new("RGB", (width, h), rgb565(LAND))
    dr = ImageDraw.Draw(img)
    pages = [m.vpages[p] for p in layer.grid.pages() if p]
    for want in (4, 3):
        for v in pages:
            for f in v.features:
                if (f.type == 4) != (want == 4):
                    continue
                if f.cls >= len(CLASSES):
                    continue
                e = bytes.fromhex(CLASSES[f.cls][1].replace(" ", ""))
                col = rgb565(struct.unpack_from("<H", e, 4)[0])
                pts = [((x - x0) / (x1 - x0) * width, (y - y0) / (y1 - y0) * h) for x, y in pm0.vpoints(v, f)]
                if want == 4 and len(pts) >= 3:
                    dr.polygon(pts, fill=col)
                elif want == 3 and len(pts) >= 2:
                    dr.line(pts, fill=col, width=max(1, e[8] >> 6))
    img.save(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("name", help="map name (letters, digits, _), shown in the map list")
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    ap.add_argument("--geofabrik", nargs="+", metavar="REGION", help="e.g. north-america/us/delaware")
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    a = ap.parse_args()
    if len(a.name) > 28 or not all(c.isalnum() or c in "_-" for c in a.name):
        sys.exit("name: up to 28 letters, digits, _ or - (room for a strip number)")
    if not a.bbox and not a.geofabrik:
        sys.exit("give --geofabrik REGION and/or --bbox WEST SOUTH EAST NORTH")
    cache = os.path.join(a.out, "cache")
    os.makedirs(cache, exist_ok=True)
    if a.geofabrik:
        got = [fetch_geofabrik(r, cache) for r in a.geofabrik]
        pbfs, poly = [g[0] for g in got], unary_union([g[1] for g in got])
        if not a.bbox:
            px0, py0, px1, py1 = poly.bounds
            a.bbox = (px0 / UNIT - 256, 256 - py1 / UNIT, px1 / UNIT - 256, 256 - py0 / UNIT)
    w, s, e, n = a.bbox
    if not (w < e and s < n) or not a.geofabrik and (e - w) * (n - s) > 1.0:
        sys.exit("bbox must be WEST SOUTH EAST NORTH in degrees (at most 1 square degree with Overpass)")
    # whole level-10 cells
    xw, yn = to_units(w, n)
    xe, ys = to_units(e, s)
    xw, yn = xw // STEP10 * STEP10, yn // STEP10 * STEP10
    xe, ys = -(-xe // STEP10) * STEP10, -(-ys // STEP10) * STEP10
    a.bbox = (xw / UNIT - 256, 256 - ys / UNIT, xe / UNIT - 256, 256 - yn / UNIT)
    area = box(xw, yn, xe, ys)
    print("area widened to whole cells: %.4f %.4f %.4f %.4f" % a.bbox)
    if a.geofabrik:
        geoms, pois, coast = load_pbf(pbfs, area)
        cover = poly.intersection(area)         # where the extracts have complete data
    else:
        cover = area
        elements = fetch_osm(a.bbox, cache)
        geoms, coast = geometries(elements), coast_lines(elements)
        pois = fetch_pois(a.bbox, cache)
    seas = [("water", p, (), None) for p in sea(coast, cover)]
    geoms = seas + geoms                        # sea first: drawn under lakes and islands
    print("%d features (%d sea polygons), %d points of interest and places" % (len(geoms), len(seas), len(pois)))
    # one file, or strips (along the longer side, on level-10 cell lines) small enough
    # for the RI page; named NAME_1, NAME_2, ...
    nc, nr = round((xe - xw) / STEP10), round((ys - yn) / STEP10)
    k = -(-nc * nr // MAX_CELLS10)
    if k <= 1:
        strips = [(a.name, xw, yn, xe, ys)]
    elif nc >= nr:
        cuts = [xw + (nc * i // k) * STEP10 for i in range(k + 1)]
        strips = [("%s_%d" % (a.name, i + 1), cuts[i], yn, cuts[i + 1], ys) for i in range(k)]
    else:
        cuts = [yn + (nr * i // k) * STEP10 for i in range(k + 1)]
        strips = [("%s_%d" % (a.name, i + 1), xw, cuts[i], xe, cuts[i + 1]) for i in range(k)]
    if len(strips) > 1:
        print("too many cells for one file: %d strips" % len(strips))
    tree = STRtree([g for _, g, _, _ in geoms])
    for name, sx0, sy0, sx1, sy1 in strips:
        sb = box(sx0, sy0, sx1, sy1)
        scover = cover.intersection(sb)
        if not any(scover.covers(box(x, y, x + STEP10, y + STEP10))
                   for x in range(int(sx0), int(sx1), STEP10) for y in range(int(sy0), int(sy1), STEP10)):
            continue
        sgeoms = [geoms[i] for i in sorted(tree.query(sb, predicate="intersects"))]
        spois = [p for p in pois if sx0 <= p[1] < sx1 and sy0 <= p[2] < sy1]
        sbbox = (sx0 / UNIT - 256, 256 - sy1 / UNIT, sx1 / UNIT - 256, 256 - sy0 / UNIT)
        write_map(name, sgeoms, spois, sbbox, scover, a.out)


def write_map(name, geoms, pois, bbox, cover, outdir):
    m = build_map(name, geoms, pois, bbox, cover)
    data = m.serialize()
    back = pm0.Map.parse(data)
    if back.serialize() != data:
        sys.exit("internal error: map does not round-trip")
    stem = name + "-Vector-Map"
    out = os.path.join(outdir, stem + ".pm0")
    with open(out, "wb") as fh:
        fh.write(data)
    preview(back, back.layers[-1], os.path.join(outdir, stem + "_preview.png"))
    for L in back.layers:
        pages = [p for p in L.grid.pages() if p]
        print("  level %d: %d pages, %d features, largest page %d bytes" % (
            L.level, len(pages), sum(len(back.vpages[p].features) for p in pages),
            max(len(back.vpages[p].serialize()) for p in pages)))
    print("wrote %s: %.2f MB, %d pages" % (out, len(data) / 1e6, m.npages))
    print("install: copy it to SD:\\basedata\\ (mods build) and restart the unit")


if __name__ == "__main__":
    main()
