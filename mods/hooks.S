@ Entry trampolines for the patch sites. build.py points the firmware at these.
@
@ The firmware was built with the old APCS ABI and doesn't keep sp 8-byte
@ aligned, so every call into C realigns sp first.

    .syntax unified
    .arm
    .section .text.hooks, "ax"

@ main() calls NMEA_start(r0, r1, r2, r3, [sp]) through the literal at
@ 0x30082884, which now points here. Set up the logger and the ascent
@ state, then tail-call the original with the registers and stack exactly
@ as we got them.
    .global nmea_start_tramp
nmea_start_tramp:
    push    {r0-r3, r4, lr}
    mov     r4, sp
    bic     sp, sp, #7
    bl      bbox_init
    bl      nmealog_init
    bl      ascent_init
    bl      profile_init
    bl      prof_init
    mov     sp, r4
    pop     {r0-r3, r4, lr}
    ldr     pc, =fw_nmea_start

@ nmea_send(buf, len) at 0x3014B564 is called by both NMEA tasks. Its third
@ instruction locks the NMEA mutex; the instruction after that (0x3014B578,
@ "ldr r3, =0x003F2A4C") is now "b nmea_send_tramp", so the copy into the ring
@ is serialized by the firmware's own lock. Here r4 = buf, r5 = len.
    .global nmea_send_tramp
nmea_send_tramp:
    push    {r0-r3, r4, lr}
    mov     r4, sp
    bic     sp, sp, #7
    ldr     r0, [r4, #16]           @ the caller's r4 (buf), saved by our push
    mov     r1, r5
    bl      nmealog_append
    mov     sp, r4
    pop     {r0-r3, r4, lr}
    ldr     r3, =0x003F2A4C         @ the instruction the branch replaced
    b       fw_nmea_send_locked

@ The UI task's main loop (0x30085E14) calls time_now() at 0x30085EE4 each
@ time it posts its periodic event, i.e. every few ms. That "bl time_now" is
@ now "bl ui_loop_tramp": run the logger and profile ticks, then tail-call
@ time_now so the loop gets its return value as before.
    .global ui_loop_tramp
ui_loop_tramp:
    push    {r4, lr}
    mov     r4, sp
    bic     sp, sp, #7
    bl      prof_loop_top
    bl      bbox_tick
    bl      nmealog_ui_tick
    bl      profile_tick
    mov     sp, r4
    pop     {r4, lr}
    b       fw_time_now

@ The key handler calls take_screenshot() at 0x30086434 (UI task). That call
@ is now "bl screenshot_tramp": take the screenshot, then write the logger's
@ diagnostics next to it.
    .global screenshot_tramp
screenshot_tramp:
    push    {r4, lr}
    mov     r4, sp
    bic     sp, sp, #7
    bl      fw_screenshot
    bl      nmealog_screenshot
    bl      prof_screenshot
    bl      bbox_screenshot
    mov     sp, r4
    pop     {r4, pc}

@ trip_update (UI task, once per valid fix) calls trip_update_elev(trip,
@ elev) at 0x300C623C; that "bl" is now "bl elev_tramp". r0 = trip, r1:r2 =
@ elevation (double, lo/hi words: old APCS doesn't pair-align it). Run the
@ original, then hand the same sample to the accumulator. The caller only
@ relies on r4-r11 and sp afterwards.
    .global elev_tramp
elev_tramp:
    push    {r0-r2, r4, r5, lr}
    bl      fw_trip_update_elev
    ldm     sp, {r0-r2}
    mov     r4, sp
    bic     sp, sp, #7
    bl      ascent_sample
    mov     sp, r4
    pop     {r0-r2, r4, r5, pc}

@ trip_reset_elev(trip) at 0x300C5CD4 is the one path every elevation reset
@ takes. Its first instruction is now "b reset_elev_tramp": clear the totals,
@ then run the replaced "str r4, [sp, #-4]!" and continue in the original.
    .global reset_elev_tramp
reset_elev_tramp:
    push    {r0-r3, r4, lr}
    mov     r4, sp
    bic     sp, sp, #7
    bl      ascent_reset
    mov     sp, r4
    pop     {r0-r3, r4, lr}
    str     r4, [sp, #-4]!
    b       fw_trip_reset_elev_body

    .ltorg
