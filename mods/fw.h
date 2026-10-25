/* PN-40 firmware 2.9.6.70319: functions and globals used by mods.
 *
 * Addresses are in fw.ld. All functions are ARM code built with an old GCC
 * (APCS); int/pointer arguments pass the same way as ours. Don't pass 64-bit
 * values or doubles to them.
 */
#ifndef FW_H
#define FW_H

typedef void FW_FILE;

/* libc-style file API (calls must come from the UI task: the fopen wrapper
 * builds paths in a shared static buffer and is not reentrant) */
FW_FILE *fw_fopen(const char *path, const char *mode);              /* modes r w a r+ w+ a+ [b] */
unsigned fw_fwrite(const void *p, unsigned size, unsigned n, FW_FILE *f); /* returns n on success */
int fw_fclose(FW_FILE *f);
int fw_stat(const char *path, void *buf);                           /* 0 if it exists; buf >= 52 bytes */

/* OS20 time: ticks since boot, wraps at 2^32 (about 22 minutes) */
unsigned fw_time_now(void);
unsigned fw_ticks_per_sec(void);                                    /* 3250000 (26 MHz / 8) */

/* device clock, as used for screenshot names; year is years since 2000 */
void fw_get_datetime(int *mon, int *day, int *year, int *hour, int *min, int *sec);

/* USB export state: 0 = SD card exported to a PC, 1 = internal drive exported, 2 = neither */
int fw_usb_export_state(void);

/* OS20 memory_allocate from the calling task's partition (0 if none left) */
void *fw_malloc(unsigned size);

/* --- UI (see re/UI.md) ------------------------------------------------- */

/* every event and handler result: {code, param}; results come back through
 * a hidden first pointer argument */
struct ui_result {
    unsigned code, param;
};

/* pane class (data field / page widget) descriptor */
struct pane_desc {
    struct ui_result *(*handler)(struct ui_result *res, const struct ui_result *ev, int page, int cell);
    unsigned char is_field;             /* listed in "Change Info Fields" */
    unsigned char needs_baro;           /* hidden on units without a barometer */
    unsigned char pad[2];
    const char *long_name, *short_name;
};

#define PANE_TABLE_LEN 164              /* 161 stock classes + our fields */
extern const struct pane_desc *const pane_table[PANE_TABLE_LEN];

extern void *fw_page_objs[];            /* 0x00043164: page objects; +cell*4 = cell rect */
struct ui_result *fw_ui_handle_result(struct ui_result *out, const struct ui_result *res);
int fw_cell_obscured(void *page_obj, int cell);
int fw_field_needs_redraw(const struct ui_result *ev);
void fw_rect_copy(void *dst, const void *src);
void fw_rect_init(void *ctx, const void *rect);
void fw_draw_field_label(void *ctx, int page, int cell, int flags);
void fw_draw_field_value(void *ctx, const char *value, const char *unit, int a, int b, int c);
void fw_draw_field_dashes(void *ctx, int a, int b);
int fw_has_barometer(void);

/* battery: Battery Charger task's latest reading, float volts (raw IEEE bits) */
unsigned fw_battery_raw_bits(void);

/* --- trip computer / elevation (re/notes/ascent.md) ------------------- */

extern unsigned char fw_trip[];         /* 0x0011CEE8: trip struct, 0x6C bytes = DEVICE_STATE.TXT */
void fw_trip_save(void *trip);          /* writes MSYS1:/system/DEVICE_STATE.TXT (UI task) */
int fw_elev_source(void);               /* 0 baro not ready, 1 GPS (baro off), 2 baro, 3 baro auto-cal */

/* elevation text the way the stock elevation fields show it: fill spec,
 * then fmt_elevation(metres as an IEEE double in two words, lo first) */
void fw_fmt_spec_init(unsigned spec[4]);
void fw_fmt_elevation(unsigned lo, unsigned hi, char *value, char *unit, const unsigned spec[4]);

/* --- graphics (re/notes/graphics.md): Thumb library, called through the
 * firmware's ARM veneers. Coordinates are window-buffer relative and
 * inclusive; most primitives draw nothing if a point is off the buffer. */
int fw_gfx_fill_rect(void *win, int x1, int y1, int x2, int y2, unsigned rgb565);
int fw_gfx_hline(void *win, int x1, int x2, int y, unsigned rgb565);
int fw_gfx_vline(void *win, int x, int y1, int y2, unsigned rgb565);
int fw_gfx_draw_string(void *win, const void *font, int x, int y, const char *s, unsigned fg, unsigned bg); /* fg == bg: transparent */
int fw_font_string_width(const void *font, const char *s);
extern const void *fw_font_slots[];     /* 0x003F2348: [0] 9 px bold, [3] 10 px, [4] 10 px bold, [9] 13 px bold */

/* settings */
extern unsigned char fw_prefs[];        /* 0x0012B76C */
unsigned fw_theme_color(void *prefs, int idx); /* 0-4 light..dark, 5 background, 6 text, 7 highlight text */
int fw_pref_measures(void);             /* 0 statute, 1 metric, 2 NM/ft, 3 NM/m */

#endif
