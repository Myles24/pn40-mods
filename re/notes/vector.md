# .pm0 vector map packages (base map, Topo 9 regional, Topo 10)

Decoded 2026-10-30. Format notes for the vector layers, the goal being a converter that writes
base maps from open data. `mapgen/pm0.py` (`VPage`, `Feature`, `Text`) implements all of this,
and `mapgen/test_pm0v.py` reads and re-writes all 44 DeLorme vector maps on hand byte for byte
(261,275 TV pages, 24.4 million line/area features, 8.5 million text records). The container, page table, page 0, layer descriptors and the `RI`
grid index are the same as for raster maps (see [pm0.md](pm0.md)): `pm0.Grid` parses and
re-serializes every vector index byte for byte. Vector layers are `TV` instead of `TR`.

Files: the 33 maps of the stock `MSYS0:/basedata` (World-Mag1/3/5 and 30 Topo 9 Regional)
and 11 Topo 10 / Topo 8 state maps from the SD card.

## Layer descriptors

| Map | Label | Level | Zoom shown |
|---|---|---|---|
| World-Mag1/3/5 | `BASEMAP` / PN-SERIES Base Map - DeLorme World Base Map | 1 / 3 / 5 | 1-1 ... 4-7 |
| Topo 9 Regional | `REGIONAL` / PN-Series Region East 2A: DC, DE, MD, NJ | 9 | 8-12 |
| Topo 10 | `TOPO NA 10` / Topo North America 10.0 Data Series | 10 | 9-11 |

Flags +0x1A = 0x10 on every vector layer. Descriptor byte +0x9C equals the level on raster
layers but not always on vector ones (regional: level 9, byte 10). Page 0 map type at 0x32:
01 28 (world), 09 48 (regional), 0c 08 (Topo 10).

## Directory records

`IG`, `IA`, `IR` (search indices, non-empty here), `TS` (feature classes, below), `EL`
(licence; present even in the base maps, but basedata is never checked), `VB` (`03000200`,
`04000500`).

### TS: feature classes

`u16 count`, then count × 10 bytes, indexed by the class number in each record
(`FUN_303447D0`: TS data + 2 + class × 10):

| Offset | Contents |
|---|---|
| +0 | u32 class code, hierarchical: 0x1104/0x1107... lines, 0x1183/0x1184... areas, 0x0881... points |
| +4 | u16 colour 1 (RGB565) |
| +6 | u16 colour 2 (RGB565; 0xFFFF often) |
| +8 | u8, u8: width / style (lines 0x40 0x02, 0x20 0x02, 0xA0 0x30...; areas 0x01 0x00) |

Examples (East 2A): water area 0x11841010 `B67C B67C` (light blue); river 0x11040830 `655B
FFFF`; roads 0x11070C10 `A3C1 FFFF`, 0x11070430 `0012 BD1A`. Labels (types 0x82/0x83) have
their own entries with the same code and colour 2 = 0.

The firmware treats some codes specially (`FUN_30317BE8`, display options at UI +0x338):
lines 0x1104, 0x1105, 0x1107, 0x1108, 0x110E, 0x1110; areas 0x1183, 0x1184, 0x1186, 0x118A,
0x118D. The draw passes iterate a class flag table at 0x30685F94 (`FUN_30318B88`).

## TV pages

```
"TV"
u32 x0, y0, x1, y1        quantization box (see Coordinates); pages are cut small enough
+0x12 u32, +0x16 u32      unknown (0x0015487b / 0x002d2d2d / 0x00169696, 0x00020202 ...)
+0x1A u16                 8 (regional), 0 (world)
+0x1C u16                 land fill colour (0xF77A), drawn first when +0x1E bit 0 is set
+0x1E u16                 flags (1)
+0x20 u16 nf              line/area records
+0x22 u16 nl              label draw list entries
+0x24 u16 nt              text/point records
+0x26 u16 ng              region groups
+0x28 u16                 offset of the region groups
+0x2A u16                 index of the last area feature (areas come first; 0 if none)
+0x2C u16, +0x2E u16      index of the first line label (both the same)
+0x30 u16                 index of the first area label
+0x32 u16 offset[nf + nt] record offsets (features, then text/points)
      nl × (u16 offset, u8)   name index: one entry per name, sorted alphabetically;
                          u8 = where the name starts in the record's names (2-byte
                          entries before version 0x19D)
      pad byte to an even offset (= +0x28)
      ng × 5 u16          groups: region id, first/last feature offset, first/last text offset
                          (0 = none)
      u16 n, n × u16      every record offset (n = nf + nt), grouped: the item list search
                          refers to
records                   back to back up to the end of the page, no gaps
```

Texts are ordered: points and shields (kind 0xC0, and 0x81 points), then line labels
(kind 0x40), then area labels (kind 0x80). The counts at +0x2A..+0x30 and the name index
follow this on all 14,511 pages of Topo 10 MD DE DC and Topo 9 East 2A (scratch
`vcounts2.py`); only the line/area-label split was off on 2% of pages.

**Confirmed** by the round trip: every offset in a record or table points at a record start.
Records are not stored in index order or item-list order; the writer keeps the stored order.

Accessors: `FUN_30336A3C` feature i, `FUN_30336A60` text i, `FUN_30336AAC`/`FUN_30336B6C`
label draw list, `FUN_30336E4C`/`FUN_30336F54` region groups, `FUN_30336944` item list.

### Coordinates

Every point is 2 bytes (x, y), 0-255 across the page's box (`FUN_3033472C`):
x = x0 + b0 × (x1 − x0) / 255, y = y0 + b1 × (y1 − y0) / 255. **Confirmed** by rendering
(scratch `vrender.py`): East 2A draws the Delaware Valley correctly in its TS colours.

### Line records (type 3 = one segment, 10 = several)

```
u8 type, u16 class, u8 bbox x0, y0, x1, y1 (0-255), u16 label offset (0 = none),
u8 flags, u16 n, n × (u8 x, u8 y)
[flags 0x80] u16 shield label offset
[flags 0x10 or 0x20] routing data:
   type 3:  4 × u16 (0x10) or 4 × u32 (0x20)
   type 10: u16 count, count × (4 values as above, u16 first point index)
[flags 0x08] u16 (unknown)
```

Flag 0x40 adds no bytes. `FUN_30336878`, `FUN_303368E0`, `FUN_30336BE8`, `FUN_3033713C`.

### Area records (type 4)

`u8 4, u16 class, u8 bbox[4], u16 label offset, u16 n, n × (x, y)`: one ring.

**Confirmed** by the round trip of all 44 files.

### Text and point records (types 0x80-0x83)

```
u8 type, u16 class, u8 x, y (points; 0 0 for labels), u8 kind (high 3 bits) | ref count,
u8 name count, ref count × u16 feature offset, names (NUL ended)
```

`FUN_303369D8` reads the names. 0x80/0x81 are points (`80 07 00 0e b8 c0 01` + "Varden State
Park"), 0x82 labels of features (`82 05 00 00 00 42 02` + 2 feature offsets + "SR 296",
"Easton Tpke"), 0x83 route shields ("296"). **Confirmed** by the round trip.

### Labels on screen

`FUN_30317F84` walks the texts: 0x82 labels whose class code is in the line (>> 23 = 0x22)
or area (0x23) family are placed along their referenced features; 0x83 shields draw a route
marker whose TS byte 8 is the style (1 state, 2 US, 3 Interstate; colour = the road's).
Label TS entries are the feature's code, 0, the text colour, 0.

## Layer label and draw order

`FUN_30314454` maps the layer label to a priority; lower draws on top: `Color DOQQ` 34000,
`SAT10` 38000, `ARCGIS VECTOR` 38500, `Custom Vectors` 39000, `TOPO NA 10` 41000, `REGIONAL`
51000, `BASEMAP` 52000. The label is used nowhere else. mapgen's vector maps use
`Custom Vectors`.

Every page paints its whole box with the land colour first (+0x1E bit 0), covering maps
drawn below it, so a generated map needs data across all of its cells (device, 2026-10-30:
a cell past the data edge showed as an empty band).

All 126,442 of DeLorme's vector pages have bit 0 set (fill colour 0xF77A, or 0xB67C water
on 14 open-sea pages). Pages with bit 0 clear, which painted only their covered part with a
land polygon, **drew nothing on the device**: the whole cell stayed white, with neither the
page nor the maps below it (2026-10-30, a Delaware level-10 edge page). mapgen therefore
only writes full pages, and leaves cells the data doesn't fully cover to other maps.

DeLorme's area records never exceed 148 points (lines: 622). mapgen's had up to 987 without
visible trouble, but this may be a limit worth keeping to.

## Still unknown

- Header +0x12, +0x16, +0x1A; region ids; line flag 0x08's u16 and flag 0x40.
- How pages are cut, and how page boxes relate to the grid cells.
- `IG`/`IA`/`IR` search pages: whether a map with no search data (as in mapgen's raster
  maps) is accepted.
- Text record kinds (the high 3 bits of byte 5: 0x40, 0x80, 0xC0).
