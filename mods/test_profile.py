"""Emulator test for the elevation profile page (profile.c), run against the
built out\\PN-40.hex.

Recording: elevation samples go through the real patched trip_update call
site (0x300C623C) and records through the real UI main loop site
(0x30085EE4), with the trip odometer set in the trip struct.

Page: the real firmware sets up graphics (gfx_init with the RAM .data image
loaded), page_init(9) and page_show(9) run with the patched page descriptor
and pane table, and the screen buffer is compared and saved as PNG
(out\\profile_*.png) for a look.

usage: python test_profile.py [out dir]
"""
import os
import struct
import sys
import zlib

import unicorn
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_CPSR

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_nmealog import SYMS, TICKS_PER_S, OUT
from test_ascent import AscentDevice, TRIP, PREFS

PROFILE_RAM = 0x0076FA40
RING = 0x0076FB00
NREC = 3600
GAP = -32768
UI_LOOP_CALL = 0x30085EE4
PAGE_OBJS = 0x00043164
PAGE_DESC9 = 0x003F0C54 + 9 * 24
RAM_DATA = 0x003F0C14
DATA_IMAGE = 0x3044822C               # flash source of RAM_DATA (copied at boot)
SCREEN_WIN_PTR = 0x000E435C
SCREEN_BUF = 0x0010A068
PAGE_INIT, PAGE_SHOW, PAGE_HIDE = 0x300937CC, 0x30093604, 0x3009351C
GFX_INIT = 0x300C5BF0
LCD_INIT = 0x30428B10                  # Thumb: panel power-up, needs real hardware
HANDLE_RESULT = 0x300E3B70
KEY_ZOOM_IN, KEY_ZOOM_OUT, KEY_ENTER, KEY_MENU = 0x3ED, 0x3EE, 0x3F1, 0x3EF


class ProfileDevice(AscentDevice):
    def __init__(self, graphics=False):
        super().__init__()
        self.odo = 0.0
        self.flushes = 0
        self.mu.hook_add(unicorn.UC_HOOK_CODE, self._flush_req, begin=HANDLE_RESULT, end=HANDLE_RESULT)
        if graphics:
            # what the boot code does: copy .data from its image in (patched) flash
            n = os.path.getsize(os.path.join(HERE, "..", "re", "ram_data.bin"))
            self.mu.mem_write(RAM_DATA, bytes(self.mu.mem_read(DATA_IMAGE, n)))
            self.mu.mem_map(0x38000000, 0x40000)          # panel command/data ports
            self.mu.hook_add(unicorn.UC_HOOK_CODE, self._thumb_ret, begin=LCD_INIT, end=LCD_INIT)
            self.call(GFX_INIT)                           # font tables + screen window; lcd_init skipped

    def _thumb_ret(self, uc, addr, size, _):
        lr = uc.reg_read(UC_ARM_REG_LR)
        if not lr & 1:                                    # back to ARM code
            uc.reg_write(UC_ARM_REG_CPSR, uc.reg_read(UC_ARM_REG_CPSR) & ~0x20)
        uc.reg_write(UC_ARM_REG_PC, lr & ~1)

    def _flush_req(self, uc, addr, size, _):
        if struct.unpack("<I", bytes(uc.mem_read(self.r(1), 4)))[0] == 0x7D4:
            self.flushes += 1

    def ps(self):
        names = ("magic head count started next_time sum_cm nsum last_cm have_sample have_odo last_odo mode "
                 "zoom0 zoom1 dirty records gaps draws").split()
        return dict(zip(names, struct.unpack("<5I5iI4i3I", bytes(self.mu.mem_read(PROFILE_RAM, 4 * len(names))))))

    def ring(self):
        st = self.ps()
        recs = []
        for k in range(st["count"]):
            i = (st["head"] - st["count"] + k) % NREC
            recs.append(struct.unpack("<hH", bytes(self.mu.mem_read(RING + 4 * i, 4))))
        return recs

    def tick(self, dt=0.0):
        self.now = (self.now + int(dt * TICKS_PER_S)) & 0xFFFFFFFF
        self.mu.mem_write(TRIP + 0x30, struct.pack("<d", self.odo))
        self.ui_event()

    def walk(self, seconds, elev_fn, speed=0.0, fix=True):
        """seconds of 1 Hz samples (elev_fn(t) metres), UI ticks every second"""
        for t in range(seconds):
            self.odo += speed
            self.mu.mem_write(TRIP + 0x30, struct.pack("<d", self.odo))
            if fix:
                self.sample(elev_fn(t), dt=1)
            else:
                self.now = (self.now + TICKS_PER_S) & 0xFFFFFFFF
            self.ui_event()

    # page ------------------------------------------------------------------
    def page_setup(self):
        obj = 0x00180800
        self.mu.mem_write(obj, b"\0" * 0x40)
        self.mu.mem_write(PAGE_OBJS + 9 * 4, struct.pack("<I", obj))
        self.call(PAGE_INIT, (obj, 9))
        self.obj = obj
        return obj

    def show(self):
        win = struct.unpack("<I", bytes(self.mu.mem_read(SCREEN_WIN_PTR, 4)))[0]
        self.call(PAGE_SHOW, (self.obj, win))

    def event(self, code, param=0):
        res, ev = 0x00180000, 0x00180010
        self.mu.mem_write(ev, struct.pack("<II", code, param))
        handler = struct.unpack("<I", bytes(self.mu.mem_read(PAGE_DESC9 + 0x14, 4)))[0]
        self.call(handler, (res, ev, 9))
        return struct.unpack("<II", bytes(self.mu.mem_read(res, 8)))

    def screen(self):
        return bytes(self.mu.mem_read(SCREEN_BUF, 176 * 220 * 2))

    def save_png(self, name):
        px = struct.unpack("<%dH" % (176 * 220), self.screen())
        raw = bytearray()
        for y in range(220):
            raw.append(0)
            for x in range(176):
                c = px[y * 176 + x]
                raw += bytes((((c >> 11) & 31) * 255 // 31, ((c >> 5) & 63) * 255 // 63, (c & 31) * 255 // 31))

        def chunk(t, d):
            return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 176, 220, 8, 2, 0, 0, 0)) + \
            chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b"")
        path = os.path.join(OUT, name)
        open(path, "wb").write(png)
        return path


def fresh(graphics=False):
    d = ProfileDevice(graphics)
    d.mu.mem_write(TRIP, b"\0" * 0x6C)
    d.boot()
    d.call(0x300C5F54, (TRIP,))                # trip_reset_all
    return d


def px(d, x, y):
    return struct.unpack_from("<H", d.screen(), 2 * (y * 176 + x))[0]


def theme(d, idx):
    return d.call(0x300D0090, (PREFS, idx))


def run_tests():
    ok = 0

    # --- patches: page 9 descriptor (via .data), name, widget classes ---------
    d = fresh(graphics=True)
    desc = struct.unpack("<6I", bytes(d.mu.mem_read(PAGE_DESC9, 24)))
    assert desc[0] == 9 and desc[1] == 1, "still a PAGE-cycle page"
    assert desc[4] == 0 and desc[5] == SYMS["profile_page"], [hex(x) for x in desc]
    name = struct.unpack("<I", bytes(d.mu.mem_read(0x3011F52C, 4)))[0]
    assert d.cstr(name) == "Elev. Profile"
    tab = struct.unpack("<164I", bytes(d.mu.mem_read(SYMS["pane_table"], 164 * 4)))
    assert all(tab[i] == SYMS["profile_pane_desc"] for i in range(84, 90))
    assert tab[83] == 0x00053B70 + 16 * 83 and tab[90] != SYMS["profile_pane_desc"]
    st = d.ps()
    assert st["magic"] == 0x50524F46 and st["count"] == 0 and (st["zoom0"], st["zoom1"]) == (1, 1)
    ok += 1; print("ok  page 9 -> profile_page (menu none), named \"Elev. Profile\", widgets 84-89 blank")

    # --- recording: one record per 12 s = average of the samples --------------
    d = fresh()
    d.tick()                                          # starts the 12 s clock
    for t in range(1, 13):
        d.sample(100.0 + t, dt=1)
        d.tick()
    assert d.ring() == [(213, 0)], d.ring()           # mean 106.5 m in half metres
    for t in range(24):                               # 24 s without a fix
        d.tick(dt=1)
    assert d.ring()[1:] == [(GAP, 0), (GAP, 0)] and d.ps()["gaps"] == 2
    for t in range(12):
        d.sample(-50.25, dt=1)
        d.tick()
    assert d.ring()[-1] == (-101, 0), d.ring()        # negative, rounded away from zero
    for t in range(12):                               # more than 16 samples in one record: first 16
        d.sample(200.0, dt=0.5)
        d.sample(200.0, dt=0.5)
        d.tick()
    assert d.ring()[-1] == (400, 0), d.ring()
    ok += 1; print("ok  records every 12 s: averaged samples, gaps without a fix, negatives")

    # --- distance from the trip odometer ----------------------------------------
    d = fresh()
    d.tick()
    d.walk(36, lambda t: 500.0, speed=1.5)            # 3 records
    assert [r[1] for r in d.ring()] == [0, 18, 18], d.ring()   # first record has no previous odometer
    d.odo = 0.0                                       # trip reset
    d.walk(12, lambda t: 500.0)
    d.walk(12, lambda t: 500.0, speed=2.0)
    d.odo += 250000.0                                 # implausible jump
    d.walk(12, lambda t: 500.0)
    d.walk(12, lambda t: 500.0, speed=1.0)
    assert [r[1] for r in d.ring()[3:]] == [0, 24, 0, 12], d.ring()
    ok += 1; print("ok  distance per record from the odometer; trip reset and jumps ignored")

    # --- UI task held up: gaps for the missed intervals, cadence kept -----------
    d = fresh()
    d.tick()
    d.tick(dt=12)
    d.tick(dt=60)                                     # 60 s late: 1 record + 4 gaps
    assert len(d.ring()) == 6 and d.ps()["gaps"] == 6
    d.tick(dt=11.9)
    assert len(d.ring()) == 6
    d.tick(dt=0.2)
    assert len(d.ring()) == 7
    n = d.ps()["records"]
    d.tick(dt=600)                                    # 10 min late (the 32-bit timer allows < 11)
    assert d.ps()["records"] == n + 50
    d.tick(dt=11.8)                                   # next one due 11.9 s after the catch-up
    assert d.ps()["records"] == n + 50
    d.tick(dt=0.2)
    assert d.ps()["records"] == n + 51
    ok += 1; print("ok  late ticks: gaps fill the missed intervals, cadence kept")

    # --- ring wraps: last 3600 records kept, in order ----------------------------
    d = fresh()
    d.tick()
    for k in range(4000):
        d.sample(k / 2.0, dt=0)                        # one sample per record: k half metres
        d.tick(dt=12)
    recs = d.ring()
    assert len(recs) == NREC and [r[0] for r in recs] == list(range(400, 4000)), recs[:3]
    ok += 1; print("ok  ring wraps after 12 h, keeping the newest 3600 records in order")

    # --- inert before init -------------------------------------------------------
    d = ProfileDevice()                                 # no boot: mod RAM is random
    d.mu.mem_write(PROFILE_RAM, b"\0" * 0xC0)
    d.mu.mem_write(RING, b"\xAB" * 64)
    d.tick(); d.tick(dt=30)
    assert bytes(d.mu.mem_read(PROFILE_RAM, 0xC0)) == b"\0" * 0xC0
    assert bytes(d.mu.mem_read(RING, 64)) == b"\xAB" * 64
    ok += 1; print("ok  recording inert until boot init")

    # --- the page, through page_init / page_show ------------------------------------
    import math
    d = fresh(graphics=True)
    d.page_setup()
    d.walk(600, lambda t: 1200.0, fix=False)                      # 10 min without a fix
    d.walk(3000, lambda t: 300 + 80 * math.sin(t / 400.0) + t / 30.0, speed=1.2)
    d.walk(400, lambda t: 400.0, fix=False)
    d.walk(900, lambda t: 400 - t / 10.0, speed=1.5)
    d.show()
    assert d.ps()["draws"] == 1 and d.ps()["dirty"] == 0
    assert px(d, 1, 1) == theme(d, 4), "header"
    fill = sum(1 for x in range(36, 172) for y in range(27, 177) if px(d, x, y) == theme(d, 1))
    assert fill > 2000, fill
    time_view = d.save_png("profile_time.png")
    t_screen = d.screen()
    ok += 1; print("ok  page_show draws the profile (header, plot area filled: %d px)" % fill)

    # --- keys --------------------------------------------------------------------------
    f0 = d.flushes
    assert d.event(KEY_ENTER) == (0xB, 0) and d.ps()["mode"] == 1 and d.flushes == f0 + 1
    assert d.screen() != t_screen
    d.save_png("profile_dist.png")
    for _ in range(5):
        assert d.event(KEY_ZOOM_OUT) == (0xB, 0)
    assert d.ps()["zoom1"] == 3 and d.ps()["zoom0"] == 1
    for _ in range(5):
        d.event(KEY_ZOOM_IN)
    assert d.ps()["zoom1"] == 0
    d.event(KEY_ZOOM_OUT)
    assert d.event(0x3E9) == (10, 0) and d.event(KEY_MENU) == (10, 0), "other keys go on to the firmware"
    opened = []
    d.mu.hook_add(unicorn.UC_HOOK_CODE, lambda uc, a, s, u: opened.append(a), begin=0x3009B994, end=0x3009B994)
    d.mu.mem_write(0x00180010, struct.pack("<II", KEY_MENU, 0))
    d.call(0x300926F0, (0x00180000, d.obj, 0x00180010))           # page_menu_key
    assert struct.unpack("<II", bytes(d.mu.mem_read(0x00180000, 8))) == (0xB, 0) and not opened
    d.mu.mem_write(PREFS + 0x90, struct.pack("<I", 1))           # metric
    d.event(KEY_ZOOM_OUT)
    d.save_png("profile_dist_metric.png")
    ok += 1; print("ok  ENTER switches time/distance, ZOOM spans clamp, MENU opens nothing")

    # --- redraw rules ----------------------------------------------------------------
    n = d.ps()["draws"]
    d.event(2)
    assert d.ps()["draws"] == n, "periodic event without a new record: no redraw"
    d.walk(12, lambda t: 300.0)
    assert d.ps()["dirty"] == 1
    f0 = d.flushes
    assert d.event(2) == (10, 0) and d.ps()["draws"] == n + 1 and d.flushes == f0 + 1
    d.event(0x7D6)
    assert d.ps()["draws"] == n + 2
    d.mu.mem_write(d.obj + 0x3C, b"\0")                           # page hidden
    d.walk(12, lambda t: 300.0)
    d.event(2)
    d.event(KEY_ENTER)
    assert d.ps()["draws"] == n + 2, "nothing drawn while the page is hidden"
    ok += 1; print("ok  redraw on a new record / settings change, never while hidden")

    # --- other themes, empty page --------------------------------------------------------
    d.mu.mem_write(d.obj + 0x3C, b"\1")
    d.mu.mem_write(PREFS + 0x38, struct.pack("<I", 4))           # Midnight
    d.event(KEY_ENTER)
    d.event(KEY_ZOOM_OUT)
    assert px(d, 1, 1) == theme(d, 4) == 0x033F
    d.save_png("profile_midnight.png")
    e = fresh(graphics=True)
    e.page_setup()
    e.show()
    assert e.ps()["draws"] == 1 and e.ps()["count"] == 0
    e.save_png("profile_empty.png")
    ok += 1; print("ok  Midnight theme and empty page render (PNGs in %s)" % os.path.dirname(time_view))

    print("\nall %d tests passed" % ok)


if __name__ == "__main__":
    run_tests()
