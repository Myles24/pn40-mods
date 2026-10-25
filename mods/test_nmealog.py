"""Emulator test for the NMEA logger, run against the built out\\PN-40.hex.

Executes the real patched firmware entry points (NMEA_start call literal,
nmea_send, ui_dispatch) and the mod code under unicorn. Firmware services
the mod uses (file API, clock, USB state, OS20 mutex, NMEA stream write) are
replaced by Python stubs that model an SD card.

usage: python test_nmealog.py [out dir]
"""
import os
import struct
import sys

import unicorn
from unicorn.arm_const import *

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pnhex

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "out")
SYMS = {}
for line in open(os.path.join(OUT, "symbols.txt")):
    a, n = line.split()
    SYMS[n] = int(a, 16)

TICKS_PER_S = 3250000                  # 26 MHz / 8
RAM_STATE = 0x0076B600
RING = RAM_STATE + 0x400
RING_SIZE = 0x4000

FW = dict(fopen=0x30187E80, fwrite=0x30187904, fclose=0x30187DB8, stat=0x3018902C, time_now=0x301FA9A0,
          get_datetime=0x300B966C, usb_export_state=0x300D0050, nmea_start=0x301518E4,
          mutex_lock=0x301FBB4C, mutex_unlock=0x301FB088, stream_write=0x301742C4,
          ticks_per_sec=0x301FA838, screenshot=0x30084B4C)
UI_LOOP_CALL = 0x30085EE4          # "bl time_now" in the UI task main loop
SCREENSHOT_CALL = 0x30086434       # "bl take_screenshot" in the key handler
REGS = [UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5,
        UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11]
RET = 0x00010000           # magic return address
STACK_TOP = 0x00700000


class Device:
    def __init__(self):
        self.mu = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_ARM)
        base, img = pnhex.image(open(os.path.join(OUT, "PN-40.hex"), "rb").read())
        self.mu.mem_map(0x30000000, 0x800000)
        self.mu.mem_write(0x30000000, b"\xff" * 0x800000)
        self.mu.mem_write(base, bytes(img))
        self.mu.mem_map(0, 0x800000)                 # SDRAM
        self.mu.mem_map(0x10100000, 0x100000)        # peripherals as plain memory (sysinfo dump, gfx)
        self.mu.mem_map(0xA0000000, 0x1000)          # internal RAM with the second vector table
        self.mu.hook_add(unicorn.UC_HOOK_INTR, self._swi)
        self.swis = []
        self.mallocs = []
        self.heap = 0x00500000
        self.mu.hook_add(unicorn.UC_HOOK_CODE, self._malloc, begin=0x30189890, end=0x30189890)
        self.stress_results = [0x5EED5EED]      # canned (the real worker is tested in test_prof); None = run it
        if "stress_pass" in SYMS:
            self.mu.hook_add(unicorn.UC_HOOK_CODE, self._stress, begin=SYMS["stress_pass"],
                             end=SYMS["stress_pass"])
        # prof.c's CPU clock loops: skipped, taking as long as on a cpu_hz CPU
        self.cpu_hz = 117000000
        for name, cycles in (("cpu_loop16", 20), ("cpu_loop48", 52)):
            if name in SYMS:
                self.mu.hook_add(unicorn.UC_HOOK_CODE, self._cpu_loop, begin=SYMS[name], end=SYMS[name],
                                 user_data=cycles)
        self.mu.mem_write(0x003F5170, struct.pack("<I", 26))
        self.mu.mem_write(RAM_STATE, os.urandom(0x8000))   # heap leftovers before init
        self.now = 0x7FFFFF00                        # near a wrap on purpose
        self.files = {}
        self.dirs = {"SD:/nmea"}
        self.exported = False
        self.fopen_fail = False
        self.short_write = False
        self.open_handles = {}
        self.stream = []
        self.nmea_start_args = None
        self.datetime = (10, 24, 26, 16, 41, 27)     # mon, day, year-2000, h, m, s
        self.c_entry_sp = []
        self.screenshots_taken = 0
        self.locked = False
        self.appends = []
        for name, addr in FW.items():
            self.mu.hook_add(unicorn.UC_HOOK_CODE, self._stub, begin=addr, end=addr, user_data=name)
        for name in ("nmealog_init", "nmealog_append", "nmealog_ui_tick"):
            self.mu.hook_add(unicorn.UC_HOOK_CODE, self._c_entry, begin=SYMS[name], end=SYMS[name])
        self.mu.hook_add(unicorn.UC_HOOK_MEM_WRITE, self._nor_write, begin=0x30000000, end=0x307FFFFF)

    def _malloc(self, uc, addr, size, _):
        n = self.r(0)
        self.mallocs.append(n)
        p = self.heap
        self.heap += (n + 7) & ~7
        uc.mem_write(p, os.urandom(n))          # not zeroed, like the real heap
        self._ret(p)

    def _stress(self, uc, addr, size, _):
        if self.stress_results is not None:
            self._ret(self.stress_results.pop(0) if len(self.stress_results) > 1 else self.stress_results[0])

    def _swi(self, uc, intno, _):
        """OS20's SWI 0/1: set/clear the caller's IRQ mask (handler 0x301FC798)"""
        assert intno == 2, "unexpected exception %d" % intno
        pc = uc.reg_read(UC_ARM_REG_PC)
        num = struct.unpack("<I", bytes(uc.mem_read(pc - 4, 4)))[0] & 0xFFFFFF
        assert num in (0, 1), "unexpected SWI %d" % num
        self.swis.append(num)
        cpsr = uc.reg_read(UC_ARM_REG_CPSR)
        uc.reg_write(UC_ARM_REG_CPSR, cpsr | 0x80 if num == 0 else cpsr & ~0x80)

    def _cpu_loop(self, uc, addr, size, cycles):
        self.now = (self.now + cycles * self.r(0) * TICKS_PER_S // self.cpu_hz) & 0xFFFFFFFF
        self._ret(0)

    def _nor_write(self, uc, access, addr, size, value, _):
        raise AssertionError("write to NOR at %08X" % addr)

    def _c_entry(self, uc, addr, size, _):
        self.c_entry_sp.append(uc.reg_read(UC_ARM_REG_SP))
        if addr == SYMS["nmealog_append"]:
            assert self.locked, "nmealog_append called without the NMEA mutex"
            self.appends.append(bytes(uc.mem_read(self.r(0), self.r(1))))

    def r(self, i):
        return self.mu.reg_read(REGS[i])

    def cstr(self, a):
        return bytes(self.mu.mem_read(a, 256)).split(b"\0")[0].decode()

    def _ret(self, v=0):
        self.mu.reg_write(UC_ARM_REG_R0, v & 0xFFFFFFFF)
        self.mu.reg_write(UC_ARM_REG_PC, self.mu.reg_read(UC_ARM_REG_LR))

    def _stub(self, uc, addr, size, name):
        r0, r1, r2, r3 = (self.r(i) for i in range(4))
        sp = uc.reg_read(UC_ARM_REG_SP)
        if name == "fopen":
            path, mode = self.cstr(r0), self.cstr(r1)
            d = path.rsplit("/", 1)[0]
            if self.exported or self.fopen_fail or d not in self.dirs:
                return self._ret(0)
            h = 0x100 + len(self.open_handles) + 1
            if mode == "w" or path not in self.files:
                self.files[path] = bytearray()
            assert mode in ("a", "w"), mode
            self.open_handles[h] = path
            return self._ret(h)
        if name == "fwrite":
            assert r1 == 1 and r3 in self.open_handles
            n = r2 // 2 if self.short_write else r2
            self.files[self.open_handles[r3]] += bytes(uc.mem_read(r0, n))
            return self._ret(n)
        if name == "fclose":
            assert r0 in self.open_handles, "fclose of unknown handle"
            del self.open_handles[r0]
            return self._ret(0)
        if name == "stat":
            return self._ret(0 if (self.cstr(r0) in self.dirs and not self.exported) else -1)
        if name == "time_now":
            return self._ret(self.now)
        if name == "get_datetime":
            for ptr, v in zip((r0, r1, r2, r3, *struct.unpack("<II", bytes(uc.mem_read(sp, 8)))), self.datetime):
                uc.mem_write(ptr, struct.pack("<i", v))
            return self._ret(0)
        if name == "usb_export_state":
            return self._ret(0 if self.exported else 2)
        if name == "nmea_start":
            self.nmea_start_args = (r0, r1, r2, r3, struct.unpack("<I", bytes(uc.mem_read(sp, 4)))[0], sp,
                                    uc.reg_read(UC_ARM_REG_LR))
            return self._ret(0)
        if name in ("mutex_lock", "mutex_unlock"):
            assert r0 == 0x003EBFCC
            assert self.locked == (name == "mutex_unlock"), "unbalanced NMEA mutex"
            self.locked = name == "mutex_lock"
            return self._ret(0)
        if name == "stream_write":
            self.stream.append(bytes(uc.mem_read(r1, r2)))
            return self._ret(r2)
        if name == "ticks_per_sec":
            return self._ret(TICKS_PER_S)
        if name == "screenshot":         # the real one checks the card and creates the folder
            self.screenshots_taken += 1
            self.dirs.add("SD:/screenshots")
            return self._ret(0)

    def call(self, addr, args=(), stack_args=(), misalign=True):
        sp = STACK_TOP - (4 if misalign else 0) - 4 * len(stack_args)
        for k, v in enumerate(stack_args):
            self.mu.mem_write(sp + 4 * k, struct.pack("<I", v))
        for i, v in enumerate(args):
            self.mu.reg_write(REGS[i], v)
        canaries = [0x11110000 + i for i in range(4, 12)]
        for i, v in zip(range(4, 12), canaries):
            self.mu.reg_write(REGS[i], v)
        self.mu.reg_write(UC_ARM_REG_SP, sp)
        self.mu.reg_write(UC_ARM_REG_LR, RET)
        self.mu.emu_start(addr, RET, count=5000000)
        assert self.mu.reg_read(UC_ARM_REG_SP) == sp, "stack not balanced"
        assert [self.r(i) for i in range(4, 12)] == canaries, "callee-saved registers clobbered"
        return self.r(0)

    # firmware entry points -------------------------------------------------
    def boot(self):
        tramp = struct.unpack("<I", bytes(self.mu.mem_read(0x30082884, 4)))[0]
        assert tramp == SYMS["nmea_start_tramp"]
        self.call(tramp, (0xD, 0x00080046, 1, 0), stack_args=(0xABCD0000,))

    def nmea_send(self, s):
        buf = 0x00180D58                       # the firmware's own sentence buffer
        self.mu.mem_write(buf, s)
        assert self.call(0x3014B564, (buf, len(s))) == 0

    def run_site(self, site):
        """Execute the single patched "bl" at site, as the firmware would."""
        canaries = [0x22220000 + i for i in range(4, 12)]
        for i, v in zip(range(4, 12), canaries):
            self.mu.reg_write(REGS[i], v)
        sp = STACK_TOP - 4                         # deliberately not 8-aligned
        self.mu.reg_write(UC_ARM_REG_SP, sp)
        self.mu.emu_start(site, site + 4, count=5000000)
        assert self.mu.reg_read(UC_ARM_REG_SP) == sp, "stack not balanced"
        assert [self.r(i) for i in range(4, 12)] == canaries, "callee-saved registers clobbered"
        return self.r(0)

    def ui_event(self):
        assert self.run_site(UI_LOOP_CALL) == self.now, "UI loop didn't get time_now's result"

    def screenshot(self):
        self.run_site(SCREENSHOT_CALL)

    def state(self):
        names = ("magic head tail last_flush started prev_now sub_ticks uptime_s sentences bytes_in dropped "
                 "ui_ticks checks flushes bytes_out open_err write_err export_skips nodir status_writes "
                 "last_export last_stat status_fail screenshots").split()
        return dict(zip(names, struct.unpack("<%dI" % len(names), bytes(self.mu.mem_read(RAM_STATE, 4 * len(names))))))


def sentences(n, start=0):
    out = []
    for k in range(start, start + n):
        body = b"GPGGA,%06d.00,4250.0000,N,07130.0000,W,1,08,1.0,10.0,M,-33.0,M,," % (k % 1000000)
        if k % 7 == 3:
            body = b"GPGSV,3,1,12," + b"%02d,45,123,40," % (k % 32) * 4
        cs = 0
        for c in body:
            cs ^= c
        out.append(b"$" + body + b"*%02X\r\n" % cs)
    return out


def run_tests():
    ok = 0
    # --- boot: init runs, NMEA_start gets identical args/stack/return --------
    d = Device()
    d.boot()
    a = d.nmea_start_args
    assert a[:5] == (0xD, 0x00080046, 1, 0, 0xABCD0000), a
    assert a[5] == STACK_TOP - 8 and a[6] == RET, "NMEA_start saw a different stack/return address"
    st = d.state()
    assert st["magic"] == 0x4E4D4541 and st["head"] == st["tail"] == 0 and st["sentences"] == 0
    ok += 1; print("ok  boot: logger initialised, NMEA_start called transparently")

    # --- sentences go to the ring AND still to the firmware's NMEA port -------
    d.mu.mem_write(0x003F2A4C, b"\x01")                          # port A enabled
    d.mu.mem_write(0x00180D38, struct.pack("<I", 0x12345))       # its stream handle
    sent = sentences(40)
    for s in sent:
        d.nmea_send(s)
    assert d.stream == sent, "original nmea_send output changed"
    assert d.appends == sent, "logger saw different buf/len than nmea_send got"
    assert not d.locked
    assert d.state()["sentences"] == 40
    assert all(sp % 8 == 0 for sp in d.c_entry_sp), "C code entered with misaligned stack"
    ok += 1; print("ok  nmea_send: copied to ring, firmware output unchanged, stack aligned for C")

    # --- first UI event only starts the timer; flush after 10 s ----------------
    d.ui_event()
    assert not d.files
    d.now = (d.now + 5 * TICKS_PER_S) & 0xFFFFFFFF
    d.ui_event()
    assert not d.files, "flushed before 10 s"
    d.now = (d.now + 6 * TICKS_PER_S) & 0xFFFFFFFF           # crosses the 32-bit wrap
    d.ui_event()
    log = "SD:/nmea/2026-10-24_16-41-27.nmea"
    assert set(d.files) == {log, "SD:/nmea/status.txt"}, d.files.keys()
    assert bytes(d.files[log]) == b"".join(sent)
    assert not d.open_handles, "file left open"
    status = d.files["SD:/nmea/status.txt"].decode()
    assert "file=" + log in status and "sentences=40" in status and "bytes_out=%d" % len(b"".join(sent)) in status
    ok += 1; print("ok  ui tick: flush after 10 s (across timer wrap), file name, status.txt")

    # --- many cycles: ring wraps, appends stay in order -------------------------
    expect = bytearray(d.files[log])
    k = 40
    for cycle in range(30):
        batch = sentences(70, k); k += 70
        for s in batch:
            d.nmea_send(s)
        expect += b"".join(batch)
        d.now = (d.now + 10 * TICKS_PER_S) & 0xFFFFFFFF
        d.ui_event()
    assert bytes(d.files[log]) == bytes(expect), "content mismatch after ring wrap"
    assert d.state()["dropped"] == 0
    ok += 1; print("ok  30 flush cycles, %d bytes, ring wrapped %d times, no loss"
                   % (len(expect), len(expect) // RING_SIZE))

    # --- half-full ring flushes early, without waiting 10 s ---------------------
    before = len(d.files[log])
    while d.state()["head"] - d.state()["tail"] % RING_SIZE < 0 or \
            ((d.state()["head"] - d.state()["tail"]) % RING_SIZE) < RING_SIZE // 2:
        s = sentences(1, k)[0]; k += 1
        d.nmea_send(s); expect += s
    d.now = (d.now + 1 * TICKS_PER_S) & 0xFFFFFFFF
    d.ui_event()
    assert len(d.files[log]) > before and bytes(d.files[log]) == bytes(expect)
    ok += 1; print("ok  ring half full -> early flush")

    # --- overflow: whole sentences dropped, never partial -----------------------
    total = 0
    while True:
        s = sentences(1, k)[0]; k += 1
        st0 = d.state()["dropped"]
        d.nmea_send(s)
        if d.state()["dropped"] > st0:
            break
        expect += s
    for _ in range(50):
        d.nmea_send(sentences(1, k)[0]); k += 1
    d.now = (d.now + 10 * TICKS_PER_S) & 0xFFFFFFFF
    d.ui_event()
    assert bytes(d.files[log]) == bytes(expect), "overflow corrupted the log"
    assert d.state()["dropped"] > 0
    ok += 1; print("ok  overflow: %d bytes dropped as whole sentences, log intact" % d.state()["dropped"])

    # --- SD exported to PC: no file access at all, data kept for later ----------
    d.exported = True
    calls_before = dict(d.files)
    batch = sentences(20, k); k += 20
    for s in batch:
        d.nmea_send(s)
    expect += b"".join(batch)
    d.now = (d.now + 10 * TICKS_PER_S) & 0xFFFFFFFF
    d.ui_event()
    assert d.files == calls_before and d.state()["export_skips"] == 1
    d.exported = False
    d.now = (d.now + 10 * TICKS_PER_S) & 0xFFFFFFFF
    d.ui_event()
    assert bytes(d.files[log]) == bytes(expect)
    ok += 1; print("ok  SD exported over USB: skipped, then written after")

    # --- fopen fails / short write: nothing lost, retried ------------------------
    d.fopen_fail = True
    batch = sentences(10, k); k += 10
    for s in batch:
        d.nmea_send(s)
    expect += b"".join(batch)
    d.now = (d.now + 10 * TICKS_PER_S) & 0xFFFFFFFF
    d.ui_event()
    assert d.state()["open_err"] == 1
    d.fopen_fail = False
    d.short_write = True
    d.now = (d.now + 10 * TICKS_PER_S) & 0xFFFFFFFF
    d.ui_event()
    assert d.state()["write_err"] == 1 and not d.open_handles
    d.short_write = False
    # the short write put half the chunk in the file; only the rest is retried
    d.now = (d.now + 10 * TICKS_PER_S) & 0xFFFFFFFF
    d.ui_event()
    assert bytes(d.files[log]) == bytes(expect), "short write lost or duplicated data"
    ok += 1; print("ok  fopen failure and short write: data kept, resumed without duplication")

    # --- folder removed: logging stops, ring discarded, no files touched --------
    d.dirs.discard("SD:/nmea")
    size = len(d.files[log])
    for s in sentences(30, k):
        d.nmea_send(s)
    d.now = (d.now + 10 * TICKS_PER_S) & 0xFFFFFFFF
    d.ui_event()
    st = d.state()
    assert len(d.files[log]) == size and st["head"] == st["tail"] and st["nodir"] == 1
    ok += 1; print("ok  no SD:/nmea folder: nothing written, buffer discarded")

    # --- no NMEA at all: status.txt still appears every 10 s, no log file -----
    d3 = Device()
    d3.boot()
    d3.ui_event()
    for _ in range(3):
        d3.now = (d3.now + 10 * TICKS_PER_S) & 0xFFFFFFFF
        d3.ui_event()
    assert set(d3.files) == {"SD:/nmea/status.txt"}, d3.files.keys()
    status = d3.files["SD:/nmea/status.txt"].decode()
    for want in ("sentences=0", "checks=3", "status_writes=3", "uptime_s=30", "usb_state=2", "ui_ticks=4"):
        assert want + "\r\n" in status, (want, status)
    ok += 1; print("ok  no sentences: status.txt still written (diagnostics never silent)")

    # --- screenshot: picture taken, then flush + diagnostics next to it -------
    d4 = Device()
    d4.boot()
    d4.ui_event()
    batch = sentences(5)
    for s in batch:
        d4.nmea_send(s)
    d4.screenshot()                                   # well before the 10 s flush
    assert d4.screenshots_taken == 1
    diag = d4.files["SD:/screenshots/nmealog.txt"].decode()
    assert "logger v3" in diag and "screenshots=1\r\n" in diag and "sentences=5\r\n" in diag, diag
    assert "ticks_per_sec=3250000\r\n" in diag and "stat_rc=0\r\n" in diag
    assert bytes(d4.files["SD:/nmea/2026-10-24_16-41-27.nmea"]) == b"".join(batch)
    d4.dirs.discard("SD:/nmea")                       # diagnostics even with logging off
    d4.screenshot()
    diag = d4.files["SD:/screenshots/nmealog.txt"].decode()
    assert "screenshots=2\r\n" in diag and "nodir=1\r\n" in diag, diag
    ok += 1; print("ok  screenshot: still taken, forces a flush, writes SD:/screenshots/nmealog.txt")

    # --- before boot init (magic garbage): hooks do nothing --------------------
    d2 = Device()
    d2.mu.mem_write(0x003F2A4C, b"\x01")
    d2.mu.mem_write(0x00180D38, struct.pack("<I", 0x12345))
    d2.nmea_send(sentences(1)[0])
    d2.ui_event()
    assert d2.stream == sentences(1) and not d2.files
    d2.screenshot()
    assert d2.screenshots_taken == 1 and set(d2.files) == {"SD:/screenshots/nmealog.txt"}
    assert d2.files["SD:/screenshots/nmealog.txt"].startswith(b"PN-40 NMEA logger v3: not initialised")
    ok += 1; print("ok  hooks inert until the logger is initialised")

    print("\nall %d tests passed" % ok)


if __name__ == "__main__":
    run_tests()
