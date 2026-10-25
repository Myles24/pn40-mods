# Total ascent / descent: research (fw 2.9.6.70319)

All from disassembly of the stock image. **Implemented** in `mods\ascent.c` (field ids 162/163,
option 3: save every 25 m); see mods\README.md. Emulator-tested (`mods\test_ascent.py`), not yet
on the device. Checked since: the old-format DEVICE_STATE converter 0x300C6484 (from `trip_load`)
writes +0x04..+0x24 and +0x30..+0x54, not +0x28..+0x2F; `0x300B8D8C` returns the elevation source
(0 baro not ready, 1 GPS, 2 baro, 3 baro auto-cal).

## Elevation source and update rate (confirmed)
- `ui_before_dispatch` 0x30085B10 (UI task) drains the GPS fix queue (`gps_fix_receive`
  0x301528D8) and calls the trip update `trip_update` 0x300C6134(&trip) once per received fix
  (about 1 Hz) at 0x30085BAC.
- In `trip_update`, elevation is used only when the fix is valid (0x300B7E5C) and
  `get_baro_elev` 0x300B8DC0(&double) returns nonzero:
  - barometer enabled (`pref_baro_enabled` 0x300D0854), auto-cal on (`pref_baro_autocal`
    0x300D0838): fix copy +0x78 (0x4312C);
  - barometer enabled, auto-cal off: fix +0x68 (0x4311C);
  - barometer disabled: GPS elevation fix +0x70 (0x43124), valid = GPS fix valid;
  - barometer not ready (0x300B8CF4 == 0): last value, returns 0 (not used).
  Units: metres, IEEE double.
- Then trip+0x50 = elevation (last elevation, double), and `trip_update_elev`
  0x300C5C80(trip, elev) raises max (+0x04) / lowers min (+0x0C).
- **Hook point:** the call at 0x300C623C: `bl 0x300C5C80` with r0 = trip (also in r6),
  r1:r2 = elevation (lo, hi words: old APCS, not register-pair aligned). After it, code
  continues at 0x300C6240 using r6/r7/sp only.

## Storage (likely; checked as far as static analysis goes)
- `trip` 0x0011CEE8, 0x6C bytes = DEVICE_STATE.TXT payload (version 6).
- Bytes +0x28..+0x2F have no known reader or writer:
  - none of the 36 code sites that load &trip accesses offsets 0x28-0x2F (direct, ldm/stm,
    derived pointers);
  - functions that take the struct (`trip_reset_*`, `trip_defaults`, 0x300C645C, 0x300C6440,
    0x300C63D8, `trip_update_elev` 0x300C5C80, 0x300C5D04, 0x300C5E80, 0x300C5ECC, 0x300C60EC)
    don't touch them;
  - load/save copy all 0x6C bytes (`trip_load` 0x300C65F8 memcpy; `trip_save` 0x300C638C).
- Stock firmware ignores and preserves them, so a rollback is harmless (unlike new field ids
  on pages; see mods\README).
- Never written by stock code => zero in files the stock firmware made (struct is .bss).
  `trip_defaults` does not clear them; the reset hook below does.

## Reset paths (confirmed): all go through `trip_reset_elev` 0x300C5CD4(trip)
| caller | path |
|---|---|
| 0x300C5F6C `trip_reset_all` | ← `trip_defaults` 0x300C6534 (first boot / bad DEVICE_STATE.TXT), ← 0x300D17C4 |
| 0x300C6430 in 0x300C63D8 | Reset dialog: "Reset Trip Elevations" checkbox (trip+0x60), ← UI command 0x300E6074 |
**Hook point:** entry of 0x300C5CD4 (first instruction `str r4, [sp, #-4]!`, r0 = trip).

## Saving (confirmed / likely)
- DEVICE_STATE.TXT is written only by `trip_save` 0x300C638C (via state writer 0x30086A4C).
- Callers: `trip_autosave` 0x300C6588 (every 600 s, from ui_before_dispatch 0x30085BD8),
  reset dialog / checkbox handlers (0x300C6438, 0x300C6454, 0x300C647C, 0x300CD4F8..54C),
  0x300E5CC4/D64/E14 (commands setting +0x68), 0x30084218 (per-fix routine 0x30083FE0,
  sets +0x68), 0x300AD66C (map page).
- **No save at power-off found** (likely): stock trip data (odometer, times, max/min) can lose
  up to 10 minutes at shutdown. Our totals would behave the same unless we save more often.

## Jumps in the elevation source (confirmed that they can happen)
- Manual altimeter calibration (Device Setup -> Calibrate Altimeter: known elevation,
  pressure, or GPS elevation) shifts barometric elevation at once.
- Toggling "Enable Barometric Altimeter" / "Auto Calibrate Altimeter" switches between
  sources (+0x68 / +0x78 / GPS +0x70).
- GPS elevation is noisy (typically ±5-15 m).

## Design implied by the above
- Accumulate in the 0x300C623C hook (same samples as the stock max/min, ~1 Hz, fix valid).
- Hysteresis: keep a reference elevation; count only when |elev - ref| > deadband
  (3 m barometric, 10 m GPS-only), then move the reference.
- Jump guard: a change faster than ~15 m/s since the last sample is a re-calibration or source
  switch: move the reference without counting.
- Totals in trip+0x28 (ascent) / +0x2C (descent), u32 centimetres; sanity check at first use
  (> 1,000,000 m treated as garbage -> 0).
- Clear totals + reference in the `trip_reset_elev` hook.
- Optional: call `trip_save` after the totals grow by >= 25 m since the last save, to limit
  loss at power-off (also persists the stock trip values more often).
