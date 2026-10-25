# PAGES: full-screen pages, PAGE cycle, panes, redraw

Firmware 2.9.6.70319. Addresses are flash code/rodata (0x30xxxxxx) or RAM (0x00xxxxxx). RAM initial
values are from `ram_data.bin`. Note: Ghidra splits several of these functions and drops the
"fall-through" in some of them (it shows early `return`s that the code doesn't have). Where that matters
I checked the ARM disassembly (`fwdis.py`) and say so.

## 1. Overview (confirmed)

- There are **40 pages** (ids 0..39). Each id has: a static **descriptor** (`page_desc[id]`, 24 bytes
  at 0x003F0C54, in .data), a heap **page object** (`page_objs[id]` at 0x00043164, 0x40 bytes), a
  **layout record** (0x003F1120) and a **pane-assignment record** (0x003F19DC).
- A page is a grid of **panes**. Each pane is an instance of one of 161 **pane classes** (0x00054580[]).
  Classes 0..44 are the user-selectable data fields ("Speed", "Heading", ...). Classes 45..160 are
  special widgets (map, compass rose, satellite sky, lists, buttons).
- The "current page" is `cur_page` = 0x00053B64. The PAGE-cycle position is `page_cycle_pos` =
  0x00053B6C, an index into `page_order[13]` at 0x003F1014.
- Only the 13 ids in `page_order` can be reached with PAGE. All other pages (Find, Edit Route, details,
  dialogs) are opened with a result `{0xFA0, id}` from menus or code.

## 2. Structures

### page_desc[40] at 0x003F0C54, stride 24 (confirmed)
BRIEF's "flags at 0x003F0C5C+i*24" is field +8 of this record.

| off | type | meaning | evidence |
|---|---|---|---|
| +0x00 | u32 | page id (== index) | init data 0..39; `page_desc_index` 0x30090760 looks the id up |
| +0x04 | u8 | **cyclable**: page can be in the PAGE cycle / Page Order list | tested as a byte at 0x3F0C58 in `page_next` 0x3008616c, `page_prev` 0x30086674, Page Order draw 0x301332d4. =1 for ids 0..11 and 26 |
| +0x08 | u32 | **bit0 = enabled (shown in PAGE cycle)**. This is the user setting | toggled by ENTER in the Page Order dialog (0x301338f0, also 0x30133e64); tested by `ui_main_loop`, `page_next`, `page_prev`, `ui_page_commands` |
| +0x0C | u32 | default for +0x08 | `settings_restore_defaults` 0x300d109c with mask 0x10 copies +0xC into +0x8 |
| +0x10 | menu* | the page's MENU-key menu (0 = none) | `page_menu_key` 0x300926f0: `ldr r1,[desc,#0x10]; bl app_open_menu` |
| +0x14 | fn | page event handler `h(result*, event*, page_id)` | called from `ui_dispatch_to_app` (disasm 0x30093bf0), `page_broadcast`, `page_show`/`page_hide`, `ui_dispatch_late` |

Initial values: enabled (+8) = 1 only for ids 0,1,2,3,11. Cyclable (+4) = ids 0..11 and 26.

### page object (malloc(0x40) x 40, filled by `page_init` 0x300937cc) (confirmed)
The constructor loop is in a startup function around decomp.c line 2110: `for i<0x28: page_objs[i]=malloc(0x40); page_init(obj,i)`.
After that, every page gets event 0x1770 (6000) through `ui_dispatch_to_app`.

| off | type | meaning |
|---|---|---|
| +0x00..0x27 | u8[4] x10 | pane rects {x0,y0,x1,y1}. x is 0/0x58 .. 0x57/0xAF, rows of 44 px (computed by `layout_grid_rects` 0x3009223c) |
| +0x28 | int | number of panes |
| +0x2C | int | focused pane index |
| +0x30 | int | focus state: 0 = none; 1 = pane-select ("customise info field") mode; 2 = focused pane is active and gets the keys (`page_set_focus` 0x300924cc) |
| +0x34 | surface* | drawing surface. The main screen is `main_surface` 0x000E435C |
| +0x38 | int | page id |
| +0x3C | u8 | page is shown (set in `page_show`, cleared in `page_hide`) |

`page_init` zeroes the rects, does `page_set_focus(0,0)`, applies the current layout, then calls the
page handler with event **2000 (0x7D0)**: one-time init.

### Layouts (confirmed)
- `page_layouts[40]` at 0x003F1120, stride 24: +0 id; +4 `int **layouts` (each layout is `{n, size[n]}`,
  sizes in half-row-cell units, 10 units = full screen: size 1 = half-width 44-px cell, 2 = full-width
  row, 6 = three rows ...); +8 number of layouts; +0xC number of modes (1 or 2); +0x10 ptr to the
  default layout index[2] (flash); +0x14 ptr to the current layout index[2] (RAM). Accessors:
  `page_layout_get` 0x30090820, `page_layout_set` 0x300908d8, `page_default_layout_get` 0x30090900.
- `page_panes[40]` at 0x003F19DC, stride 20: +0 id; +4/+8 default class lists (mode 0/1); +0xC/+0x10
  current class lists (mode 0/1), each `int *[layout] -> class[n]`. Restore-defaults copy is in
  0x300d162c. `pane_class_get(page,pane)` 0x30090848 returns `pane_classes[list[mode][cur_layout][pane]]`.
- **Layout mode** `layout_mode` 0x00054814 (`layout_mode_get` 0x300907f8 / `_set` 0x30090808): 0 =
  normal, 1 = navigating. Set to 1 when a route or goto starts (0x30087a6c, 0x3011331c just before
  `{0xFA0,1}`), and set back to 0 when navigation ends (result 0x213D in `ui_global_keys`). Only pages
  1, 2 and 3 have different mode-1 lists. **likely**
- `page_apply_layout` 0x30093228 handles two special cases. On the Map page with info fields hidden
  (`0x300cf7a4()==0`, layout != 6) it forces layout 5, a full-screen map. On Trip Info in layout 1
  (mode 1) it uses `layout_rows3_rects` 0x30092314, which gives 3 rows of 73 px.
- These are persisted together with the page enable bits and the order (header size 0xD8 = 13*4 order
  + 40*4 flags + 4) by 0x300d28a4..0x300d2bb8. That code references "MSYS1:/system/page_state.dat"
  (0x304D4648, literal at 0x300d2ba4). **likely**

### Pane classes: `pane_classes[161]` at 0x00054580 -> 16-byte defs at 0x00053B70+i*16 (confirmed)
Filled by 0x3008ffc8 (the pointer table) and 0x30090928/0x30090f58 (the defs).

| off | meaning |
|---|---|
| +0 | handler `h(result*, event*, page_id, pane_idx)` |
| +4 | u8: user-selectable data field (1 for classes 0..44) |
| +5 | u8: needs the barometer (Elevation: Barometer, Pressure Amb/MSL). Skipped in the chooser when `0x30111088()` == 0 |
| +8 | long name ("Distance to Next Turn"), +0xC: short name ("Dist to Turn") |

Classes 0..44 in order: Back on Course, Battery Life, Bearing, Coordinates: Primary/Secondary, Course,
Device Odometer, Distance to Finish/Next Stop/Next Turn, Elevation: Barometer/GPS/Maximum/Minimum, ETA
at Finish/Next Turn/Next Stop, Finish, GPS Accuracy, GPS Status, Heading, Moonrise/Moonset, Next Stop,
Next Turn, Next Turn Arrow, Off Course, Pressure: Ambient/MSL, Speed, Speed: Average/Maximum/Moving
Average, Sunrise/Sunset, Time/Date, Time to Finish/Moonrise/set/Next Stop/Next Turn/Sunrise/set, Trip:
Odometer/Time Moving/Time Stopped/Time Total, Velocity Made Good, Vertical Speed. Handlers run from
0x300b63e0 (class 0) down to 0x300b0a28 (class 44).

## 3. All pages

`cyc` = in `page_order` (the PAGE cycle); `on` = enabled by default. Names marked confirmed come from
the "Pages" menu item table (`{name, 4000, id}` at 0x00171170, built in 0x3011e444/0x3011e494) or from
other menu items `{"X...", 4000, id}`. `page_menu_index` 0x3011a4e4 maps an id to its name in that
13-entry table (the Page Order dialog uses it). The `layouts` column gives pane classes; data fields are
written by name.

| id | name | conf | handler (+0x14) | menu obj | layouts / panes | cyc/on |
|---|---|---|---|---|---|---|
| 0 | Satellites | confirmed | 3012b8b8 | 00171e18 ("Satellites Menu") | [6,4]: 116 (300fd6c0 sky), 117 (300fdba8) | cyc/on |
| 1 | Map | confirmed (str 0x30525e08) | 300acbd0 | 00171c3c | 7 layouts, map pane = class 92 (300abf54) + up to 4 fields (default layout idx 3 = map + Speed + Heading); L6 = map + 93/95/94 (nav) | cyc/on |
| 2 | Compass | confirmed | 300cb084 | 00171884 ("Compass Menu") | 3 layouts: class 48 (300caf64 rose) + fields | cyc/on |
| 3 | Trip Info | confirmed | 300c66cc | 00171dd4 ("Trip Info Menu") | L0 8 fields (Coord, Trip Odo, Moving, Speed, Stopped, Max, Total, Avg); L1 3 big rows | cyc/on |
| 4 | Waypoints | confirmed | 30107328 | 00171ee4 | 141 list (301055e0), 142, 143 | cyc |
| 5 | Routes | confirmed | 3012e450 | 00171c80 | 112..115 (3012e1d0, 3012e06c, 3012bc54, 3012ba78) | cyc |
| 6 | Tracks | confirmed | 30116160 | 00171d4c ("Track Menu") | 132 (30112618), 133 (30112dd8) | cyc |
| 7 | Sun/Moon | confirmed | 3012412c | 00171e5c ("Sun Moon Menu") | 120..125 | cyc |
| 8 | Tide | confirmed (str 0x30525e54) | 30118c38 | 00171ea0 ("Tide Menu") | 126..131 | cyc |
| 9 | Hunt/Fish | confirmed | 300b7b7c | 00171bf8 ("Hunt/Fish Menu") | 84..89 | cyc |
| 10 | Device Setup | confirmed | 300c679c | 00171b2c | 57 (300c6a84, full-screen list: System, Display, ..., Page Order...) | cyc |
| 11 | Route Directions | confirmed | none (0) | 00171d08 ("Directions Menu") | 111 (3012e73c) | cyc/on, only while navigating |
| 12 | Edit Route | likely (menu list 0x171360 = "Edit Route Menu"; "Add Point", "Max Points Reached ... 25 points in a route") | 3008b4c4 | 00171cc4 | 109 (3012fb68 "Directions"), 110, 108 (3008a584 "Add Point") | |
| 13 | Find | likely (FIND key goes here; nearby code uses "MSYS1:/system/FIND.TXT", "Searching") | 300bbf48 | 00171a1c | 64 (300c0fe4), 65, 66 | |
| 14 | point projection/selection (from Edit Route "Add Point") | guess | 3008f9f4 | 00171b2c | 103, 102 ("Project Waypoint"), 99 ("Distance"), 101 ("Symbol/Label"), 100 | |
| 15 | Track Detail | likely ("Track Detail Menu", "Track Playback") | 30115c2c | 00171d90 | 134..140 | |
| 16 | Waypoint Detail | likely ("Waypoint Detail Menu", "Delete Waypoint") | 3010aa48 | 00171f28 | 153..158 | |
| 17 | Average Waypoint | guess ("Readings", "Estimated Accuracy"; same shape as 28) | 30106034 | 00171b2c | 147..152 | |
| 18 | Data Management (map file list) | guess ("View Map", "data layer(s) in this map are hidden") | 300c8fc4 | 0017190c | 55 | |
| 19 | Data Detail | confirmed (menu "Data Detail..." -> 0x13) | 300c8e20 | 00171b2c | 49, 50, 51 | |
| 20 | About this Device | confirmed | 3010278c | 00171b2c | 45 (301027f4) | |
| 21 | waypoint multi-select / delete | guess ("Deleting Waypoints") | 30105cdc | 00171b2c | 159, 160 | |
| 22 | Owner Information | confirmed | 30095af0 | 00171b2c | 97, 98 | |
| 23 | ? (opened by result 0xFA7 when not geocache) | unknown | 300cc88c | 00171b2c | 47 (300cc848) | |
| 24 | ? (from the Map menu dispatcher 0x30119708/0x30119860; pane uses compass_cal.dat) | guess: compass calibration | 300d716c | 00171b2c | 46 (300d72fc) | |
| 25 | Demo / welcome | likely (selected at boot when demo is active: `ui_main_loop`; "MSYS0:/demo/demo0.PNB") | 300d8cd8 | 00171b2c | 56 (300d8820) | |
| 26 | Geocaches | confirmed | 300dcea0 | 00171b70 ("Geocache Menu") | 67, 68, 69 | cyc |
| 27 | Geocache Detail | likely ("Last Logged", "Delete Geocache") | 300dfd40 | 00171bb4 | 76..81 | |
| 28 | Average Geocache | confirmed | 300dbc90 | 00171b2c | 70..75 | |
| 29 | geocache multi-select / delete | guess ("Deleting Geocaches") | 300dba48 | 00171b2c | 82, 83 | |
| 30 | Data Layering | guess (opened from the "Data Layering..." / "Data Management..." menu 0x300cec30 and from Layer Detail) | 300e2b6c | 001718c8 | 52 | |
| 31 | owner info at startup | guess (pane near "Display Info at Startup") | 300e3954 | 00171b2c | 96 (30095ffc) | |
| 32 | Log Geocache | likely (opened by 0x300df8a4 "Log Geocache Find"; "Quick Notes") | 300e7b58 | 00171b2c | 60..63 | |
| 33 | ? (from the Geocaches page) | unknown | 300ebe30 | 00171b2c | 144 (300f180c) | |
| 34 | Waypoint Details | likely (menu 0x171f6c = "Waypoint Details Menu" with "Edit Waypoint...") | 300f12a8 | 00171f6c | 145, 146 | |
| 35 | Edit Waypoint | confirmed | 300f0c18 | 00171994 | 58, 59 | |
| 36 | Layer Detail | confirmed | 300e2a84 | 00171b2c | 53, 54 | |
| 37 | point file select | guess (opened from "Point File", from "Discard file", and from MARK when no point file) | 300fd3c0 | 00171b2c | 118, 119 | |
| 38 | Project Waypoint | confirmed (menu "Project Waypoint..."/"Project Geocache..." -> 0x26) | 30100bec | 00171b2c | 104..107 ("How do you want to get there?", "Route Name") | |
| 39 | ? (map + heading; opened from 0x3010278c) | unknown | 301012b8 | 00171b2c | 90 (300ab4a0), 91 (300ac48c) | |

Menu object 0x00171b2c is shared by many pages. It is built from item list 0x0017246c in 0x3011d0a4.

**Key handling per page:** most keys are handled by the focused **pane** (state 2), not by the page
handler. Generic handling (confirmed):
- state 0: `page_menu_key` 0x300926f0 handles only MENU (0x3EF).
- state 1/2: `page_pane_nav` 0x30092768. The rocker (0x3E9..0x3EC) moves the focus between panes and
  sends 0x7D8 (lose focus) and 0x7D7 (gain focus) to the panes. MENU opens the page menu. In state 1,
  ENTER opens the data-field chooser (`FUN_3009ba64(app, 0x171840, rect, class)`) and BACK goes back to
  state 0.

Page-specific examples:
- Map 300acbd0: 0x3ED/0x3EE -> `map_key` 0x300a90cc -> `map_zoom_step` 0x300a8f20 (zoom level byte
  map+0x18, range 1..18; 0x3ED = +1, 0x3EE = -1). Rocker/ENTER enter pane state 2 (panning). 0x3F0
  (PAGE) cancels pan mode and then falls through (returns 10, so the global PAGE still runs).
- Find 300bbf48: on 0x7DC (enter) focuses pane 0.

## 4. Event flow (confirmed, from disassembly)

`ui_dispatch_event` 0x300e6638:
1. `ui_global_filter` (key repeat/power). Event 2 goes to `ui_periodic` 0x3008648c.
2. `ui_dispatch_to_app`(res, cur_page_obj, ev) 0x300939d0. Ghidra cut it at 84 bytes; it really runs to
   0x30093c1c. Order:
   a. `app_dispatch_layers` (menus/dialogs);
   b. the page handler `page_desc[id].h(res, ev, id)`;
   c. if state == 2: the focused pane handler `h(res, ev, id, pane)`;
   d. state 0 -> `page_menu_key`, state 1/2 -> `page_pane_nav`.
   Each step's result goes to `ui_handle_result`. 0xB stops.
3. If not handled: `ui_dispatch_late(ev, 1)` -> `ui_result_redraw` 0x300e3a64 -> `ui_app_commands`
   0x300e5368 -> **`ui_global_keys` 0x300e3c48** -> settings handler 0x300d1870(0x12B76C).
4. Then the result queue 0x0015634C is drained. Each queued result goes through
   `ui_dispatch_late(r, 0)`. For queued results the page handler and the active pane also see the
   event, before `ui_global_keys`.

`page_broadcast(page, ev, mask)` 0x300938a0 (Ghidra shows early returns that the disassembly doesn't
have): bit 4 = app layers, bit 2 = page handler, bit 1 = every pane (last to first).
`page_broadcast_current(ev, mask)` 0x300e3c00 does the same for `page_objs[cur_page]`.

## 5. PAGE cycle, BACK, page switching (confirmed)

| result / fn | effect |
|---|---|
| `{0xFA0, id}` "go to page" | in `ui_global_keys`. Hides the current page, records prev_page (0x00053B68 via 0x300907a4), clears or pushes the page history stack 0x0004309C, then posts `{0xFA1,id}`. Sets 0x000430B0=1 for ids 10,18,19,20,22,23,31 |
| `{0xFA1, id}` "set page" | in `ui_page_commands` 0x300e452c. `cur_page=id`; if not in a flow and the page is enabled, `page_cycle_pos = page_order_index(id)` (0x300907b4); then `page_show(page_objs[id], main_surface)`. If the result queue isn't empty it re-queues itself |
| `page_next` 0x3008616c (**PAGE key 0x3F0**) | `page_hide(cur)`. Advances `page_cycle_pos` (mod 13), skipping entries whose cyclable byte (+4) is 0, whose enabled bit (+8 bit0) is 0, or id 11 when not navigating (`0x3035e0b0(0x3E4A8C)`==0). Posts `{0xFA1, page_order[pos]}`. In a flow (`ui_flow_mode` 0x000430AC != 0) it pushes the old page onto the history; otherwise it clears the history |
| `page_prev` 0x30086674 (**BACK key 0x3F2**, when nothing else handled it) | if the history stack is non-empty: pop it and go there. Otherwise step `page_cycle_pos` backwards with the same skip rules |
| boot (`ui_main_loop` 0x30085e14) | the first enabled entry of `page_order` (by default Satellites) or Demo page 25; `page_show` |

- **page_order** 0x003F1014 (13 x u32), default copied from flash 0x304D1734:
  `0 Satellites, 1 Map, 2 Compass, 3 Trip Info, 4 Waypoints, 26 Geocaches, 5 Routes, 6 Tracks,
  7 Sun/Moon, 8 Tide, 9 Hunt/Fish, 10 Device Setup, 11 Route Directions`. With the default enable bits,
  PAGE cycles Satellites -> Map -> Compass -> Trip Info (-> Route Directions while navigating).
- **User setting:** Device Setup list item "Page Order..." gives result 0x138F, which opens the
  **Page Order dialog** (`app_open_page_order` 0x3009bb14 -> app+0x18 = 0x00175A20, init 0x30133820,
  event handler 0x301338f0, draw 0x301330c4/0x301332d4, title "Page Order" 0x305CF6E4):
  - up/down move the selection;
  - **ENTER toggles the enabled bit** (+8 bit0) of the selected page; Map (id 1) can't be disabled;
  - right selects the move-arrow buttons; ENTER on an arrow swaps the page with its neighbour in
    `page_order` (it keeps `page_cycle_pos` pointing at the current page);
  - 0x3ED/0x3EE jump a screen up/down;
  - BACK (0x3F2) closes the dialog (result 0x13AA);
  - "Restore Defaults" posts `{0x1771, 0x10}` = restore page order and enables.
- `ui_flow_mode` 0x000430AC (`ui_set_flow_mode` 0x300860c0, get 0x3008323c, clear 0x30083308) marks
  multi-page tasks (route creation etc., modes 2, 8, 9, 10...). Pages that leave check it on 0x7DE to
  decide whether to end the flow. **likely**

## 6. Logical keys (from these code paths)

| k | press | key | conf | evidence |
|---|---|---|---|---|
| 5 | 0x3ED | ZOOM IN | likely | map: zoom level +1 (`map_zoom_step(map,1)`); Page Order: screen up |
| 6 | 0x3EE | ZOOM OUT | likely | map: zoom level -1; Page Order: screen down. Direction: the map shows "Zoom %2d" and the level is capped at 18, so a higher level = closer, by DeLorme convention |
| 7 | 0x3EF | MENU | confirmed | `page_menu_key` opens `page_desc[id].menu` with `app_open_menu` 0x3009b994 (app+4 = menu layer) |
| 8 | 0x3F0 | PAGE | confirmed | `ui_global_keys`: 0x3F0 -> `page_next` |
| 10 | 0x3F2 | BACK/ESC | likely | `ui_global_keys`: -> `page_prev` (history pop / previous page); closes the Page Order dialog; leaves pane-select mode |
| 11 | 0x3F3 | MARK | likely | `ui_global_keys`: -> 0x30107c50 / 0x30103504 (strings "Add Waypoint", "...Create a new file to mark wa...") |
| 12 | 0x3F4 | FIND | likely | `ui_global_keys`: posts `{4000, 13}`, the page whose panes use FIND.TXT / "Searching" |
| 9 | 0x3F1 | ENTER | (known) | |

Releases are 0x5DC+k (for example 0x5E5 = ENTER release, 0x5E6 = BACK release, used by the map for
long-press timing).

## 7. Page / pane events and redraw (confirmed unless marked)

| code | sent by | meaning |
|---|---|---|
| 1 | `ui_periodic` broadcast when 0x000432A8 is set | new data tick: data fields redraw. **likely** a GPS/sensor update |
| 2 | main loop every 5 ms (no event) -> `ui_periodic` 0x3008648c -> `page_broadcast_current(ev,0xFF)` | periodic tick to layers, page and all panes. Also checks the backlight timeout and nav state flags (posts 0x7D6 / 0x213D) |
| 0x7D0 | `page_init` | page one-time init |
| 0x7D1/0x7D2/0x7D3 | shutdown loop (around decomp line 1569), sent to every page in reverse | power-off phases. The Map saves its state on 0x7D3 |
| 0x7D4 | result | **request screen flush**: param 0 or main surface -> 0x00053B5C=1; else 0x00053B60=surface (`ui_result_redraw` 0x300e3a64) |
| 0x7D5 | result | flush the page surface (`page_flush` 0x30093218) if `param->+8` |
| 0x7D6 | broadcast | redraw contents (relayout, settings change, nav change) |
| 0x7D7 / 0x7D8 | pane | gain / lose focus |
| 0x7D9 / 0x7DA / 0x7DB | pane | show (draw) / shown (after flush) / hide |
| 0x7DC / 0x7DD / 0x7DE | page handler | enter / shown (after flush) / leave |

**`page_show(page, surface)` 0x30093604:** store the surface; `page_apply_layout`; page 0x7DC; panes
0x7D9 (last to first); `+0x3C=1`; flush (thunk 0x3042907c(surface)); page 0x7DD; panes 0x7DA; if state
is 1/2, the focused pane gets 0x7D7.

**`page_hide` 0x3009351c:** state 1 -> 0; close the app layers (0x3009b818); page 0x7DE; panes 0x7DB;
`+0x3C=0`.

Drawing is immediate-mode: handlers draw into `page->surface` when they get 0x7D9/0x7D6/0x7D7/0x7D8/1.
The data-field test is `0x300af724`. They then return result 0x7D4. After each dispatch `ui_main_loop`
blits `main_surface` if 0x00053B5C is set, and blits 0x00053B60 if an overlay layer is open
(`0x3009b5cc(app)`). Both are cleared afterwards. There is no per-pane dirty rectangle; the dirty state
is the two globals.

Other page commands (`ui_page_commands` 0x300e452c):
- 0xFAB/0xFAC: focus pane `param` in state 2/1.
- 0xFAE: set layout `param` (`page_change_layout` 0x30093358, then 0x7D6 + 0xFA2).
- 0xFAF: set the focused pane's class to `param` (`page_set_pane_class` 0x3009265c).
- 0xFA9/0xFAA: `0x300cf7b4(0/1)` = hide/show the map info fields, then relayout. **likely**
- 0x232B: toggle between layouts 0 and 1.
- 0xFA2 (posted after layout changes) presumably saves the settings in 0x300d1870. **guess**

## Open questions
- Names of pages 14, 17, 18, 21, 23, 24, 29, 30, 31, 33, 37, 39 are guesses. A title string is probably
  drawn by a pane or by the menu title; not traced.
- The menu titles for 0x00171884 (Compass) and 0x0017190c, 0x001718c8, 0x00171994 come from item lists
  set up in code Ghidra split (0x3011d038..0x3011d098).
- Event 1's producer: 0x30085cf4/0x30085d00/0x30085d0c set 0x000432A8 (split code, not traced).
