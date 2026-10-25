/* NMEA logger: copies every sentence the firmware's NMEA engine produces to
 * SD:/nmea/<date>_<time>.nmea, one file per power-on.
 *
 * On/off switch: logging happens only while a folder named "nmea" exists in
 * the root of the SD card. Delete or rename it to stop logging.
 *
 * Two contexts:
 *   nmealog_append()  NMEA output or input task (prio 8, 1.9 KB stack), always
 *                     with the firmware's NMEA mutex held: only copies into
 *                     the RAM ring, never blocks.
 *   nmealog_ui_tick() UI task (prio 3, 64 KB stack), from its main loop:
 *                     every FLUSH_SECS appends the ring to the file with
 *                     open/write/close, so a power-off loses at most that much,
 *                     and rewrites status.txt.
 * The ring is single-producer/single-consumer: head is written only under
 * the NMEA mutex, tail only by the UI task.
 */
#include "fw.h"

#define MAGIC        0x4E4D4541u        /* "NMEA" */
#define RING_SIZE    0x4000u            /* 16 KB, about 35 s of output */
#define FLUSH_SECS   10u
#define DIR          "SD:/nmea"

struct state {
    unsigned magic;
    volatile unsigned head;             /* next byte the NMEA task writes */
    volatile unsigned tail;             /* next byte the UI task writes to SD */
    unsigned last_flush;                /* fw_time_now() of the last flush check */
    unsigned started;
    unsigned prev_now;                  /* fw_time_now() at the previous UI tick */
    unsigned sub_ticks;                 /* uptime remainder, < 1 s */
    unsigned uptime_s;                  /* since the first UI event */

    /* counters, reported in status.txt */
    unsigned sentences, bytes_in, dropped;
    unsigned ui_ticks, checks, flushes, bytes_out, open_err, write_err, export_skips, nodir;
    unsigned status_writes;
    int last_export;                    /* last fw_usb_export_state() result */
    int last_stat;                      /* last fw_stat(DIR) result */
    unsigned status_fail;               /* status.txt could not be opened */
    unsigned screenshots;

    char path[64];
    char statbuf[128];
    char text[512];
};

extern char LOGGER_RAM[], LOGGER_RAM_END[];
#define ST   ((struct state *)LOGGER_RAM)
#define RING (LOGGER_RAM + 0x400)

typedef char state_fits[(sizeof(struct state) <= 0x400) ? 1 : -1];

/* GCC may emit calls to these even in freestanding code */
void *memset(void *d, int c, unsigned n)
{
    char *p = d;
    while (n--)
        *p++ = (char)c;
    return d;
}

void *memcpy(void *d, const void *s, unsigned n)
{
    char *p = d;
    const char *q = s;
    while (n--)
        *p++ = *q++;
    return d;
}

static unsigned used(unsigned head, unsigned tail)
{
    return head >= tail ? head - tail : RING_SIZE - tail + head;
}

void nmealog_init(void)
{
    if ((unsigned)(LOGGER_RAM_END - LOGGER_RAM) < 0x400 + RING_SIZE)
        return;
    memset(ST, 0, sizeof *ST);
    ST->magic = MAGIC;
}

void nmealog_append(const char *buf, unsigned len)
{
    struct state *st = ST;
    unsigned head, tail, n;

    if (st->magic != MAGIC || len == 0 || len >= RING_SIZE)
        return;
    head = st->head;
    tail = st->tail;
    st->sentences++;
    st->bytes_in += len;
    /* keep sentences whole: drop the entire sentence if it doesn't fit */
    if (len > RING_SIZE - 1 - used(head, tail)) {
        st->dropped += len;
        return;
    }
    n = RING_SIZE - head;
    if (n > len)
        n = len;
    memcpy(RING + head, buf, n);
    memcpy(RING, buf + n, len - n);
    head += len;
    if (head >= RING_SIZE)
        head -= RING_SIZE;
    st->head = head;
}

/* --- text helpers ------------------------------------------------------- */

static char *put_str(char *p, const char *s)
{
    while (*s)
        *p++ = *s++;
    return p;
}

/* v / 10 without libgcc (exact for all 32-bit v) */
static unsigned div10(unsigned v)
{
    return (unsigned)(((unsigned long long)v * 0xCCCCCCCDu) >> 35);
}

static char *put_uint(char *p, unsigned v, int width)
{
    char tmp[10];
    int n = 0;
    do {
        unsigned q = div10(v);
        tmp[n++] = (char)('0' + (v - q * 10));
        v = q;
    } while (v);
    while (n < width)
        tmp[n++] = '0';
    while (n)
        *p++ = tmp[--n];
    return p;
}

static char *put_field(char *p, const char *name, unsigned v)
{
    p = put_str(p, name);
    *p++ = '=';
    p = put_uint(p, v, 1);
    *p++ = '\r';
    *p++ = '\n';
    return p;
}

static void make_path(struct state *st)
{
    int mon = 0, day = 0, year = 0, hour = 0, min = 0, sec = 0;
    char *p;

    fw_get_datetime(&mon, &day, &year, &hour, &min, &sec);
    p = put_str(st->path, DIR "/");
    p = put_uint(p, 2000u + (unsigned)year, 4);
    *p++ = '-';
    p = put_uint(p, (unsigned)mon, 2);
    *p++ = '-';
    p = put_uint(p, (unsigned)day, 2);
    *p++ = '_';
    p = put_uint(p, (unsigned)hour, 2);
    *p++ = '-';
    p = put_uint(p, (unsigned)min, 2);
    *p++ = '-';
    p = put_uint(p, (unsigned)sec, 2);
    p = put_str(p, ".nmea");
    *p = 0;
}

static void write_status(struct state *st, const char *path)
{
    FW_FILE *f;
    char *p = st->text;

    p = put_str(p, "PN-40 NMEA logger v3\r\nfile=");
    p = put_str(p, st->path);
    p = put_str(p, "\r\n");
    p = put_field(p, "uptime_s", st->uptime_s);
    p = put_field(p, "ui_ticks", st->ui_ticks);
    p = put_field(p, "checks", st->checks);
    p = put_field(p, "sentences", st->sentences);
    p = put_field(p, "bytes_in", st->bytes_in);
    p = put_field(p, "buffered", used(st->head, st->tail));
    p = put_field(p, "dropped", st->dropped);
    p = put_field(p, "bytes_out", st->bytes_out);
    p = put_field(p, "flushes", st->flushes);
    p = put_field(p, "open_err", st->open_err);
    p = put_field(p, "write_err", st->write_err);
    p = put_field(p, "export_skips", st->export_skips);
    p = put_field(p, "nodir", st->nodir);
    p = put_field(p, "usb_state", (unsigned)st->last_export);
    p = put_field(p, "stat_rc", (unsigned)st->last_stat);
    p = put_field(p, "status_fail", st->status_fail);
    p = put_field(p, "screenshots", st->screenshots);
    p = put_field(p, "ticks_per_sec", fw_ticks_per_sec());
    p = put_field(p, "status_writes", st->status_writes + 1);

    f = fw_fopen(path, "w");
    if (!f) {
        st->status_fail++;
        return;
    }
    fw_fwrite(st->text, 1, (unsigned)(p - st->text), f);
    fw_fclose(f);
    st->status_writes++;
}

static void write_data(struct state *st, unsigned head)
{
    unsigned tail = st->tail, n, w;
    FW_FILE *f;

    if (!st->path[0])
        make_path(st);
    f = fw_fopen(st->path, "a");
    if (!f) {
        st->open_err++;
        return;
    }
    while (tail != head) {
        n = head > tail ? head - tail : RING_SIZE - tail;
        w = fw_fwrite(RING + tail, 1, n, f);
        if (w > n)
            w = 0;
        st->bytes_out += w;
        tail += w;
        if (tail >= RING_SIZE)
            tail -= RING_SIZE;
        st->tail = tail;
        if (w != n) {
            st->write_err++;            /* keep the rest; try again next time */
            break;
        }
    }
    fw_fclose(f);
    st->flushes++;
}

/* every FLUSH_SECS: append buffered sentences, then rewrite status.txt */
static void flush(struct state *st)
{
    unsigned head = st->head;

    st->checks++;
    st->last_export = fw_usb_export_state();
    if (st->last_export == 0) {         /* the PC owns the SD card */
        st->export_skips++;
        return;
    }
    st->last_stat = fw_stat(DIR, st->statbuf);
    if (st->last_stat != 0) {           /* logging switched off, or no card */
        st->nodir++;
        st->tail = head;
        return;
    }
    if (head != st->tail)
        write_data(st, head);
    write_status(st, DIR "/status.txt");
}

/* After each screenshot (UI task; the screenshot code has just checked the
 * card and created SD:/screenshots): flush now, then write the diagnostics
 * next to the picture, whether or not SD:/nmea exists. */
void nmealog_screenshot(void)
{
    struct state *st = ST;
    FW_FILE *f;
    char msg[96], *p;

    if (st->magic != MAGIC) {
        p = put_str(msg, "PN-40 NMEA logger v3: not initialised, magic=");
        p = put_uint(p, st->magic, 1);
        p = put_str(p, "\r\n");
        f = fw_fopen("SD:/screenshots/nmealog.txt", "w");
        if (f) {
            fw_fwrite(msg, 1, (unsigned)(p - msg), f);
            fw_fclose(f);
        }
        return;
    }
    st->screenshots++;
    flush(st);
    write_status(st, "SD:/screenshots/nmealog.txt");
}

void nmealog_ui_tick(void)
{
    struct state *st = ST;
    unsigned now, interval, second;

    if (st->magic != MAGIC)
        return;
    st->ui_ticks++;
    now = fw_time_now();
    if (!st->started) {
        st->started = 1;
        st->last_flush = st->prev_now = now;
        return;
    }
    second = fw_ticks_per_sec();
    st->sub_ticks += now - st->prev_now;
    st->prev_now = now;
    while (st->sub_ticks >= second) {
        st->sub_ticks -= second;
        st->uptime_s++;
    }
    interval = FLUSH_SECS * second;
    if (now - st->last_flush < interval && used(st->head, st->tail) < RING_SIZE / 2)
        return;
    st->last_flush = now;
    flush(st);
}
