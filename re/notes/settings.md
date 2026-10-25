# Settings and data fields (PN-40 fw 2.9.6.70319)

Confidence tags: **C** = confirmed (code shows it), **L** = likely, **G** = guess.
Pointer tables were dumped from `ram_data.bin` (initial .data); RAM addresses < 0x3F0AAC are .bss.

## 1. Data fields ("info fields")

### 1.1 The cell-widget registry (C)
All page cells, data fields included, come from one registry: a 16-byte descriptor array at
**RAM 0x00053B70** (161 entries, filled by static ctor `FUN_30090928`) plus a pointer table
**RAM 0x00054580[161]** (end 0x54804; filled by ctor `FUN_3008ffc8`, `ptr[i] = &desc[i]`).
The table index is the **widget id**, and page layouts store this id.

| off | type | meaning |
|---|---|---|
| +0x0 | fn | `handler(result* out, event* ev, int page_idx, int cell_idx)` |
| +0x4 | u8 | 1 = data field, listed in the field picker (ids 0..44); 0 = page-specific widget |
| +0x5 | u8 | 1 = needs the barometer: hidden when `has_barometer()` (`FUN_30111088`) is 0 |
| +0x8 | char* | long name: picker list, and the label in cells wider than screen_w/2 |
| +0xC | char* | short name: label in narrow cells (`FUN_300b010c` reads +0xC at 0x300B02D4) |

`widget_desc(page, cell)` = `FUN_30090848`:
`ptr_table[ layouts[page].cur[mode][ cur_layout(page,mode) ][cell] ]`, with
mode = `nav_mode` (`*0x54814`, 0 = normal, 1 = navigating; get `FUN_300907f8`, set `FUN_30090808`).
`FUN_300908c8` returns desc->fn.

### 1.2 Data field ids (C, from the ctor at 0x30090928)
| id | long name | short name | handler | value source |
|---|---|---|---|---|
| 0 | Back on Course | Back on Course | 300b63e0 | nav object 0x3E4A8C (`3035df60`) |
| 1 | Battery Life | Battery Life | 300b621c | `301a8d80(voltage)` -> level 0..7 -> "100".."0" + "%" |
| 2 | Bearing | Bearing | 300b5fe4 | nav 0x3E4A8C |
| 3 | Coordinates: Primary | Primary | 300b5d98 | `get_position` 300b89e8, format pref +0x84/+0x8c |
| 4 | Coordinates: Secondary | Secondary | 300b5b28 | same, pref +0x88/+0xe4 |
| 5 | Course | Course | 300b5998 | nav (`3035df9c`) |
| 6 | Device Odometer | Device Odometer | 300b5804 | trip +0x14 (m) / 1000 |
| 7 | Distance to Finish | Dist to Finish | 300b563c | nav `3035e028` |
| 8 | Distance to Next Stop | Dist to Stop | 300b5474 | nav `3035e00c` |
| 9 | Distance to Next Turn | Dist to Turn | 300b52ac | nav `3035dff0` |
| 10 | Elevation: Barometer | Elevation: Baro | 300b5040 | `get_baro_elev` 300b8dc0 (needs baro) |
| 11 | Elevation: GPS | Elevation: GPS | 300b4e98 | fix +0x70 |
| 12 | Elevation: Maximum | Elevation: Max | 300b4cd4 | trip +0x04 |
| 13 | Elevation: Minimum | Elevation: Min | 300b4b0c | trip +0x0c |
| 14 | ETA at Finish | ETA at Finish | 300b47ac | nav + time |
| 15 | ETA at Next Turn | ETA at Turn | 300b4470 | nav + time |
| 16 | ETA at Next Stop | ETA at Stop | 300b4138 | nav + time |
| 17 | Finish | Finish | 300b3fe4 | nav (name) |
| 18 | GPS Accuracy | GPS Accuracy | 300b3e2c | fix +0xa8 (int, /100000 -> km, so cm, L) |
| 19 | GPS Status | GPS Status | 300b3c38 | fix +0x01/+0x02, GPS mode pref +0x20 |
| 20 | Heading | Heading | 300b3964 | `get_heading` 300b8bf0 (fix +0x58 or compass) |
| 21 | Moonrise/Moonset | Moonrise/set | 300b3648 | position + date, `300b8f84` |
| 22 | Next Stop | Next Stop | 300b349c | nav |
| 23 | Next Turn | Next Turn | 300b32e4 | nav |
| 24 | Next Turn Arrow | Turn Arrow | 300b314c | nav |
| 25 | Off Course | Off Course | 300b2f4c | nav `3035df44` |
| 26 | Pressure: Ambient | Pressure: Amb | 300b2d98 | fix +0x80 via 300b8d0c (needs baro) |
| 27 | Pressure: MSL | Pressure: MSL | 300b2ba8 | pressure + baro elev (needs baro) |
| 28 | Speed | Speed | 300b2a14 | fix +0x60 via `get_speed` 300b7fc4 |
| 29 | Speed: Average | Speed: Avg | 300b28ac | trip +0x1c |
| 30 | Speed: Maximum | Speed: Max | 300b2744 | trip +0x20 |
| 31 | Speed: Moving Average | Speed: Mov Avg | 300b25dc | trip +0x24 |
| 32 | Sunrise/Sunset | Sunrise/set | 300b22d8 | position + date |
| 33 | Time/Date | Time/Date | 300b20d4 | `get_local_datetime` 300b9500 |
| 34 | Time to Finish | Time to Finish | 300b1f8c | nav |
| 35 | Time to Moonrise/set | Time to Moon r/s | 300b1ab0 | astro |
| 36 | Time to Next Stop | Time to Stop | 300b1968 | nav |
| 37 | Time to Next Turn | Time to Turn | 300b1820 | nav |
| 38 | Time to Sunrise/set | Time to Sun r/s | 300b1250 | astro |
| 39 | Trip: Odometer | Trip: Odometer | 300b10bc | trip +0x30 (m) |
| 40 | Trip: Time Moving | Trip: Moving | 300b0fac | trip +0x38 (s) |
| 41 | Trip: Time Stopped | Trip: Stopped | 300b0e9c | trip +0x3c (s) |
| 42 | Trip: Time Total | Trip: Total | 300b0d8c | trip +0x40 (s) |
| 43 | Velocity Made Good | VMG | 300b0be4 | nav `3035dfb8` |
| 44 | Vertical Speed | Vertical Speed | 300b0a28 | fix +0x84..+0x94 via 300b7f3c |

Ids 45..160 are page widgets (desc+4 = 0, no names): map 92 (`300abf54`), compass 48, the
satellite/GPS-status page 70..75, sun/moon/hunt-fish 84..89, route/waypoint/geocache detail
cells and others. All 161 handlers are in `settings_names.tsv`. Default layouts (initial .data):
- Map page (page 1): layout 0 = [92, 6, 10, 28, 20]; there are 7 layouts with 1..5 cells, and
  the nav-mode set uses [92, 24, 7, 37, 9].
- Compass page (2): [48, 6, 10, 28, 20].
- Trip computer (page 3): layout 0 = [3, 39, 40, 28, 41, 30, 42, 29]; layout 1 = [28, 40, 39].

### 1.3 How a field draws (C)
Cell events (all {code, param}):
| code | when |
|---|---|
| 1 | new GPS fix: `ui_before_dispatch` 0x30085B10 copies the fix and sets `0x432a8`, then `broadcast(ev{1,0}, 0xff)` |
| 0x7d6 | full refresh (`FUN_300e3c00(&{0x7d6,0},0xff)` after a settings change) |
| 0x7d9 / 0x7da | page shown: draw, then post-draw (`FUN_30093604`) |
| 0x7d7 / 0x7d8 | the cell gains / loses focus in edit mode |
`FUN_300af724(ev)` is true for codes 1, 0x7d6, 0x7d7, 0x7d8 and 0x7d9: these are the events a
field redraws on. `FUN_300e3c00(ev, mask)` sends an event to the current page: bit 0 = every
cell, bit 1 = the page handler, bit 2 = the app layers.

Every field handler follows the same template (C, e.g. Battery 0x300B621C):
```
if (cell_obscured(page_obj, cell) /*FUN_3009244c*/ && ev->code == 1) return {10,0};
if (!is_draw_event(ev) /*FUN_300af724*/)                             return {10,0};
u8 r[4]; rect_copy(r, page_obj + cell*4) /*300ba15c*/; rect_init(ctx, r) /*300ba548*/;
draw_field_label(ctx, page, cell, 0)  /*300b010c: background, long/short name, colors 6/7*/;
... compute value ...
if (invalid) draw_field_dashes(ctx, 1, 1)                  /*300afe78: "- -"*/;
else         draw_field_value(ctx, valtxt, unittxt, 1, 1, 0) /*300b0410*/;
ui_handle_result(tmp, &{0x7d4,0});    /* request flush */
return {0xb,0};
```
Value-to-text helpers (C, from their unit strings and constants): `fmt_speed` 0x3010F4A4 (m/s
to km/h ×3.6, kn ×1.9438, mph ×2.2369), `fmt_elevation` 0x3010F680 (m to m or ft),
`fmt_distance` 0x3010F854 (km input; m/km/mi/ft/NM), `fmt_vspeed` 0x3010F35C (m/s, ft/s),
`fmt_pressure` 0x3010D188 (mb/hPa/in), `fmt_coord` 0x3010DACC, `fmt_angle` 0x3010CD6C,
`fmt_time_of_day` 0x3010CC58, `draw_duration` 0x300B0850 (seconds; draws by itself). Unit
choice comes from pref Measures (`FUN_300cfd0c`) and Pressure (`FUN_300cfd1c`).

### 1.4 Field picker and storage (C)
- Page object (`0x43164[page]`): `+0x00` cell rects u8[4] {x0,y0,x1,y1} × N (at most 10),
  `+0x28` ncells, `+0x2c` focused cell, `+0x30` mode (1 = edit fields, 2 = all cells
  selectable), `+0x34` gfx, `+0x38` page index, `+0x3c` drawn flag. Key handler `FUN_30092768`
  (rocker 0x3e9..0x3ec moves the focus; ENTER 0x3f1 in mode 1 opens the picker).
- Picker: ENTER computes the index of the current field among the visible data fields (loop over
  0x54580..0x54804), then `FUN_3009ba64(app, 0x171840 /*picker layer*/, rect, idx)`. The list is
  built by `FUN_3011b9a8`: menu storage **0x170B60, capacity 0x32 = 50 entries** × 16 bytes;
  it adds every desc with +4 set (and +5 clear unless a barometer is present) as
  {label = long name, event {0xFAF, widget_id}}. The loop bound `cmp r5,#0xa1` is at 0x3011BA04.
- Choosing a field posts {0xFAF, id}. Handler `FUN_300e452c` -> `set_cell_widget` 0x3009265C
  writes the id into the *current* layout array: `*(int*)(cur[mode][layout] + cell*4) = id`.
  0xFAE = choose a layout (`FUN_30093358`); 0x232B = large/small text toggle (`0x53b58`).
- Per-page layout tables (40 pages):
  - `PageLayouts @0x3F1120 + p*0x18`: +0 page idx, +4 `int** ncells[nlayouts]`, +8 nlayouts,
    +0xC nmodes (1 or 2), +0x10 ?, +0x14 `int* cur_layout[nmodes]` (get `FUN_30090820`,
    set `FUN_300908d8`).
  - `PageFields @0x3F19E0 + p*0x14`: +0 default ids mode 0, +4 default ids mode 1,
    +8 current mode 0, +0xC current mode 1, +0x10 page id. Each entry is `int* [nlayouts]`, and
    each of those points to `int ids[ncells]`. A reset copies default into current
    (`FUN_300d109c`, masks 0x10000000/0x20000000/0x40000000 = pages 1/2/3).
  - `PageInfo @0x3F0C54 + p*0x18`: +0 id, +4 ?, +8 in-rotation flag (saved), +0xC default of
    +8, +0x10 UI object, +0x14 page handler `fn(result*, ev*, page)`. Page order is at
    0x3F1014[13] (default 0x304D1734).

### 1.5 Adding a new data field: what it takes
1. Write a handler in free flash using the template in 1.3.
2. Add a 16-byte descriptor {fn, 1, needs_baro, long, short}. Only pointers lead to it, so it
   can be a constant in flash.
3. The pointer table can't grow in place: 0x54804..0x54813 holds the shared "layout 0" words
   (0x54804, 0x5480C) and `nav_mode` is at 0x54814. **Relocate it.** Copy the 161 pointers and
   append yours (a flash constant table works: it holds the RAM addresses of 0x53B70+16*i).
   Then patch every literal that holds 0x00054580: **0x300904D8** (ctor 3008ffc8; harmless if
   left), **0x300908C4** (`widget_desc`), **0x3009320C** (picker preselect), **0x3011BA70**
   (picker list). Patch the end pointer 0x00054804 at **0x30093214** (only that literal: the
   .data copies at 0x3044874C.. are the layout-0 pointers, so leave them) and the immediate
   `#0xa1` at **0x3011BA04**.
   - Simpler: overwrite an existing descriptor's fn and names after the ctor runs (or patch the
     ctor), or swap a page widget id into a layout. This needs no table changes.
4. The picker holds 50 entries. 45 fields are visible with a barometer, so up to 5 new fields
   fit. More needs a bigger buffer than 0x170B60 (patch 0x3011BA6C/0x3011F444 and `mov r2,#0x32`
   at 0x3011B9B8).
5. Persistence works with no change: ids are saved as u32 in page_state.dat. But an id >= 161
   in that file would index past the table on stock firmware (crash risk after a downgrade).
6. To put the field in a default layout, edit the default arrays (e.g. trip page ids at
   0x3F1824, a .data initial image, flash 0x3044822C + (addr-0x3F0C14)).

## 2. Settings storage

### 2.1 Persistence files (C)
All use `state_write` 0x30086A4C / `state_read` 0x30086BDC. The file layout is: u32 file magic
(`*0x53358` = 2, set by 0x30086958), then a 12-byte header {u32 type_id, u32 size, u32 checksum},
then the data. The checksum (`FUN_3010bb88`) is the sum of the data as LE u32 words, with the
trailing bytes zero-padded. Read errors: 0x8004 bad magic, 0x8080 bad type, 0x800A bad checksum.

| file | type | size | RAM | read / write |
|---|---|---|---|---|
| MSYS1:/system/pref_state.dat | 400 | 0x3D4 | prefs 0x12B76C | `prefs_load` 0x300D2BB8 / `prefs_save` 0x300D0BAC |
| MSYS1:/system/page_state.dat | 450 (0x1C2) | variable, ≤ 0x1000 | page tables §1.4 | 0x300D28A4 / tail of 0x300D0BAC |
| MSYS1:/system/DEVICE_STATE.TXT | 100 | 0x6C | trip 0x11CEE8 | 0x300C65F8 (falls back to a legacy text format via 0x3010C22C) / 0x300C638C |
| MSYS1:/system/OWNER_STATE.TXT | 500 | 0x108 | owner 0x54818 | 0x30095A60 / 0x300959FC |
| nav_state.dat 900/0x2C, MAP_STATE.TXT 300, compass_cal.dat 700/0x34 | | | | other areas |

- `prefs_save(&prefs)` (36 callers) sets prefs.version = 0x6F, writes pref_state.dat, then
  builds and writes page_state.dat. **Every pref change rewrites both files.**
- Boot (`FUN_30084ff8`): `prefs_init` 0x300D2C84 first applies all defaults
  (`prefs_reset(p, 0x7FFFFFFF)`), then loads both files (0x300D2C6C). It forces GPS mode to
  Normal if it was "Disable GPS" or "Demo", then applies the prefs to the hardware
  (`FUN_300d0988`). If the reset condition is detected at boot (`301a350c`/`301a351c`), it
  restores defaults and saves instead.
- prefs_load copies however many bytes it read (at most 0x3D4). If version ≤ 0x6E it runs the
  migrations 300cf6d4, 300cf714, 300cf764 and 300d0478.
- DEVICE_STATE is also saved every 10 minutes (`FUN_300c6588`).

page_state.dat data (C, from the writer's disassembly 0x300D0C00..0x300D0DD4):
| off | content |
|---|---|
| 0x00 | u32 0x1A (format version; the loader rejects any other value) |
| 0x04 | u32[40] `PageInfo[i].+8` (page in rotation) |
| 0xA4 | u32[13] page order 0x3F1014 |
| 0xD8 | for p = 0..39, for m < min(nmodes,2): u32 current layout index |
| ... | for p = 0..39, for m < min(nmodes,2), for L < nlayouts: u32 ids[ncells(L)] (current) |

### 2.2 Pref struct `prefs` @ RAM 0x0012B76C (0x3D4 bytes = pref_state.dat payload)
The setter is the global pref event handler `prefs_on_event` **0x300D1870** (ev code -> field,
then `prefs_save`). It is reached from `ui_dispatch_late` 0x300E639C for every event no layer
consumed. Getters are the 3-instruction functions at 0x300CF7A4..0x300CFFD0. Defaults come from
`prefs_reset` 0x300D109C (mask groups below). Field-to-event and getter matches are **C**;
names are taken from the menu item that uses each getter/event.

| off | setting | event | getter | values (default) |
|---|---|---|---|---|
| 0x000 | struct version | - | - | 0x6F |
| 0x004 | ? (set to 1 by reset mask 0x10000000) | - | 300cf7a4/set 300cf7b4 | |
| 0x008 | Orientation | 0x203A | 300cf7c4 | 0 North Up, 1 Heading Up, 2 Course Up (1) |
| 0x00C | ? | - | - | (0) |
| 0x010 | POI Detail | 0x203D | 300cf804 | 0 Hide..3 All (1) |
| 0x014 | Hide Waypoint Symbols (scale idx) | 0x203E | 300cf814 | (0) |
| 0x018 | Small Waypoint Symbols | 0x2040 | 300cf834 | (0) |
| 0x01C | Scale | 0x2042 | 300cf864 | 0 Bar, 1 Zoom, 2 Ratio (0) |
| 0x020 | GPS Settings | 0x1772 | 300cfaf8 | 0 Normal, 1 Power Saving, 2 Disable GPS, 3 Demo (0) |
| 0x024 | WAAS | 0x1773 | 300cfb08 | 0 Enabled, 1 Disabled (0) |
| 0x028 | Battery Type | 0x1774 | 300cfb18 | 0 Alkaline, 1 Lithium, 2 Li-Ion, 3 NiMH (0) |
| 0x02C | USB Power | 0x1775 | 300cfb8c | 0 Enabled, 1 Disabled (0) |
| 0x030 | ? | 0x1778 | - | (1) |
| 0x038 | Color Scheme | 0x177A | 300cf874 | 0 Spring..4 Midnight (0) |
| 0x03C | Backlight Intensity (slider 0x172B44, 11 steps) | 0x177B | 300cfc8c | (5); stealth uses 0x3D0 |
| 0x040 | Backlight Timer | 0x177C | 300cfbdc | 5s,15s,30s,45s,1m,On,Off (1) |
| 0x044 | Contrast (slider 0x172BB4, 10 steps) | 0x177D | 300cf884 | (5); LCD gets v*3+100 |
| 0x048 | ? (1 if `*0x40`==1) | - | - | |
| 0x050 | Sound: Button Press | 0x1782 | 300cf8f4 | 0,1,0xB=Off (0) |
| 0x054 | Sound: Message | 0x1783 | 300cf924 | 0..0xB (1) |
| 0x058 | Sound: Power | 0x1784 | 300cf944 (−7) | 7..0xB (10) |
| 0x05C | Sound: Turn Alert | 0x1785 | 300cf958 | 0..0xB (9) |
| 0x060 | Volume (slider 0x172BEC, 11 steps) | 0x1787 | 300cf978 | (10) |
| 0x064 | Calculation Method | 0x2145 | 300cf9f0 | 0 Driving, 1 Hiking (1) |
| 0x068 | Calculation Speed | 0x2146 | 300cfa30 (always 2) | (2) |
| 0x06C | Back on Track (Hiking) | 0x2147 | 300cfa88 | 0 Prompt, 1 Off, 2 Automatic (2) |
| 0x070 | Back on Track Threshold | 0x2149 | 300cfaa8 | 0..5 = 100ft..0.25mi / 50m..400m (2) |
| 0x074 | Compass Navigation Arrow | 0x2141 | 300cfa10 | 0 Direct to Next Wpt, 1 Back On Course (0) |
| 0x078 | Time Format | 0x1789 | 300cf9a4 | 0 Local 12 hr, 1 Local 24 hr (0) |
| 0x07C | Time Zone | 0x178A | 300cf9b4 | 0 Automatic, 1..25 (UTC offset via 300cf9c4 table 0x304D4938) (0) |
| 0x080 | Enable Daylight Saving | 0x178B (toggle) | 300cf9e0 | (0) |
| 0x084 | Coordinates: Primary | 0x178D | 300cfcec | 0 D,1 DM,2 DMS,3 UTM/UPS,4 USNG/MGRS (1) |
| 0x088 | Coordinates: Secondary | 0x178F | 300cfcfc | same (3) |
| 0x08C | Datum: Primary | 0x178E | 300cfccc | datum index (0x6F) |
| 0x090 | Measures | 0x1792 | 300cfd0c | 0 Statute, 1 km, 2 NM/ft, 3 NM/m (0) |
| 0x094 | Bearing | 0x1791 | 300cfd2c | 0 True, 1 Magnetic (0) |
| 0x098 | Record points by | 0x2332 | 300cfd5c | 0 Time, 1 Distance (1) |
| 0x09C | track record interval (float, slider 0x172808) | 0x2333 | 300cfd6c | (3.05f) |
| 0x0A0 | track record interval, distance | 0x2334 | 300cfd7c | (2) |
| 0x0A4 | When track is full | 0x2331 | 300cfd8c | 0 Overwrite, 1 Stop, 2 New Track (2) |
| 0x0A8 | Prompt To Reset Trip Info | 0x13B2/4 | 300cfdac | (1) |
| 0x0AC | Sounds | 0x1788 | 300cfbbc | 0 Play All, 1 Critical, 2 Mute (0) |
| 0x0B0 | char[32] next waypoint name ("Wpt 001") | - | 300d0224 | |
| 0x0D0 | ? | - | 300cfebc | |
| 0x0D4 / 0x0D8 | track playback / sim-nav value | 0x2330 | 300cfd9c / 300cfa38 | |
| 0x0DC | Back on Track (Driving) | 0x2148 | 300cfa98 | (1) |
| 0x0E4 | Datum: Secondary | 0x1790 | 300cfcdc | (0x6F) |
| 0x0E8 | Map Style | 0x203C | 300cf7d4 | 0 Topo, 1 Street (0) |
| 0x0EC | Show Hybrid Map | 0x13B2/6 | 300cf7e4 | (0) |
| 0x0F0 | Pressure units | 0x1793 | 300cfd1c | 0 inHg, 1 mbar, 2 Pa (0) |
| 0x0F4 | ? | 0x233D | 300cff50 | (0) |
| 0x0F8 | Enable Electronic Compass | 0x13B2/8 | 300d0870 | (1 if baro hw) |
| 0x0FC | Enable Barometric Altimeter | 0x13B2/9 | 300d0854 | (same) |
| 0x100 | Auto Calibrate Altimeter | 0x13B2/10 | 300d0838 | (same) |
| 0x108 | ? | - | 300cf894 | (1) |
| 0x10C | North Up above .5 mi/1 km/.5 NM scale | 0x13B2/7 | 300cf7f4 | (1) |
| 0x110 | hiking speed, float km/h (slider 0x172a54) | 0x2142 | 300cfa20 | (4.828 = 3 mph) |
| 0x114 / 0x118 | geocache-related | 0x1F45 / 0x1F46 | 300cff94 / 300cffa4 | (1 / 0) |
| 0x11C | ? 0/1 | 0x214A (set 300cfae4) | 300cfad4 | (1) |
| 0x120 | Hide Geocache Symbols | 0x203F | 300cf824 | (0) |
| 0x124 | Small Geocache Symbols | 0x2041 | 300cf844 | (0) |
| 0x128 | Use Midnight at sunset | 0x177F (toggle) | 300cf8a4 | (1) |
| 0x12C | ? toggle | - | 300cffd4 | (0) |
| 0x130 | char[80] | - | - | |
| 0x180 | Hide Logged Caches | 0x13B2/0xF | 300cf854 | (0) |
| 0x184 / 0x1B4 / 0x1F4 / 0x2F4 / 0x338 / 0x37C / 0x39C / 0x3AC | strings, 32/64/256/64/64/32/16/16 bytes (file names, G) | - | - | |
| 0x1AC, 0x1B0, 0x334, 0x3C4 | u32 with get/set/clear accessors | - | - | |
| 0x378 | When Connected (USB mode) | 0x1776 (`300d0908`) | 300cf8c4 | (2) |
| 0x3BC | Enable display rotation | 0x1780 (toggle) | 300d088c | (0) |
| 0x3C0 | Screenshots On | 0x1781 (toggle, `300d0f50`) | 300cf8b4 | (0) |
| 0x3C8 | Use XMap 7 Mode | 0x13B2/0xB | 300cf8d4 | (0) |
| 0x3CC | Stealth Mode | 0x13B2/0xC | 300cfbac | (0) |
| 0x3D0 | backlight intensity in stealth mode | 0x177B | 300cfc8c | (0) |

Not saved (past 0x3D4): `0x12BB40`/`0x12BB41` bytes, `0x12BB44` (event 0x1786), and
`0x12BB48`/`0x12BB4C` (GPS mode stashed during USB, 0x300D2470).
Unmapped events in the handler: 0x1777 toggles something through `300d08a8` using 0x378 and
0x3C8; 0x1786 ?; 0x1771 = reset.

Trip computer `trip` @0x11CEE8 (DEVICE_STATE.TXT, 0x6C bytes, C from the field handlers and
the reset functions): +0 u32 ver 6; +0x04 f64 elev max (−99999 = none); +0x0C f64 elev min
(+99999); +0x14 f64 device odometer (m); +0x1C f32 avg speed; +0x20 f32 max speed; +0x24 f32
moving avg (m/s); +0x30 f64 trip odometer (m); +0x38/+0x3C/+0x40 u32 time moving / stopped /
total (s); +0x44..+0x54 ? (last position/defaults, G); +0x58/+0x5C/+0x60/+0x64 u32 checkboxes
"Reset Trip Values / Speeds / Elevations / Device Odometer" (events 0x13B2/0..3); +0x68 ?.
Owner info @0x54818 (0x108 bytes): +0 u32 ver 1, +4 text, +0x104 u8 flag (G).

Reset groups (`prefs_reset` mask, posted as {0x1771, mask} by each category's "Restore
Defaults" button, C): 1 System, 2 Display, 4 Connect to Computer, 8 Sound, 0x10 page
rotation/order, 0x20 Time, 0x40 Units, 0x80 Compass & Altimeter, 0x100000 Map,
0x200000 Track, 0x400000 Routing, 0x800000 (+0xF4), 0x1000000 geocache, 0x2000000 / 0x4000000
Navionics, 0x10000000/0x20000000/0x40000000 = reset fields of page 1/2/3, 0xFFFFFFFF = factory
reset, 0x7FFFFFFF = all defaults with no save.

## 3. Settings UI: defining an item

Setup categories are list pages (0x64-byte objects), built at boot by `setup_menus_init`
0x30097DC0 through `FUN_30097254(page, nitems, title, item_ptr_array, style=*0x3F236C, sel)`.
Page fields: +0x10 u16 count, +0x12 u16 visible, +0x34 title, +0x38 items, +0x44 style.
| category page | count | title @ | items @ (.data) |
|---|---|---|---|
| 0x54D60 | 9 (10 with Navionics) | "Map Setup" 0x3F1D04 | 0x3F1D10 / 0x3F1D34 (`FUN_30097618`) |
| 0x54DC4 | 6 | Map Point Management | 0x3F1D74 |
| 0x54E28 | 11 | Navionics Setup | 0x3F1D9C |
| 0x54E8C | 7 | System | 0x3F1DD0 |
| 0x54EF0 | 6 / 7 (with rotation hw) | Display | 0x3F20D0 / 0x3F20E8 (`FUN_3009780c`) |
| 0x54F54 | 3 | Connect to Computer | 0x3F1E08 |
| 0x54FB8 | 5 | Compass & Altimeter | 0x3F1E28 |
| 0x5501C | 7 | Sound | 0x3F1E44 |
| 0x55080 | 7 | Routing | 0x3F1E68 |
| 0x550E4 | 4 | Time | 0x3F1E8C |
| 0x55148 | 8 / 7 (no baro: no Pressure) | Units | 0x3F1EA4 / 0x3F1EC4 |
| 0x551AC | 5 | Track Settings | 0x3F1EF0 |

Item classes (each is a C++-style object, vtable at +0: [0] dtor, [1] delete, [2] draw,
[3] `on_event(result*, item*, ev*)`):
1. **Choice list** (vtable 0x304D3188; ctor `FUN_300c3d84`; event `FUN_300c3450`). The
   object is 0x64 bytes and its choice array follows it inline. Set it up with
   `FUN_300c21f0(item, name, choices, n, getter)`: +0x8/+0x5C n, +0x10 name, +0x14 getter
   (returns the current index), +0x58 choices. A choice is 12 bytes {char* label, u32 ev_code,
   u32 ev_param}. Static choices are filled in `FUN_300c3df4`; dynamic lists use
   `list_begin` 0x300C1610(builder, storage, cap) and `list_add` 0x300C1564(builder, label,
   &ev), as GPS Settings 0x300C2900 and Datum 0x300C29E0 do. All items are set up in
   `FUN_300c2ae8`. Picking a choice posts {ev_code, ev_param}, which reaches `prefs_on_event`.
2. **Checkbox** (vtable 0x304D42A8, 0x18 bytes; `FUN_300cd118(item, label, &ev, style=*0x3F2368,
   getter)`: +4 label, +8/+0xC event, +0x10 getter (bool), +0x14 style). All are set up in
   `FUN_300cd650`. ENTER (`FUN_300cd3dc`) posts the event, or for code 0x13B2 toggles the
   id-numbered setting in place.
3. **Button** `FUN_300ce614(item 0x28, label, &ev, style)` (e.g. Restore Defaults, Calibrate).
4. **Slider** `FUN_301288c4(item, label, &ev, style=*0x3F2354, getter, nsteps)`: Backlight
   Intensity 0x177B (item 0x172B44), Contrast 0x177D (0x172BB4), Volume 0x1787 (0x172BEC); set up in `FUN_30129260`.

**Adding a setting** (e.g. a new choice in Display):
1. Pick a pref slot: an unused word below 0x3D4 (none is proven free; candidates are the "?"
   rows above, so verify with a write-watch), or grow the struct. Growing means patching
   `mov r3,#0x3d4` in prefs_save (0x300D0BDC) and prefs_load (`mov r3,#0x3d4` 0x300D2C00, `cmp r2,#0x3d4` 0x300D2C14). Old files
   still load: the checksum covers the size stored in the file.
2. Write a getter `ldr r3,=0x12B76C; ldr r0,[r3,#off]; bx lr`.
3. Define the item object (0x64 bytes + n×12 choices, in free RAM) with vtable 0x304D3188, and
   call `FUN_300c3d84` + `FUN_300c21f0` (hook a boot ctor such as `FUN_300c2ae8`).
4. Choose an unused event code (e.g. 0x1794..0x1F44) and handle it: hook `prefs_on_event`
   0x300D1870 (wrap it: if ev==mine { prefs[off]=param; prefs_save(&prefs); return {0xb,0}; }
   else call the original). Optionally post {0x7D6,0} with mask 0xFF to redraw.
5. Add the item pointer to a category: copy the `.data` item array to free RAM with one more
   entry, then patch that category's `FUN_30097254` call (count immediate and array literal)
   in `setup_menus_init` 0x30097DC0.
6. Add a default in `prefs_reset` (or rely on the zero from .bss).

## 4. GPS data the UI reads

`ui_before_dispatch` (0x30085B10) polls `gps_fix_receive` 0x301528D8. That copies a 0xB0-byte
fix message from the queue 0x184F40 (the producer master copy is at 0x18384C, published by
0x30152930 with seq+1) into **`ui_fix` @ RAM 0x000430B4**. It then runs the trip and other
updaters and broadcasts event 1. The UI then reads `ui_fix` through the accessors at
0x300B7DF0..0x300B9500.

| off (abs) | type | meaning | accessor | conf |
|---|---|---|---|---|
| +0x00 (430B4) | u8 | sequence counter | - | C |
| +0x01 (430B5) | u8 | fix: 1 no fix, 2 2D, 3 3D, 4/5 = 2D/3D differential; valid = 2..5 | `gps_fix_valid` 300b7e5c, `gps_status_text` 300b8870 | C |
| +0x02 (430B6) | u8 | DGPS/WAAS in use (promotes 2→4, 3→5) | 300b7e1c | L |
| +0x04 (430B8) | u8 | flag | 300b7e2c | G |
| +0x14 (430C8) | u32 | GPS week | 300b84d4 | L |
| +0x18 (430CC) | f64 | time of week, ms (×0.001 to s) | 300b84d4 → 30153320 | L |
| +0x26 (430DA) | u8 | flags, bit 2 used for heading | 300b7e44 | G |
| +0x2C (430E0) | f64 | latitude | `get_position` 300b89e8 (0,0 = none: last fix is cached at 0x3F2170/0x3F2178 or taken from trip) | C (units unverified) |
| +0x34 (430E8) | f64 | longitude | same | C |
| +0x3C / +0x44 | f64 ×2 | second position pair | 300b7e8c | G |
| +0x58 (4310C) | f32 | course over ground, deg | `get_heading` 300b8bf0 (holds the last value when speed < 0.5 m/s; compass otherwise) | L |
| +0x60 (43114) | f32 | ground speed, m/s | `get_speed` 300b7fc4 (0 if no fix) | C |
| +0x68 (4311C) | f64 | baro altitude, m (manual calibration) | `get_baro_elev` 300b8dc0 | L |
| +0x70 (43124) | f64 | GPS altitude, m | 300b8020 | C |
| +0x78 (4312C) | f64 | baro altitude, auto-calibrated | 300b8dc0 (if pref 0x100) | L |
| +0x80 (43134) | f32 | ambient pressure | `get_pressure` 300b8d0c | L |
| +0x84/+0x8C/+0x94 | f64 ×3 | velocity components, m/s (vertical speed) | 300b7f3c | L |
| +0xA8 (4315C) | s32 | estimated accuracy (cm, L), −1 = n/a | 300b7df0 | C |

Time and date: `gps_time_hms` 0x300B857C and `gps_date_ymd` 0x300B8670 (both cache the last
valid value) feed `get_local_datetime` 0x300B9500 (struct tm; adds the zone and DST from prefs
via 0x300B94A4). `get_datetime` 0x300B966C is the existing wrapper.
Satellite counts are not in `ui_fix`. The per-satellite table (0x820 bytes at 0x183910, mutex
0x1838FC) is copied by `gps_sat_snapshot` 0x3015288C, used by the GPS status page.

## Open questions
- Satellites in view/used and HDOP: the layout of the 0x820-byte table at 0x183910 is not
  decoded.
- Lat/lon units (degrees or radians) are not checked; see `fmt_coord` 0x3010DACC and 0x3010B914.
- Unknown pref words (0x004, 0x00C, 0x030, 0x048, 0x0D0, 0x108, 0x12C, the string blocks) and
  events 0x1777/0x1786.
- When page_state.dat is written after a field edit: any `prefs_save` writes it, but no call
  was found on the 0xFAF path.
- Logical key numbers: rocker codes 0x3E9..0x3EC move the cell focus; 0x3EF is handled by the
  page key handler.
