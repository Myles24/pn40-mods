/* Black box: why does the unit shut down when "Connect to Computer" exports
 * the SD card at a raised CPU clock?
 *
 * Records, in RAM the firmware never touches (it survives a watchdog or soft
 * reset, not a power-off):
 *   - a heartbeat every 2 s: USB export state, battery mV, the longest gap
 *     between UI loop passes, the watchdog slot closest to expiring;
 *   - USB export state changes;
 *   - every call of the shutdown routine 0x3008423C (power off / reboot) with
 *     its caller, and of the immediate-reset routine 0x301A869C;
 *   - a watchdog slot expiring (watchdog task 0x301A88FC), with the slot table;
 *   - CPU exceptions: undefined instruction, prefetch and data abort. Stock
 *     firmware's handlers are "b ." (the vector literals point at the vectors
 *     themselves), so a fault freezes the unit until the hardware watchdog
 *     resets it. bbox_hooks.S saves the registers first, then does the same.
 *
 * Reports:
 *   bbox_live.txt  this session, on the medium that is NOT exported (SD:/ or
 *                  MSYS2:/, the user-visible internal drive): every 5 s while
 *                  a drive is exported, at every shutdown call, and on SD at
 *                  every screenshot.
 *   bbox_prev.txt  once per boot on every available medium: the previous
 *                  session from RAM (if it survived) plus the previous
 *                  MSYS2:/bbox_live.txt.
 */
#include "fw.h"
#include "mods.h"

#define BB_MAGIC   0x42424F58u          /* "BBOX" */
#define EXC_MAGIC  0x45584321u          /* "EXC!", written last by bbox_hooks.S */
#define NEV        64                   /* power of 2 */
#define HEART_MS   2000
#define LIVE_MS    5000
#define TICKS_MS   3250                 /* 3.25 MHz timer */

#define UI_STACK_LO 0x00043358u         /* task_init(ui_task, 0, 0x43358, 0x10000, ...) */
#define UI_STACK_HI 0x00053358u
#define WD_SLOTS    ((volatile unsigned char *)0x002B006C)   /* 10 x {u8 on; u32 timeout; u32 kicked} */
#define BOOT_REG    (*(volatile unsigned *)0x101E901C)
#define RESET_REG   (*(volatile unsigned *)0x101E0018)
#define LDR_PC_PC18 0xE59FF018u
#define RAMVEC      ((volatile unsigned *)0xA0000000)
extern volatile unsigned fw_lowvec[16]; /* vectors at 0: "ldr pc, [pc, #0x18]" -> 0xA00000xx */

enum { EV_BOOT = 1, EV_HEART, EV_USB, EV_SHUTDOWN, EV_RESET, EV_WATCHDOG, EV_NOTE };

struct bb_event {
    unsigned ms, type, a, b, c, d;
};

/* filled by bbox_hooks.S: keep the layout in step with the offsets there */
struct bb_exc {
    unsigned magic;                     /* 0x00 EXC_MAGIC when complete */
    unsigned type;                      /* 0x04 1 undefined, 2 prefetch abort, 3 data abort */
    unsigned r[13];                     /* 0x08 r0-r12 */
    unsigned lr_exc;                    /* 0x3C exception-mode lr */
    unsigned spsr;                      /* 0x40 */
    unsigned sp, lr;                    /* 0x44 0x48 of the interrupted mode */
    unsigned fsr, far;                  /* 0x4C 0x50 CP15 fault status / address */
    unsigned stack[16];                 /* 0x54 words at the interrupted sp */
};

struct bb_wd {
    unsigned ms, now, slot, raw_ptr;
    unsigned char slots[120];           /* copy of the slot table */
};

struct bb_session {
    struct bb_exc exc;                  /* first: bbox_hooks.S writes BBOX_RAM + 0 */
    unsigned magic, boot_no, build_id, boot_reg, reset_reg, exc_state;
    unsigned date[2];                   /* packed date/time at boot */
    unsigned nev;
    struct bb_wd wd;
    struct bb_event ev[NEV];
};

/* runtime state, not part of the persisted record */
struct bb_rt {
    unsigned last_now, acc, uptime_ms;
    unsigned next_heart, next_live, max_gap;
    int usb, ticked;
    int prev_sd, prev_msys;             /* bbox_prev.txt written to SD / MSYS2 */
    int prev_read;                      /* previous MSYS2:/bbox_live.txt read */
    char *prev_live;                    /* its text (malloc'd) */
    unsigned prev_live_len;
    volatile int pending;               /* shutdown from another task: UI task writes */
    unsigned writes, write_fail;
};

/* aligned: otherwise GCC accesses these byte by byte */
extern char BBOX_RAM[] __attribute__((aligned(8))), BBOX_RT[] __attribute__((aligned(8))),
    BBOX_PREV[] __attribute__((aligned(8)));
#define CUR  ((struct bb_session *)BBOX_RAM)
#define RT   ((struct bb_rt *)BBOX_RT)
#define PREV ((struct bb_session *)BBOX_PREV)

typedef char session_fits[(sizeof(struct bb_session) <= 0x780) ? 1 : -1];
typedef char rt_fits[(sizeof(struct bb_rt) <= 0x80) ? 1 : -1];

void bbox_init(void);
void bbox_tick(void);
void bbox_screenshot(void);
void bbox_shutdown(int param, unsigned caller);
void bbox_reset(int param, unsigned caller);
void bbox_watchdog(unsigned slot_ptr);
void bbox_exc_undef(void);
void bbox_exc_pabort(void);
void bbox_exc_dabort(void);

unsigned cpu_irq_mask(unsigned bits);   /* prof_hooks.S: OS20 SWI 0 */
void cpu_irq_restore(unsigned cpsr);
int battery_mv(void);                   /* battfield.c */
extern const unsigned mods_build_id;
#define BUILD_ID (*(const volatile unsigned *)&mods_build_id)

static void copy(void *dst, const void *src, unsigned n)
{
    unsigned *d = dst;
    const unsigned *s = src;
    for (n /= 4; n; n--)
        *d++ = *s++;
}

static void zero(void *p, unsigned n)
{
    unsigned *w = p;
    for (n /= 4; n; n--)
        *w++ = 0;
}

/* ms since boot, from any task */
static unsigned bb_ms(void)
{
    struct bb_rt *rt = RT;
    return rt->uptime_ms + udiv(rt->acc + (fw_time_now() - rt->last_now), TICKS_MS);
}

static void event(unsigned type, unsigned a, unsigned b, unsigned c, unsigned d)
{
    struct bb_session *s = CUR;
    struct bb_event *e;
    unsigned old = cpu_irq_mask(0x80);

    e = &s->ev[s->nev++ & (NEV - 1)];
    e->ms = bb_ms();
    e->type = type;
    e->a = a;
    e->b = b;
    e->c = c;
    e->d = d;
    cpu_irq_restore(old);
}

/* exception handlers: install only where the stock "b ." loop is in place */
static unsigned exc_install(void)
{
    static const struct { unsigned vec; void (*fn)(void); } h[] = {
        {1, bbox_exc_undef}, {3, bbox_exc_pabort}, {4, bbox_exc_dabort},
    };
    unsigned i, done = 0;

    for (i = 0; i < 3; i++) {
        unsigned v = h[i].vec, self = 0xA0000000u + 4 * v;
        if (fw_lowvec[v] != LDR_PC_PC18 || fw_lowvec[v + 8] != self ||
                RAMVEC[v] != LDR_PC_PC18 || RAMVEC[v + 8] != self)
            continue;
        RAMVEC[v + 8] = (unsigned)h[i].fn;
        done |= 1u << v;
    }
    return done;                        /* bit 1 undef, 3 prefetch, 4 data abort */
}

/* boot (nmea_start_tramp): keep the previous session, start a new one */
void bbox_init(void)
{
    struct bb_session *s = CUR, *p = PREV;
    struct bb_rt *rt = RT;
    int mon, day, year, h, m, sec;

    if (s->magic == BB_MAGIC && s->boot_no < 1000000u)
        copy(p, s, sizeof *s);
    else
        p->magic = 0;
    zero(s, sizeof *s);
    zero(rt, sizeof *rt);
    rt->last_now = fw_time_now();
    s->boot_no = p->magic == BB_MAGIC ? p->boot_no + 1 : 1;
    s->build_id = BUILD_ID;
    s->boot_reg = BOOT_REG;
    s->reset_reg = RESET_REG;
    fw_get_datetime(&mon, &day, &year, &h, &m, &sec);
    s->date[0] = (unsigned)year << 16 | (unsigned)mon << 8 | (unsigned)day;
    s->date[1] = (unsigned)h << 16 | (unsigned)m << 8 | (unsigned)sec;
    s->exc_state = exc_install();
    s->wd.slot = ~0u;                   /* no watchdog expiry yet */
    s->magic = BB_MAGIC;
    rt->usb = -1;
    event(EV_BOOT, s->boot_reg, s->reset_reg, p->magic == BB_MAGIC, s->exc_state);
}

/* --- report text ----------------------------------------------------------- */

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

static char *put_int(char *q, int v)
{
    if (v < 0) {
        *q++ = '-';
        return put_uint(q, -(unsigned)v);
    }
    return put_uint(q, (unsigned)v);
}

static char *put_hex(char *q, unsigned v)
{
    int i;
    q = put_str(q, "0x");
    for (i = 28; i >= 0; i -= 4)
        *q++ = "0123456789ABCDEF"[(v >> i) & 15];
    return q;
}

static char *put_2(char *q, unsigned v)
{
    *q++ = (char)('0' + udiv(v, 10));
    *q++ = (char)('0' + v - 10 * udiv(v, 10));
    return q;
}

/* "123.456s" */
static char *put_secs(char *q, unsigned ms)
{
    unsigned s = udiv(ms, 1000), r = ms - s * 1000;
    q = put_uint(q, s);
    *q++ = '.';
    *q++ = (char)('0' + udiv(r, 100));
    q = put_2(q, r - 100 * udiv(r, 100));
    return put_str(q, "s");
}

static const char *usb_name(int u)
{
    return u == 0 ? "SD card exported" : u == 1 ? "internal drive exported" : u == 2 ? "not exported" : "?";
}

static char *put_slots(char *q, const volatile unsigned char *t, unsigned now)
{
    unsigned i;
    for (i = 0; i < 10; i++, t += 12) {
        unsigned timeout = *(const volatile unsigned *)(t + 4), kicked = *(const volatile unsigned *)(t + 8);
        if (!t[0])
            continue;
        q = put_str(q, "  slot ");
        q = put_uint(q, i);
        q = put_str(q, ": timeout ");
        q = put_uint(q, udiv(timeout, TICKS_MS));
        q = put_str(q, " ms, last kick ");
        q = put_uint(q, udiv(now - kicked, TICKS_MS));
        q = put_str(q, " ms ago\r\n");
    }
    return q;
}

static char *put_event(char *q, const struct bb_event *e)
{
    q = put_secs(q, e->ms);
    *q++ = ' ';
    switch (e->type) {
    case EV_BOOT:
        q = put_str(q, "BOOT boot_reg=");
        q = put_hex(q, e->a);
        q = put_str(q, " reset_reg=");
        q = put_hex(q, e->b);
        q = put_str(q, e->c ? " previous session in RAM" : " no previous session in RAM");
        q = put_str(q, " exc_handlers=");
        q = put_hex(q, e->d);
        break;
    case EV_HEART:
        q = put_str(q, "usb=");
        q = put_uint(q, e->a);
        q = put_str(q, " batt=");
        q = put_int(q, (int)e->b);
        q = put_str(q, "mV ui_max_gap=");
        q = put_uint(q, e->c);
        q = put_str(q, "ms wd_on=");
        q = put_hex(q, e->d & 0x3FF);
        q = put_str(q, " wd_min_slack=slot");
        q = put_uint(q, (e->d >> 10) & 15);
        *q++ = ':';
        q = put_uint(q, e->d >> 16);
        q = put_str(q, "00ms");
        break;
    case EV_USB:
        q = put_str(q, "USB ");
        q = put_str(q, usb_name((int)e->a));
        q = put_str(q, " -> ");
        q = put_str(q, usb_name((int)e->b));
        break;
    case EV_SHUTDOWN:
        q = put_str(q, e->a ? "SHUTDOWN(reboot)" : "SHUTDOWN(power off)");
        q = put_str(q, " caller=");
        q = put_hex(q, e->b);
        q = put_str(q, " batt=");
        q = put_int(q, (int)e->c);
        q = put_str(q, e->d ? "mV (UI task)" : "mV (other task)");
        break;
    case EV_RESET:
        q = put_str(q, "IMMEDIATE RESET 0x301A869C caller=");
        q = put_hex(q, e->b);
        q = put_str(q, " arg=");
        q = put_uint(q, e->a);
        break;
    case EV_WATCHDOG:
        q = put_str(q, "WATCHDOG SLOT EXPIRED slot=");
        q = put_uint(q, e->a);
        break;
    case EV_NOTE:
        q = put_str(q, "write failures=");
        q = put_uint(q, e->a);
        break;
    default:
        q = put_str(q, "? type=");
        q = put_uint(q, e->type);
    }
    return put_str(q, "\r\n");
}

static char *put_exc(char *q, const struct bb_exc *x)
{
    static const char *const names[] = {"?", "UNDEFINED INSTRUCTION", "PREFETCH ABORT", "DATA ABORT"};
    unsigned i, back = x->type == 3 ? 8 : 4;

    if (x->magic != EXC_MAGIC)
        return put_str(q, "exception: none\r\n");
    if ((x->spsr & 0x20) && x->type != 3)
        back = 2;                       /* Thumb */
    q = put_str(q, "EXCEPTION: ");
    q = put_str(q, names[x->type < 4 ? x->type : 0]);
    q = put_str(q, " at pc=");
    q = put_hex(q, x->lr_exc - back);
    q = put_str(q, " spsr=");
    q = put_hex(q, x->spsr);
    q = put_str(q, " fsr=");
    q = put_hex(q, x->fsr);
    q = put_str(q, " far=");
    q = put_hex(q, x->far);
    q = put_str(q, "\r\n");
    for (i = 0; i < 13; i++) {
        q = put_str(q, " r");
        q = put_uint(q, i);
        *q++ = '=';
        q = put_hex(q, x->r[i]);
        if (i % 4 == 3)
            q = put_str(q, "\r\n");
    }
    q = put_str(q, " sp=");
    q = put_hex(q, x->sp);
    q = put_str(q, " lr=");
    q = put_hex(q, x->lr);
    q = put_str(q, "\r\n stack:");
    for (i = 0; i < 16; i++) {
        *q++ = ' ';
        q = put_hex(q, x->stack[i]);
    }
    return put_str(q, "\r\n");
}

/* a whole session: header, exception, watchdog, events (oldest first) */
static char *put_session(char *q, const struct bb_session *s, unsigned now_ms)
{
    unsigned i, n = s->nev < NEV ? s->nev : NEV;

    q = put_str(q, "session boot_no=");
    q = put_uint(q, s->boot_no);
    q = put_str(q, " build=");
    q = put_hex(q, s->build_id);
    q = put_str(q, " started 20");
    q = put_2(q, s->date[0] >> 16);
    *q++ = '-';
    q = put_2(q, (s->date[0] >> 8) & 0xFF);
    *q++ = '-';
    q = put_2(q, s->date[0] & 0xFF);
    *q++ = ' ';
    q = put_2(q, s->date[1] >> 16);
    *q++ = ':';
    q = put_2(q, (s->date[1] >> 8) & 0xFF);
    *q++ = ':';
    q = put_2(q, s->date[1] & 0xFF);
    q = put_str(q, " boot_reg=");
    q = put_hex(q, s->boot_reg);
    q = put_str(q, " reset_reg=");
    q = put_hex(q, s->reset_reg);
    q = put_str(q, " exc_handlers=");
    q = put_hex(q, s->exc_state);
    if (now_ms) {
        q = put_str(q, " uptime=");
        q = put_secs(q, now_ms);
    }
    q = put_str(q, "\r\n");
    q = put_exc(q, &s->exc);
    if (s->wd.slot < 10) {
        q = put_str(q, "WATCHDOG: slot ");
        q = put_uint(q, s->wd.slot);
        q = put_str(q, " expired at ");
        q = put_secs(q, s->wd.ms);
        q = put_str(q, "; slots then:\r\n");
        q = put_slots(q, s->wd.slots, s->wd.now);
    }
    q = put_str(q, "events (");
    q = put_uint(q, s->nev);
    q = put_str(q, " total, last ");
    q = put_uint(q, n);
    q = put_str(q, "):\r\n");
    for (i = s->nev - n; i != s->nev; i++)
        q = put_event(q, &s->ev[i & (NEV - 1)]);
    return q;
}

#define TEXT_MAX 9000                   /* 64 events x <100 + header, exception, slots */

static int write_parts(const char *path, const char *a, unsigned na, const char *b, unsigned nb)
{
    FW_FILE *f = fw_fopen(path, "w");
    int ok;

    if (!f)
        return 0;
    ok = fw_fwrite(a, 1, na, f) == na && (!nb || fw_fwrite(b, 1, nb, f) == nb);
    fw_fclose(f);
    return ok;
}

static void note_write(struct bb_rt *rt, int ok)
{
    if (ok)
        rt->writes++;
    else
        rt->write_fail++;
}

/* this session's report to every medium that isn't exported (UI task) */
static void __attribute__((noinline)) write_live(int sd, int msys)
{
    struct bb_rt *rt = RT;
    char text[TEXT_MAX], *q = text;
    unsigned now = bb_ms();

    q = put_str(q, "PN-40 black box v1: live (this session) usb=");
    q = put_str(q, usb_name(fw_usb_export_state()));
    q = put_str(q, " writes=");
    q = put_uint(q, rt->writes);
    q = put_str(q, " write_failures=");
    q = put_uint(q, rt->write_fail);
    q = put_str(q, "\r\n");
    q = put_session(q, CUR, now);
    q = put_str(q, "watchdog slots now:\r\n");
    q = put_slots(q, WD_SLOTS, fw_time_now());
    if (sd)
        note_write(rt, write_parts("SD:/bbox_live.txt", text, (unsigned)(q - text), 0, 0));
    if (msys)
        note_write(rt, write_parts("MSYS2:/bbox_live.txt", text, (unsigned)(q - text), 0, 0));
}

static int sd_free(int usb)
{
    return usb != 0;
}

static int msys_free(int usb)
{
    return usb != 1;
}

/* the previous MSYS2:/bbox_live.txt, before this session overwrites it */
static void read_prev_live(struct bb_rt *rt)
{
    FW_FILE *f;
    unsigned n;

    f = fw_fopen("MSYS2:/bbox_live.txt", "r");
    if (!f)
        return;
    rt->prev_live = fw_malloc(TEXT_MAX + 512);
    if (rt->prev_live) {
        n = fw_fread(rt->prev_live, 1, TEXT_MAX + 512, f);
        rt->prev_live_len = n <= TEXT_MAX + 512 ? n : 0;
    }
    fw_fclose(f);
}

static void __attribute__((noinline)) write_prev(struct bb_rt *rt, int sd, int msys)
{
    char text[TEXT_MAX + 400], *q = text;
    const struct bb_session *p = PREV;

    q = put_str(q, "PN-40 black box v1: previous session (written by boot_no ");
    q = put_uint(q, CUR->boot_no);
    q = put_str(q, ", build ");
    q = put_hex(q, BUILD_ID);
    q = put_str(q, ")\r\nthis boot: boot_reg=");
    q = put_hex(q, CUR->boot_reg);
    q = put_str(q, " reset_reg=");
    q = put_hex(q, CUR->reset_reg);
    q = put_str(q, "\r\n\r\n--- from RAM (survives a reset, not a power-off) ---\r\n");
    if (p->magic == BB_MAGIC)
        q = put_session(q, p, 0);
    else
        q = put_str(q, "none: RAM didn't hold a session (power-off or first boot of this build)\r\n");
    q = put_str(q, "\r\n--- MSYS2:/bbox_live.txt as found at boot ---\r\n");
    if (!rt->prev_live_len)
        q = put_str(q, "(none)\r\n");
    if (sd) {
        rt->prev_sd = write_parts("SD:/bbox_prev.txt", text, (unsigned)(q - text), rt->prev_live, rt->prev_live_len);
        note_write(rt, rt->prev_sd);
    }
    if (msys) {
        rt->prev_msys = write_parts("MSYS2:/bbox_prev.txt", text, (unsigned)(q - text), rt->prev_live,
                                    rt->prev_live_len);
        note_write(rt, rt->prev_msys);
    }
}

static void heartbeat(struct bb_rt *rt, int usb)
{
    unsigned i, now = fw_time_now(), on = 0, min_slot = 0;
    int min_slack = 0x7FFFFFFF;
    const volatile unsigned char *t = WD_SLOTS;

    for (i = 0; i < 10; i++, t += 12) {
        int slack;
        if (!t[0])
            continue;
        on |= 1u << i;
        slack = (int)(*(const volatile unsigned *)(t + 4) - (now - *(const volatile unsigned *)(t + 8)));
        if (slack < min_slack) {
            min_slack = slack;
            min_slot = i;
        }
    }
    if (min_slack < 0 || min_slack == 0x7FFFFFFF)
        min_slack = 0;
    event(EV_HEART, (unsigned)usb, (unsigned)battery_mv(), udiv(rt->max_gap, TICKS_MS),
          on | min_slot << 10 | udiv((unsigned)min_slack, TICKS_MS * 100) << 16);
    rt->max_gap = 0;
}

/* UI main loop (ui_loop_tramp), every few ms */
void bbox_tick(void)
{
    struct bb_session *s = CUR;
    struct bb_rt *rt = RT;
    unsigned now, dt, q;
    int usb;

    if (s->magic != BB_MAGIC)
        return;
    now = fw_time_now();
    dt = now - rt->last_now;
    rt->last_now = now;
    if (rt->ticked && dt > rt->max_gap)
        rt->max_gap = dt;
    rt->ticked = 1;
    rt->acc += dt;
    q = udiv(rt->acc, TICKS_MS);
    rt->uptime_ms += q;
    rt->acc -= q * TICKS_MS;

    usb = fw_usb_export_state();
    if (usb != rt->usb) {
        if (rt->usb >= 0)
            event(EV_USB, (unsigned)rt->usb, (unsigned)usb, 0, 0);
        rt->usb = usb;
        rt->next_live = rt->uptime_ms + 1000;    /* first live report soon after a change */
    }
    if (!rt->prev_read) {
        if (msys_free(usb))
            read_prev_live(rt);
        rt->prev_read = 1;
    }
    if (rt->prev_read && ((!rt->prev_sd && sd_free(usb)) || (!rt->prev_msys && msys_free(usb))))
        write_prev(rt, !rt->prev_sd && sd_free(usb), !rt->prev_msys && msys_free(usb));
    if ((int)(rt->uptime_ms - rt->next_heart) >= 0) {
        rt->next_heart = rt->uptime_ms + HEART_MS;
        heartbeat(rt, usb);
    }
    if (rt->pending || (usb != 2 && (int)(rt->uptime_ms - rt->next_live) >= 0)) {
        rt->next_live = rt->uptime_ms + LIVE_MS;
        write_live(sd_free(usb), msys_free(usb));
        rt->pending = 0;
    }
}

/* after each screenshot (UI task) */
void bbox_screenshot(void)
{
    if (CUR->magic == BB_MAGIC && sd_free(fw_usb_export_state()))
        write_live(1, 0);
}

static int in_ui_task(void)
{
    char here;
    unsigned sp = (unsigned)&here;
    return sp >= UI_STACK_LO && sp < UI_STACK_HI;
}

/* entry of the shutdown routine 0x3008423C (shutdown_tramp): param 0 = power
 * off, 1 = reboot. Record it and get the report out before the firmware
 * turns things off. */
void bbox_shutdown(int param, unsigned caller)
{
    struct bb_rt *rt = RT;
    int ui = in_ui_task(), k, usb;

    if (CUR->magic != BB_MAGIC)
        return;
    event(EV_SHUTDOWN, (unsigned)param & 0xFF, caller, (unsigned)battery_mv(), (unsigned)ui);
    usb = fw_usb_export_state();
    if (ui) {
        write_live(sd_free(usb), msys_free(usb));
        return;
    }
    /* file I/O belongs to the UI task: ask it, wait up to 2 s */
    rt->pending = 1;
    for (k = 0; k < 20 && rt->pending; k++)
        fw_task_delay(fw_ticks_per_sec() / 10);
}

/* entry of 0x301A869C (watchdog_reset_now; wdreset_tramp) */
void bbox_reset(int param, unsigned caller)
{
    if (CUR->magic == BB_MAGIC)
        event(EV_RESET, (unsigned)param, caller, 0, 0);
}

/* watchdog task 0x301A88FC: a slot expired, the unit resets next. Runs on
 * the watchdog task's 1 KB stack: keep it small, RAM only. */
void bbox_watchdog(unsigned slot_ptr)
{
    struct bb_session *s = CUR;
    unsigned slot;

    if (s->magic != BB_MAGIC)
        return;
    slot = udiv(slot_ptr - (unsigned)WD_SLOTS, 12);
    s->wd.ms = bb_ms();
    s->wd.now = fw_time_now();
    s->wd.raw_ptr = slot_ptr;
    copy(s->wd.slots, (const void *)WD_SLOTS, sizeof s->wd.slots);
    s->wd.slot = slot;
    event(EV_WATCHDOG, slot, 0, 0, 0);
}
