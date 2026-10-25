/* "Elev. Profile" page: replaces the Hunt/Fish page (page id 9).
 *
 * Recording (always on, RAM only, lost at power-off):
 *   profile_feed()  every elevation sample the trip computer uses (from the
 *                   ascent hook: UI task, ~1 Hz, only with a valid fix);
 *   profile_tick()  UI main loop: every REC_SECS it stores one record = the
 *                   average elevation of the samples since the last record
 *                   (or a gap marker if there were none) plus the trip
 *                   odometer distance travelled since the last record.
 *   NREC records = 12 h.
 *
 * Page: elevation over time or over distance. ENTER switches between them,
 * ZOOM IN/OUT changes the span. Redrawn when a record is added.
 *
 * build.py points page 9's handler at profile_page (and clears its menu),
 * maps its six widget classes (84-89) to profile_pane_desc, which draws
 * nothing, and renames the page in the Pages menu / Page Order list.
 */
#include "fw.h"
#include "mods.h"

#define MAGIC      0x50524F46u          /* "PROF" */
#define REC_SECS   12
#define NREC       3600                 /* 12 h */
#define GAP        (-32768)             /* record without samples */
#define MAX_FEED   16                   /* samples averaged per record (keeps the sum in 32 bits) */
#define PAGE_ID    9

#define KEY_ZOOM_IN   0x3ED
#define KEY_ZOOM_OUT  0x3EE
#define KEY_ENTER     0x3F1

struct rec {
    short elev;                         /* half metres, GAP = no data */
    unsigned short dist;                /* metres travelled since the previous record */
};

struct profile_state {
    unsigned magic;
    unsigned head;                      /* next record to write */
    unsigned count;                     /* valid records, <= NREC */
    int started;
    unsigned next_time;                 /* fw_time_now() when the next record is due */
    int sum_cm, nsum;                   /* samples since the last record */
    int last_cm, have_sample;           /* latest sample, for the footer */
    int have_odo;
    unsigned last_odo;                  /* odometer (m) at the last record */
    int mode;                           /* 0 time, 1 distance */
    int zoom[2];                        /* span index per mode */
    int dirty;                          /* a record was added since the last draw */
    unsigned records, gaps, draws;      /* counters */
};

extern char PROFILE_RAM[], PROFILE_RING[];
#define PS   ((struct profile_state *)PROFILE_RAM)
#define RING ((struct rec *)PROFILE_RING)

typedef char state_fits[(sizeof(struct profile_state) <= 0xC0) ? 1 : -1];

void profile_init(void);
void profile_tick(void);
struct ui_result *profile_page(struct ui_result *res, const struct ui_result *ev, int page);
struct ui_result *profile_pane(struct ui_result *res, const struct ui_result *ev, int page, int cell);
int double_to_m(unsigned lo, unsigned hi, unsigned *m);
unsigned udiv(unsigned n, unsigned d);
void profile_draw(void *win);

/* the Hunt/Fish widgets: nothing to draw, the page handler draws everything */
const struct pane_desc profile_pane_desc = { profile_pane, 0, 0, {0, 0}, 0, 0 };
const char profile_page_name[] = "Elev. Profile";

static const unsigned time_span[4] = {1800, 7200, 21600, 43200};
static const char *const time_label[4] = {"30 min", "2 h", "6 h", "12 h"};
static const unsigned dist_span[3][4] = {
    {805, 3219, 8047, 32187},           /* statute: 0.5, 2, 5, 20 mi */
    {1000, 3000, 10000, 30000},         /* metric */
    {926, 3704, 9260, 27780},           /* nautical: 0.5, 2, 5, 15 NM */
};
static const char *const dist_label[3][4] = {
    {"0.5 mi", "2 mi", "5 mi", "20 mi"},
    {"1 km", "3 km", "10 km", "30 km"},
    {"0.5 NM", "2 NM", "5 NM", "15 NM"},
};

/* n / d without libgcc */
unsigned udiv(unsigned n, unsigned d)
{
    unsigned q = 0, r = 0;
    int i;

    if (!d)
        return 0;
    for (i = 31; i >= 0; i--) {
        r = (r << 1) | ((n >> i) & 1);
        if (r >= d) {
            r -= d;
            q |= 1u << i;
        }
    }
    return q;
}

/* rounded n / d, sign of n */
static int sdiv_round(int n, unsigned d)
{
    unsigned a = n < 0 ? -(unsigned)n : (unsigned)n, q = udiv(a + d / 2, d);
    return n < 0 ? -(int)q : (int)q;
}

/* IEEE double (lo, hi words) >= 0 -> whole units, truncated. 0 for
 * negative, NaN/infinity or >= 2^31. */
int double_to_m(unsigned lo, unsigned hi, unsigned *m)
{
    unsigned e = (hi >> 20) & 0x7FF, mh = (hi & 0xFFFFF) | 0x100000;
    int sh;

    if (hi >> 31 || e == 0x7FF || e >= 1023 + 31)
        return 0;
    if (e < 1023) {
        *m = 0;
        return 1;
    }
    sh = 1075 - (int)e;                 /* value = (mh:lo) >> sh, sh 22..52 */
    *m = sh >= 32 ? mh >> (sh - 32) : (mh << (32 - sh)) | (lo >> sh);
    return 1;
}

/* --- recording ----------------------------------------------------------- */

void profile_init(void)
{
    struct profile_state *st = PS;
    unsigned *p = (unsigned *)st;
    unsigned i;

    for (i = 0; i < sizeof *st / 4; i++)
        p[i] = 0;
    st->zoom[0] = 1;                    /* 2 h */
    st->zoom[1] = 1;
    st->magic = MAGIC;
}

void profile_feed(int cm)
{
    struct profile_state *st = PS;

    if (st->magic != MAGIC)
        return;
    st->last_cm = cm;
    st->have_sample = 1;
    if (st->nsum < MAX_FEED) {
        st->sum_cm += cm;
        st->nsum++;
    }
}

static void push(struct profile_state *st, int elev, unsigned dist)
{
    RING[st->head].elev = (short)elev;
    RING[st->head].dist = (unsigned short)(dist > 0xFFFF ? 0xFFFF : dist);
    st->head = st->head + 1 >= NREC ? 0 : st->head + 1;
    if (st->count < NREC)
        st->count++;
    st->records++;
    st->dirty = 1;
}

static void add_record(struct profile_state *st)
{
    unsigned odo, dist = 0;
    int elev = GAP;

    if (st->nsum) {
        elev = sdiv_round(sdiv_round(st->sum_cm, (unsigned)st->nsum), 50);
        if (elev > 32767)
            elev = 32767;
        if (elev < -32767)
            elev = -32767;
    } else {
        st->gaps++;
    }
    st->sum_cm = st->nsum = 0;
    if (double_to_m(((unsigned *)fw_trip)[0x30 / 4], ((unsigned *)fw_trip)[0x34 / 4], &odo)) {
        /* the trip odometer only goes up, except when the trip is reset */
        if (st->have_odo && odo >= st->last_odo && odo - st->last_odo < 100000)
            dist = odo - st->last_odo;
        st->last_odo = odo;
        st->have_odo = 1;
    }
    push(st, elev, dist);
}

/* UI main loop, every few ms */
void profile_tick(void)
{
    struct profile_state *st = PS;
    unsigned now, step, n;

    if (st->magic != MAGIC)
        return;
    now = fw_time_now();
    step = REC_SECS * fw_ticks_per_sec();
    if (!st->started) {
        st->started = 1;
        st->next_time = now + step;
        return;
    }
    if ((int)(now - st->next_time) < 0)
        return;
    add_record(st);
    st->next_time += step;
    /* UI task held up for more than one interval: gaps, then resync */
    for (n = 0; (int)(now - st->next_time) >= 0; n++) {
        if (n >= NREC) {
            st->next_time = now + step;
            break;
        }
        push(st, GAP, 0);
        st->gaps++;
        st->next_time += step;
    }
}

/* --- drawing --------------------------------------------------------------- */

#define SCR_W   176
#define HDR_H   18
#define PX0     36                      /* plot area, inclusive */
#define PX1     171
#define PY0     27
#define PY1     176
#define PW      (PX1 - PX0 + 1)
#define PH      (PY1 - PY0 + 1)
#define MIN_RANGE  40                   /* half metres: never zoom in closer than 20 m */

static unsigned color(int idx)
{
    return fw_theme_color(fw_prefs, idx);
}

static char *put_str(char *p, const char *s)
{
    while (*s)
        *p++ = *s++;
    *p = 0;
    return p;
}

/* elevation (half metres) as text the way the stock fields show it;
 * unit is " ft" or " m" */
static void fmt_elev(int hm, char *value, char *unit)
{
    unsigned spec[4] = {0, 0, 0, 0};
    unsigned long long d;
    unsigned i;

    for (i = 0; i < 16; i++)
        value[i] = unit[i] = 0;
    fw_fmt_spec_init(spec);
    spec[1] = 7;
    d = cm_to_double(hm < 0 ? (unsigned)-hm * 50 : (unsigned)hm * 50);
    if (hm < 0)
        d |= 1ull << 63;
    fw_fmt_elevation((unsigned)d, (unsigned)(d >> 32), value, unit, spec);
}

static int units_index(void)
{
    int m = fw_pref_measures();
    return m == 1 ? 1 : m >= 2 ? 2 : 0;
}

static int y_of(int v, int lo, int range)
{
    return PY1 - (int)udiv((unsigned)(v - lo) * (PH - 1) + (unsigned)range / 2, (unsigned)range);
}

static void text_right(void *win, const void *font, int xr, int y, const char *s, unsigned fg)
{
    fw_gfx_draw_string(win, font, xr - fw_font_string_width(font, s), y, s, fg, fg);
}

void profile_draw(void *win)
{
    struct profile_state *st = PS;
    short cmin[PW], cmax[PW];
    unsigned bg = color(5), fg = color(6), line = color(4), fill = color(1), grid = color(3);
    const void *f10 = fw_font_slots[3], *f10b = fw_font_slots[4], *f9 = fw_font_slots[0];
    unsigned span, k, idx, pos;
    int c, lo = 32767, hi = -32768, have = 0, range, prev_top = -1, prev_bot = -1, ui = units_index();
    char buf[48], value[16], unit[16], *p;

    st->draws++;
    st->dirty = 0;
    for (c = 0; c < PW; c++) {
        cmin[c] = 32767;
        cmax[c] = -32768;
    }

    /* bucket the records into columns, newest at the right */
    span = st->mode ? dist_span[ui][st->zoom[1]] : time_span[st->zoom[0]];
    pos = 0;                            /* seconds or metres back from now */
    idx = st->head;
    for (k = 0; k < st->count; k++) {
        const struct rec *r;
        idx = idx ? idx - 1 : NREC - 1;
        r = &RING[idx];
        if (!st->mode)
            pos = k * REC_SECS;
        if (pos >= span)
            break;
        if (r->elev != GAP) {
            c = PW - 1 - (int)udiv(pos * PW, span);
            if (r->elev < cmin[c])
                cmin[c] = r->elev;
            if (r->elev > cmax[c])
                cmax[c] = r->elev;
            if (r->elev < lo)
                lo = r->elev;
            if (r->elev > hi)
                hi = r->elev;
            have = 1;
        }
        if (st->mode)
            pos += r->dist;             /* distance between this record and the newer one */
    }

    /* distance mode: columns between two records are not missing data */
    if (st->mode && have) {
        int last = -1;
        for (c = 0; c < PW; c++) {
            if (cmax[c] == -32768)
                continue;
            if (last >= 0 && c - last > 1) {
                int a = (cmin[last] + cmax[last]) / 2, b = (cmin[c] + cmax[c]) / 2, j;
                for (j = last + 1; j < c; j++)
                    cmin[j] = cmax[j] = (short)(a + sdiv_round((b - a) * (j - last), (unsigned)(c - last)));
            }
            last = c;
        }
    }

    fw_gfx_fill_rect(win, 0, 0, SCR_W - 1, 219, bg);

    /* header: title, then mode and span */
    fw_gfx_fill_rect(win, 0, 0, SCR_W - 1, HDR_H - 1, line);
    fw_gfx_draw_string(win, f10b, 3, 4, "Elevation", color(7), color(7));
    p = put_str(buf, st->mode ? "Dist " : "Time ");
    put_str(p, st->mode ? dist_label[ui][st->zoom[1]] : time_label[st->zoom[0]]);
    text_right(win, f10b, SCR_W - 4, 4, buf, color(7));

    /* plot frame and grid */
    fw_gfx_hline(win, PX0, PX1, PY0, grid);
    if (have)
        fw_gfx_hline(win, PX0, PX1, PY0 + PH / 2, grid);
    fw_gfx_hline(win, PX0, PX1, PY1, grid);
    fw_gfx_vline(win, PX0, PY0, PY1, grid);

    if (!have) {
        fw_gfx_draw_string(win, f10, PX0 + 12, PY0 + PH / 2 - 16, "No elevation data", fg, fg);
        fw_gfx_draw_string(win, f10, PX0 + 12, PY0 + PH / 2 + 6, "in this span yet", fg, fg);
    } else {
        int mid;
        /* vertical scale: data range, at least MIN_RANGE, plus a margin */
        range = hi - lo;
        if (range < MIN_RANGE) {
            lo -= (MIN_RANGE - range) / 2;
            range = MIN_RANGE;
        }
        lo -= range / 16;
        range += range / 8;
        mid = lo + range / 2;

        for (c = 0; c < PW; c++) {
            int top, bot;
            if (cmax[c] == -32768) {
                prev_top = -1;
                continue;
            }
            top = y_of(cmax[c], lo, range);
            bot = y_of(cmin[c], lo, range);
            if (bot < PY1)
                fw_gfx_vline(win, PX0 + c, bot + 1, PY1 - 1, fill);
            /* join to the previous column so steep parts stay connected */
            if (prev_top >= 0) {
                if (prev_top > bot)
                    bot = prev_top;
                if (prev_bot < top)
                    top = prev_bot;
            }
            fw_gfx_vline(win, PX0 + c, top, bot, line);
            prev_top = y_of(cmax[c], lo, range);
            prev_bot = y_of(cmin[c], lo, range);
        }

        /* scale labels: top, middle, bottom */
        fmt_elev(lo + range, value, unit);
        text_right(win, f9, PX0 - 3, PY0 - 4, value, fg);
        fmt_elev(mid, value, unit);
        text_right(win, f9, PX0 - 3, PY0 + PH / 2 - 4, value, fg);
        fmt_elev(lo, value, unit);
        text_right(win, f9, PX0 - 3, PY1 - 4, value, fg);
        text_right(win, f9, PX0 - 3, PY0 + 8, unit + (unit[0] == ' '), fg);
    }

    /* x axis */
    p = put_str(buf, "-");
    put_str(p, st->mode ? dist_label[ui][st->zoom[1]] : time_label[st->zoom[0]]);
    fw_gfx_draw_string(win, f9, PX0, PY1 + 4, buf, fg, fg);
    text_right(win, f9, PX1, PY1 + 4, "now", fg);

    /* footer: current elevation, totals, keys */
    fw_gfx_hline(win, 0, SCR_W - 1, 194, grid);
    p = put_str(buf, "Now ");
    if (st->have_sample) {
        fmt_elev(sdiv_round(st->last_cm, 50), value, unit);
        p = put_str(p, value);
        put_str(p, unit);
    } else {
        put_str(p, "--");
    }
    fw_gfx_draw_string(win, f10b, 3, 197, buf, fg, fg);
    if (TRIP_ASCENT(fw_trip) <= TOTAL_MAX_CM && TRIP_DESCENT(fw_trip) <= TOTAL_MAX_CM) {
        p = put_str(buf, "+");
        fmt_elev((int)udiv(TRIP_ASCENT(fw_trip) + 25, 50), value, unit);
        p = put_str(p, value);
        p = put_str(p, " -");
        fmt_elev((int)udiv(TRIP_DESCENT(fw_trip) + 25, 50), value, unit);
        p = put_str(p, value);
        put_str(p, unit);
        text_right(win, f10b, SCR_W - 4, 197, buf, fg);
    }
    fw_gfx_draw_string(win, f9, 3, 209, "ENTER time/distance  ZOOM span", grid, grid);
}

/* --- page and pane handlers ------------------------------------------------ */

static void flush(void)
{
    struct ui_result tmp, req = {0x7D4, 0};
    fw_ui_handle_result(&tmp, &req);
}

struct ui_result *profile_page(struct ui_result *res, const struct ui_result *ev, int page)
{
    struct profile_state *st = PS;
    unsigned char *obj = fw_page_objs[page];
    void *win = obj ? *(void **)(obj + 0x34) : 0;
    int shown = obj && obj[0x3C], handled = 0;

    res->code = 10;
    res->param = 0;
    if (st->magic != MAGIC || !win)
        return res;
    switch (ev->code) {
    case 0x7DC:                         /* enter: page_show flushes after the panes */
        profile_draw(win);
        break;
    case 2:                             /* periodic */
    case 0x7D6:                         /* settings changed */
        if (shown && (st->dirty || ev->code == 0x7D6)) {
            profile_draw(win);
            flush();
        }
        break;
    case KEY_ENTER:
        st->mode ^= 1;
        handled = 1;
        break;
    case KEY_ZOOM_IN:
        if (st->zoom[st->mode] > 0)
            st->zoom[st->mode]--;
        handled = 1;
        break;
    case KEY_ZOOM_OUT:
        if (st->zoom[st->mode] < 3)
            st->zoom[st->mode]++;
        handled = 1;
        break;
    }
    if (handled) {
        if (shown) {
            profile_draw(win);
            flush();
        }
        res->code = 0xB;
    }
    return res;
}

struct ui_result *profile_pane(struct ui_result *res, const struct ui_result *ev, int page, int cell)
{
    (void)ev; (void)page; (void)cell;
    res->code = 10;
    res->param = 0;
    return res;
}
