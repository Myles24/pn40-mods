# PN-40 UI reverse engineering: shared brief

Target: DeLorme Earthmate PN-40, firmware 2.9.6.70319. ARM926EJ-S (ST Nomadik/Cartesio
family), OS20 RTOS, code mostly ARM with ~250 Thumb functions. Screen 176x220, RGB565.

## Files (all under `re/`)
- `out/decomp.c`: Ghidra decompilation of all 12,851 functions, address order. Each starts
  with `/* FUNC <addr> size N callers N [thumb] */`. Grep it; don't read it whole (13 MB).
- `sh fn.sh <addr> [addr...]` prints whole functions (lowercase hex, 8 digits).
- `out/functions.tsv`: addr, name, size, callers, callees, thumb, strings used.
- `out/strings.tsv`: string addr, text, functions that reference it. Best entry point:
  find a UI string, then the functions that use it.
- `out/ui_strings.txt`: printable strings in the UI text area 0x304D0000-0x30530000.
- `names.tsv`: names identified so far (also applied in decomp.c).
- `fw_stock.bin`: the firmware image, loaded at 0x30080000. RAM .data initial values:
  `ram_data.bin` = RAM 0x003F0C14.. (copied from flash 0x3044822C at boot).
- Raw disassembly if needed: Python with `capstone` (ARM mode, base 0x30080000).

Do NOT run Ghidra (it would lock the project). Do not edit files outside `re/notes/`.

## Memory map
- Code (ARM/Thumb) 0x30080000-0x3044807F; rodata/strings/fonts 0x30448080-0x306CC90B.
- RAM: .bss 0x40-0x3F0AAC (zeroed), .data 0x3F0C14-0x462584, heap partitions 0x4E7DCC-0x7DB5CC.
- `DAT_00xxxxxx` in decomp.c = RAM globals; `DAT_30xxxxxx` = flash constants.

## Established UI architecture
- UI task `ui_task` (0x3008608C, prio 3) -> `ui_main_loop` (0x30085E14). Loop: every 1 ms
  `ui_get_event`; every 5 ms posts periodic event {2, 0}; each event -> `ui_dispatch_event`
  (0x300E6638).
- Event = 8 bytes {u32 code, u32 param}. Handler result = 8 bytes {u32 code, u32 param},
  returned through a hidden pointer as the first argument. Result code 10 = not handled.
- Keys: InputController task polls 13 buttons. Logical key k = 1..13: press event 0x3E8+k,
  release event 0x5DC+k (`ui_translate_keys` 0x300E4DA4). Known: k=1..4 rocker directions,
  k=9 (0x3F1) ENTER, k=13 (0x3F5) POWER. The others (k=5,6,7,8,10,11,12) are PAGE, MENU,
  FIND, MARK, back/ESC, ZOOM IN, ZOOM OUT in some order: unknown.
- Dispatch: `ui_global_filter` (0x300E4934) -> `ui_dispatch_to_app` (0x300939D0) ->
  `app_dispatch_layers` (0x3009BC30): the app object `*(0x003F0C2C)` has layer pointers at
  +4,+8,+0xC,+0x10,+0x14,+0x18,+0x24,+0x1C,+0x20 (checked in that order); the first non-null
  gets the event, handlers 3011DAE8, 300AEBE8, 3009AFB0, 300FCE4C, 300984C0, 301338F0,
  30130CC4, 301329B8. Then `ui_handle_result` (0x300E3B70, 238 callers) acts on the result.
- Pages: page objects in table `0x00043164[]`, current page index `0x00053B64`. Initial page
  order list at `0x003F1014` (13 entries), per-page flags at `0x003F0C5C + index*24`.
  0x30093604(page, ...) is called on the selected page at start.
- Screenshot: `take_screenshot` 0x30084B4C allocates a 0x12E80-byte image and fills it via
  0x304478A4 (an ARM->Thumb veneer), then `write_bmp` (0x30086C80) reads an image struct:
  width u16 at +0x18, height u16 at +0x1A, RGB565 pixels at +0x34.

## Output conventions
Write ONLY to your assigned files in `re/notes/`:
- `<area>.md`: findings. For every claim give the address(es) and the evidence (a string,
  a call, a constant). Mark confidence: **confirmed** (code clearly shows it) / **likely** /
  **guess**. Include struct layouts as offset tables.
- `<area>_names.tsv`: `addr<TAB>name<TAB>comment`, lowercase 8-digit hex, functions or data
  you're confident about (likely or better). Short snake_case names.
Keep the .md focused and dense; another agent will merge all areas.
