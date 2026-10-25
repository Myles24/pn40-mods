# PN-40 UI: how it works (firmware 2.9.6.70319)

Overview and mod recipes. The details, with addresses and evidence for every claim, are in:
[pages](notes/pages.md) · [layers, menus, dialogs](notes/layers.md) · [graphics](notes/graphics.md) ·
[settings, data fields](notes/settings.md). All names below are in `names.tsv` and are applied
in `out/decomp.c`; `sh fn.sh <addr>` prints any function.

Status: from static analysis of the decompilation and disassembly, 2026-10-24. Nothing here has
been exercised on the device yet. Items marked ✓ I re-checked in the code myself.

## 1. The big picture

```
InputController task (prio 13)        NMEA/Nav tasks
  polls 13 keys / 10 ms                  new fix -> event {1,0}
        │ {key_idx, down}                     │
        ▼                                     ▼
UI task (prio 3) ui_main_loop 0x30085E14 ✓ ── every 1 ms: ui_get_event; every 5 ms: {2,0}
        │
        ▼ ui_dispatch_event 0x300E6638 ✓
  1. ui_global_filter 0x300E4934      key repeat, backlight, screenshot combo ✓
  2. app object *(0x003F0C2C): topmost open layer gets it ✓, else the current page
  3. ui_dispatch_late 0x300E639C      queued commands (menu picks etc.) -> command switches
        │
        ▼ handler returns {code, param}  (10 = not handled, 0xB = done, 0x7D4 = flush screen)
  ui_handle_result 0x300E3B70 ✓  (238 callers)
        │
        ▼ after dispatch: if the dirty flag (0x53B5C) is set -> win_update(g_screen_win) ✓
```

Everything is {u32 code, u32 param}, both events and results. Results are returned through a
hidden first pointer argument (`fn(result*, ...)`).

## 2. Keys and event codes

Logical key k = 1..13: **press = 0x3E8 + k, release = 0x5DC + k** ✓ (`ui_translate_keys` 0x300E4DA4).

| k | key | press | confidence |
|---|---|---|---|
| 1-4 | rocker | 0x3E9-0x3EC | ✓ (GPIO 6-9, auto-repeat) |
| 5 / 6 | ZOOM IN / OUT (order unknown) | 0x3ED / 0x3EE | likely (both agents) |
| 7 | MENU | 0x3EF | confirmed (both agents) |
| 8 | PAGE | 0x3F0 | confirmed ✓ (page_next caller) |
| 9 | ENTER | 0x3F1 | ✓ (screenshot combo) |
| 10 | BACK | 0x3F2 | likely |
| 11 | MARK | 0x3F3 | likely |
| 12 | FIND | 0x3F4 | likely |
| 13 | POWER | 0x3F5 | ✓ |

Other codes:

| code | meaning |
|---|---|
| 1 | new GPS fix (data fields redraw) |
| 2 | 5 ms periodic tick |
| 0x7D4 | result: screen dirty, flush |
| 0x7D6 | full refresh broadcast |
| 0x7D7 / 0x7D8 | cell focus in / out |
| 0x7D9 / 0x7DA | page shown: draw / post-draw |
| 0xFA0 / 0xFA1 | go to page (menu item / PAGE key) ✓ |
| 0xFAE / 0xFAF | choose layout / choose data field for the focused cell |
| 0x1389 | menu item that opens a submenu |
| 0x13AA | close menu |
| 0x13AD | message box answer: `page<<24 \| button<<16 \| ctx` |

## 3. Screens: pages, panes, layers

- **Pages** ([notes](notes/pages.md)): 40 page objects (`page_objs` 0x43164[], 0x40 bytes each),
  described by `page_desc` 0x3F0C54 + id*24 {id, enabled, in-rotation flag, menu obj, handler}.
  `cur_page` 0x53B64. PAGE cycles `page_order` 0x3F1014[13] ✓, skipping disabled pages and Route
  Directions unless navigating. Default rotation: Satellites, Map, Compass, Trip Info (+ Directions).
- **Panes**: each page is a grid of up to 10 cells. Every cell holds a **pane class id** 0..160,
  from a 16-byte descriptor table (0x53B70, pointer table 0x54580 ✓). **0..44 are the
  user-selectable data fields**; 45..160 are widgets (map, compass rose, satellite sky...).
  Per-page layouts: 0x3F1120 (layouts), 0x3F19E0 (default/current cell ids).
- **Layers** ([notes](notes/layers.md)): slots in the app object, opened one at a time (an
  opener closes the others, 0x3009B754):

| slot | layer |
|---|---|
| +0x04 | menu / popup list |
| +0x08 | on-screen keyboard |
| +0x0C | message box |
| +0x10 | input dialog (message box + text field) |
| +0x14 | option-list screens (Device Setup sub-screens, Route Options) |
| +0x18 | Page Order |
| +0x1C | grid picker (symbols) |
| +0x20 | unknown (dropdown marker?) |
| +0x24 | power / lock dialog |

## 4. Drawing ([notes](notes/graphics.md))

- 176x220 panel with its own GRAM; the CPU writes it directly: command port 0x38020000, data
  0x38000000 ✓ (pointers at RAM 0x460878/0x46087C).
- The UI draws into RAM **windows** (0x38-byte structs, 16 slots at 0x3EF778): main screen
  `g_screen_win` = `*(0x000E435C)` ✓, a popup buffer 0xE4368, and a save-under buffer 0xF71E8.
  `win_update` pushes a window to the panel.
- The graphics library is **Thumb** code (0x30424ED4-0x3042B060). Call it from ARM mods through
  the existing ARM->Thumb veneers:

| call | veneer (call this) | real |
|---|---|---|
| `gfx_fill_rect(win, x1, y1, x2, y2, rgb565)` ✓ | 0x3044791C | 0x3042792C |
| `gfx_draw_string(win, font, x, y, str, fg, bg)` (fg == bg: transparent) | 0x30447970 | 0x30427E54 |
| `win_update(win)` ✓ | 0x304478EC | 0x3042907C |
| `win_create(type, buf, x1, y1, x2, y2, w, h, border, 0, color)` | 0x304479AC | 0x304258E0 |
| `win_destroy(win, 0)` | 0x304479A0 | 0x304253F0 |

- Fonts: 6 text fonts (9, 10, 10 bold, 13, 16, 23 px) + a 16x16 symbol font, 1-bit glyphs; the UI
  font table is at 0x3F2348 (regular 10 px = `*(0x3F2354)`).
- Colours: RGB565. 5 themes (Spring, Summer, Autumn, Winter, Midnight) x 8 colours at 0x3F23A4.
- Bitmaps: 0x18-byte header {w, h, transparent key 0xF81F, pixels, hotspot} + raw RGB565.

## 5. Settings and data ([notes](notes/settings.md))

- All user settings: `prefs` struct at RAM 0x12B76C (0x3D4 bytes, version 0x6F), mapped field by
  field in the notes. Every change goes through `prefs_set` 0x300D1870, then `prefs_save`
  0x300D0BAC, which writes `MSYS1:/system/pref_state.dat` + `page_state.dat` (format: magic 2,
  {type, size, checksum}, data).
- GPS data for the UI: a 0xB0-byte fix copy at RAM 0x430B4, refreshed before each dispatch: fix
  type +0x01, lat/lon +0x2C/+0x34 (doubles; units open), speed m/s +0x60, altitude +0x70,
  accuracy +0xA8. Satellite counts / HDOP: not decoded yet.

## 6. Mod recipes (designs, untested)

**Draw something on the current screen** (from the UI task, e.g. our ui_loop hook):
```c
win *scr = *(win **)0x000E435C;
gfx_fill_rect(scr, 0, 0, 175, 20, color);
gfx_draw_string(scr, *(font **)0x003F2354, 4, 5, "Hello", 0xFFFF, 0xFFFF);
win_update(scr);          /* or return {0x7D4, 0} from a handler */
```
It is overwritten at the next redraw, so a persistent overlay must redraw after the page does.

**Add a data field** (the most useful UI mod):
1. Write `handler(result*, ev*, page, cell)` using the field template (notes/settings.md §1.3):
   redraw on codes 1/0x7D6-0x7D9, draw the label, format the value with `fmt_speed`/`fmt_distance`/...,
   `draw_field_value`, return {0xB, 0} after requesting a flush.
2. Easiest: **repurpose** an existing descriptor (patch its handler and names at boot).
3. Proper: add a descriptor and relocate the 161-entry pointer table; the literals to patch are
   listed in notes/settings.md §1.5. The picker has room for 5 more fields.

**Add a menu item**: menus are built at runtime from `{label, code, param, flags}` items
(`menu_add` 0x3011A400 into builders such as 0x3011BCF0). Hook a builder to append an item with a
new code, and handle that code in a page or global command handler (`ui_dispatch_late` path).

**Show a message box**: `msgbox(0x55C9C, title, text, type, default_button, page, ctx, cb)` at
0x3009A670 ✓ (call signature seen at the "SD card is not accessible" site). The answer comes back as
{0x13AD, ...}.

## 7. Open questions

- Which of k5/k6 is ZOOM IN; MARK/FIND/BACK are "likely", not proven.
- Names of ~12 rarely used pages; who opens layer +0x20; codes 0xFA2, 0xFA4/5, 0x1391, 0x13B0.
- Satellite count / HDOP location (table 0x183910); lat/lon units in the fix copy.
- When page_state.dat is saved after a field edit.
- Panel controller part number; who sets 180° orientation.
- The 25 Thumb functions in low RAM (loaded at runtime, not in the flash image).

## 8. Tooling

| Tool | Use |
|---|---|
| `sh run_export.sh 6` | apply `names.tsv`, fix no-return flags, decompile everything (about 2 min) |
| `python -I merge_names.py` | fold `notes/*_names.tsv` into `names.tsv` |
| `ghidra_scripts/` | `pn40_setup.py` (memory map, seeds), `pn40_thumb.py`, `pn40_names.py`, `pn40_noreturn.py`, `pn40_export.py` |
