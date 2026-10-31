# .pm0 raster map packages (3DTQ topo quads, CDOQQ aerials)

Decoded 2026-10-25. `mapgen/pm0.py` implements all of this. `mapgen/test_pm0.py` reads and
re-writes all 14 DeLorme raster maps on the unit byte for byte, and every tile decodes.
SAT10 (10 m satellite) uses a different, undecoded tile codec. Vector maps (Topo, base
maps) are out of scope.

Map packages live in `MSYS2:/maps`, `SD:/maps` and `MSYS0:/basedata`. The map engine is
C++ code at 0x3030xxxx-0x303Fxxxx.

## Coordinates

8.23 fixed point, plate carrée: x = (lon + 256) × 2^23, y = (256 − lat) × 2^23. So y
grows southwards (**confirmed**: tile mosaics and the on-device map).

## File header (big-endian)

| Offset | Contents |
|---|---|
| 0x00 | map name, NUL padded to 0x20 (DeLorme: the file name without `.pm0`) |
| 0x20 | `80 00 00 01` |
| 0x24 | timestamp ×3 (u32 BE, seconds since 1904-01-01; DeLorme's are 2009-2014) |
| 0x30 | `00 00 00 01`, 8 zero bytes |
| 0x3C | `CMap` `XMap`, 8 zero bytes |
| 0x4C | u16 page count n |
| 0x4E | page table: n × (u32 file offset, u32 page number = index) |
| 0x4E+8n | 2 zero bytes, then page 0 |

A page runs to the next page's offset. **Confirmed** check in `FUN_30344860`: the file may
end at most 64 KB after the last page starts. DeLorme always ends with a 2-byte `RN` page.

## Pages

| Page | Contents |
|---|---|
| 0 | header and directory (below) |
| 1 | `RI`: the raster indices of all layers |
| 33 / 49 / 305 | `SG` / `IA` / `IR`: empty search indices, the same bytes in every raster map |
| 321... | `TR` tiles, then one trailing `RN` |
| others | `RN` (2 bytes): unused slots |

Two maps cut by "HH In-House Map Cutter 376." use pages 65/81/337 and tiles from 353.

## Page 0 (little-endian except the version)

| Offset | Contents |
|---|---|
| 0x00 | u16 BE version 0x019F. The engine accepts 0x19B-0x19F (`FUN_303125EC`, error 0x8004) |
| 0x02 | u32 timestamp (a few seconds before the header's) |
| 0x06 | `B6 0D 00 00` `XMap` |
| 0x0E | creator, 0x1C bytes ("HH In-House Map Cutter") |
| 0x2A | map centre: u32 y, u32 x |
| 0x32 | map type: 0x0E 3DTQ, 0x0F CDOQQ; then 0x04 |
| 0x34 | the cutter's work path, zero padded to 0x1B4 |
| 0x1B4 | u16 layer count |
| 0x1B6 | layer descriptors, 0x9E bytes each |
| then | u16 record count, records `u16 tag, u16 length (even), data` |

**Layer descriptor** (read by `FUN_30340238`, **confirmed**):

| Offset | Contents |
|---|---|
| +0x00 | u32 x0, y0, x1, y1: the layer's bounds |
| +0x10 | u16 native level, 1-19 (aerial 11/13/15, topo 12/14) |
| +0x12 | `TR` (raster; `TV` = vector) |
| +0x14 | u16 page of the index (1), u16 offset in that page, u16 index size |
| +0x1A | u32 flags (0) |
| +0x1E | label and description, NUL separated, 0x40 bytes: `COLOR DOQQ\0Color Aerial Imagery`, `3DTQ\0USGS Quads (3DTQ)` |
| +0x5E | copyright, 0x3C bytes |
| +0x9A | u8 min zoom, max zoom (aerial 10/13-17, topo 10-16), level, 0 |

The label sets the draw order (`FUN_30314454`: `Color DOQQ` 34000, `DOQQ` 35000, `3DTQ`
36000, ..., `REGIONAL` 51000, `BASEMAP` 52000, unknown 50000; compared case-insensitively).
Several layers can share a level: one per source quad.

**Directory records** (`FUN_30343F98`, **confirmed**): `IG` (12 bytes: u32 page 33, u32 12,
u32 64000), `IA` (49, 8, 64000), `IR` (305, 8, 64000), `EL` (licence, below), `VB` (`01 00 00 00`).
`CX` continues the list on another page. An odd length is an error.

## Raster index (RI page)

`RI`, then each layer's index at the offset its descriptor gives. DeLorme puts the finest
layer first. Each index is a grid of cells (**confirmed** by byte-exact re-serialization
of every map):

```
u16 size, u16 nrows, u16 first_page (lowest tile page of the layer), u32 y0, u32 ystep
nrows x { u16 ncols, u16 offset, u16 mask bytes (2 per 16 cells), u32 x0, u32 xstep }
per row, at offset: bitmask (MSB first; 1 = the cell is a list offset), u16 cell[ncols]
lists: u16 n, n x { u32 x0, y0, x1, y1, u16 tile page }
```

A cell holds 0 (no data), a tile page (one tile covers the cell's data), or an offset to a
list (several tiles, side by side). Offsets are relative to the index start. The lists follow
the row data in row order.

- Grid step = 256 pixel rows at the layer's level, and xstep = ystep in map units.
- Pixel height = 90 × 2^(15 − level) units: level 15 = 90 (1.19 m), 14 = 180, 13 = 360,
  12 = 720, 11 = 1440.
- A cell is round(256 × cos(lat)) pixels wide (198 at 39.5° N), so pixels are about square
  on the ground.
- A full 198×256 cell is two 99-px tiles in a list. Edge cells that fit in one tile are
  direct.
- DeLorme's tiles are at most 256 rows and 63,488 raw bytes; tile pages are up to 61,032
  bytes (a 64 KB tile buffer is allocated in `FUN_30318FA8`).

## Tile pages

```
"TR" 01 00 06 01 00 00 00 00
u32 x0, y0, x1, y1            the tile's exact pixel extent (clipped to the map at edges)
03 00 00 00 00 00 01 00 00 00
u16 width, height, 16 (bpp), stride = (2 width + 3) & ~3, compression, data size
data
```

- Rows are stored **south first** (bottom-up), for aerials and topo (**confirmed** by
  rendering).
- Pixels are little-endian RGB565.
- **Compression 1 (raw):** rows at `stride`; the padding bytes are junk.
- **Compression 4 (RLE):** each row is `u16 length (including itself)`, then runs, then
  `00 00`.
  - A run is `00 n` + n pixels (literals, n ≥ 3), or `c pixel` (c ≥ 1, repeated c times).
  - As in BMP RLE, `00 00` ends a row, `00 01` ends the tile and `00 02` is presumably
    BMP's delta escape. DeLorme's literals are 3-162 pixels and runs 1-197. Lone pixels
    are 1-runs. **Confirmed on the device:** 1- and 2-pixel literals draw correctly at
    full size but smear rows when the map is drawn reduced; with literals of 3 or more the
    same map draws correctly at every zoom.
  - After the last row comes `00 01`, plus a pad byte to an even length.

## Licence ("EL") and basedata

`EL` holds a per-unit licence. `FUN_30345C78` returns error 0x8005 ("Map not updated for use
with this PN-40") when neither `EL` nor `2L` is present, and 0x8007 when the check fails.
`mapgen` leaves the licence scheme alone: generated maps have no `EL` record.

Instead they go in `MSYS0:/basedata`, the built-in base map folder.
- `FUN_300AA2E0` (0x300AA338) passes "check licence" = 0 for any path that starts with
  `MSYS0:/basedata` (**confirmed**). Its 115 DeLorme regional base maps load that way.
- The bootloader's partition update copies `SD:\update\0\...` into MSYS0 file by file (see
  `nor/BOOTLOADER.md`), so one map can be added or removed without touching the rest.

Device history: an edited copy of a DeLorme map in `MSYS2:/maps` was rejected with "Map not
updated for use with this PN-40". Edited or generated maps belong in basedata.
