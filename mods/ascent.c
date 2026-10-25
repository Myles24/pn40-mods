/* "Total Ascent" / "Total Descent" data fields (field ids 162, 163).
 *
 * Accumulates the elevation samples the trip computer feeds its stock
 * maximum/minimum (trip_update -> trip_update_elev, UI task, about 1 Hz,
 * only with a valid fix), so the totals follow the same source: barometric
 * elevation (manual or auto-calibrated) or GPS elevation with the barometer
 * off. See re/notes/ascent.md.
 *
 * - Deadband: a change counts once it exceeds 3 m from the reference (10 m
 *   on GPS elevation); then the whole change counts and the reference moves.
 * - Jump guard: a change faster than 15 m/s since the previous sample is a
 *   calibration or source switch: the reference moves, nothing is counted.
 *   Gaps under 1 s count as 1 s (the UI task can drain queued fixes back to
 *   back). A change of source (GPS / barometer / auto-cal) also restarts the
 *   reference.
 * - Storage: centimetres in trip +0x28 (ascent) and +0x2C (descent), bytes
 *   the stock firmware never uses but saves and loads with the rest of the
 *   trip struct (DEVICE_STATE.TXT). Cleared with the trip elevations
 *   (trip_reset_elev covers every reset path).
 * - Saving: trip_save whenever either total has grown 25 m since the last
 *   save, so a power-off loses at most about that much (stock autosave is
 *   every 10 minutes).
 */
#include "fw.h"
#include "mods.h"

#define MAGIC        0x41534344u        /* "ASCD" */
#define DEADBAND_BARO_CM  300
#define DEADBAND_GPS_CM   1000
#define MAX_RATE_CM_S     1500
#define SAVE_STEP_CM      2500
#define ELEV_MAX_CM       100000000     /* ignore samples beyond +-1,000 km */

struct ascent_state {
    unsigned magic;
    int have_ref;                       /* ref/last/source valid */
    int ref_cm;                         /* elevation the deadband is measured from */
    int last_cm;                        /* previous sample */
    unsigned last_time;                 /* fw_time_now() of the previous sample */
    int source;                         /* fw_elev_source() of the previous sample */
    int saved_valid;                    /* saved_* hold the totals at the last save */
    unsigned saved_up, saved_down;

    /* counters, for tests and diagnostics */
    unsigned samples, jumps, source_changes, saves, bad_samples, garbage_resets;
};

extern char ASCENT_RAM[];
#define AS ((struct ascent_state *)ASCENT_RAM)

typedef char state_fits[(sizeof(struct ascent_state) <= 0x40) ? 1 : -1];   /* profile state follows */

struct ui_result *field_total_ascent(struct ui_result *res, const struct ui_result *ev, int page, int cell);
struct ui_result *field_total_descent(struct ui_result *res, const struct ui_result *ev, int page, int cell);
void ascent_init(void);
void ascent_reset(void *trip);
void ascent_sample(void *trip, unsigned lo, unsigned hi);

const struct pane_desc ascent_desc = {
    field_total_ascent, 1, 0, {0, 0}, "Elevation: Total Ascent", "Total Ascent",
};
const struct pane_desc descent_desc = {
    field_total_descent, 1, 0, {0, 0}, "Elevation: Total Descent", "Total Descent",
};

/* IEEE double (lo, hi words) metres -> centimetres, rounded half away from
 * zero. Returns 0 for NaN/infinity or |value| >= 2^21 m. Uses only 32-bit
 * shifts and a 64x32 multiply (no libgcc). */
int double_to_cm(unsigned lo, unsigned hi, int *cm)
{
    unsigned e = (hi >> 20) & 0x7FF, xh, xl, r;
    unsigned long long x;
    int s;

    if (e == 0x7FF || e >= 1023 + 21)
        return 0;
    if (e < 1013) {                     /* |value| < 2^-10 m */
        *cm = 0;
        return 1;
    }
    /* value = m * 2^(e-1075), m < 2^53; cm = m*100 >> (1075-e), shift 32..62 */
    x = ((((unsigned long long)((hi & 0xFFFFF) | 0x100000)) << 32) | lo) * 100u;
    xh = (unsigned)(x >> 32);
    xl = (unsigned)x;
    s = 1075 - (int)e - 32;
    r = s ? (xh >> s) + ((xh >> (s - 1)) & 1) : xh + (xl >> 31);
    *cm = hi >> 31 ? -(int)r : (int)r;
    return 1;
}

/* centimetres -> IEEE double metres as (hi << 32 | lo). cm / 100 is done as
 * cm * (2^38 / 100) (relative error < 1e-9), then normalised. */
unsigned long long cm_to_double(unsigned cm)
{
    unsigned long long x = (unsigned long long)cm * 0xA3D70A3Eu;     /* metres * 2^38 */
    int e = 1023 + 52 - 38;

    if (!x)
        return 0;
    while (x >> 53) {
        x >>= 1;
        e++;
    }
    while (!(x >> 52)) {
        x <<= 1;
        e--;
    }
    return ((unsigned long long)e << 52) | (x & 0xFFFFFFFFFFFFFull);
}

/* boot (nmea_start_tramp): the RAM holds heap leftovers until here */
void ascent_init(void)
{
    struct ascent_state *st = AS;
    unsigned *p = (unsigned *)st;
    unsigned i;

    for (i = 0; i < sizeof *st / 4; i++)
        p[i] = 0;
    st->magic = MAGIC;
}

/* trip_reset_elev hook: every elevation reset (menu, full trip reset, defaults) */
void ascent_reset(void *trip)
{
    struct ascent_state *st = AS;

    TRIP_ASCENT(trip) = 0;
    TRIP_DESCENT(trip) = 0;
    if (st->magic != MAGIC)             /* reset before boot init: init clears the rest */
        return;
    st->have_ref = 0;
    st->saved_valid = 0;                /* trip_load may still follow (boot) */
}

static unsigned add_sat(unsigned total, unsigned d)
{
    return d > TOTAL_MAX_CM - total ? TOTAL_MAX_CM : total + d;
}

/* trip_update_elev hook: one elevation sample (metres, double) */
void ascent_sample(void *trip, unsigned lo, unsigned hi)
{
    struct ascent_state *st = AS;
    unsigned up, down, now, dt, tps, ad;
    int cm, src, d;

    if (st->magic != MAGIC)
        return;
    if (!double_to_cm(lo, hi, &cm) || cm > ELEV_MAX_CM || cm < -ELEV_MAX_CM) {
        st->bad_samples++;
        return;
    }
    st->samples++;
    profile_feed(cm);
    up = TRIP_ASCENT(trip);
    down = TRIP_DESCENT(trip);
    if (up > TOTAL_MAX_CM || down > TOTAL_MAX_CM) {
        up = down = 0;
        st->garbage_resets++;
    }
    if (!st->saved_valid) {             /* first sample since boot or reset: totals as loaded */
        st->saved_valid = 1;
        st->saved_up = up;
        st->saved_down = down;
    }

    src = fw_elev_source();             /* 1 GPS, 2 baro, 3 baro auto-cal; 0 not ready */
    if (src == 0 && st->have_ref)
        src = st->source;
    now = fw_time_now();
    if (!st->have_ref || src != st->source) {
        if (st->have_ref)
            st->source_changes++;
        st->have_ref = 1;
        st->ref_cm = cm;
    } else {
        tps = fw_ticks_per_sec();
        dt = now - st->last_time;
        if (dt < tps)
            dt = tps;
        d = cm - st->last_cm;
        ad = d < 0 ? -(unsigned)d : (unsigned)d;
        if ((unsigned long long)ad * tps > (unsigned long long)MAX_RATE_CM_S * dt) {
            st->jumps++;
            st->ref_cm = cm;
        } else {
            int db = src == 1 ? DEADBAND_GPS_CM : DEADBAND_BARO_CM;
            if (cm - st->ref_cm > db) {
                up = add_sat(up, (unsigned)(cm - st->ref_cm));
                st->ref_cm = cm;
            } else if (st->ref_cm - cm > db) {
                down = add_sat(down, (unsigned)(st->ref_cm - cm));
                st->ref_cm = cm;
            }
        }
    }
    st->source = src;
    st->last_cm = cm;
    st->last_time = now;
    TRIP_ASCENT(trip) = up;
    TRIP_DESCENT(trip) = down;

    if (up - st->saved_up >= SAVE_STEP_CM || down - st->saved_down >= SAVE_STEP_CM) {
        st->saved_up = up;
        st->saved_down = down;
        st->saves++;
        fw_trip_save(trip);
    }
}

/* Same steps as the stock "Elevation: Maximum" field (0x300B4CD4). */
static struct ui_result *draw_total(struct ui_result *res, const struct ui_result *ev, int page, int cell,
                                    unsigned cm)
{
    struct ui_result e = *ev, tmp, flush = {0x7D4, 0};
    unsigned char rect[8], ctx[32];
    unsigned spec[4] = {0, 0, 0, 0};
    char value[16], unit[16];
    unsigned long long m;
    unsigned i;

    if (fw_cell_obscured(fw_page_objs[page], cell) && ev->code == 1)
        goto not_handled;
    if (!fw_field_needs_redraw(&e))
        goto not_handled;
    fw_rect_copy(rect, (char *)fw_page_objs[page] + cell * 4);
    fw_rect_init(ctx, rect);
    fw_draw_field_label(ctx, page, cell, 0);
    if (cm > TOTAL_MAX_CM) {
        fw_draw_field_dashes(ctx, 1, 1);
    } else {
        for (i = 0; i < sizeof value; i++)
            value[i] = unit[i] = 0;
        fw_fmt_spec_init(spec);
        spec[1] = 7;                    /* max digits (stock 5 shows "- -" from 100,000 ft); no padding */
        m = cm_to_double(cm);
        fw_fmt_elevation((unsigned)m, (unsigned)(m >> 32), value, unit, spec);
        fw_draw_field_value(ctx, value, unit, 1, 1, 0);
    }
    fw_ui_handle_result(&tmp, &flush);
    res->code = 0xB;
    res->param = 0;
    return res;

not_handled:
    res->code = 10;
    res->param = 0;
    return res;
}

struct ui_result *field_total_ascent(struct ui_result *res, const struct ui_result *ev, int page, int cell)
{
    return draw_total(res, ev, page, cell, TRIP_ASCENT(fw_trip));
}

struct ui_result *field_total_descent(struct ui_result *res, const struct ui_result *ev, int page, int cell)
{
    return draw_total(res, ev, page, cell, TRIP_DESCENT(fw_trip));
}
