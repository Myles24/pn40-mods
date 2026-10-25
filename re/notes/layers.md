# Overlay layers, menus, dialogs (PN-40 fw 2.9.6)

Method: decomp.c + raw ARM disassembly (capstone). Several decomp functions here are truncated
(Ghidra stops after the first `bl 3011a400` / misses stack args), so menu tables and message box
args were extracted by a register/stack back-tracking scan of every `bl` to the builder
(scripts in scratchpad `lay/`). Confidence: **C** confirmed, **L** likely, **G** guess.

## 0. Corrections / key map found on the way
- **C** Logical keys (from menu, msgbox, keyboard and fallback handlers):
  k1 0x3E9 UP, k2 0x3EA DOWN, k3 0x3EB LEFT, k4 0x3EC RIGHT (menu 3011dae8 decrements index on
  0x3E9; msgbox 3009ac34 moves to previous button on 0x3EB), **k7 0x3EF MENU** (300926f0 /
  30092768 case 0x3ef -> open page menu), **k8 0x3F0 PAGE** (300e3c48 -> 3008616c next page in
  order list 0x3F1014), k9 0x3F1 ENTER, **k10 0x3F2 BACK/ESC** (closes every layer: result 0x13AA;
  fallback 30086674 = previous page from history 0x4309C), **k11 0x3F3 MARK** (fallback
  -> 30103504 "Add Waypoint"), **k12 0x3F4 FIND** (fallback -> {0xFA0, 0x0D} = Find page; 0x0D is
  the page whose menu is "Find Menu"), k5 0x3ED / k6 0x3EE = ZOOM pair (in lists: page up/down;
  in keyboard: backspace/space). Which of k5/k6 is IN vs OUT: **G** (k5 = list page-up).
- **C** BRIEF names wrong: `ui_modal_loop` 3009aa6c is really `msgbox_show_next` (pops the
  message-box queue 0x57E04); `ui_dispatch_alt` 3009a4c4 is `msgbox_load` (copies a queued
  record into the msgbox object and opens layer +0xC via 3009bb6c at 3009a588).

## 1. App object and layer slots
App object = `*(0x3F0C2C)` = **0x00043330** (.data init). +0 u8 `locked` (3009b940 sets it; when set
`app_top_layer` returns dummy 0x12BD70), +1 u8 `power_dialog_active` (set by 3009bae4,
cleared by 3009b818).

Generic ops (all **C**):
| addr | name | what |
|---|---|---|
| 3009b5a0 | app_layers_clear | zero all 9 slots |
| 3009b5cc | app_any_layer | 1 if any slot non-null (else app[0]) |
| 3009b644 | app_top_layer | first non-null of +4,+8,+C,+10,+14,+18,+1C,+20,+24 |
| 3009b754 | app_close_all_layers | calls every close fn, zeroes all slots. **Every opener calls it first** => only one layer is ever open |
| 3009b818 | app_close_top_layer | closes the topmost; returns 1 |
| 3009bc30 | app_dispatch_layers | (brief) |
| 3009bda0 | app_open_layer_desc | reopen a layer from a saved descriptor (result 0x1388) |
| 3009b6e8 | app_clear_dropdown | +0x20 := 0 if it equals arg |

Slots (opener / closer / event handler / draw):
| slot | what | open fn (layer obj) | init/draw | close | handler | evidence |
|---|---|---|---|---|---|---|
| +0x04 | **menu / popup list** | 3009b994(app,menu) full-screen; 3009ba64(app,menu,rect,sel) popup at rect | 3011d564 (full: window 6,6..0xA9,0xD5), 3011b650 (popup above/below rect), 3011b7f0 (in given window); draw 3011b03c | 3011aa70 | 3011dae8 | **C** |
| +0x08 | **on-screen keyboard** (obj 0xD4E70) | 3009ba18(app,kbd,rect) | 300aea98 (window 173x81 at x=2, below rect if rect.y1<0x8B else above); draw 300ae254 | 300aeb84 | 300aebe8 | **C** layout string, result 0x138B |
| +0x0C | **message box** (obj 0x55C9C) | 3009bb6c(app,box) | 3009a0c4 (window, text, buttons 30099784, sel 300995b4, paint 300996b4) | 3009abd4 (-> show next queued) | 3009afb0 (keys -> 3009ac34) | **C** |
| +0x10 | **input dialog** (msgbox with embedded text field, obj 0x169FC4) | 3009bb40(app,obj) via result 0x1391 | 300fc940 (same geometry as msgbox); buttons 300fc734 (Close / Ok,Cancel / Yes,No / custom) | 300fcc7c | 300fce4c (ENTER -> {0x13AE, packed}) | **L** |
| +0x14 | **option-list screen** (Setup sub-screens, Route Options, Playback Speed...) 0x64-byte objs 0x54D60..0x55C38 | 3009bba8(app,scr,sel) full-screen; 3009bab8(app,scr) half-screen (Map Setup) | 30097c04 / 30097ae0; draw 30096ac8 | 300971d4 | 300984c0 | **C** |
| +0x18 | **Page Order editor** (obj 0x175A20) | 3009bb14(app) via 0x138F ("Page Order...") | 30133820 | 301337f8 | 301338f0 | **C** (bug: 3009bb14 tests +0x14 not +0x18) |
| +0x1C | **grid picker** (symbol/colour picker, objs 0x175860, 0x175818) | 3009b9c4(app,obj,rect,sel) via 0x1392 | 30132400 (popup by rect), 30132260 (given window); draw 30131a6c | 30131a44 | 301329b8 (ENTER -> 0x13AF, BACK -> 0x13AA) | **L** |
| +0x20 | **dropdown marker** of a choice widget (class vtable 0x304D3188, window at +0x3C) | setter not found | - | 300c1680 | none (dispatch returns 10) | **G** |
| +0x24 | **power dialog** (obj 0x1737EC) | 3009bae4(app,mode) | 30131160 | 30130710 (+ app[1]=0, show queued msgbox) | 30130cc4 | **C** strings |

Power dialog modes (30131160, strings at 0x3F2868/6C/70): POWER release (300863xx) -> mode 1 if
keys locked (301a32b4()==1) else 2: "Power Menu" with "Lock the buttons?"/"Unlock the buttons?"
and a list/slider widget at +0x70 (result 0x177B, backlight **G**). Long POWER hold (3008648c,
time >= *(0x43264)) -> mode 0 "Shutting down the device.". Key while locked (300e4da4) -> mode 3
"Press the POWER button to unlock the device.". Handler auto-closes (0x13AA) after 4 ticks in
modes 1-3. **C/L**

### Menu layer object (0x44 bytes, e.g. 0x171B2C..0x171F6C) **C**
| off | field |
|---|---|
| 0x00 | window (304258e0 create / 304253f0 destroy) |
| 0x04 | menu_def* (main items) |
| 0x08 | menu_def* extra items = 0x1723AC "page list" (or 0) |
| 0x0C | open submenu def (0 = none) |
| 0x10 | submenu table {u16 n; s16* item_idx @+4; menu_def** @+8} |
| 0x14 | cursor; 0x18 submenu cursor (-1 none); 0x1C previous cursor; 0x20 top row |
| 0x28 | u8 close_on_select (1: ENTER posts 0x13AA instead of item result) |
| 0x29 | u8 owns window; 0x2A u8 flag |
| 0x2C | total items (main + extra); 0x30 visible rows |
| 0x34 | rect (4 bytes); 0x38 inner rect |
| 0x40 | key auto-repeat deadline (500 ms first, 300 ms repeat via 301a34ac/301a34bc key_is_down) |
Init: 3011ac80(obj, def, submenu_tbl, has_page_list, close_on_select) -> 3011ac04 (reset, rebuild
page list via 3011ab0c).

## 2. Menu system
### Data structures **C**
- `menu_def` (0x18 bytes, init 3011a2e4(def,title,items,count) for dynamic, 3011a478(def,title,
  strings[],results[],count|-1,flags[]) for static):
  +0 title, +4 char*[] (static), +8 result[] {code,param} (static), +0xC flags[] (static),
  +0x10 u8 dynamic, +0x12 s16 count, +0x14 item[] (dynamic).
- dynamic item = 16 bytes `{char* label; u32 code; u32 param; u32 flags}`.
- builder `{item* buf; int n; int cap}`: 3011aaa4 init(b, buf, cap), **3011a400 add(b, label,
  &{code,param}, flags)**. Accessors: 3011a30c get_result(def,i), 3011a37c get_label, 3011a3b4 get_flags.
- Page-type table **0x3F0C54 + type*0x18**: +0 id, +4 u8 enabled, +8 flags (bit0 = in PAGE rotation),
  +0x10 menu layer obj, +0x14 page command handler (called by ui_dispatch_late). Menu objs are bound
  to defs at boot by 3011d0a4/3011d218.
- Many items are ALSO written directly into static item arrays (16-byte stores) instead of via
  3011a400 (e.g. 3011e494 Device Setup, 3011ea68 page list, 3011fd28 Find categories).

### Flow **C**
1. MENU (0x3EF) reaches the page (30092768 / 300926f0): if page-type menu obj (0x3F0C64+type*0x18)
   != 0 -> 3009b994(app, menuobj) -> 3009b754 closes other layers, slot +4 = obj, 3011d564.
2. 3011d564 first calls 3011d45c, which rebuilds the dynamic def for the current page index
   (switch on 0x53B64: 1 -> 300a6f64 (Map, builds "Map Menu" via 3011d654), 3 -> 3011bab4(0x171538,3)
   Trip Info, 4 -> 3011bcf0(0x1724E8,0x11), 5 -> (0x172518,8), 0xB -> (0x1724A0,10) Directions,
   0xC -> (0x171360,9), 0x1A -> (0x171648,5) Geocache), creates the window and draws (3011b03c).
3. Handler 3011dae8: UP/DOWN move (auto-repeat), k5/k6 page up/down, BACK closes submenu or posts
   0x13AA, ENTER: item code 0x1389 opens its submenu (looked up in +0x10 table); otherwise the item's
   `{code,param}` is passed to ui_handle_result, i.e. **posted to event queue 0x15634C** and later
   dispatched by ui_dispatch_late -> 300e5368 (global command switch) -> page-type handler
   (0x3F0C68) -> 300e3c48 (global fallback). Items from the +8 page list are `{0xFA0, page}`.
4. Opening any other layer from a command closes the menu (3009b754); otherwise 0x13AA closes it.
5. 3011bcf0(def, kind) is the big builder: kind 0 Compass Menu, 1 Device Setup, 2 Waypoint Edit,
   3/7 generic "Menu", 4 Find categories, 5 Geocache Menu, 6 Geocache Detail, 8 Route Menu,
   9 Edit Route Menu, 10 Directions Menu, 11/12 Satellites Menu, 13/14 Track Menu, 15/16 Track Detail,
   17 Waypoint Menu, 18-21 Waypoint Details (jump table 0x3011BD0C). Titles: Trip Info Menu
   (3011bb2c), Map Menu (3011d654, def 0x170F00), Find Menu (3011e17c/2a8/318), Data Layering,
   Data Management, Sun Moon, Tide, Hunt/Fish (3011ce4c/3011cf00).

### Concrete item -> action (code, param) **C** (from disassembly; handler in brackets)
| menu | item | result | action |
|---|---|---|---|
| Map Menu 3011d654 | Stop Simulation | 0x213D,0 | stop sim/nav [300e3c48] |
| | Simulation Speed... | 0x1394 | open list 0x55788 [300e5368] |
| | Hide Route | 0x2144 | 30087d04 |
| | View Comment Details... | 0xFA7 | detail page / {0xFA0,0x22 or 0x17} |
| | Log Your Geocache Find | 0x213C | msgbox "Log Geocache" type 0x6B (Found It!/Didn't Find It/Write Note/Maintenance) |
| | Route Options... | 0x13A6 | 300976b4 then screen 0x554CC "Route Options" |
| | Stop Track Playback / Playback Speed... | 0x232F / 0x1393 | -; screen 0x55724 |
| | Turn Charts On/Off | 0x2343 | charts toggle |
| | Turn Imagery On/Off | 0x2043 | imagery toggle |
| | Map Setup... | 0x139B | 3009bab8(app,0x54D60) half-screen "Map Setup" |
| | Measure Distance | 0xFAD | - |
| | Simulate GPS Edit Off/On | 0xFA6,0/1 | - |
| | Info Fields... | 0x1396 | screen 0x55274 or 0x55210 "Info Fields Setup" |
| Device Setup (kind 1, also 3011e854) | System... / Display... / Connect to Computer... / Compass & Altimeter... / Sound... / Time... / Units... | **0x138E**, screen 0x54E8C / 0x54EF0 / 0x54F54 / 0x54FB8 / 0x5501C / 0x550E4 / 0x55148 | 3009bba8 open option-list screen (titles from 30097dc0: "System", "Connect to Computer", "Sound", "Time", "Units"...) |
| | Page Order... | 0x138F | layer +0x18 |
| | Owner Information... / About this Device... | 0xFA0, 0x16 / 0x14 | go to page |
| | Restore Setup Defaults / Reset to Factory Defaults | 0x1771, 0xFFF / 0xFFFFFFFF | restore with mask |
| Satellites Menu | Enable GPS / Disable GPS | 0x1772, 0 / 2 | GPS mode |
| | Calibrate... / Set Current Location | 0x13A2 / 0x2044 | screen 0x558B4 "Calibrate Sensors" |
| Waypoint Menu (kind 17, direct stores) | Switch to Sort By Name/Distance | 0x233D, 0/1 | |
| | Delete By Symbol / File... | 0x2339 / 0x13A7 | |
| Geocache Menu (kind 5) | Sort By Name/Distance 0x1F45; Show (Not) Logged 0x1F46; Delete By Symbol 0x1F41; Upload Field Notes 0x1F49; View Field Notes 0x1F4A; File... 0x13A8 | | |
| Track Menu | File... 0x13A9; Edit Track Settings... 0x1395 (screen 0x551AC); Show Active Track Area... 0x2337; Show Track Area... 0x2336 | | |
| Waypoint Details | Edit Waypoint... {0xFA0,?}; Delete Waypoint 0x2338; Project Waypoint... {0xFA0,0x26}; Move to GPS Location 0x233B; Average Waypoint... 0x233C | | |
| Find Menu | Switch to Near Map Center/Find by Name 0x1D4C; Name Contains/Begins With 0x1D4E; Clear Recent Finds 0x1D54; Reset to Current Location 0x1D55; Clear All Fields 0x1D53; categories All..Cities {0x1D4D, 0..12}; POI sub-cats {0x1D4F,n}, natural {0x1D50,n}, Hunt/Fish {0x1D51,n} | | |
| Page list (3011ea68, +8 items) | Satellites 0, Map 1, Compass 2, Trip Info 3, Waypoints 4, Routes 5, Tracks 6, Sun/Moon 7, Tide 8, Hunt/Fish 9, Device Setup 0xA, Route Directions 0xB, Geocaches 0x1A | {0xFA0, page} | go to page |
| Route Options screen items (C++ item objs, 30119860) | Stop Navigating 0x213D; Back on Track 0x2140; Switch to Hiking/Driving 0x2143; Use Find 0x213A / Use Map 0x213B | | |
| Info field screens | Hide/Show Info Fields 0xFA9/0xFAA; Change Info Fields 0xFAC; layouts " 4 Narrow Fields" etc {0xFAE, n}; Restore Default Info Fields 0x1771 | | |
| Playback/Simulate Speed screens | 1X..50X {0x2330, 0..5} | | |
| File Options screens | Close 0x1B5D, New 0x1B5E, Open 0x1B5F, Save 0x1B60, Save As... 0x1B61 | | |

Option-list screens (+0x14): 0x64-byte objects built by 30097254(obj, n_items, title, item_ptrs[],
font, init_sel) / 300972d8(.. , &back_result, ..) in 30097dc0; items are C++ widget objects
(vtable slot 3 = event handler; e.g. menu-item class vtable 0x30525BC4: +4 label, +8 {code,param},
+0x20 font; created by 301194d8). Events go to the selected item first; init event 0x13AB, refresh
0x7D6. Layout: +4 owner, +8 window, +0xC s16 sel, +0x10 s16 n, +0x12 s16 init sel,
+0x14 u8 has_back, +0x18 back result {code,param} (e.g. {0x138E, 0x558B4}), +0x24 rect,
+0x30 u8 owns window, +0x34 title, +0x38 items[], +0x44 font, +0x60 u8 editing.

## 3. Message boxes **C**
`msgbox_show(box=0x55C9C, title, text, type, def_btn, page, ctx, dismiss_cb)` = **3009a670**
(60 callers). Stack args: [sp]=def_btn, [sp+4]=page (-1 = current), [sp+8]=ctx (u16 id echoed in
the result), [sp+0xC]=dismiss_cb (polled on event 1; nonzero -> auto close, e.g. 30083fd4 for
"Low Satellite Signal", 300fec0c "Device in Use").
- If a box is already up (top layer == 0x55C9C), the request is queued in the ring buffer
  **0x57E04** (10 x 0xFC records at 0x57E14; 3009b36c dup check, 3009b448 push front for urgent
  types (30099028: 100,102,103,104,112,113), 3009b528 push back). 3009abd4 on close -> 3009aa6c shows next.
- Record (0xFC): +0 title[32], +0x20 text[200] (truncated with "..."), +0xE8 type, +0xEC def_btn,
  +0xF0 page, +0xF4 ctx, +0xF8 cb. Box object: +0 window, +4 title, +0x24 text, +0xEC type,
  +0xF4 def_btn, +0xF8 n_buttons, +0xFC cb, **+0x100 packed result** = page<<24 | button<<16 | ctx.
- Buttons are 0x30-byte records at 0x57D14 + 0x30*i (+0x28 selected, +0x2C code) laid out by
  30099784 per type. ENTER -> 30099050 picks the code -> result **{0x13AD, packed}** posted to the
  queue; the page / 300e3c48 decodes with 30098f9c (ctx = low 16) and 30098f90 (button = bits 16-23).
  LEFT/RIGHT select (UP/DOWN for vertical types 0x6A-0x6F).

| type | buttons (code) |
|---|---|
| 1, 0x64, 0x6D | Close (1 / 100) |
| 2, 0x65, 0x73 | Ok (1 / 100) |
| 3, 0x66 | Ok (1/100), Cancel (2/0x65) |
| 4, 0x67 | Yes (3/0x68), No (4/0x69) |
| 5 | Reset 5, Cancel 2 |
| 6 | Save 6, Discard 7 |
| 7 | Save As... 6, Cancel 2 |
| 0x68 | Disable 0x6A, Continue 0x69 |
| 0x69 | Move 0x75, Delete 0x76 |
| 0x6A | Drive 0x6B, Hike 0x6C ("How do you want to get there?") |
| 0x6B | Found It! 0x6D, Didn't Find It 0x6E, Write Note 0x6F, Maintenance 0x70 (vertical) |
| 0x6C | Find Next Closest 0x71, View List 0x72, Stop Geocaching 0x73 |
| 0x6E / 0x6F | Open SD Card 0x78 / Open Internal Drive 0x77, Use GPS 0x79 |
| 0x70 | none ("Device in Use" busy) ; 0x71 Cancel 0x65 ; 0x72 Close 0x66 |

Examples (decoded call sites): "No SD Card"/"Insert an SD card to take screenshots." (30083424):
type 0x65, def 0, page -1, ctx -1. "Delete Map" confirm (300c9eb0, text built with sprintf from
0x304D3C50 "Are you sure you want to delete the selected map?"): type 4 Yes/No, def_btn 1 (No),
page 0x13, ctx 0. "Delete Route" 3008bffc type 4 def 1; "Off Route" 3012e400 type 0x67 with
cb 3012e03c; GPS-mode conflict boxes use ctx 0x1F5-0x1FB, decoded in 300e3c48 (e.g. ctx 0x1F7 +
button 0x6A Disable -> {0x1772, 2} disable GPS).

## 4. Result / command codes
ui_handle_result (300e3b70): 300e3a64 handles 0xB/0xA/0x7D4/0x7D5 immediately; any other code is
**pushed onto event queue 0x15634C** (cap 10) and returns 0xB. The queue is drained at the end of
ui_dispatch_event (300e6638) through ui_dispatch_late: 300e3a64 -> **300e5368** global commands ->
page-type handler (0x3F0C68+type*0x18) -> page field handler (300908c8) -> **300e3c48** fallback.

| code | meaning | conf |
|---|---|---|
| 0x2 | periodic tick (re-posted) | C |
| 0xA / 0xB | not handled / consumed | C |
| 0x7D4 | redraw request: param = object (0 = whole screen); sets 0x53B5C dirty, 0x53B60 obj | C |
| 0x7D5 | redraw current page if param->+8 | C |
| 0x7D6 | refresh event sent into layers/widgets | C |
| 0x7D7 / 0x7D8 | widget focus gained / lost | L |
| 0xBBA / 0xBBB | activate / deactivate widget; 0xBBC widget changed; 0xBBD text changed; 0xBBE text edit finished (param = field, 0 = cancelled) | C(BBD/BBE) L |
| 0xFA0 | go to page `param` (push history 0x4309C, then 0xFA1) | C |
| 0xFA1 | switch current page to `param` (sets 0x53B64, 30093604) | C |
| 0xFA3 | clear page history | C |
| 0xFA4/0xFA5 | 300860c0(param) / 30083308 | C (call), meaning ? |
| 0xFA9/0xFAA/0xFAB/0xFAC/0xFAE/0xFAF | info-field show/hide/edit/layout/select type | L |
| 0x1388 | reopen layer from descriptor `param` (3009bda0) | C |
| 0x1389 | menu item = open submenu | C |
| 0x138A | open keyboard at rect `param` (0 = page field rect) | C |
| 0x138B | keyboard char `param` (8 BS, 0xE right, 0xF left, 3 done, 0x18 cancel, 0x7F clear) | C |
| 0x138C / 0x138D | close top layer | C |
| 0x138E | open option-list screen `param` (layer +0x14) | C |
| 0x138F | open Page Order (+0x18) | C |
| 0x1390 | open message box layer (+0xC, 0x55C9C) | C |
| 0x1391 | open input dialog (+0x10, 0x169FC4) | C |
| 0x1392 | open grid picker: param -> {rect, obj, sel} (+0x1C) | C |
| 0x1393..0x13A6 | open a specific setup screen (see 300e5368: 0x1393 Playback Speed 0x55724, 0x1394 Simulate Speed 0x55788, 0x1395 Track Settings 0x551AC, 0x1396 Info Fields, 0x1397/98/99 Arrange Info Fields, 0x139B Map Setup, 0x139C Map Point Mgmt 0x54DC4, 0x139D Add Point 0x55468, 0x139E Compass Arrow 0x5565C, 0x139F/13A0/13A1 altimeter cal screens, 0x13A2 Calibrate Sensors, 0x13A3/13A4 compass cal, 0x13A5 Reset Trip Info 0x556C0, 0x13A6 Route Options) | C |
| 0x13AA | close top layer + redraw (from Navionics Setup returns to Map Setup) | C |
| 0x13AB | item/list init event | C |
| 0x13AD | message box answer {0x13AD, page<<24 | btn<<16 | ctx} | C |
| 0x13AE | input dialog answer | L |
| 0x13AF | grid picker selection | L |
| 0x13B1 | refresh option-list item | C |
| 0x1771 / 0x1772 | restore defaults (mask) / set GPS mode | C |
| 0x2xxx, 0x1Bxx, 0x1Dxx, 0x1Fxx | feature commands (route, track, file, find, geocache), see table in §2 | C |

## 5. On-screen keyboard (layer +8, obj 0xD4E70) **C**
- Opened by a text field: field widget 300e7444 on ENTER sets editing, then posts
  {0x138A, &rect(field+0x760, y+5)}; 300e5368 -> 3009ba18(app, 0xD4E70, rect). Also from
  300954c0, 300e9f0c, 300f8338 (input dialog field 0x169DCC), 30121600.
- Grid 11 x 5, cell index at obj+4 (s16), char table ptr obj+0xC, case mode obj+0x10:
  `"ABCDEF  123GHIJKL  456MNOPQR  789STUVWX  0+-YZ,'!&  ./\n"` (lowercase variant for mode 0).
  Columns 6-7 are function keys: row0 = cursor left (0xF) / right (0xE), row1 = case cycle
  (cell 17) / backspace (cell 18 -> 8), row2 = Done (code 3), row3 = space, row4 = Clear (0x7F,
  switches to upper case).
- Keys: arrows move (300ae010/048/084/140, auto-repeat), ENTER = press cell (300ae870),
  k5 -> backspace {0x138B,8}, k6 -> space (cell 39), k11 -> jump to Done cell (28), k12 -> case
  cycle (cell 17), BACK -> {0x138B,0x18} cancel. Each press posts {0x138B, char}; the page forwards
  it to its focused field. Close (300aeb84) posts a cancel unless already done.
- Text field widget (300e7444 / 300e6ad0): +0 editing, +3 replace-on-first-key, +8 cursor,
  +0xC length, +0x10 char[256], +0x110 scroll. Insert/backspace/cursor in 300e6ad0 (beeps
  300849e8 at 255 chars); done -> {0xBBE, field}, cancel -> {0xBBE, 0}, other -> {0xBBD, field}.
- "Enter signature text" (0x30523A78) is the last entry of the Log Geocache quick-text table
  0x30523A90 (TFTC, TFTH, TNLN, SL, TTB, LTB, FTF, "Enter signature text"), used by page code
  300e8d10 whose text widget 0x156E28 (300e9f0c) opens the keyboard. **L**

## Open questions
- Which of k5/k6 is ZOOM IN vs OUT.
- +0x20 setter and exact class (choice-widget dropdown) not found.
- Exact producers of 0x1391 (input dialog) and 0xFA2; meaning of 0xFA4/0xFA5, 0x13B0.
- Power-menu widget (result 0x177B/0x177C): backlight vs lock choice not decoded.
