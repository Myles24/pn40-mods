# PN-40 firmware mods: handoff notes (2026-10-25)

These notes are for whoever picks this project up next. The project modifies the firmware of a
DeLorme Earthmate PN-40 handheld GPS (firmware 2.9.6.70319, ARM926EJ-S, OS20 RTOS). The owner
installs builds by copying a `.hex` file to the SD card.

**Start with these:** `README.md` (overview), `mods/README.md` (every patch, address and design
decision), `nor/BOOTLOADER.md` (how updates are flashed), `re/UI.md` and `re/notes/*.md`
(reverse-engineering notes).

## Rules that must be kept

- Never write below 0x30080000: that is the bootloader, and there is no recovery except JTAG.
  `build.py` refuses to.
- Never write the identity/marker blocks either (0x307FC000+, the last block).
- Every patch checks the original bytes before replacing them.
- Derive firmware facts from the code, not from assumptions.
- Data field IDs are appended (161 Battery Voltage, 162 Total Ascent, 163 Total Descent).
  Never renumber or remove one. To add one, add an entry to `OUR_FIELDS` and raise
  `PANE_TABLE_LEN` in both `build.py` and `fw.h` (currently 164).
- Calling convention: old APCS. A double after a pointer arrives in r1:r2. Realign sp to 8 bytes
  before calling C.
- No libgcc and no floating point in mod code (`udiv()` exists for division).
- File I/O only from the UI task, because `fopen` uses a shared static buffer.
- Mods live at 0x306D0000. State lives in RAM freed from heap region 1 (0x0076A5CC-0x007735CB);
  `.data` and `.bss` must stay empty.
- Save every installed build as `mods/baseline_<sha8>/PN-40.hex`; it is the base for the next
  delta update.

## Environment

- Compiler: MSYS2 `arm-none-eabi-gcc` in `C:\msys64\mingw64\bin`.
- Python with unicorn and capstone: `C:\Users\Carson\tools\pn40-venv\Scripts\python.exe -I`.
- Build: `cd mods; python build.py ..\update\PN-40.hex [--base baseline_<installed>\PN-40.hex]`.
  - Writes `out/PN-40.hex`.
  - With `--base`, also writes `out/PN-40.delta-from-<sha8>.hex`: only the changed 64 KB blocks,
    checked by a flash simulation. A delta is only valid on exactly that base build.
- Tests: `test_pnhex.py` (6), `test_nmealog.py` (12), `test_ascent.py` (17), `test_profile.py` (10),
  `test_prof.py` (11), `test_bbox.py` (11). All 67 pass.
  - Map tests: `mapgen/test_pm0.py` (14 DeLorme raster maps round-trip),
    `mapgen/test_pm0v.py` (44 vector maps from `dumps/`) and `mapgen/test_make_map.py`
    (3 synthetic maps). All pass. The venv now also has numpy,
    Pillow and tifffile.
- Install: copy the file to `SD:\update\PN-40.hex`, with no `0\` folder and no other `.hex` in
  `update\`, then power-cycle.
- The SD card appears as `H:`, through USB drive mode or a card reader. In USB mode, "Connect to
  Computer" chooses the SD card or the internal drive; the internal drive is `MSYS2:` on the
  device (maps, FieldNotes.txt).

## Device state right now

- **Installed (2026-10-30):** delta build `f8d1281c…`, build id `D1D0F9F4`, saved in
  `mods/baseline_f8d1281c`. Use it as the base for the next delta.
  - It is `b1d80a58` (CPU at 234 MHz, every feature below, including the black box) plus
    `BASEDATA_ON_SD`: the base map folder is `SD:/basedata` (see `mods/README.md`).
  - Owner's report: the base map draws, at about the same speed as from MSYS0.
  - Not yet seen on the device: a map that exists only on the card (the base maps and the
    Thomas Cove NAIP map are also still in `MSYS0:/basedata`); exporting the SD card over USB
    and back.
- **Reverts:**
  - `mods/out/PN-40.revert-to-b1d80a58-from-f8d1281c.hex` (base map folder back on MSYS0);
  - from `b1d80a58`: `mods/out/PN-40.revert-to-2bc68299-from-b1d80a58.hex` (175.5 MHz build)
    and `mods/out/PN-40.revert-to-7a061979-from-b1d80a58.hex` (stock clock).
- **SD card:** new 32 GB card (2026-10-30): MBR, one FAT32 partition (type 0x0B, 4 MiB
  offset, 32 KB clusters) like the old 4 GB card. It holds the old card's files and
  `SD:\basedata` (the stock base maps from `dumps/internal0_files/basedata`, plus the Thomas
  Cove NAIP map), checked against sha256 sums. Old card backup:
  `dumps/sdcard_files_2026-10-30`.
- **Internal drive (`MSYS2:`) maps removed** (2026-10-30, owner's request): `maps\` is empty.
  All 24 DeLorme maps (and `temp_bulk_copy\`) are in `internal drive/maps`, checked by sha256
  just before the delete. Thomas Cove had been restored there on 2026-10-25 (c44fbe50…6497).
- **First generated map:** `mapgen/out/Thomas_Cove_NAIP-CDOQQ-MAP.pm0` (NAIP, same footprint
  as the DeLorme Thomas Cove map).
  - The first install worked: the map was copied into MSYS0 and drew correctly at full size.
  - Drawn reduced, rows smeared: the RLE encoder had written 1-2 pixel literals, which are
    escapes. Fixed.
  - **Installed and confirmed:** the fixed build (sha 98f9996e) replaced the bad copy on the
    next power-cycle and draws correctly zoomed in and out. The bootloader leaves the source in
    `SD:\update\0\basedata\`; that's harmless.

## Features built (all in mods/, details in mods/README.md)

| Feature | Files | Notes |
|---|---|---|
| GPS week-rollover fix | `patched/make_patch.py` | weeks 1976-2999 |
| NMEA logger | `nmealog.c` | logs to `SD:\nmea\` while that folder exists |
| Battery Voltage field (161) | `battfield.c`, `fields.S` | pane table copied and extended |
| Total Ascent/Descent (162/163) | `ascent.c` | 3 m baro / 10 m GPS deadband, 15 m/s jump guard, trip save every 25 m; totals in trip +0x28/+0x2C (cm) |
| Elevation profile page | `profile.c` | replaces Hunt/Fish (page 9); ENTER switches time/distance, ZOOM changes span; 12 h ring in RAM |
| Diagnostics on every screenshot | `prof.c`, `prof_hooks.S` | `maptime.txt`, `cpuprof.txt` (only while `SD:\prof` exists), `sysinfo.txt` (clock, bus, stress test, compass, registers) |
| Compass task retry | `prof_hooks.S` | retries a failed sensor read instead of stopping (a safeguard) |
| CPU overclock | `build.py` `CPU_CLOCK` | 234 MHz: SRC_PLLFR 0x16002202, HCLK field 3, bus stays 58.5 MHz |
| Black box | `bbox.c`, `bbox_hooks.S` | records shutdowns, watchdog expiries and CPU faults; the record survives a reset; writes `bbox_live.txt` / `bbox_prev.txt` |
| Delta updates | `pnhex.py` | only the changed 64 KB blocks |
| Base maps on SD | `build.py` `BASEDATA_ON_SD` | `MSYS0:/basedata` -> `SD:/basedata`, plus 3 USB-export patches (build f8d1281c, installed) |

## Findings worth knowing

- **Clocks:** CPU = PLL1 = 26 MHz × (bits 8-13 + 2) >> bits 0-2 of SRC_PLLFR (0x101E0014).
  - Bus = PLL1 / (SRC_CR bits 14:13 + 1); measured.
  - Stock is 117 MHz. 175.5 and 234 MHz are tested; 234 passed the stress test.
  - Tasks run in user mode. Mask IRQs with OS20 SWI 0/1.
- **USB shutdown:** at 175.5 MHz the unit used to shut down when the SD card was exported over
  USB. At 234 MHz with the black box it worked, including switching SD → internal → SD, with no
  faults.
  - The battery dropped from 2.97 to 2.79 V the moment the export began.
  - Leading hypothesis: the firmware's critical-battery power-off (0x30084DD8). Unconfirmed.
  - If it recurs, `bbox_prev.txt` names the caller.
- **Watchdog:** SP805 at 0x101E1000 (5 s). The watchdog task checks 10 software slots at
  0x2B006C: slot 1 = UI loop (120 s), slot 4 = NMEA loop (20 s).
- **Fault vectors:** stock undefined/abort vectors are self-loops, so a fault freezes the unit
  until the watchdog resets it.
- **Map slowness:** in the slowest map redraw, about a third of the time was file reads and the
  rest CPU (soft-float, memcpy, divides).
  - Cache is write-through, with no maintenance after boot.
  - Maps on SD versus internal storage hasn't been benchmarked yet; a proposed benchmark is
    still open.

## Maps from public data (mapgen/, format: re/notes/pm0.md)

The owner wants to replace their outdated maps with maps made from public data.

- **Raster format fully decoded** (3DTQ topo, CDOQQ aerial). This covers the container,
  page-0 layer descriptors and directory, the RI grid index and tiles (RGB565 raw/RLE,
  rows stored south first).
  - `mapgen/pm0.py` reads and re-writes all 14 DeLorme raster maps byte for byte
    (`test_pm0.py`).
  - SAT10 uses an undecoded codec.
- **Vector format decoded** (base map, Topo 9 regional, Topo 10; `re/notes/vector.md`,
  2026-10-30). Same container and grid index; `TV` pages hold lines/areas as bytes across
  each page's box, plus label/point records and search tables.
  - `pm0.py` (`VPage`, `Feature`, `Text`) reads and re-writes all 44 vector maps on hand
    byte for byte (`test_pm0v.py`, about 4 minutes).
  - **Converter:** `mapgen/make_vector.py` (OpenStreetMap via the Overpass API; shapely in
    the venv). It copies Topo 10's layout (levels 12 and 10, one page per grid cell),
    DeLorme's TS classes and colours, and the label "Custom Vectors" (priority 39000: above
    Topo 10's 41000, below aerials' 34000). Road/water/park names, route shields and
    coastline water, towns and points of interest (DeLorme's point class codes, so the
    firmware picks DeLorme's icons); no search data yet.
  - Device test 1 (2026-10-30): it drew, but cells past the data edge showed as empty
    bands, and it had no names or shields. Fixed: the area widens to whole level-10
    cells, and labels and shields were added.
  - Build 2 (`mapgen/out/Thomas_Cove_OSM-Vector-Map.pm0`, 0.39 MB, area -75.736 39.350
    -75.473 39.525) is not on the card yet, nor is the build after it, which adds 235
    points of interest. Copied to the card once it was unlocked; the owner reports that
    it looks right.
  - **Geofabrik:** `--geofabrik north-america/us/delaware` reads the state .osm.pbf
    (osmium in the venv), splits crowded cells by halving as DeLorme does, and at the
    extract's edge paints land only over the covered part (page flag 0 plus a "land" area).
    Delaware checked on the device: looks right. The Thomas Cove OSM test map was removed
    from the card.
  - **Per-state maps failed on the device (2026-10-30):** a level-10 "edge" page (flag
    0, painting only its state's part) drew nothing, and the cell stayed white. DeLorme
    never uses flag 0. The generator now merges several extracts into one region (objects
    deduplicated by OSM id), writes only full pages, and leaves cells the extracts don't
    fully cover to other maps. Areas are also split to at most 148 points (DeLorme's
    maximum).
  - **On the card:** `MidAtlantic_OSM_1..3-Vector-Map.pm0` (DE+MD+NJ+PA, 37 + 66 + 87 MB,
    strips split at about -78.29 and -76.00; built in 23 minutes from
    `--geofabrik north-america/us/{delaware,maryland,new-jersey,pennsylvania}`). The state
    files were removed. An RI page caps a file at about 30,000 cells (`MAX_CELLS10`).
    DC is not covered: the Maryland extract's outline leaves it out, so add
    `north-america/us/district-of-columbia` to the region if wanted. Not yet checked on
    the device.
- **Generator:** `mapgen/make_map.py` takes NAIP or USGS Topo (The National Map export
  services, EPSG:4326) or a GeoTIFF. It copies DeLorme's layout: levels, labels, page
  numbering and tile splitting. `test_make_map.py` runs offline.
- **No licence work.** Generated maps carry no `EL` licence record. They load only from
  `MSYS0:/basedata`, which the firmware never licence-checks (0x300AA338: the check flag is 0
  for paths starting `MSYS0:/basedata`).
  - Stock install: `SD:\update\0\basedata\<map>.pm0` + power-cycle. The bootloader's
    partition update copies file by file; a 0-byte file of the same name deletes it.
    MSYS0 has about 85 MB free.
  - With `BASEDATA_ON_SD` (build f8d1281c) the folder is `SD:\basedata`: copy maps there
    directly.
  - Don't extend the licence work in the local `maptools/` (paused after a safety stop: the
    goal is maps from public data, not unlocking DeLorme's commercial maps). `maptools/`
    and `maptest/` stay local and out of the repository.
- **Verified on the device:** the generated Thomas Cove map loads from basedata and draws
  correctly when zoomed in and out.
- **Still open:** where it stacks against the vector base map and the DeLorme Thomas Cove
  map; a topo build on the device; the 0-byte delete.

## Repository

- **GitHub:** public at https://github.com/Myles24/pn40-mods (branch main). The owner is logged in
  with `gh` as Myles24.
- **Commits:**
  - Use the repo-local email `60846900+Myles24@users.noreply.github.com`, author
    "Carson Alberding".
  - **No Claude co-author line**: the owner asked for that.
  - Commit and push only when the owner asks.
- **Excluded by .gitignore:** dumps/, internal drive/, nmea_logs/, update/0/, mods/out/,
  mods/baseline_*/, the Ghidra project and decomp.c (regenerate them with re/make_inputs.py,
  re/analyze.sh and re/run_export.sh), and the old images.
- **Committed 2026-10-25:** the black box (mods/bbox.c, bbox_hooks.S, test_bbox.py), the 234 MHz
  setting and hook changes (build.py, fw.h, fw.ld, hooks.S), README updates, re/notes/pm0.md
  and this file. maptest/ (a DeLorme map copy) and maptools/ (the licence scratch tools) are
  ignored and stay local.
- **Committed 2026-10-30:** mapgen/ (raster and vector generators, pm0.py, tests, the
  Middletown comparison screenshots), re/notes/vector.md, the `BASEDATA_ON_SD` patches
  (mods/build.py, mods/README.md), README and notes updates, and this file. mapgen/out/
  (builds and the OSM/Geofabrik cache) is ignored.
- **License:** MIT for the owner's code; the firmware images are DeLorme's. GitHub shows the
  license as "Other" because of the note appended to LICENSE.

## Open ideas the owner has considered

- Generate maps for real areas (the Thomas Cove NAIP test passed), and topo too.
- Map options: a finer level 17 (0.3 m NAIP, if the engine shows it), a contrast boost for the
  screen, decoding SAT10.
- Faster map redraws: profile at 234 MHz, and benchmark SD against internal storage.
- More features: vertical speed field, grade %, profile history that survives power-off, GPX
  track logging.
