# PN-40 mods

C code added to firmware 2.9.6.70319, linked into the empty NOR area at
0x306D0000 and hooked in with small patches. See `..\nor\BOOTLOADER.md` for
how updates are installed and what the bootloader checks.

## Build and test

```
python build.py ..\update\PN-40.hex      # -> out\PN-40.hex (+ mods.map, symbols.txt)
python test_nmealog.py                   # emulator tests against out\PN-40.hex
python test_ascent.py                    # ascent/descent emulator tests
python test_profile.py                   # elevation profile page (also renders out\profile_*.png)
```

Every build overwrites `out\`. When you install a build, keep a copy as
`baseline_<first 8 hex of its sha256>\PN-40.hex` so it stays available for rollback
and as the base for the next delta.

**Delta updates (much faster installs):**
`python build.py ..\update\PN-40.hex --base baseline_<installed>\PN-40.hex` also
writes `out\PN-40.delta-from-<base>.hex`. It holds only the 64 KB blocks that
differ from the installed build, each block complete (the bootloader 0xEE-fills
any byte a file leaves out of a block it touches, and doesn't touch other
blocks). build.py refuses to write it unless a model of the bootloader's
flashing gives byte-identical flash for base + delta and for the full file, and
the bootloader's parser accepts every line. Copy it to the card as
`SD:\update\PN-40.hex`. **It is only correct on a device running exactly the
base build**; the diagnostics reports show the running build's id (`build=`,
printed by build.py as "build id"). `test_pnhex.py` tests the delta logic.

Needs `arm-none-eabi-gcc` (MSYS2 package `mingw-w64-x86_64-arm-none-eabi-gcc`)
and Python with `unicorn` (and `capstone` for disassembly work).

The build always starts from the stock file, includes the week-rollover fix
from `..\patched\make_patch.py`, and refuses to write the result unless:
- every patch finds the stock bytes it expects;
- every record is in 0x30080000-0x307EFFFF (never the bootloader, never the
  identity/marker block), ascending, 1-16 bytes, at most 60 characters;
- the bootloader's own line parser, run in an emulator from the NOR dump,
  accepts every changed or new line;
- no firmware code loads a pointer into the mod area;
- C functions that run on the NMEA task stay within their stack budget.

## Install

Copy only `out\PN-40.hex` to `SD:\update\PN-40.hex` (the bootloader skips a
missing `update\0\` folder, and leaving out the 307 MB base data is much
faster), then eject, unplug and power-cycle.

Rollback: `baseline_074e3208\PN-40.hex` (+ Total Ascent/Descent, before the
profile page), `baseline_7e7c9e50\PN-40.hex` (logger + Battery Voltage),
`..\patched\PN-40.hex` (date fix only) or `..\update\PN-40.hex` (stock).
The profile page needs no preparation: page 9 keeps its id and widgets, so
older builds simply show Hunt/Fish again. Field ids are saved in `page_state.dat`, so
first remove from every page any field the older build doesn't have (161
Battery Voltage, 162/163 Total Ascent/Descent), or it will very likely crash
drawing that page.

## NMEA logger (nmealog.c)

Logs everything the GPS engine's NMEA output produces (by default GGA, GSA,
GSV and RMC once per second) to `SD:\nmea\YYYY-MM-DD_HH-MM-SS.nmea`, one
file per power-on, named from the device clock.

- **On/off:** logging happens only while a folder named `nmea` exists in the
  SD card root. Create it to start logging; delete or rename it to stop.
- Data is written every 10 s (sooner if the 16 KB buffer is half full), with
  open/append/close each time, so turning the unit off loses at most ~10 s.
- Nothing is written while the SD card is connected to a PC.
- `SD:\nmea\status.txt` is rewritten every 10 s while the card is usable,
  even when there is nothing to log, with counters: uptime, UI events, sentences
  seen, bytes buffered/written/dropped, errors, and the USB state
  (`usb_state` 0 = SD card exported to a PC, 1 = internal drive, 2 = none).

How it fits in:

| Patch | Where | What |
|---|---|---|
| heap region 1 size | 0x304D08A0, 0x304D08E8 | 0x232800 -> 0x22A800, frees RAM 0x0076B5CC-0x007735CB for the logger |
| NMEA_start literal | 0x30082884 | boot calls `nmea_start_tramp`: init, then the original |
| nmea_send | 0x3014B578 | after the NMEA mutex is taken: copy the sentence to the ring |
| UI main loop | 0x30085EE4 | its periodic `bl time_now` (every few ms, UI task): maybe flush the ring to SD |
| screenshot | 0x30086434 | after each screenshot: flush, then write `SD:\screenshots\nmealog.txt` (diagnostics) |

v1/v2 hooked `ui_dispatch` (0x3009A4C4) instead, which turned out to be off
the main event path, and assumed a 26 MHz tick (it's 3.25 MHz, see
`fw_ticks_per_sec`). Neither ever wrote on the device.

File I/O is done only from the UI task: the firmware's `fopen` builds paths
in a shared static buffer, so calling it from another task could collide
with the UI's own file access.

## Data fields

| id | Field | Source |
|---|---|---|
| 161 | Battery Voltage | `battfield.c` |
| 162 | Elevation: Total Ascent ("Total Ascent") | `ascent.c` |
| 163 | Elevation: Total Descent ("Total Descent") | `ascent.c` |

New fields go at the end of `OUR_FIELDS` in build.py; raise `PANE_TABLE_LEN`
in build.py and fw.h. Never renumber or remove one. The "Change Info Fields"
picker holds 50 entries; 48 are used.

## Total ascent / descent (ascent.c)

Research: `..\re\notes\ascent.md`. The totals use the same elevation samples as
the stock Elevation: Maximum/Minimum: once per valid fix (about 1 Hz), from
the barometer (manual or auto-calibrated) or from GPS with the barometer off.

- **Deadband:** a change counts once it is more than 3 m from the reference
  (10 m on GPS elevation); then the whole change counts and the reference moves.
- **Jump guard:** a change faster than 15 m/s since the previous sample (gaps
  under 1 s count as 1 s) moves the reference without counting: altimeter
  calibration, switching the barometer or auto-calibration. Changing the
  source (GPS / barometer / auto-cal) also restarts the reference. A
  calibration that shifts less than 15 m in one fix counts like a real change
  beyond the deadband.
- **Storage:** centimetres in trip +0x28 (ascent) / +0x2C (descent), which the
  stock firmware never touches but saves with the trip in
  `MSYS1:/system/DEVICE_STATE.TXT`. A rollback keeps the bytes and ignores them.
- **Reset:** with the trip elevations ("Reset Trip Elevations", full trip
  reset, defaults); every path goes through `trip_reset_elev`.
- **Saving:** `trip_save` whenever either total has grown 25 m since the last
  save (stock saves every 10 minutes), so a power-off loses at most about
  that much. This also saves the stock trip values more often.
- **Display:** the stock elevation formatter (ft or m per Measures), with up to
  7 digits instead of the stock 5, so it never shows "- -" below the
  1,000,000 m cap.

| Patch | Where | What |
|---|---|---|
| trip_update_elev call | 0x300C623C | `bl elev_tramp`: stock max/min, then `ascent_sample` |
| trip_reset_elev entry | 0x300C5CD4 | `b reset_elev_tramp`: clear totals and reference, then the original |
| NMEA_start literal | 0x30082884 | (shared) `nmea_start_tramp` also calls `ascent_init` |

State (reference, last sample, counters) is in RAM at 0x0076FA00, after the
logger's 0x4400 bytes, set up at boot.

## Elevation profile page (profile.c)

Replaces **Hunt/Fish** (page 9). Turn it on in Device Setup > Page Order,
where it is listed as "Elev. Profile"; PAGE then reaches it like any page.

- **Views:** elevation over time or over distance; **ENTER** switches. **ZOOM
  IN/OUT** change the span: 30 min / 2 h / 6 h / 12 h, or 0.5 / 2 / 5 / 20 mi
  (1 / 3 / 10 / 30 km, 0.5 / 2 / 5 / 15 NM, following Measures). Each view keeps its
  own span. (Which physical key is ZOOM IN is not proven; if they feel
  reversed, swap `KEY_ZOOM_IN`/`KEY_ZOOM_OUT`.)
- **Screen:** scale in ft or m with top/middle/bottom labels (at least 20 m
  tall, so noise doesn't look like hills), "now" at the right, current
  elevation and total ascent/descent at the bottom, theme colours.
- **Recording:** always on, also while the page isn't shown. Every 12 s one
  record: the average of the elevation samples since the last record (the
  same samples as the trip computer and ascent totals) and the trip odometer
  distance since then. No fix = a gap (blank in the time view, bridged in
  the distance view). 3600 records = the last 12 h; RAM only, so the
  history starts empty after power-on. The distance view covers whatever
  distance those 12 h hold.
- **Redraw:** when a record arrives (every 12 s), on a settings change, and
  after a key.

| Patch | Where | What |
|---|---|---|
| page_desc[9] menu (.data image) | 0x30448354 | Hunt/Fish Menu -> none (MENU does nothing) |
| page_desc[9] handler (.data image) | 0x30448358 | -> `profile_page` |
| page name literal | 0x3011F52C | "Hunt/Fish" -> "Elev. Profile" (Pages menu, Page Order) |
| pane_table ids 84-89 | (generated table) | Hunt/Fish widgets -> `profile_pane_desc` (draws nothing) |
| UI main loop | 0x30085EE4 | (shared) `ui_loop_tramp` also calls `profile_tick` |
| NMEA_start literal | 0x30082884 | (shared) `nmea_start_tramp` also calls `profile_init` |

RAM: state at 0x0076FA40 (0xC0 bytes), ring at 0x0076FB00 (3600 x 4 bytes,
to 0x00773340). The page draws with the firmware's graphics library through
its ARM veneers (see `..\re\notes\graphics.md`). `test_profile.py` runs the
firmware's own `gfx_init`, `page_init` and `page_show` for page 9 and saves
the screen as PNG.

## UI timing and CPU sampling diagnostics (prof.c, prof_hooks.S)

Finds where slow screen updates (e.g. the map) spend their time. Every
screenshot writes two reports covering the time since the previous
screenshot, then starts a new window. So: screenshot, do the slow thing,
screenshot.

`SD:\screenshots\maptime.txt` (always on):
- per wrapped call: calls, total, max and average ms for `before_dispatch`
  (GPS fixes, trip computer), `dispatch` (pages, panes, the map),
  `push_screen` / `push_popup` (copying to the panel), `fread`, `fseek`;
- `io_outside_frames`: fread/fseek time outside UI loop passes (other tasks);
- the 10 slowest frames (UI loop passes), slowest first: page, event code and
  param, total, before, dispatch, push, io, io calls.
The screenshot's own dispatch is left out.

`SD:\screenshots\cpuprof.txt` (only while a folder `SD:\prof` exists):
on every IRQ, the interrupted code address (any task) is counted in 256-byte
bins over 0x30080000-0x306DFFFF; the report lists the 120 busiest bins plus
total and "other" samples. The sampler hooks the IRQ path at runtime, by
pointing the handler literal at 0xA0000038 at `prof_irq`, but only after
checking that vector 0x18 still leads there (literal 0x38 = 0xA0000018,
`ldr pc, [pc, #0x18]` at 0xA0000018) and that the saved handler is
plausible. It is removed again when the folder disappears or the card is
exported over USB, and it is never installed if the card is missing at boot.
The histogram (52 KB) comes from the firmware's `malloc`, once.

`SD:\screenshots\sysinfo.txt` (every screenshot): the CPU clock measured
with two timed loops (`cpu_loop16` / `cpu_loop48` differ by 32 single-cycle
adds per iteration; fastest of 7 runs against the 3.25 MHz timer), the live
PLL register `SRC_PLLFR` (0x101E0014) decoded with the Linux clk-nomadik
formula (PLL1 = 26 MHz x (bits 8-13 + 2) >> bits 0-2; at boot 0x16002203 =
117 MHz), and read-only dumps of the clock/reset, timer, NOR flash and SDRAM
controller registers. All reports carry `build=` (the running build id).

| Patch | Where | What |
|---|---|---|
| UI loop calls | 0x30085F38 / F50 / F6C / F98 | `bl wrap_before / wrap_dispatch / wrap_push_screen / wrap_push_popup` |
| fread / fseek entry | 0x30187BA4 / 0x30187D38 | first instruction -> `b wrap_fread / wrap_fseek` (thunks run it) |
| UI main loop top | 0x30085EE4 | (shared) `ui_loop_tramp` calls `prof_loop_top` first (frame end, sampler switch) |
| screenshot | 0x30086434 | (shared) `screenshot_tramp` also calls `prof_screenshot` |
| IRQ handler pointer | RAM 0xA0000038 | runtime only, while `SD:\prof` exists |

State: RAM 0x00773340-0x007735BF. `test_prof.py` runs the patched sites
with timed stubs and enters `prof_irq` in IRQ mode.

## Black box (bbox.c, bbox_hooks.S)

Finds out why the unit shuts down when "Connect to Computer" exports the SD
card at a raised clock. It records in RAM that survives a watchdog or soft reset
(0x0076A600-0x0076B57F, freed by shrinking heap region 1 another 4 KB):

- a heartbeat every 2 s: USB export state, battery mV, the longest gap
  between UI loop passes, and the watchdog slot closest to expiring;
- USB export state changes;
- every call of the shutdown routine 0x3008423C (power off / reboot) with its
  caller (low battery 0x30084dd8, auto-off in ui_periodic, a PC command
  0x3014xxxx...), and of the immediate reset 0x301A869C;
- a watchdog slot expiring (the watchdog task resets the unit when a task
  misses its deadline: slot 1 = UI loop, 120 s; slot 4 = NMEA loop, 20 s), with
  the slot table;
- CPU exceptions. Stock firmware's undefined/prefetch/data abort vectors are
  self-loops (literal 0xA0000024/2C/30 points back at its own vector), so a
  fault freezes the unit until the 5 s hardware watchdog resets it. bbox.c
  replaces only those self-loops (checked at boot) with handlers that save
  r0-r12, pc, spsr, the fault status/address and the task's sp/lr/stack, then
  loop the same way.

Reports (`bbox_live.txt` / `bbox_prev.txt`, never on the drive the PC has):
- `bbox_live.txt`: this session. While a drive is exported, every 5 s on the
  other medium (`MSYS2:/`, the user-visible internal drive, while the SD card
  is exported), at every shutdown call, and on `SD:/` at every screenshot.
- `bbox_prev.txt`: once per boot on each available medium: the previous
  session from RAM (if it survived: not after a real power-off) plus the
  previous `MSYS2:/bbox_live.txt`.

| Patch | Where | What |
|---|---|---|
| shutdown entry | 0x3008423C | `push {r4-r8, lr}` -> `b shutdown_tramp` |
| watchdog_reset_now entry | 0x301A869C | `push {r4, lr}` -> `b wdreset_tramp` |
| watchdog task, slot expired | 0x301A88FC | `bl 0x301A864C` -> `bl wd_expire_tramp` |
| exception literals | RAM 0xA0000024/2C/30 | runtime, only over the stock self-loops |
| heap region 1 | 0x304D08A0 / 0x304D08E8 | 0x22A800 -> 0x229800 |

`test_bbox.py` runs the patched sites, the shutdown path from the UI task
and from another task, a data abort in abort mode, and a "reset" (boot with
the RAM kept) and a "power-off" (RAM scrambled).

## CPU clock (build.py `CPU_CLOCK`)

Measured on the device (sysinfo.txt): the CPU runs at **117 MHz** = PLL1
(26 MHz crystal x 36 >> 3, `SRC_PLLFR` = 0x16002203; the Linux clk-nomadik
formula is confirmed by measurement). Firmware tasks run in user mode;
masking IRQs needs OS20's `SWI 0` / `SWI 1`. With interrupts on, the UI task
only sees ~78 MHz: interrupts and higher-priority tasks (GPS) take about a
third of the CPU.

The boot code (bootloader 0x3003E57C, then firmware 0x30081880) sets the
clock once: `SRC_PLLFR` from the literal at 0x30081B90, then `SRC_CR`
(0x30081B94 before the PLL locks, 0x30081BA8 after). The bus clock HCLK =
PLL1 divided by `SRC_CR` bits 14:13 (Linux: "1 (passthru), 2, 3 or 4"; its
code reads the field as the divisor, its comment as divisor-1; stock field = 1).

`CPU_CLOCK = {"pllfr": 0x16001902, "src_cr_hclk": 2}` = **175.5 MHz**
(26 x 27 >> 2; PLL2 unchanged). The bus stays at 58.5 MHz if the field means
/(n+1), or drops to 87.75 MHz if it means /n; it never gets faster than stock.
Set `CPU_CLOCK = None` for the stock clock.

**Recovery** if a clock change doesn't boot: copy the full hex of the last
stock-clock build (`baseline_f67640e7\PN-40.hex`) to `SD:\update\` with a card
reader and power on; the bootloader sets its own (stock) clock first.

## Firmware facts used (fw.h / fw.ld)

| Address | What |
|---|---|
| 0x30187E80 / 0x30187904 / 0x30187DB8 | fopen / fwrite / fclose |
| 0x3018902C | stat (0 = exists) |
| 0x301FA9A0 / 0x301FA838 | OS20 time_now / ticks per second (3,250,000) |
| 0x30085E14 | UI task main loop (posts a periodic event every 5 ms) |
| 0x300B966C | get_datetime(&mon, &day, &year2000, &h, &m, &s) |
| 0x300D0050 | USB export state: 0 = SD card exported to PC |
| 0x30189890 / 0x30189748 | malloc / free (not used yet) |
| 0x301FC4A8 | OS20 task_init (UI task: prio 3, 64 KB stack; NMEA tasks: prio 8) |
| 0x0011CEE8 | trip struct (0x6C bytes = DEVICE_STATE.TXT payload) |
| 0x300C5C80 / 0x300C5CD4 | trip_update_elev(trip, elev) / trip_reset_elev(trip) |
| 0x300C638C | trip_save(trip) |
| 0x300B8D8C | elevation source: 0 baro not ready, 1 GPS, 2 baro, 3 baro auto-cal |
| 0x300AF9D4 / 0x3010F680 | elevation format spec init / fmt_elevation(lo, hi, value, unit, spec) |
| 0x003F0C54 + 24*id | page_desc: +0x10 menu, +0x14 handler; initialised from the .data image at 0x3044822C (RAM 0x003F0C14) |
| 0x300D0090 | theme_color(prefs, idx) |
| 0x3044791C / 0x30447988 / 0x30447958 / 0x30447970 / 0x30447994 | gfx fill_rect / hline / vline / draw_string / font_string_width (ARM veneers) |
| 0x003F2348 | font slots ([0] 9 px bold, [3] 10 px, [4] 10 px bold) |
