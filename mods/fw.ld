/* Link mods into the unused NOR area above the PN-40 firmware. */

MEMORY
{
    /* stock firmware ends at 0x306CC90B; 0x306D0000 starts a fresh 64 KB block */
    CODE (rx) : ORIGIN = 0x306D0000, LENGTH = 0x00010000
}

/* RAM taken from the heap: heap region 1 (0x00540DCC, 0x232800 bytes) is shrunk
 * by 36 KB in the partition tables at 0x304D08A0 / 0x304D08E8, which frees
 * 0x0076A5CC-0x007735CB. The firmware never touches it after that. */
BBOX_RAM       = 0x0076A600;        /* bbox.c: this session (0x780), survives a reset */
BBOX_RT        = 0x0076AD80;        /* bbox.c runtime state (0x80) */
BBOX_PREV      = 0x0076AE00;        /* bbox.c: copy of the previous session (0x780) */
BBOX_END       = 0x0076B580;
LOGGER_RAM     = 0x0076B600;
LOGGER_RAM_END = 0x007735C0;
ASCENT_RAM     = 0x0076FA00;        /* after the logger's 0x400 state + 16 KB ring */
PROFILE_RAM    = 0x0076FA40;        /* profile.c state, 0xC0 bytes */
PROFILE_RING   = 0x0076FB00;        /* 3600 records x 4 bytes */
ASSERT(PROFILE_RING + 3600 * 4 <= LOGGER_RAM_END, "profile ring doesn't fit in the mod RAM")
PROF_RAM       = 0x00773340;        /* prof.c state, to LOGGER_RAM_END (0x280 bytes) */
PROF_RAM_END   = LOGGER_RAM_END;
ASSERT(PROF_RAM >= PROFILE_RING + 3600 * 4, "prof state overlaps the profile ring")
ASSERT(BBOX_END <= LOGGER_RAM, "black box overlaps the logger")

/* firmware functions */
fw_fopen            = 0x30187E80;
fw_fwrite           = 0x30187904;
fw_fclose           = 0x30187DB8;
fw_stat             = 0x3018902C;
fw_time_now         = 0x301FA9A0;
fw_ticks_per_sec    = 0x301FA838;
fw_get_datetime     = 0x300B966C;
fw_usb_export_state = 0x300D0050;
fw_malloc           = 0x30189890;
fw_fread            = 0x30187BA4;   /* fread(ptr, size, n, f) */
fw_task_delay       = 0x301FB8DC;
fw_lowvec           = 0x00000000;   /* exception vectors: "ldr pc, [pc, #0x18]" each */
fw_vec_irq_lit      = 0x00000038;   /* exception vector 0x18 (IRQ) does ldr pc, [this] */

/* UI / data fields (re/UI.md, re/notes/settings.md) */
fw_page_objs          = 0x00043164;
fw_ui_handle_result   = 0x300E3B70;
fw_cell_obscured      = 0x3009244C;
fw_field_needs_redraw = 0x300AF724;
fw_rect_copy          = 0x300BA15C;
fw_rect_init          = 0x300BA548;
fw_draw_field_label   = 0x300B010C;
fw_draw_field_value   = 0x300B0410;
fw_draw_field_dashes  = 0x300AFE78;
fw_has_barometer      = 0x30111088;
fw_battery_raw_bits   = 0x301A8A98;   /* returns *(u32 *)0x003F4FB0 */

/* trip computer / elevation (re/notes/ascent.md) */
fw_trip               = 0x0011CEE8;
fw_trip_save          = 0x300C638C;
fw_elev_source        = 0x300B8D8C;
fw_fmt_spec_init      = 0x300AF9D4;
fw_fmt_elevation      = 0x3010F680;

/* graphics: ARM veneers of the Thumb library (re/notes/graphics.md) */
fw_gfx_fill_rect      = 0x3044791C;
fw_gfx_hline          = 0x30447988;
fw_gfx_vline          = 0x30447958;
fw_gfx_draw_string    = 0x30447970;
fw_font_string_width  = 0x30447994;
fw_font_slots         = 0x003F2348;

/* settings */
fw_prefs              = 0x0012B76C;
fw_theme_color        = 0x300D0090;
fw_pref_measures      = 0x300CFD0C;

/* hook targets */
fw_nmea_start       = 0x301518E4;   /* NMEA_start(), called once from main at 0x30082574 */
fw_nmea_send_locked = 0x3014B57C;   /* nmea_send(buf, len), just after taking the NMEA mutex */
fw_screenshot       = 0x30084B4C;   /* take_screenshot(), called from the key handler at 0x30086434 */
fw_trip_update_elev = 0x300C5C80;   /* trip_update_elev(trip, elev double), called from trip_update at 0x300C623C */
fw_trip_reset_elev_body = 0x300C5CD8; /* trip_reset_elev(trip), after its first instruction */
fw_ui_before_dispatch = 0x30085B10; /* called from the UI main loop at 0x30085F38 */
fw_ui_dispatch_event = 0x300E6638;  /* ui_dispatch_event(&ev), UI main loop 0x30085F50 */
fw_win_update       = 0x304478EC;   /* ARM veneer of win_update(win), UI main loop 0x30085F6C / 0x30085F98 */
fw_fread_body       = 0x30187BA8;   /* fread(ptr, size, n, f) 0x30187BA4, after its first instruction */
fw_fseek_body       = 0x30187D3C;   /* fseek(f, off, whence) 0x30187D38, after its first instruction */
fw_compass_loop     = 0x301AA858;   /* top of the Compass Process task loop (0x301AA804) */
fw_shutdown_body    = 0x30084240;   /* shutdown(reboot) 0x3008423C, after its first instruction */
fw_wdreset_body     = 0x301A86A0;   /* watchdog_reset_now 0x301A869C, after its first instruction */
fw_wd_force_reset   = 0x301A864C;   /* watchdog load = 1: reset at once (watchdog task 0x301A88FC) */

/* only build.py's patches point at these, so keep them from --gc-sections */
EXTERN(profile_page profile_page_name mods_build_id)

SECTIONS
{
    .text : {
        KEEP(*(.text.hooks .text.hooks_*))
        *(.text .text.*)
        KEEP(*(.rodata.pane_table))
        *(.rodata .rodata.*)
    } > CODE

    /* Code runs from flash: writable sections would land in NOR. All state
     * lives at LOGGER_RAM instead, so these must stay empty. */
    .data : { *(.data .data.*) } > CODE
    .bss  : { *(.bss .bss.* COMMON) } > CODE
    ASSERT(SIZEOF(.data) == 0 && SIZEOF(.bss) == 0, "mods must not use .data/.bss")

    /DISCARD/ : { *(.ARM.exidx*) *(.ARM.attributes) *(.comment) }
}
