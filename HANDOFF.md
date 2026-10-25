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
- Install: copy the file to `SD:\update\PN-40.hex`, with no `0\` folder and no other `.hex` in
  `update\`, then power-cycle.
- The SD card appears as `H:`, through USB drive mode or a card reader. In USB mode, "Connect to
  Computer" chooses the SD card or the internal drive; the internal drive is `MSYS2:` on the
  device (maps, FieldNotes.txt).

## Device state right now

- **Installed:** full build `b1d80a58…c179`, build id `85236098`, saved in `mods/baseline_b1d80a58`.
  Use it as the base for the next delta.
  - CPU at 234 MHz.
  - Includes every feature below, including the black box.
- **Reverts:**
  - `mods/out/PN-40.revert-to-2bc68299-from-b1d80a58.hex` (175.5 MHz build);
  - `mods/out/PN-40.revert-to-7a061979-from-b1d80a58.hex` (stock clock).
- **Map test file still on the device:** the internal drive holds a colour-inverted test copy of
  `maps/Thomas_Cove_1155857-CDOQQ-MAP.pm0`. The original is in `internal drive/maps/` (also in
  `dumps/internal2_files/maps/`). Copy it back when the owner exports the internal drive, then
  check with `cmp`.

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

## Map file (.pm0) research (notes: re/notes/pm0.md)

The owner wants to replace their outdated maps with maps made from public data (USGS topo
GeoTIFFs, NAIP aerials, maybe OpenStreetMap).

- **Raster tiles** (3DTQ topo quads, CDOQQ aerials): each tile page starts `TR 01 00 06 01`.
  - Then four u32 bounds, width, height, bpp 16, stride (padded to 4), compression and data size.
  - Pixels are RGB565: compression 1 = raw rows; compression 4 = RLE rows
    (`u16 len` + runs + `00 00`, where a run is `00 n` + n pixels, or `c pixel` repeated).
  - Confirmed by rendering tiles and by editing a map on the device.
- **SAT10 maps** use a different, undecoded codec. Vector maps (Topo 10, base maps) are out of scope.
- **Coordinates:** x = (lon + 256) × 2^23 and y = (256 − lat) × 2^23 (8.23 fixed point,
  plate carrée). Checked against several known places.
- **Container:**
  - 0x00: map name; 0x3C: `CMap` `XMap`.
  - 0x4C: big-endian u16 page count. 0x4E: page table, 8 bytes per page (big-endian u32 file
    offset, u32 index); page size = next offset − offset.
  - Page 0: header and directory (format version 0x019F; must be 0x19B-0x19F).
  - Page 1: `RI` raster index, with 7 levels, per-level entries and grids of tile page numbers.
    Not fully decoded.
  - Remaining pages: `TR` tiles, 2-byte `RN` empty slots, and small `SG`/`IA`/`IR` pages.
- **Licensing:** maps carry a per-device licence record that the firmware checks when a map is
  added. Work on this was paused after a safety stop, so treat it carefully: the goal is maps made
  from public data for the owner's own unit, not unlocking DeLorme's commercial maps. The scratch
  files in `maptools/` and `maptest/` are local only and are not in the repository.

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
- **License:** MIT for the owner's code; the firmware images are DeLorme's. GitHub shows the
  license as "Other" because of the note appended to LICENSE.

## Open ideas the owner has considered

- Restore the original Thomas Cove map (see "Device state").
- Map converter: finish the RI index, then generate a `.pm0` from a public GeoTIFF for one area
  and test it on the device.
- Faster map redraws: profile at 234 MHz, and benchmark SD against internal storage.
- More features: vertical speed field, grade %, profile history that survives power-off, GPX
  track logging.
