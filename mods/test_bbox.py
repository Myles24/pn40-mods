"""Emulator tests for the black box (bbox.c, bbox_hooks.S), run against the
built out\\PN-40.hex: the patched firmware sites (shutdown entry, immediate
reset, watchdog expiry), the exception handlers, survival of the record
across a reset, and the reports on SD:/ and MSYS2:/.

usage: python test_bbox.py [out dir]
"""
import os
import re
import struct
import sys

import unicorn
from unicorn.arm_const import *

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_nmealog import Device, SYMS, REGS, TICKS_PER_S, STACK_TOP, RET

BBOX_RAM, BBOX_PREV = 0x0076A600, 0x0076AE00
WD_SLOTS = 0x002B006C
SHUTDOWN, WDRESET, WD_SITE = 0x3008423C, 0x301A869C, 0x301A88FC
UI_SP = 0x00053000                      # inside the UI task's stack 0x43358-0x53358
OTHER_SP = 0x001837A0                   # the NMEA input task's stack
BATT_BITS = 0x003F4FB0                  # Battery Charger task: float volts
EXC_MAGIC = 0x45584321


class BBoxDevice(Device):
    def __init__(self, vectors=True):
        super().__init__()
        self.usb = 2
        self.dirs |= {"SD:", "MSYS2:"}
        self.reads = {}
        self.opens = []
        self.delays = 0
        self.mu.mem_write(BATT_BITS, struct.pack("<f", 2.82))     # + 0.13 V offset = 2950 mV
        self.mu.hook_add(unicorn.UC_HOOK_CODE, self._stub, begin=0x30187BA4, end=0x30187BA4, user_data="fread")
        self.mu.hook_add(unicorn.UC_HOOK_CODE, self._stub, begin=0x301FB8DC, end=0x301FB8DC,
                         user_data="task_delay")
        if vectors:
            self.vectors()

    def vectors(self, skip=()):
        """the device's two-level vector tables (sysinfo dump): undefined,
        prefetch and data abort end in "ldr pc" loops on themselves"""
        for v in (1, 3, 4):
            self.mu.mem_write(4 * v, struct.pack("<I", 0xE59FF018))
            self.mu.mem_write(0x20 + 4 * v, struct.pack("<I", 0xA0000000 + 4 * v))
            self.mu.mem_write(0xA0000000 + 4 * v, struct.pack("<I", 0xE59FF018))
            self.mu.mem_write(0xA0000020 + 4 * v, struct.pack("<I", 0x30123456 if v in skip else 0xA0000000 + 4 * v))

    def _stub(self, uc, addr, size, name):
        r0, r1, r2, r3 = (self.r(i) for i in range(4))
        self.exported = self.usb == 0
        if name == "usb_export_state":
            return self._ret(self.usb)
        if name == "fopen":
            path, mode = self.cstr(r0), self.cstr(r1)
            self.opens.append((path, mode, uc.reg_read(UC_ARM_REG_SP)))
            medium = path.split("/", 1)[0]
            if (medium == "SD:" and self.usb == 0) or (medium == "MSYS2:" and self.usb == 1) or \
                    path.rsplit("/", 1)[0] not in self.dirs:
                return self._ret(0)
            h = 0x100 + len(self.open_handles) + 1
            if mode == "r":
                if path not in self.files:
                    return self._ret(0)
                self.reads[h] = 0
            elif mode == "w" or path not in self.files:
                self.files[path] = bytearray()
            self.open_handles[h] = path
            return self._ret(h)
        if name == "fread":
            assert r3 in self.reads and r1 == 1
            data = bytes(self.files[self.open_handles[r3]])[self.reads[r3]:self.reads[r3] + r2]
            self.reads[r3] += len(data)
            uc.mem_write(r0, data)
            return self._ret(len(data))
        if name == "fclose":
            self.reads.pop(r0, None)
        if name == "task_delay":
            self.delays += 1
            return self._ret(0)
        return super()._stub(uc, addr, size, name)

    def u32(self, a):
        return struct.unpack("<I", bytes(self.mu.mem_read(a, 4)))[0]

    def tick(self, secs, step=0.25):
        for _ in range(int(secs / step)):
            self.now = (self.now + int(step * TICKS_PER_S)) & 0xFFFFFFFF
            self.ui_event()

    def text(self, path):
        return bytes(self.files[path]).decode()

    def run_entry(self, addr, until, sp, args=(), lr=0x30099990):
        """execute a patched function entry up to `until` (just past our
        trampoline); returns the sp there"""
        for i, v in enumerate(args):
            self.mu.reg_write(REGS[i], v)
        for i in range(4, 12):
            self.mu.reg_write(REGS[i], 0x33330000 + i)
        self.mu.reg_write(UC_ARM_REG_SP, sp)
        self.mu.reg_write(UC_ARM_REG_LR, lr)
        self.mu.emu_start(addr, until, count=5000000)
        assert [self.r(i) for i in range(4, 12)] == [0x33330000 + i for i in range(4, 12)], "callee-saved changed"
        assert [self.r(i) for i in range(len(args))] == list(args), "arguments changed"
        assert self.mu.reg_read(UC_ARM_REG_LR) == lr, "lr changed"
        return self.mu.reg_read(UC_ARM_REG_SP)

    def set_slot(self, i, on, timeout_ms, kicked_ago_ms):
        self.mu.mem_write(WD_SLOTS + 12 * i, struct.pack("<BxxxII", on, timeout_ms * 3250,
                                                        (self.now - kicked_ago_ms * 3250) & 0xFFFFFFFF))


def run_tests():
    ok = 0

    # --- boot: new session, exception handlers installed only over the stock loops --
    d = BBoxDevice()
    d.mu.mem_write(BBOX_RAM, os.urandom(0x1000))           # power-on garbage
    d.mu.mem_write(0x101E901C, struct.pack("<I", 0xB7200000))
    d.boot()
    assert d.u32(BBOX_RAM + 0x94) == 0x42424F58, "session magic"
    for v, fn in ((1, "bbox_exc_undef"), (3, "bbox_exc_pabort"), (4, "bbox_exc_dabort")):
        assert d.u32(0xA0000020 + 4 * v) == SYMS[fn], v
    d2 = BBoxDevice()
    d2.vectors(skip=(3,))
    d2.boot()
    assert d2.u32(0xA000002C) == 0x30123456 and d2.u32(0xA0000024) == SYMS["bbox_exc_undef"]
    d3 = BBoxDevice(vectors=False)
    d3.boot()
    assert d3.u32(0xA0000024) == 0 and d3.u32(0xA0000030) == 0, "installed without the stock vectors"
    ok += 1; print("ok  boot: session started; handlers replace only the stock self-loops")

    # --- first ticks: bbox_prev.txt on both media, heartbeats ----------------------
    d.set_slot(1, 1, 120000, 100)
    d.set_slot(4, 1, 20000, 300)
    d.tick(5)
    prev = d.text("SD:/bbox_prev.txt")
    assert "none: RAM didn't hold a session" in prev and "(none)" in prev, prev
    assert d.text("MSYS2:/bbox_prev.txt") == prev
    assert "SD:/bbox_live.txt" not in d.files and "MSYS2:/bbox_live.txt" not in d.files, "live report while idle"
    d.screenshot()
    live = d.text("SD:/bbox_live.txt")
    assert "BOOT boot_reg=0xB7200000" in live and "exc_handlers=0x0000001A" in live, live
    hb = [l for l in live.split("\r\n") if " usb=2 " in l]
    assert len(hb) == 3 and "batt=2950mV" in hb[0] and "wd_on=0x00000012" in hb[0], hb
    assert "wd_min_slack=slot4:19" in hb[0], hb[0]
    assert "slot 4: timeout 20000 ms" in live
    ok += 1; print("ok  first ticks: bbox_prev.txt on SD and MSYS2, heartbeats every 2 s, live on screenshot")

    # --- SD exported: live report on MSYS2 every 5 s, never on SD ----------------------
    d.usb = 0
    before = len(d.opens)
    d.tick(12)
    sd_opens = [o for o in d.opens[before:] if o[0].startswith("SD:")]
    assert not [o for o in sd_opens if o[1] != "r"], sd_opens
    ms_live = [o for o in d.opens[before:] if o[0] == "MSYS2:/bbox_live.txt"]
    assert len(ms_live) == 3, ms_live                      # at +1 s, then every 5 s
    live = d.text("MSYS2:/bbox_live.txt")
    assert "USB not exported -> SD card exported" in live and " usb=0 " in live, live
    ok += 1; print("ok  SD exported: live report to MSYS2:/ every 5 s, nothing written to SD")

    # --- shutdown from the UI task: recorded, report written at once, entry transparent --
    before = len(d.opens)
    sp = d.run_entry(SHUTDOWN, 0x30084240, UI_SP, args=(0x100, 0x22, 0x33, 0x44), lr=0x3008653C)
    assert sp == UI_SP - 24
    assert struct.unpack("<6I", bytes(d.mu.mem_read(sp, 24))) == (0x33330004, 0x33330005, 0x33330006,
                                                                  0x33330007, 0x33330008, 0x3008653C)
    w = [o for o in d.opens[before:] if o[1] == "w"]
    assert [o[0] for o in w] == ["MSYS2:/bbox_live.txt"] and UI_SP - 0x10000 < w[0][2] < UI_SP, w
    live = d.text("MSYS2:/bbox_live.txt")
    assert "SHUTDOWN(power off) caller=0x3008653C batt=2950mV (UI task)" in live, live
    ok += 1; print("ok  shutdown in the UI task: logged with its caller, report written before the firmware goes on")

    # --- shutdown from another task: no file I/O there, the UI task writes it ---------
    before, delays = len(d.opens), d.delays
    d.run_entry(SHUTDOWN, 0x30084240, OTHER_SP, args=(1,), lr=0x3014C000)
    assert not d.opens[before:], "file I/O outside the UI task"
    assert d.delays - delays == 20, "should wait 2 s for the UI task"
    d.tick(0.25)
    live = d.text("MSYS2:/bbox_live.txt")
    assert "SHUTDOWN(reboot) caller=0x3014C000 batt=2950mV (other task)" in live
    ok += 1; print("ok  shutdown in another task: waits for the UI task to write the report")

    # --- immediate reset and watchdog expiry --------------------------------------------
    d.run_entry(WDRESET, 0x301A86A0, OTHER_SP, args=(0,), lr=0x3012D2D8)
    d.set_slot(4, 1, 20000, 20400)
    d.mu.reg_write(REGS[4], WD_SLOTS + 12 * 4)
    d.mu.reg_write(REGS[0], 0)
    d.mu.reg_write(UC_ARM_REG_SP, 0x002B04E0)               # watchdog task stack: 0x2B00E4 + 0x400
    d.mu.emu_start(WD_SITE, 0x301A864C, count=100000)       # stops at the original reset call
    assert d.mu.reg_read(UC_ARM_REG_LR) == 0x301A8900 and d.r(0) == 0 and d.r(4) == WD_SLOTS + 48
    assert d.mu.reg_read(UC_ARM_REG_SP) == 0x002B04E0
    ok += 1; print("ok  immediate reset and watchdog expiry recorded; firmware path unchanged")

    # --- data abort: registers saved, then the stock-style loop -------------------------
    d.mu.reg_write(UC_ARM_REG_CPSR, 0x60000010)               # a task, user mode
    d.mu.reg_write(UC_ARM_REG_SP, 0x00181700)
    d.mu.reg_write(UC_ARM_REG_LR, 0x30111111)
    d.mu.mem_write(0x00181700, struct.pack("<16I", *range(0xA0, 0xB0)))
    d.mu.reg_write(UC_ARM_REG_CPSR, 0x600000D7)               # entered abort mode
    d.mu.reg_write(UC_ARM_REG_SPSR, 0x60000010)
    d.mu.reg_write(UC_ARM_REG_LR, 0x30123450 + 8)
    for i in range(13):
        d.mu.reg_write([UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4,
                        UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R8, UC_ARM_REG_R9,
                        UC_ARM_REG_R10, UC_ARM_REG_R11, UC_ARM_REG_R12][i], 0x0D000000 + i)
    d.mu.emu_start(SYMS["bbox_exc_dabort"], 0, count=400)
    pc = d.mu.reg_read(UC_ARM_REG_PC)
    assert d.u32(pc) == 0xEAFFFFFE, "handler should end in a b . loop (pc %08X)" % pc
    exc = struct.unpack("<37I", bytes(d.mu.mem_read(BBOX_RAM, 0x94)))
    assert exc[0] == EXC_MAGIC and exc[1] == 3
    assert list(exc[2:15]) == [0x0D000000 + i for i in range(13)]
    assert exc[15] == 0x30123458 and exc[16] == 0x60000010
    assert exc[17] == 0x00181700 and exc[18] == 0x30111111, (hex(exc[17]), hex(exc[18]))
    assert list(exc[21:37]) == list(range(0xA0, 0xB0))
    d.mu.reg_write(UC_ARM_REG_CPSR, 0x600000D3)
    ok += 1; print("ok  data abort: r0-r12, pc, spsr, task sp/lr and stack saved; then loops like stock")

    # --- the reset: next boot finds the session in RAM and reports it --------------------
    d.opens.clear()
    d.usb = 2
    d.boot()
    d.tick(1)
    prev = d.text("SD:/bbox_prev.txt")
    assert "EXCEPTION: DATA ABORT at pc=0x30123450" in prev, prev
    assert " r12=0x0D00000C" in prev and "sp=0x00181700 lr=0x30111111" in prev
    assert "WATCHDOG: slot 4 expired" in prev and "slot 4: timeout 20000 ms, last kick 20400 ms ago" in prev
    assert "IMMEDIATE RESET 0x301A869C caller=0x3012D2D8" in prev
    assert "SHUTDOWN(power off) caller=0x3008653C" in prev
    assert "--- MSYS2:/bbox_live.txt as found at boot ---\r\nPN-40 black box v1: live" in prev
    assert d.text("MSYS2:/bbox_prev.txt") == prev
    assert "written by boot_no 2" in prev and "session boot_no=1 " in prev
    d.screenshot()
    assert "exception: none" in d.text("SD:/bbox_live.txt"), "new session starts clean"
    ok += 1; print("ok  after the reset: bbox_prev.txt has the exception, watchdog, events and the old live file")

    # --- a power-off: RAM lost, the MSYS2 live file still comes through -------------------
    d.mu.mem_write(BBOX_RAM, os.urandom(0x1000))
    d.boot()
    d.tick(1)
    prev = d.text("SD:/bbox_prev.txt")
    assert "none: RAM didn't hold a session" in prev and "SHUTDOWN(reboot)" in prev.split("as found at boot")[1]
    ok += 1; print("ok  after a power-off: no RAM record, previous live file still reported")

    # --- ring: oldest first, last 64 of many -----------------------------------------------
    d.tick(200)
    d.screenshot()
    live = d.text("SD:/bbox_live.txt")
    times = [float(m) for m in re.findall(r"^(\d+\.\d+)s ", live, re.M)]
    total = int(re.search(r"events \((\d+) total, last (\d+)\)", live).group(1))
    assert len(times) == 64 and times == sorted(times) and total > 64, (len(times), total)
    ok += 1; print("ok  event ring: last 64 of %d, oldest first" % total)

    # --- inert before init: the patched entries pass straight through --------------------
    e = BBoxDevice()
    sp = e.run_entry(SHUTDOWN, 0x30084240, UI_SP, args=(0,), lr=0x3008653C)
    assert sp == UI_SP - 24 and not e.opens
    ok += 1; print("ok  hooks inert before init")

    print("\nall %d tests passed" % ok)


if __name__ == "__main__":
    run_tests()
