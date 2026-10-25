"""Emulator test for the UI timing and CPU sampling diagnostics (prof.c,
prof_hooks.S), run against the built out\\PN-40.hex.

Runs the real patched UI main loop call sites and the patched fread/fseek
entries; their callees are stubs that take a known (simulated) time:
ui_before_dispatch, ui_dispatch_event, win_update, the filesystem read/seek.
The IRQ sampler is entered in IRQ mode the way the vector would, and must
reach the firmware's handler with every register unchanged.

usage: python test_prof.py [out dir]
"""
import os
import struct
import sys

import unicorn
from unicorn.arm_const import UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_CPSR

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_nmealog import Device, SYMS, TICKS_PER_S, REGS

PROF_RAM = 0x00773340
CUR_PAGE = 0x00053B64
SITE_BEFORE, SITE_DISPATCH, SITE_PUSH, SITE_POPUP = 0x30085F38, 0x30085F50, 0x30085F6C, 0x30085F98
FREAD, FSEEK = 0x30187BA4, 0x30187D38
STUBS = dict(before=0x30085B10, dispatch=0x300E6638, win_update=0x304478EC, fs_read=0x30195BE4,
             fs_seek=0x301952F0)
MS = TICKS_PER_S // 1000
FW_IRQ = 0xA0000200                    # stands in for the firmware's IRQ handler (internal RAM)
HEAP = 0x00500000
NBINS = 0x660000 >> 8
IRQ_STACK = 0x00110000


class ProfDevice(Device):
    def __init__(self):
        super().__init__()
        self.cost = dict(before=2, dispatch=5, win_update=40, fs_read=30, fs_seek=4)    # ms
        self.stress_results = [0xC0FFEE11]                    # screenshot path: canned result
        self.seen = []
        self.mu.mem_write(0x101E0014, struct.pack("<I", 0x16002203))   # the PLL value the boot code sets
        self.mu.mem_write(0x10110000, bytes(range(64)))
        self.vectors()
        for name, addr in STUBS.items():
            self.mu.hook_add(unicorn.UC_HOOK_CODE, self._stub3, begin=addr, end=addr, user_data=name)
        for name in ("prof_enter", "prof_exit", "prof_loop_top", "prof_screenshot", "prof_init"):
            self.mu.hook_add(unicorn.UC_HOOK_CODE, self._c_entry, begin=SYMS[name], end=SYMS[name])

    def vectors(self, vec=0xA0000018, ramvec=0xE59FF018):
        self.mu.mem_write(0x38, struct.pack("<I", vec))
        self.mu.mem_write(0xA0000018, struct.pack("<I", ramvec))
        self.mu.mem_write(0xA0000038, struct.pack("<I", FW_IRQ))

    def _stub3(self, uc, addr, size, name):
        self.now = (self.now + self.cost[name] * MS) & 0xFFFFFFFF
        args = [self.r(i) for i in range(4)]
        self.seen.append((name, args, uc.reg_read(UC_ARM_REG_SP)))
        if name == "fs_read":
            self.mu.reg_write(REGS[1], 0xBEEF)                # r1 garbage: fread's result is r0 only
            return self._ret(args[2])                         # all bytes read
        if name == "dispatch":
            self.mu.reg_write(REGS[1], 0x22222222)
            return self._ret(0x11111111)
        return self._ret(0x900D)

    def site(self, addr, r0=0):
        self.mu.reg_write(REGS[0], r0)
        return self.run_site(addr)

    def frame(self, dispatch_ms, event=(0x3F1, 0), reads=0, page=1):
        """one UI main loop pass: before, dispatch (+ reads), push, back to the top"""
        self.mu.mem_write(CUR_PAGE, struct.pack("<I", page))
        self.site(SITE_BEFORE, 1)
        for _ in range(reads):
            self.fread(64)
        self.cost["dispatch"] = dispatch_ms
        ev = 0x00180040
        self.mu.mem_write(ev, struct.pack("<II", *event))
        self.site(SITE_DISPATCH, ev)
        self.site(SITE_PUSH, 0x003EF778)
        self.ui_event()                                      # loop top: ui_loop_tramp

    def fread(self, n):
        f = 0x00180100
        self.mu.mem_write(f, struct.pack("<I", 0x1234))
        return self.call(FREAD, (0x00180200, 1, n, f))

    def st(self):
        w = struct.unpack("<160I", bytes(self.mu.mem_read(PROF_RAM, 160 * 4)))
        names = ("hist saved_irq samples other magic active start io_at_start io_calls_at_start").split()
        hdr = dict(zip(names, w[:9]))
        names = ("outside_io outside_calls nframes slow_frames frames window_start skip_dispatch irq_state "
                 "last_check buf").split()
        hdr.update(dict(zip(names, w[18:28])))
        slots = [dict(zip("calls lo hi max".split(), w[28 + 4 * i:32 + 4 * i])) for i in range(6)]
        slow = [dict(zip("event param page total before dispatch push io io_calls".split(), w[52 + 9 * i:61 + 9 * i]))
                for i in range(10)]
        return hdr, slots, slow

    def bin(self, pc):
        return struct.unpack("<H", bytes(self.mu.mem_read(HEAP + 2 * ((pc - 0x30080000) >> 8), 2)))[0]

    def irq(self, pc):
        """take an IRQ while executing pc: IRQ mode, lr = pc + 4, through prof_irq"""
        cpsr = self.mu.reg_read(UC_ARM_REG_CPSR)
        self.mu.reg_write(UC_ARM_REG_CPSR, (cpsr & ~0x3F) | 0xD2)      # IRQ mode, IRQ/FIQ masked, ARM
        self.mu.reg_write(UC_ARM_REG_SP, IRQ_STACK)
        self.mu.reg_write(UC_ARM_REG_LR, pc + 4)
        canary = [0x33330000 + i for i in range(13)]
        for i in range(13):
            self.mu.reg_write(getattr(unicorn.arm_const, "UC_ARM_REG_R%d" % i), canary[i])
        self.mu.mem_write(IRQ_STACK - 16, b"\xEE" * 16)
        target = struct.unpack("<I", bytes(self.mu.mem_read(0xA0000038, 4)))[0]
        self.mu.emu_start(target, FW_IRQ, count=10000)
        assert self.mu.reg_read(UC_ARM_REG_PC) == FW_IRQ, "didn't reach the firmware's handler"
        assert self.mu.reg_read(UC_ARM_REG_SP) == IRQ_STACK, "IRQ stack not restored"
        assert self.mu.reg_read(UC_ARM_REG_LR) == pc + 4, "lr_irq changed"
        assert [self.mu.reg_read(getattr(unicorn.arm_const, "UC_ARM_REG_R%d" % i)) for i in range(13)] == canary
        assert self.mu.reg_read(UC_ARM_REG_CPSR) & 0x3F == 0x12, "mode changed"
        self.mu.reg_write(UC_ARM_REG_CPSR, cpsr)


def stress_model(words, seed):
    """prof.c stress_pass, in Python"""
    M = 0xFFFFFFFF
    table = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = 0xEDB88320 ^ (c >> 1) if c & 1 else c >> 1
        table.append(c)
    x, buf = seed, []
    for i in range(words):
        x = (x * 1664525 + 1013904223) & M
        buf.append(x)
    acc = 0
    for i in range(words):
        v = buf[(i * 7 + 3) & (words - 1)]
        v = ((v << 13) | (v >> 19)) & M
        buf[i] ^= (v + i * 0x9E3779B9) & M
        acc = (acc + buf[i] * (v | 1)) & 0xFFFFFFFFFFFFFFFF
    crc = M
    for v in buf:
        for _ in range(4):
            crc = table[(crc ^ v) & 0xFF] ^ (crc >> 8)
            v >>= 8
    return (~crc & M) ^ (acc & M) ^ (acc >> 32)


def fresh():
    d = ProfDevice()
    d.boot()
    d.ui_event()                                             # first loop top: no frame yet
    return d


def ms(t):
    return round(t / MS)


def run_tests():
    ok = 0

    # --- fread / fseek: same results, every call timed -----------------------
    d = fresh()
    hdr, slots, _ = d.st()
    assert hdr["magic"] == 0x54494D45 and slots[4]["calls"] == 0 and hdr["hist"] == 0
    assert d.fread(100) == 100, "fread result changed"
    name, args, sp = d.seen[-1]
    assert name == "fs_read" and args[0] == 0x1234 and args[1] == 0x00180200 and args[2] == 100
    f = 0x00180100
    assert d.call(FSEEK, (f, 512, 0)) == 0x900D and d.seen[-1][1][:2] == [0x1234, 512]
    assert d.call(FSEEK, (0, 0, 0)) == 0x16, "fseek's own checks still run"
    hdr, slots, _ = d.st()
    assert slots[4]["calls"] == 1 and ms(slots[4]["lo"]) == 30 and ms(slots[4]["max"]) == 30
    assert slots[5]["calls"] == 2 and ms(slots[5]["lo"]) == 4
    assert hdr["outside_calls"] == 3 and ms(hdr["outside_io"]) == 34, "no frame active: counted outside"
    assert all(sp % 8 == 0 for sp in d.c_entry_sp), "C code entered with misaligned stack"
    ok += 1; print("ok  fread/fseek entries: results and arguments unchanged, timed, outside-frame io counted")

    # --- UI loop sites: originals called with the same arguments, results kept --
    d = fresh()
    d.mu.mem_write(0x00180040, struct.pack("<II", 7, 8))
    assert d.site(SITE_DISPATCH, 0x00180040) == 0x11111111 and d.r(1) == 0x22222222
    assert d.seen[-1][0] == "dispatch" and d.seen[-1][1][0] == 0x00180040
    assert d.site(SITE_PUSH, 0x003EF778) == 0x900D and d.seen[-1][1][0] == 0x003EF778
    assert d.site(SITE_POPUP, 0x0055C9C0) == 0x900D and d.seen[-1][1][0] == 0x0055C9C0
    assert d.site(SITE_BEFORE, 1) == 0x900D and d.seen[-1][1][0] == 1
    ok += 1; print("ok  UI loop wrappers: same arguments in, same r0:r1 out, registers and stack intact")

    # --- frames: the slowest kept, slowest first, with page/event/split ----------
    d = fresh()
    d.frame(5)                                               # 2 + 5 + 40 = 47 ms
    d.frame(900, event=(2, 0), reads=10, page=1)             # a slow map redraw with 10 reads
    for k in range(15):
        d.frame(100 + 10 * k, event=(0x3ED, k), page=3)
    hdr, slots, slow = d.st()
    assert hdr["frames"] == 17 and hdr["slow_frames"] == 16 and hdr["nframes"] == 10, hdr
    fr = slow[0]
    assert (fr["page"], fr["event"], fr["param"]) == (1, 2, 0)
    assert ms(fr["before"]) == 2 and ms(fr["dispatch"]) == 900 and ms(fr["push"]) == 40
    assert ms(fr["io"]) == 300 and fr["io_calls"] == 10 and ms(fr["total"]) == 1242
    assert [s["param"] for s in slow[1:]] == [14, 13, 12, 11, 10, 9, 8, 7, 6], [s["param"] for s in slow]
    ok += 1; print("ok  the 10 slowest frames kept, slowest first, with page, event and the time split")

    # --- report, then a new window ----------------------------------------------
    d.now = (d.now + 7 * TICKS_PER_S) & 0xFFFFFFFF
    d.screenshot()
    text = d.files["SD:/screenshots/maptime.txt"].decode()
    lines = text.split("\r\n")
    assert lines[0].startswith("PN-40 UI timing v2") and "frames=17 slow(>=50ms)=16 page_now=3" in lines[1], lines[:2]
    rows = lines[lines.index([l for l in lines if l.startswith("slowest frames")][0]) + 1:-1]
    assert len(rows) == 10 and rows[0].split()[3] == "1242" and int(rows[1].split()[2], 16) == 14
    disp = [l for l in lines if l.startswith("dispatch ")][0].split()
    assert disp[1] == "17" and int(disp[3]) == 900, disp
    prof = d.files["SD:/screenshots/cpuprof.txt"].decode()
    assert "sampling=off (no SD:/prof folder)" in prof, prof
    bid = struct.unpack("<I", bytes(d.mu.mem_read(SYMS["mods_build_id"], 4)))[0]
    assert bid != 0xFFFFFFFF and "build=0x%08X" % bid in lines[0] and "build=0x%08X" % bid in prof, (hex(bid), lines[0])
    info = d.files["SD:/screenshots/sysinfo.txt"].decode().split("\r\n")
    assert info[0] == "PN-40 system info v7 build=0x%08X" % bid, info[0]
    khz = int(info[1].split("cpu_khz=")[1].split()[0])
    assert abs(khz - 117000) < 117 and "cpu_khz_if_branch_3_cycles=117" in info[1], info[1]
    assert info[2].startswith("cpsr=0x") and info[2].endswith("(mode 0x10 user, 0x13 supervisor)"), info[2]
    for line, name in ((info[3], "cpu_khz_irq_masked="), (info[4], "cpu_khz_irq_fiq_masked=")):
        assert line.startswith(name) and abs(int(line[len(name):].split()[0]) - 117000) < 117, line
        assert "(masked=" in line and "ticks16=" in line, line
    assert info[5] == "SRC_PLLFR=0x16002203 nomadik: PLL1=26MHz*36>>3=117000kHz PLL2=26MHz*24=624000kHz", info[5]
    assert info[6].startswith("hclk_field=0 (SRC_CR bits 14:13): bus = PLL1/1 "), info[6]
    assert info[7].startswith("bus_ps_per_load: cached=") and "src_reg=" in info[7] and \
        "sdram_uncached=" in info[7] and info[7].endswith("(n=8000, irq locked)"), info[7]
    assert info[8].startswith("stress: checksum=0xC0FFEE11 runs=16 mismatches=0 ms="), info[8]
    assert info[9].startswith("compass: state=0 valid=0 reads=0 errors=0 max_in_a_row=0 last_error=0"), info[9]
    # boot-time clock literals as build.py's CPU_CLOCK says
    import build
    img = {a: struct.unpack("<I", bytes(d.mu.mem_read(a, 4)))[0] for a in (0x30081B90, 0x30081B94, 0x30081BA8)}
    if build.CPU_CLOCK:
        h = build.CPU_CLOCK["src_cr_hclk"] << 13
        want = {0x30081B90: build.CPU_CLOCK["pllfr"], 0x30081B94: 0x0004 | h, 0x30081BA8: 0x0104 | h}
    else:
        want = {0x30081B90: 0x16002203, 0x30081B94: 0x2004, 0x30081BA8: 0x2104}
    assert img == want, {hex(k): hex(v) for k, v in img.items()}
    assert "0xA0000030: 0x00000000 0x00000000 0xA0000200 0x00000000" in info, info   # vector area dumped
    # the masking helpers in a task (user mode): IRQ via OS20's SWI 0/1, FIQ can't be masked
    d.swis.clear()
    d.mu.reg_write(UC_ARM_REG_CPSR, 0x10)                       # user mode, I and F clear
    old = d.call(SYMS["cpu_irq_mask"], (0xC0,))
    assert old & 0xDF == 0x10 and d.swis == [0], (hex(old), d.swis)
    assert d.mu.reg_read(UC_ARM_REG_CPSR) & 0xC0 == 0x80, "IRQ masked through the SWI, FIQ untouched"
    d.call(SYMS["cpu_irq_restore"], (old,))
    assert d.swis == [0, 1] and d.mu.reg_read(UC_ARM_REG_CPSR) & 0xC0 == 0
    d.mu.reg_write(UC_ARM_REG_CPSR, 0x90)                       # IRQs already off: no SWI either way
    d.swis.clear()
    old = d.call(SYMS["cpu_irq_mask"], (0x40,))
    d.call(SYMS["cpu_irq_restore"], (old,))
    assert d.swis == [] and d.mu.reg_read(UC_ARM_REG_CPSR) & 0xC0 == 0x80
    d.mu.reg_write(UC_ARM_REG_CPSR, 0x13)
    assert "0x10110000: 0x03020100 0x07060504 0x0B0A0908 0x0F0E0D0C" in info, info
    assert "0x101E0010: 0x00000000 0x16002203 0x00000000 0x00000000" in info, info
    hdr, slots, slow = d.st()
    assert hdr["frames"] == 0 and hdr["nframes"] == 0 and slots[1]["calls"] == 0, "new window"
    d.mu.mem_write(0x00180040, struct.pack("<II", 0x3F4, 0))   # the screenshot key's own dispatch...
    d.site(SITE_DISPATCH, 0x00180040)
    d.ui_event()
    assert d.st()[1][1]["calls"] == 0, "...is left out"
    d.frame(5)
    assert d.st()[1][1]["calls"] == 1
    ok += 1; print("ok  screenshot writes maptime.txt (slowest first) and cpuprof.txt, then starts a new window")
    print("    " + "\n    ".join(lines[:4]))

    # --- IRQ sampler: off without SD:/prof ------------------------------------------
    d = fresh()
    d.dirs.discard("SD:/prof")
    for _ in range(3):
        d.now = (d.now + 2 * TICKS_PER_S) & 0xFFFFFFFF
        d.ui_event()
    assert struct.unpack("<I", bytes(d.mu.mem_read(0xA0000038, 4)))[0] == FW_IRQ and not d.mallocs
    assert d.st()[0]["irq_state"] == 0

    # --- installs when the folder appears; counts samples; registers intact --------
    d.dirs.add("SD:/prof")
    d.now = (d.now + 1 * TICKS_PER_S) & 0xFFFFFFFF
    d.ui_event()
    assert struct.unpack("<I", bytes(d.mu.mem_read(0xA0000038, 4)))[0] == FW_IRQ, "checks only every 2 s"
    d.now = (d.now + 1 * TICKS_PER_S) & 0xFFFFFFFF
    d.ui_event()
    hdr = d.st()[0]
    assert hdr["irq_state"] == 1 and hdr["saved_irq"] == FW_IRQ and hdr["hist"] == HEAP == hdr["buf"]
    assert struct.unpack("<I", bytes(d.mu.mem_read(0xA0000038, 4)))[0] == SYMS["prof_irq"]
    assert d.mallocs == [NBINS * 2]
    assert bytes(d.mu.mem_read(HEAP, NBINS * 2)) == b"\0" * (NBINS * 2), "histogram cleared"
    for pc in [0x30127834] * 5 + [0x30127900] * 2 + [0x3042792C] * 3 + [0x306D1000, 0x306DFFFC]:
        d.irq(pc)
    d.irq(0xA0000100)                                        # RAM code: other
    d.irq(0x3007FFFC)                                        # below the range: other
    d.irq(0x306E0000)                                        # above: other
    assert d.bin(0x30127834) == 5 and d.bin(0x30127900) == 2 and d.bin(0x3042792C) == 3
    assert d.bin(0x306D1000) == 1 and d.bin(0x306DFFFC) == 1
    hdr = d.st()[0]
    assert hdr["samples"] == 15 and hdr["other"] == 3
    d.mu.mem_write(HEAP + 2 * ((0x30090000 - 0x30080000) >> 8), struct.pack("<H", 0xFFFF))
    d.irq(0x30090010)
    assert d.bin(0x30090010) == 0xFFFF, "saturates"
    ok += 1; print("ok  IRQ sampler: installs only with SD:/prof, bins the interrupted pc, jumps on intact")

    # --- cpuprof report: top bins, sorted; histogram restarts --------------------------
    d.mu.mem_write(HEAP + 2 * ((0x30090000 - 0x30080000) >> 8), struct.pack("<H", 4))
    d.screenshot()
    prof = d.files["SD:/screenshots/cpuprof.txt"].decode().split("\r\n")
    assert "sampling=on" in prof[1] and "samples=16 other=3" in prof[1] and "handler=0xA0000200" in prof[1], prof[1]
    bins = [l.split() for l in prof[3:] if l]
    assert bins[:4] == [["0x30127800", "5"], ["0x30090000", "4"], ["0x30427900", "3"], ["0x30127900", "2"]], bins
    assert sorted(bins[4:]) == [["0x306D1000", "1"], ["0x306DFF00", "1"]], bins
    assert bytes(d.mu.mem_read(HEAP, NBINS * 2)) == b"\0" * (NBINS * 2) and d.st()[0]["samples"] == 0
    ok += 1; print("ok  cpuprof.txt: sampling state, totals, busiest bins first; histogram restarts")

    # --- folder gone (or SD exported): handler pointer restored ---------------------
    d.dirs.discard("SD:/prof")
    d.now = (d.now + 2 * TICKS_PER_S) & 0xFFFFFFFF
    d.ui_event()
    assert struct.unpack("<I", bytes(d.mu.mem_read(0xA0000038, 4)))[0] == FW_IRQ
    assert d.st()[0]["hist"] == 0 and d.st()[0]["irq_state"] == 0
    d.dirs.add("SD:/prof")
    d.now = (d.now + 2 * TICKS_PER_S) & 0xFFFFFFFF
    d.ui_event()
    assert d.st()[0]["irq_state"] == 1 and d.mallocs.count(NBINS * 2) == 1, "buffer reused"
    d.exported = True
    d.now = (d.now + 2 * TICKS_PER_S) & 0xFFFFFFFF
    d.ui_event()
    assert struct.unpack("<I", bytes(d.mu.mem_read(0xA0000038, 4)))[0] == FW_IRQ
    ok += 1; print("ok  removing SD:/prof or exporting the SD card restores the IRQ handler")

    # --- vectors not as expected: never installed ------------------------------------
    for vec, ramvec, handler, state in ((0xA0000020, 0xE59FF018, FW_IRQ, 2), (0xA0000018, 0xEA000010, FW_IRQ, 3),
                                        (0xA0000018, 0xE59FF018, 0xA0000018, 5), (0xA0000018, 0xE59FF018, 0x00120000, 5)):
        d = fresh()
        d.vectors(vec, ramvec)
        d.mu.mem_write(0xA0000038, struct.pack("<I", handler))
        d.dirs.add("SD:/prof")
        d.now = (d.now + 2 * TICKS_PER_S) & 0xFFFFFFFF
        d.ui_event()
        assert d.st()[0]["irq_state"] == state and not d.mallocs
        assert struct.unpack("<I", bytes(d.mu.mem_read(0xA0000038, 4)))[0] == handler
    ok += 1; print("ok  unexpected vector layout or handler pointer: sampler not installed, reported")

    # --- stress test: the real worker against a Python model; mismatch counting ----------
    d = fresh()
    d.stress_results = None
    buf = 0x00600000
    for words, seed in ((1024, 7), (16384, 0x12345678)):
        got = d.call(SYMS["stress_pass"], (buf, words, seed))
        assert got == stress_model(words, seed), (words, hex(got), hex(stress_model(words, seed)))
    print("    stress checksum for the device (16384 words, seed 0x12345678): 0x%08X" % stress_model(16384, 0x12345678))
    d.stress_results = [0x1111, 0x1111, 0x2222] + [0x1111] * 13 + [0x1111]
    d.screenshot()
    line = [l for l in d.files["SD:/screenshots/sysinfo.txt"].decode().split("\r\n") if l.startswith("stress:")][0]
    assert line.startswith("stress: checksum=0x00001111 runs=16 mismatches=1 ms="), line
    assert d.mallocs.count(16384 * 4) == 1
    d.screenshot()
    assert d.mallocs.count(16384 * 4) == 1, "stress buffer allocated once"
    ok += 1; print("ok  stress test: worker matches the Python model; mismatches counted; buffer allocated once")

    # --- compass task: failed reads retried, give up after 120 in a row ------------------
    d = fresh()
    STATE, FRAME, RET_SENTINEL = 0x002B0950, 0x00180600, 0x00010000
    def compass_site(addr, err):
        """run the patched instruction with the task's registers and stack as at that point"""
        regs = {4: 0x44, 5: 0, 6: 0x002B095C, 7: 0x77, 8: STATE, 10: 0xA0A0}
        for i, v in regs.items():
            d.mu.reg_write(REGS[i], v)
        d.mu.reg_write(REGS[3], err & 0xFFFFFFFF)
        d.mu.reg_write(REGS[0], err & 0xFFFFFFFF)
        # the task's frame: 0x20 locals, then r4-r8, sl, pc (pc = sentinel)
        d.mu.mem_write(FRAME, b"\0" * 0x20 + struct.pack("<7I", 4, 5, 6, 7, 8, 10, RET_SENTINEL))
        d.mu.reg_write(UC_ARM_REG_SP, FRAME)
        d.mu.emu_start(addr, 0x301AA858, count=100000)      # stops at the loop top or the sentinel
        return d.mu.reg_read(UC_ARM_REG_PC)
    def run_until_loop_or_exit(addr, err):
        d.mu.hook_add(unicorn.UC_HOOK_CODE, lambda uc, a, s, u: uc.emu_stop(), begin=RET_SENTINEL, end=RET_SENTINEL)
        return compass_site(addr, err)
    d.mu.mem_write(STATE, struct.pack("<I", 1))
    assert run_until_loop_or_exit(0x301AA91C, 0) == 0x301AA858              # good read -> loop
    assert d.mu.reg_read(UC_ARM_REG_SP) == FRAME and d.r(5) == 0 and d.r(8) == STATE and d.r(10) == 0xA0A0
    for k in range(119):
        assert compass_site(0x301AA8E8, -10) == 0x301AA858, k                 # retried
        assert d.mu.reg_read(UC_ARM_REG_SP) == FRAME and d.r(6) == 0x002B095C and d.r(7) == 0x77
    assert struct.unpack("<I", bytes(d.mu.mem_read(STATE, 4)))[0] == 1, "still running"
    assert compass_site(0x301AA8E8, -10) == RET_SENTINEL                      # 120th in a row: stop
    assert struct.unpack("<I", bytes(d.mu.mem_read(STATE, 4)))[0] == 5
    assert d.mu.reg_read(UC_ARM_REG_SP) == FRAME + 0x20 + 7 * 4, "frame popped like the original"
    assert [d.r(i) for i in (4, 5, 6, 7, 8, 10)] == [4, 5, 6, 7, 8, 10], "registers restored from the frame"
    d.mu.mem_write(STATE, struct.pack("<I", 1))
    compass_site(0x301AA91C, 0)                                               # a good read resets the streak
    assert compass_site(0x301AA8E8, -3) == 0x301AA858
    d.screenshot()
    line = [l for l in d.files["SD:/screenshots/sysinfo.txt"].decode().split("\r\n") if l.startswith("compass:")][0]
    assert line.startswith("compass: state=1 valid=0 reads=2 errors=121 max_in_a_row=120 last_error=-3"), line
    # the patched instructions in the image
    assert struct.unpack("<I", bytes(d.mu.mem_read(0x301AA8E8, 4)))[0] >> 24 == 0xEA
    assert struct.unpack("<I", bytes(d.mu.mem_read(0x301AA91C, 4)))[0] >> 24 == 0xEA
    ok += 1; print("ok  compass task: failed reads retried, stops like stock after 120 in a row, counters reported")

    # --- inert before init -------------------------------------------------------------
    d = ProfDevice()                                         # no boot: random RAM
    d.mu.mem_write(PROF_RAM, b"\0" * 0x280)
    assert d.fread(10) == 10
    d.frame(500)
    assert bytes(d.mu.mem_read(PROF_RAM, 0x280)) == b"\0" * 0x280
    ok += 1; print("ok  wrappers pass straight through until init")

    print("\nall %d tests passed" % ok)


if __name__ == "__main__":
    run_tests()
