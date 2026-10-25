# .pm0 map files (feasibility notes, 2026-10-25)

Map packages live in `MSYS2:/maps`, `SD:/maps` and `MSYS0:/basedata` (scanned by
FUN_30362688 -> FUN_30360f10, C++ code in the 0x3035xxxx-0x303Fxxxx map engine).

## Container (not yet decoded)

- 0x00: map name, NUL padded to 0x20 (e.g. `Corn_Maze_1470795-SAT10-Map`).
- 0x20: `80 00 00 01`, then the same 4 bytes three times (a timestamp?), `00 00 00 01`, 8 zero bytes.
- 0x3C: `CMap` `XMap`, 8 zero bytes, then fields like `01 46 00 00  0a 80 00 00  00 00 00 00  0d c2 00 00 ...`.
- 0x58...: a table of 8-byte entries whose second word counts 0, 1, 2... (index or page table; the first word grows by 2 per entry).
- No key or licence files on the unit, and the same map files work on SD and internal storage, but see "Device check" below: part of the file is bound to the unit.

## Tile records

Each raster tile starts `00 00 00 01 [00]` then, at T:
`54 52 01 00 06 01 00 00 00 00` ("TR"), T+10: four u32 (the tile's bounds, unknown
projection), T+26: `03 00 00 00 00 00 01 00 00 00`, T+36: u16 width, height, bpp (16),
stride (2*width rounded up to 4), compression (1 = raw rows, 4 = RLE), data size
(u16), T+48: data. Thomas_Cove: 177 tiles (168 RLE, 9 raw), all decode exactly
(a 99x256 tile is typical; the raw ones are the zoomed-out levels).

## Device check

Inverting the RLE tiles from their second row on (v1) displayed fine. Inverting
every pixel (v2: also the 9 raw tiles and the first row of each RLE tile) gives
"Add Map Error: Map not updated for use with this PN-40" (code 0x8005, from the
map engine's add-map call 0x3031343C, shown by 0x300A9FB0). So some part of the
file is bound to the device (DeLorme's per-unit map "update"), and v1 left that part alone.

## Raster tiles: RLE RGB565 (3DTQ topo quads, CDOQQ aerials)

93-98% of those files is this encoding (re-checked by decoding and rendering):

- Pixels are little-endian RGB565, the screen's own format.
- A row is `00 00 <u16 LE length>`, then the row's runs; the length counts the whole row including those 4 bytes.
- A row is really `<u16 length incl. itself>` + runs + `00 00`; the first row of a tile follows the tile header directly.
- Runs: `00 n` + n pixels = n literal pixels (n >= 1); `c pp pp` (c >= 1) = pixel pp repeated c times.
- Seen tile widths 98/99 (also 40, 64, 161-198 at edges), up to 255 rows per block; topo rows are stored bottom-up.
- Not encrypted: entropy 4.8-5.8 bits/byte, and 0xFF appears far less often than in random data.

## SAT10 (10 m satellite)

A different codec: no RLE rows, entropy 6.8 bits/byte. Not decoded.

## Still unknown (needed for a converter)

The tile index, how tiles are laid out in a grid, georeferencing (projection, bounds, scale) and the zoom pyramid. The firmware's reader is the source of truth for these.
