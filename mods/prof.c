/* UI timing and CPU sampling diagnostics: where does a slow screen update
 * (e.g. the map) spend its time?
 *
 * Each screenshot writes two reports covering the time since the previous
 * screenshot (or boot), then starts a new window:
 *
 * SD:/screenshots/maptime.txt - UI main loop timing. Wrapped (prof_hooks.S):
 *   slot 0  ui_before_dispatch   UI main loop 0x30085F38 (GPS fixes, trip computer)
 *   slot 1  ui_dispatch_event    UI main loop 0x30085F50 (pages, panes, the map)
 *   slot 2  win_update(screen)   UI main loop 0x30085F6C (push to the panel)
 *   slot 3  win_update(popup)    UI main loop 0x30085F98
 *   slot 4  fread                entry 0x30187BA4, every caller, every task
 *   slot 5  fseek                entry 0x30187D38
 *   A "frame" is one pass of the UI main loop, from ui_before_dispatch to the
 *   next pass's top (ui_loop_tramp); the NFRAMES slowest are kept. Storage
 *   time outside frames (other tasks) is counted separately.
 *
 * SD:/screenshots/cpuprof.txt - CPU sampling, all tasks: on every IRQ,
 *   prof_irq counts the interrupted code address in 256-byte bins over
 *   0x30080000-0x306DFFFF (firmware + mods); anything else counts as "other".
 *   Only while a folder SD:/prof exists: the IRQ hook is installed at runtime
 *   (by patching the IRQ handler pointer at 0xA0000038, after checking the
 *   vectors look as expected) and removed when the folder goes away.
 *
 * SD:/screenshots/sysinfo.txt - measured CPU clock (timed loops against the
 *   3.25 MHz system timer), the live PLL register decoded with the Nomadik
 *   formula, and read-only dumps of clock, timer, flash and SDRAM controller
 *   registers.
 */
#include "fw.h"
#include "mods.h"

#define MAGIC    0x54494D45u          /* "TIME" */
#define NSLOTS   6
#define NFRAMES  10
#define CUR_PAGE (*(volatile unsigned *)0x00053B64)

/* IRQ sampling (layout of the first words is used by prof_irq) */
#define HIST_BASE  0x30080000u
#define HIST_SIZE  0x00660000u          /* to 0x306E0000 */
#define HIST_BINS  (HIST_SIZE >> 8)
extern volatile unsigned fw_vec_irq_lit;                  /* 0x38: vector 0x18 loads pc from here */
#define VEC_IRQ_LIT   fw_vec_irq_lit
#define RAMVEC_IRQ    (*(volatile unsigned *)0xA0000018)
#define RAMVEC_IRQ_LIT (*(volatile unsigned *)0xA0000038)
#define LDR_PC_PC18   0xE59FF018u
#define PROF_DIR      "SD:/prof"
#define CHECK_SECS    2

struct slot {
    unsigned calls;
    unsigned total_lo, total_hi;        /* ticks */
    unsigned max;
};

struct frame {
    unsigned event, param, page;
    unsigned total, before, dispatch, push, io;   /* ticks */
    unsigned io_calls;
};

struct prof_state {
    /* used by prof_irq (prof_hooks.S): keep these first, in this order */
    volatile unsigned short *hist;      /* HIST_BINS counters, 0 = not sampling */
    unsigned saved_irq;                 /* the firmware's IRQ handler */
    volatile unsigned samples, other;

    unsigned magic;
    int active;                         /* inside a frame */
    unsigned start;
    unsigned io_at_start, io_calls_at_start;
    struct frame cur;
    unsigned outside_io, outside_calls; /* fread/fseek ticks and calls outside frames */
    unsigned nframes, slow_frames, frames;
    unsigned window_start;              /* fw_time_now() when this window began */
    int skip_dispatch;                  /* this frame's dispatch took a screenshot: don't count it */
    int irq_state;                      /* see irq_state_name */
    unsigned last_check;
    unsigned short *buf;                /* malloc'd histogram, kept once allocated */
    struct slot slots[NSLOTS];
    struct frame slow[NFRAMES];         /* slowest first */
    unsigned *stress_buf;               /* malloc'd once for the stress test */
    unsigned compass_reads, compass_errors, compass_consec, compass_max_consec;
    int compass_last_err;
};

extern char PROF_RAM[], PROF_RAM_END[];
#define PF ((struct prof_state *)PROF_RAM)

typedef char state_fits[(sizeof(struct prof_state) <= 0x280) ? 1 : -1];   /* PROF_RAM..LOGGER_RAM_END */

void prof_init(void);
unsigned prof_enter(int slot, const unsigned *arg0);
void prof_exit(int slot, unsigned start);
void prof_loop_top(void);
void prof_screenshot(void);
void prof_irq(void);

/* set by build.py: identifies the installed build in the reports. Read
 * through BUILD_ID: a plain read of a const gets folded to the placeholder. */
const unsigned mods_build_id = 0xFFFFFFFFu;
#define BUILD_ID (*(const volatile unsigned *)&mods_build_id)

void cpu_loop16(unsigned n);
void cpu_loop48(unsigned n);
unsigned cpu_irq_mask(unsigned bits);   /* returns the old CPSR */
void cpu_irq_restore(unsigned cpsr);
unsigned cpu_get_cpsr(void);
void bus_read_loop(const volatile unsigned *p, unsigned n, unsigned stride);
unsigned stress_pass(unsigned *buf, unsigned words, unsigned seed);
int prof_compass(int err);

enum { IRQ_OFF, IRQ_ON, IRQ_BAD_VECTOR, IRQ_BAD_RAMVEC, IRQ_NO_MEMORY, IRQ_BAD_HANDLER };
static const char *const irq_state_name[] = {
    "off (no SD:/prof folder)", "on", "not installed: vector 0x18 changed",
    "not installed: 0xA0000018 changed", "not installed: no memory",
    "not installed: handler pointer at 0xA0000038 implausible",
};

static void zero(void *p, unsigned n)
{
    unsigned *w = p;
    for (n /= 4; n; n--)
        *w++ = 0;
}

static void new_window(struct prof_state *p)
{
    zero(&p->outside_io, (unsigned)((char *)&p->window_start - (char *)&p->outside_io));
    zero(p->slots, sizeof p->slots + sizeof p->slow);
    p->window_start = fw_time_now();
    p->samples = p->other = 0;
    if (p->buf)
        zero(p->buf, HIST_BINS * 2);
}

void prof_init(void)
{
    struct prof_state *p = PF;

    zero(p, sizeof *p);
    p->magic = MAGIC;
    p->window_start = fw_time_now();
}

static unsigned io_ticks(struct prof_state *p)
{
    return p->slots[4].total_lo + p->slots[5].total_lo;
}

static unsigned io_calls(struct prof_state *p)
{
    return p->slots[4].calls + p->slots[5].calls;
}

/* before the wrapped call; returns the start time */
unsigned prof_enter(int slot, const unsigned *arg0)
{
    struct prof_state *p = PF;
    unsigned now = fw_time_now();

    if (p->magic != MAGIC)
        return now;
    if (slot == 0) {                    /* a frame begins */
        zero(&p->cur, sizeof p->cur);
        p->active = 1;
        p->start = now;
        p->io_at_start = io_ticks(p);
        p->io_calls_at_start = io_calls(p);
        p->cur.page = CUR_PAGE;
    } else if (slot == 1 && arg0) {     /* ui_dispatch_event(&ev) */
        p->cur.event = arg0[0];
        p->cur.param = arg0[1];
    }
    return now;
}

/* after the wrapped call */
void prof_exit(int slot, unsigned start)
{
    struct prof_state *p = PF;
    struct slot *s;
    unsigned dt = fw_time_now() - start;

    if (p->magic != MAGIC || slot < 0 || slot >= NSLOTS)
        return;
    if (slot == 1 && p->skip_dispatch) {
        p->skip_dispatch = 0;
        return;
    }
    s = &p->slots[slot];
    s->calls++;
    if (s->total_lo + dt < s->total_lo)
        s->total_hi++;
    s->total_lo += dt;
    if (dt > s->max)
        s->max = dt;
    if (slot >= 4 && !p->active) {
        p->outside_io += dt;
        p->outside_calls++;
    }
    if (p->active) {
        if (slot == 0)
            p->cur.before += dt;
        else if (slot == 1)
            p->cur.dispatch += dt;
        else if (slot == 2 || slot == 3)
            p->cur.push += dt;
    }
}

/* keep the NFRAMES slowest, slowest first */
static void keep_slow(struct prof_state *p, const struct frame *fr)
{
    int i;

    if (p->nframes < NFRAMES)
        i = (int)p->nframes++;
    else if (fr->total > p->slow[NFRAMES - 1].total)
        i = NFRAMES - 1;
    else
        return;
    while (i > 0 && p->slow[i - 1].total < fr->total) {
        p->slow[i] = p->slow[i - 1];
        i--;
    }
    p->slow[i] = *fr;
}

/* --- IRQ sampling switch (UI task) --------------------------------------------- */

static void irq_install(struct prof_state *p)
{
    unsigned h;

    if (VEC_IRQ_LIT != 0xA0000018u) {
        p->irq_state = IRQ_BAD_VECTOR;
        return;
    }
    if (RAMVEC_IRQ != LDR_PC_PC18) {
        p->irq_state = IRQ_BAD_RAMVEC;
        return;
    }
    h = RAMVEC_IRQ_LIT;
    if (h != (unsigned)prof_irq && (h == 0xA0000018u || h & 3 ||
            !((h >= 0x30080000u && h < 0x30800000u) || (h >= 0xA0000000u && h < 0xA0100000u)))) {
        p->irq_state = IRQ_BAD_HANDLER;
        return;
    }
    if (!p->buf)
        p->buf = fw_malloc(HIST_BINS * 2);
    if (!p->buf) {
        p->irq_state = IRQ_NO_MEMORY;
        return;
    }
    zero(p->buf, HIST_BINS * 2);
    p->samples = p->other = 0;
    if (h != (unsigned)prof_irq) {
        p->saved_irq = h;               /* before the switch: prof_irq jumps there */
        p->hist = p->buf;
        RAMVEC_IRQ_LIT = (unsigned)prof_irq;
    }
    p->irq_state = IRQ_ON;
}

static void irq_remove(struct prof_state *p)
{
    if (RAMVEC_IRQ_LIT == (unsigned)prof_irq)
        RAMVEC_IRQ_LIT = p->saved_irq;
    p->hist = 0;
    p->irq_state = IRQ_OFF;
}

static void irq_check(struct prof_state *p, unsigned now)
{
    unsigned char st[64];
    int want;

    if (now - p->last_check < CHECK_SECS * fw_ticks_per_sec())
        return;
    p->last_check = now;
    want = fw_usb_export_state() != 0 && fw_stat(PROF_DIR, st) == 0;
    if (want && p->irq_state != IRQ_ON)
        irq_install(p);
    else if (!want && p->irq_state == IRQ_ON)
        irq_remove(p);
}

/* top of the UI main loop (ui_loop_tramp): the previous frame ends */
void prof_loop_top(void)
{
    struct prof_state *p = PF;
    unsigned now;

    if (p->magic != MAGIC)
        return;
    now = fw_time_now();
    p->skip_dispatch = 0;
    if (p->active) {
        p->active = 0;
        p->frames++;
        p->cur.total = now - p->start;
        p->cur.io = io_ticks(p) - p->io_at_start;
        p->cur.io_calls = io_calls(p) - p->io_calls_at_start;
        if (p->cur.total >= 50 * (fw_ticks_per_sec() / 1000))
            p->slow_frames++;
        keep_slow(p, &p->cur);
    }
    irq_check(p, now);
}

/* --- reports ------------------------------------------------------------------ */

static char *put_str(char *q, const char *s)
{
    while (*s)
        *q++ = *s++;
    return q;
}

static char *put_uint(char *q, unsigned v)
{
    char tmp[10];
    int n = 0;
    do {
        unsigned d = udiv(v, 10);
        tmp[n++] = (char)('0' + (v - d * 10));
        v = d;
    } while (v);
    while (n)
        *q++ = tmp[--n];
    return q;
}

static char *put_hex(char *q, unsigned v)
{
    int i;
    q = put_str(q, "0x");
    for (i = 28; i >= 0; i -= 4)
        *q++ = "0123456789ABCDEF"[(v >> i) & 15];
    return q;
}

static unsigned ms(unsigned ticks)
{
    return udiv(ticks + 1625, 3250);    /* 3.25 MHz */
}

static unsigned ms64(unsigned lo, unsigned hi)
{
    /* 2^32 = 1321528 * 3250 + 1296; hi stays small (2^32 ticks = 22 min) */
    return hi * 1321528u + udiv(hi * 1296u, 3250) + ms(lo);
}

static void write_file(const char *path, const char *text, unsigned n)
{
    FW_FILE *f = fw_fopen(path, "w");
    if (f) {
        fw_fwrite(text, 1, n, f);
        fw_fclose(f);
    }
}

static void write_timing(struct prof_state *p, unsigned window)
{
    static const char *const names[NSLOTS] = {
        "before_dispatch", "dispatch", "push_screen", "push_popup", "fread", "fseek",
    };
    char text[2000], *q = text;         /* UI task: 64 KB stack */
    unsigned i;

    q = put_str(q, "PN-40 UI timing v2 (ms, since the previous screenshot) build=");
    q = put_hex(q, BUILD_ID);
    q = put_str(q, "\r\nwindow=");
    q = put_uint(q, ms(window));
    q = put_str(q, " frames=");
    q = put_uint(q, p->frames);
    q = put_str(q, " slow(>=50ms)=");
    q = put_uint(q, p->slow_frames);
    q = put_str(q, " page_now=");
    q = put_uint(q, CUR_PAGE);
    q = put_str(q, " map_orientation=");
    q = put_uint(q, ((unsigned *)fw_prefs)[0x8 / 4]);
    q = put_str(q, "\r\nslot calls total max avg\r\n");
    for (i = 0; i < NSLOTS; i++) {
        struct slot *s = &p->slots[i];
        unsigned tot = ms64(s->total_lo, s->total_hi);
        q = put_str(q, names[i]);
        *q++ = ' ';
        q = put_uint(q, s->calls);
        *q++ = ' ';
        q = put_uint(q, tot);
        *q++ = ' ';
        q = put_uint(q, ms(s->max));
        *q++ = ' ';
        q = put_uint(q, s->calls ? udiv(tot, s->calls) : 0);
        q = put_str(q, "\r\n");
    }
    q = put_str(q, "io_outside_frames ");
    q = put_uint(q, p->outside_calls);
    *q++ = ' ';
    q = put_uint(q, ms(p->outside_io));
    q = put_str(q, "\r\nslowest frames: page event param total before dispatch push io io_calls\r\n");
    for (i = 0; i < p->nframes; i++) {
        struct frame *fr = &p->slow[i];
        q = put_uint(q, fr->page);
        *q++ = ' ';
        q = put_hex(q, fr->event);
        *q++ = ' ';
        q = put_hex(q, fr->param);
        *q++ = ' ';
        q = put_uint(q, ms(fr->total));
        *q++ = ' ';
        q = put_uint(q, ms(fr->before));
        *q++ = ' ';
        q = put_uint(q, ms(fr->dispatch));
        *q++ = ' ';
        q = put_uint(q, ms(fr->push));
        *q++ = ' ';
        q = put_uint(q, ms(fr->io));
        *q++ = ' ';
        q = put_uint(q, fr->io_calls);
        q = put_str(q, "\r\n");
    }
    write_file("SD:/screenshots/maptime.txt", text, (unsigned)(q - text));
}

#define TOP_BINS 120

static void write_cpuprof(struct prof_state *p, unsigned window)
{
    char text[TOP_BINS * 24 + 300], *q = text;
    unsigned samples = p->samples, other = p->other, i, k;

    q = put_str(q, "PN-40 CPU samples v1 (IRQ-time PC, 256-byte bins, since the previous screenshot) build=");
    q = put_hex(q, BUILD_ID);
    q = put_str(q, "\r\nsampling=");
    q = put_str(q, irq_state_name[p->irq_state]);
    q = put_str(q, " window_ms=");
    q = put_uint(q, ms(window));
    q = put_str(q, " samples=");
    q = put_uint(q, samples);
    q = put_str(q, " other=");
    q = put_uint(q, other);
    q = put_str(q, " handler=");
    q = put_hex(q, p->saved_irq);
    q = put_str(q, "\r\nbin count\r\n");
    if (p->buf) {
        for (k = 0; k < TOP_BINS; k++) {
            unsigned best = 0, bi = 0;
            for (i = 0; i < HIST_BINS; i++)
                if (p->buf[i] > best) {
                    best = p->buf[i];
                    bi = i;
                }
            if (!best)
                break;
            p->buf[bi] = 0;             /* the histogram restarts after the report anyway */
            q = put_hex(q, HIST_BASE + (bi << 8));
            *q++ = ' ';
            q = put_uint(q, best);
            q = put_str(q, "\r\n");
        }
    }
    write_file("SD:/screenshots/cpuprof.txt", text, (unsigned)(q - text));
}

#define LOOP_N 20000                    /* 16-add loop: ~400k cycles, 48-add loop: ~1.04M */

/* fastest of 7 runs, in timer ticks (fewest interruptions) */
static unsigned time_loop(void (*loop)(unsigned))
{
    unsigned best = ~0u, k, t0, dt;

    for (k = 0; k < 7; k++) {
        t0 = fw_time_now();
        loop(LOOP_N);
        dt = fw_time_now() - t0;
        if (dt < best)
            best = dt;
    }
    return best;
}

#define MASKED_N 5000                   /* ~1-3 ms per run with interrupts masked */

/* fastest of 3 runs of MASKED_N iterations with the CPSR bits `mask` set
 * (interrupts back on between runs); *took = CPSR seen inside the window */
static unsigned time_loop_masked(void (*loop)(unsigned), unsigned mask, unsigned *took)
{
    unsigned best = ~0u, k, t0, dt, old;

    for (k = 0; k < 3; k++) {
        old = cpu_irq_mask(mask);
        *took = cpu_get_cpsr();
        t0 = fw_time_now();
        loop(MASKED_N);
        dt = fw_time_now() - t0;
        cpu_irq_restore(old);
        if (dt < best)
            best = dt;
    }
    return best;
}

/* " name=khz (masked=yes|no)": 32 extra adds per iteration, 1 cycle each */
static char *put_masked(char *q, const char *name, unsigned mask)
{
    unsigned took16, took48, t16 = time_loop_masked(cpu_loop16, mask, &took16),
             t48 = time_loop_masked(cpu_loop48, mask, &took48);

    q = put_str(q, name);
    q = put_uint(q, t48 > t16 ? udiv(MASKED_N * 32u * 3250u, t48 - t16) : 0);
    q = put_str(q, (took16 & took48 & mask) == mask ? " (masked=yes" : " (masked=NO");
    q = put_str(q, " ticks16=");
    q = put_uint(q, t16);
    q = put_str(q, " ticks48=");
    q = put_uint(q, t48);
    return put_str(q, ")\r\n");
}

#define BUS_N 8000

/* picoseconds per load: BUS_N loads from p (stride bytes apart), fastest of
 * 3 runs with IRQs locked; includes the loop's own 3 instructions */
static unsigned bus_ps(const volatile unsigned *p, unsigned stride)
{
    unsigned best = ~0u, k, t0, dt, old;

    for (k = 0; k < 3; k++) {
        old = cpu_irq_mask(0x80);
        t0 = fw_time_now();
        bus_read_loop(p, BUS_N, stride);
        dt = fw_time_now() - t0;
        cpu_irq_restore(old);
        if (dt < best)
            best = dt;
    }
    return udiv(best * 307692u, BUS_N);  /* 1 tick = 307.692 ns */
}

static char *put_bus(char *q)
{
    static const unsigned cached_word = 0;

    q = put_str(q, "bus_ps_per_load: cached=");
    q = put_uint(q, bus_ps(&cached_word, 0));
    q = put_str(q, " src_reg=");                   /* SRC_PLLFR, read-only */
    q = put_uint(q, bus_ps((const volatile unsigned *)0x101E0014, 0));
    q = put_str(q, " sdram_uncached=");            /* 0x7F0000+: uncached in the MMU table */
    q = put_uint(q, bus_ps((const volatile unsigned *)0x007F0000, 4));
    q = put_str(q, " (n=8000, irq locked)\r\n");
    return q;
}

/* --- stress test: the same deterministic work STRESS_RUNS times; any
 * difference between runs means the CPU or memory made an error ---------- */

#define STRESS_WORDS 16384              /* 64 KB: larger than the 16 KB D-cache */
#define STRESS_RUNS  16
#define STRESS_SEED  0x12345678u

/* fill buf with an LCG sequence, mix it (rotates, multiplies, 64-bit
 * accumulate, scattered reads), then CRC-32 it. words is a power of 2. */
unsigned stress_pass(unsigned *buf, unsigned words, unsigned seed)
{
    unsigned table[256], i, j, k, c, x = seed, v, crc = ~0u;
    unsigned long long acc = 0;

    for (i = 0; i < 256; i++) {
        c = i;
        for (k = 0; k < 8; k++)
            c = c & 1 ? 0xEDB88320u ^ (c >> 1) : c >> 1;
        table[i] = c;
    }
    for (i = 0; i < words; i++) {
        x = x * 1664525u + 1013904223u;
        buf[i] = x;
    }
    for (i = 0; i < words; i++) {
        j = (i * 7 + 3) & (words - 1);
        v = buf[j];
        v = (v << 13) | (v >> 19);
        buf[i] ^= v + i * 0x9E3779B9u;
        acc += (unsigned long long)buf[i] * (v | 1);
    }
    for (i = 0; i < words; i++) {
        v = buf[i];
        for (k = 0; k < 4; k++) {
            crc = table[(crc ^ v) & 0xFF] ^ (crc >> 8);
            v >>= 8;
        }
    }
    return ~crc ^ (unsigned)acc ^ (unsigned)(acc >> 32);
}

static char *put_stress(char *q, struct prof_state *p)
{
    unsigned first = 0, bad = 0, k, r, t0;

    if (!p->stress_buf)
        p->stress_buf = fw_malloc(STRESS_WORDS * 4);
    q = put_str(q, "stress: ");
    if (!p->stress_buf)
        return put_str(q, "no memory\r\n");
    t0 = fw_time_now();
    for (k = 0; k < STRESS_RUNS; k++) {
        r = stress_pass(p->stress_buf, STRESS_WORDS, STRESS_SEED);
        if (k == 0)
            first = r;
        else if (r != first)
            bad++;
    }
    q = put_str(q, "checksum=");
    q = put_hex(q, first);
    q = put_str(q, " runs=16 mismatches=");
    q = put_uint(q, bad);
    q = put_str(q, " ms=");
    q = put_uint(q, ms(fw_time_now() - t0));
    return put_str(q, " (interrupts on, 64 KB buffer)\r\n");
}

/* --- compass task (prof_hooks.S compass_*_tramp) -------------------------- */

#define COMPASS_GIVE_UP 120             /* failures in a row (10 s at 12/s) */
#define COMPASS_STATE  (*(volatile unsigned *)0x002B0950)
#define COMPASS_STATUS (*(volatile int *)0x002B0958)
#define COMPASS_VALID  (*(volatile unsigned char *)0x002B0DE4)

/* one sensor read by the Compass Process task: err 0 = good. Returns
 * nonzero to keep the task going (retry), 0 to let it stop as stock did. */
int prof_compass(int err)
{
    struct prof_state *p = PF;

    if (p->magic != MAGIC)
        return 0;                       /* not set up yet: stock behaviour */
    if (!err) {
        p->compass_reads++;
        p->compass_consec = 0;
        return 1;
    }
    p->compass_errors++;
    p->compass_last_err = err;
    if (++p->compass_consec > p->compass_max_consec)
        p->compass_max_consec = p->compass_consec;
    return p->compass_consec < COMPASS_GIVE_UP;
}

static char *put_compass(char *q, struct prof_state *p)
{
    q = put_str(q, "compass: state=");
    q = put_uint(q, COMPASS_STATE);
    q = put_str(q, " valid=");
    q = put_uint(q, COMPASS_VALID);
    q = put_str(q, " reads=");
    q = put_uint(q, p->compass_reads);
    q = put_str(q, " errors=");
    q = put_uint(q, p->compass_errors);
    q = put_str(q, " max_in_a_row=");
    q = put_uint(q, p->compass_max_consec);
    q = put_str(q, " last_error=");
    if (p->compass_last_err < 0) {
        *q++ = '-';
        q = put_uint(q, -(unsigned)p->compass_last_err);
    } else {
        q = put_uint(q, (unsigned)p->compass_last_err);
    }
    return put_str(q, " (since boot; state 5 = stopped)\r\n");
}

static char *put_regs(char *q, unsigned base, unsigned n)
{
    unsigned i;
    for (i = 0; i < n; i++) {
        if (i % 4 == 0) {
            if (i)
                q = put_str(q, "\r\n");
            q = put_hex(q, base + 4 * i);
            *q++ = ':';
        }
        *q++ = ' ';
        q = put_hex(q, ((volatile unsigned *)base)[i]);
    }
    return put_str(q, "\r\n");
}

static void write_sysinfo(void)
{
    static const struct { unsigned base, words; const char *what; } blocks[] = {
        {0x101E0000, 24, "system/reset controller (clocks, PLL)"},
        {0x101E3000, 20, "timer block (delay counter at +0x44)"},
        {0x101E9000, 4, "set up next to the PLL at boot"},
        {0x10100000, 8, "static memory (NOR flash) controller"},
        {0x10110000, 16, "SDRAM controller"},
        {0xA0000000, 16, "internal RAM vectors (+0x38 IRQ, +0x3C FIQ handler; 0xA000001C there = no FIQ handler)"},
    };
    char text[3000], *q = text;
    unsigned t16, t48, pllfr, mul1, div1, mul2, i;

    t16 = time_loop(cpu_loop16);
    t48 = time_loop(cpu_loop48);
    pllfr = *(volatile unsigned *)0x101E0014;
    mul1 = ((pllfr >> 8) & 0x3F) + 2;
    div1 = pllfr & 7;
    mul2 = ((pllfr >> 24) & 0x3F) + 2;

    q = put_str(q, "PN-40 system info v7 build=");
    q = put_hex(q, BUILD_ID);
    q = put_str(q, "\r\ncpu_khz=");               /* 32 extra adds per iteration, 1 cycle each */
    q = put_uint(q, t48 > t16 ? udiv(LOOP_N * 32u * 3250u, t48 - t16) : 0);
    q = put_str(q, " cpu_khz_if_branch_3_cycles=");
    q = put_uint(q, t16 ? udiv(LOOP_N * 20u * 3250u, t16) : 0);
    q = put_str(q, " ticks16=");
    q = put_uint(q, t16);
    q = put_str(q, " ticks48=");
    q = put_uint(q, t48);
    q = put_str(q, " n=20000 timer=3250000Hz (interrupts on)\r\ncpsr=");
    q = put_hex(q, cpu_get_cpsr());
    q = put_str(q, " (mode 0x10 user, 0x13 supervisor)\r\n");
    q = put_masked(q, "cpu_khz_irq_masked=", 0x80);
    q = put_masked(q, "cpu_khz_irq_fiq_masked=", 0xC0);
    q = put_str(q, "SRC_PLLFR=");
    q = put_hex(q, pllfr);
    q = put_str(q, " nomadik: PLL1=26MHz*");
    q = put_uint(q, mul1);
    q = put_str(q, ">>");
    q = put_uint(q, div1);
    q = put_str(q, "=");
    q = put_uint(q, (26000u * mul1) >> div1);
    q = put_str(q, "kHz PLL2=26MHz*");
    q = put_uint(q, mul2);
    q = put_str(q, "=");
    q = put_uint(q, 26000u * mul2);
    q = put_str(q, "kHz\r\nhclk_field=");
    i = (*(volatile unsigned *)0x101E0000 >> 13) & 3;
    q = put_uint(q, i);
    q = put_str(q, " (SRC_CR bits 14:13): bus = PLL1/");
    q = put_uint(q, i ? i : 1);
    q = put_str(q, " if the field is the divisor, PLL1/");
    q = put_uint(q, i + 1);
    q = put_str(q, " if it is divisor-1\r\n");
    q = put_bus(q);
    q = put_stress(q, PF);
    q = put_compass(q, PF);
    for (i = 0; i < sizeof blocks / sizeof blocks[0]; i++) {
        q = put_str(q, "-- ");
        q = put_str(q, blocks[i].what);
        q = put_str(q, "\r\n");
        q = put_regs(q, blocks[i].base, blocks[i].words);
    }
    write_file("SD:/screenshots/sysinfo.txt", text, (unsigned)(q - text));
}

/* after each screenshot (UI task, from screenshot_tramp) */
void prof_screenshot(void)
{
    struct prof_state *p = PF;
    unsigned window;

    if (p->magic != MAGIC)
        return;
    p->active = 0;                      /* the screenshot itself isn't a map frame */
    p->skip_dispatch = 1;               /* nor part of the dispatch statistics */
    window = fw_time_now() - p->window_start;
    write_timing(p, window);
    write_cpuprof(p, window);
    write_sysinfo();
    new_window(p);
}
