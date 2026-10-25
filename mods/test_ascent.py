"""Emulator test for total ascent/descent (ascent.c), run against the built
out\\PN-40.hex.

Feeds elevation samples through the real patched call site in trip_update
(0x300C623C, so the stock trip_update_elev runs too) and resets through the
real trip_reset_elev callers. Stubs: time_now, ticks_per_sec, the elevation
source query, trip_save (records what would be written) and the field
drawing calls; the field's number formatting (spec init, fmt_elevation and
the firmware's soft-float code) runs for real.

usage: python test_ascent.py [out dir]
"""
import os
import struct
import sys
from fractions import Fraction

from unicorn.arm_const import UC_ARM_REG_SP

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_nmealog import Device, SYMS, TICKS_PER_S, REGS

TRIP = 0x0011CEE8
PREFS = 0x0012B76C
ASCENT_RAM = 0x0076FA00
ELEV_CALL = 0x300C623C             # "bl trip_update_elev" in trip_update
TRIP_RESET_ELEV = 0x300C5CD4
TRIP_RESET_ALL = 0x300C5F54
TRIP_RESET_DIALOG = 0x300C63D8     # applies the Reset dialog's checkboxes, then trip_save
UNSET_MAX = -99999.0

GPS, BARO, BARO_AUTO = 1, 2, 3
FW_EXTRA = dict(elev_source=0x300B8D8C, trip_save=0x300C638C, cell_obscured=0x3009244C,
                needs_redraw=0x300AF724, rect_copy=0x300BA15C, rect_init=0x300BA548, draw_label=0x300B010C,
                draw_value=0x300B0410, draw_dashes=0x300AFE78, handle_result=0x300E3B70)


def dwords(v):
    lo, hi = struct.unpack("<II", struct.pack("<d", v))
    return lo, hi


class AscentDevice(Device):
    def __init__(self):
        super().__init__()
        self.source = BARO
        self.saves = []
        self.drawn = None
        import unicorn
        for name, addr in FW_EXTRA.items():
            self.mu.hook_add(unicorn.UC_HOOK_CODE, self._stub2, begin=addr, end=addr, user_data=name)
        for name in ("ascent_sample", "ascent_reset", "ascent_init"):
            self.mu.hook_add(unicorn.UC_HOOK_CODE, self._c_entry, begin=SYMS[name], end=SYMS[name])

    def _stub2(self, uc, addr, size, name):
        r0, r1, r2 = self.r(0), self.r(1), self.r(2)
        if name == "elev_source":
            return self._ret(self.source)
        if name == "trip_save":
            assert r0 == TRIP
            self.saves.append(self.totals())
            return self._ret(0)
        if name == "cell_obscured":
            return self._ret(0)
        if name == "needs_redraw":
            return self._ret(1)
        if name == "draw_value":
            self.drawn = (self.cstr(r1), self.cstr(r2), self.r(3),
                          struct.unpack("<II", bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_SP), 8))))
            return self._ret(0)
        if name == "draw_dashes":
            self.drawn = "dashes"
            return self._ret(0)
        return self._ret(0)

    # firmware state ----------------------------------------------------------
    def totals(self):
        up, down = struct.unpack("<II", bytes(self.mu.mem_read(TRIP + 0x28, 8)))
        return up, down

    def set_totals(self, up, down):
        self.mu.mem_write(TRIP + 0x28, struct.pack("<II", up, down))

    def max_min(self):
        mx = struct.unpack("<d", bytes(self.mu.mem_read(TRIP + 4, 8)))[0]
        mn = struct.unpack("<d", bytes(self.mu.mem_read(TRIP + 0xC, 8)))[0]
        return mx, mn

    def st(self):
        names = ("magic have_ref ref_cm last_cm last_time source saved_valid saved_up saved_down "
                 "samples jumps source_changes saves bad_samples garbage_resets").split()
        vals = struct.unpack("<IIiiIiiII6I", bytes(self.mu.mem_read(ASCENT_RAM, 4 * len(names))))
        return dict(zip(names, vals))

    # entry points --------------------------------------------------------------
    def sample(self, metres, dt=1.0):
        """One trip_update elevation sample, dt seconds after the previous one."""
        self.now = (self.now + int(dt * TICKS_PER_S)) & 0xFFFFFFFF
        lo, hi = dwords(metres)
        for reg, v in zip(REGS, (TRIP, lo, hi)):
            self.mu.reg_write(reg, v)
        self.run_site(ELEV_CALL)

    def samples(self, values, dt=1.0):
        for v in values:
            self.sample(v, dt)

    def reset_dialog(self, elev=1, values=0):
        self.mu.mem_write(TRIP + 0x58, struct.pack("<IIII", values, 0, elev, 0))
        self.call(TRIP_RESET_DIALOG, (TRIP,))

    def field(self, which):
        res = 0x00180000
        ev = 0x00180010
        self.mu.mem_write(ev, struct.pack("<II", 1, 0))
        self.drawn = None
        self.call(SYMS["field_total_" + which], (res, ev, 1, 0))
        assert struct.unpack("<II", bytes(self.mu.mem_read(res, 8))) == (0xB, 0)
        return self.drawn


class Model:
    """What ascent_sample should do, in Python."""
    def __init__(self, up, down):
        self.up, self.down = up, down
        self.ref = self.last = self.src = None
        self.t = 0
        self.saved = (up, down)
        self.saves = []

    def sample(self, c, src, dt):
        if src == 0 and self.ref is not None:
            src = self.src
        if self.ref is None or src != self.src:
            self.ref = c
        else:
            dt = max(dt, TICKS_PER_S)
            if abs(c - self.last) * TICKS_PER_S > 1500 * dt:
                self.ref = c
            else:
                db = 1000 if src == GPS else 300
                if c - self.ref > db:
                    self.up = min(self.up + c - self.ref, 100000000)
                    self.ref = c
                elif self.ref - c > db:
                    self.down = min(self.down + self.ref - c, 100000000)
                    self.ref = c
        self.src, self.last = src, c
        if self.up - self.saved[0] >= 2500 or self.down - self.saved[1] >= 2500:
            self.saved = (self.up, self.down)
            self.saves.append(self.saved)


def fresh(totals=(0, 0)):
    d = AscentDevice()
    d.mu.mem_write(TRIP, b"\0" * 0x6C)
    d.boot()
    d.call(TRIP_RESET_ALL, (TRIP,))        # trip_defaults' path (first boot)
    d.set_totals(*totals)
    return d


def cm(x):
    return int(round(x * 100))


def run_tests():
    ok = 0

    # --- conversions ------------------------------------------------------------
    d = fresh()
    to_cm, to_dbl = SYMS["double_to_cm"], SYMS["cm_to_double"]
    out = 0x00180100
    cases = [0.0, -0.0, 0.004, 0.005, -0.005, 0.0049, 1.0, 1.005, 123.456, -123.456, 8848.86, -430.5,
             2 ** 20, 2097151.99, 1e-300, 99999.995]
    for v in cases:
        lo, hi = dwords(v)
        assert d.call(to_cm, (lo, hi, out)) == 1, v
        got = struct.unpack("<i", bytes(d.mu.mem_read(out, 4)))[0]
        x = abs(Fraction(v)) * 100                         # exact value of the double
        want = int(x + Fraction(1, 2)) * (-1 if v < 0 else 1)
        assert got == want, (v, got, want)
    for v in (float("nan"), float("inf"), -float("inf"), 2.0 ** 21, -3e6):
        assert d.call(to_cm, (*dwords(v), out)) == 0, v
    for c in (0, 1, 50, 99, 100, 12345, 2500, 32808, 9999999, 100000000):
        d.call(to_dbl, (c,))
        v = struct.unpack("<d", struct.pack("<II", d.r(0), d.r(1)))[0]
        assert abs(v - c / 100) <= max(1e-9 * c / 100, 1e-12), (c, v)
    ok += 1; print("ok  double <-> centimetre conversion (rounding, sign, NaN/inf/huge)")

    # --- boot init from heap garbage; first sample only sets the reference -----
    d = fresh()
    s = d.st()
    assert s["magic"] == 0x41534344 and s["have_ref"] == 0 and s["samples"] == 0, s
    assert d.totals() == (0, 0) and d.max_min() == (UNSET_MAX, -UNSET_MAX)
    d.sample(250.0)
    assert d.totals() == (0, 0) and d.st()["have_ref"] == 1 and d.st()["ref_cm"] == 25000
    assert d.max_min() == (250.0, 250.0), "stock trip_update_elev must still run"
    assert all(sp % 8 == 0 for sp in d.c_entry_sp), "C code entered with misaligned stack"
    ok += 1; print("ok  boot: state initialised, first sample sets reference, stock max/min still updated")

    # --- barometric deadband 3 m --------------------------------------------------
    d = fresh()
    d.samples([100.0, 102.9, 97.1, 101.5, 98.0, 102.99, 97.01] * 20)
    assert d.totals() == (0, 0), d.totals()
    d.sample(103.01)
    assert d.totals() == (301, 0), "change > 3 m counts in full"
    d.samples([103.01 + k for k in range(1, 98)])       # steady 1 m/s climb to 200.01
    up = d.totals()[0]
    assert up == 301 + 9600 and d.st()["ref_cm"] == 19901, (up, d.st())   # counted in 4 m steps
    ok += 1; print("ok  barometer: +-2.99 m noise ignored, > 3 m counts, steady climb %.2f m" % (up / 100))

    # --- descent and negative elevations ------------------------------------------
    d = fresh()
    d.samples([10.0, 5.0, 0.0, -5.0, -12.0, -20.0, -30.0])
    assert d.totals() == (0, 4000), d.totals()
    d.samples([-25.0, -15.0, -5.0, 0.0, 10.0])
    assert d.totals() == (4000, 4000), d.totals()
    d.sample(-400.0, dt=60)                               # Dead Sea: 410 m in 60 s is 6.8 m/s
    assert d.totals() == (4000, 4000 + 41000), d.totals()
    ok += 1; print("ok  descent and negative elevations")

    # --- GPS deadband 10 m ---------------------------------------------------------
    d = fresh()
    d.source = GPS
    d.samples([500.0, 509.9, 500.0, 490.1, 497.0, 505.0, 491.0, 500.0, 509.99, 500.0] * 20)
    assert d.totals() == (0, 0), d.totals()
    d.sample(510.5)
    d.sample(499.0)
    assert d.totals() == (1050, 1150), d.totals()
    ok += 1; print("ok  GPS elevation: +-9.99 m noise ignored, > 10 m counts")

    # --- jump guard: > 15 m/s is a calibration, not a climb --------------------------
    d = fresh()
    d.samples([1000.0, 1000.5])
    d.sample(1060.0)                                   # +59.5 m in 1 s (re-calibration)
    assert d.totals() == (0, 0) and d.st()["jumps"] == 1
    d.sample(1065.0)                                   # counts from the new reference
    assert d.totals() == (500, 0), d.totals()
    d.sample(1079.9)                                   # +14.9 m in 1 s: fast but allowed
    assert d.totals() == (1990, 0) and d.st()["jumps"] == 1
    d.sample(1040.0, dt=2)                             # -39.9 m in 2 s: jump
    assert d.totals() == (1990, 0) and d.st()["jumps"] == 2
    d.sample(1140.0, dt=10)                            # +100 m after a 10 s gap (10 m/s): counts
    assert d.totals() == (11990, 0), d.totals()
    d.samples([1142.0, 1144.0], dt=0)                  # fixes drained back to back: gap taken as 1 s
    assert d.totals() == (12390, 0) and d.st()["jumps"] == 2, (d.totals(), d.st())
    d.sample(1160.0, dt=0)                             # +16 m with no time between: still a jump
    assert d.st()["jumps"] == 3 and d.totals() == (12390, 0)
    ok += 1; print("ok  jump guard: > 15 m/s ignored and reference moved; gaps; back-to-back fixes")

    # --- source switch restarts the reference ---------------------------------------
    d = fresh()
    d.samples([300.0, 300.5, 301.0])
    d.source = GPS                                     # barometer switched off: GPS reads 8 m higher
    d.sample(309.0)
    assert d.totals() == (0, 0) and d.st()["source_changes"] == 1
    d.source = BARO_AUTO
    d.sample(302.0)
    assert d.totals() == (0, 0) and d.st()["source_changes"] == 2
    d.sample(306.0)
    assert d.totals() == (400, 0)
    d.source = 0                                       # "not ready" mid-way: keep the last source
    d.sample(310.5)
    assert d.totals() == (850, 0) and d.st()["source_changes"] == 2
    ok += 1; print("ok  source switch (baro/GPS/auto-cal) restarts the reference without counting")

    # --- timer wrap ------------------------------------------------------------------
    d = fresh()
    d.now = 0xFFFFFFFF - TICKS_PER_S // 2
    d.samples([50.0, 55.0, 60.0, 65.0])
    assert d.totals() == (1500, 0) and d.st()["jumps"] == 0
    ok += 1; print("ok  32-bit timer wrap between samples")

    # --- save every 25 m of growth ------------------------------------------------------
    d = fresh()
    d.samples([0.0] + [4.0 * k for k in range(1, 7)])  # 24 m: no save yet
    assert d.saves == [] and d.totals() == (2400, 0)
    d.sample(28.0)
    assert d.saves == [(2800, 0)], d.saves
    d.samples([4.0 * k for k in range(8, 20)])           # up to 76 m
    assert d.saves == [(2800, 0), (5600, 0)], d.saves
    d.samples([76.0 - 4.0 * k for k in range(1, 8)])     # 28 m down
    assert d.saves == [(2800, 0), (5600, 0), (7600, 2800)], d.saves
    d.samples([48.0, 52.0, 48.0, 52.0, 48.0] * 3)        # 4 m wobbles: +24 m / +24 m
    assert d.totals() == (10000, 5200) and len(d.saves) == 3
    d.samples([48.0, 49.0, 50.0, 49.0] * 50)             # inside the deadband
    assert d.totals() == (10000, 5200) and len(d.saves) == 3
    d.sample(52.0)
    assert d.saves[-1] == (10400, 5200) and len(d.saves) == 4
    ok += 1; print("ok  trip_save called each time a total grows 25 m since the last save")

    # --- totals loaded from DEVICE_STATE.TXT: kept, next save 25 m later --------------
    d = fresh(totals=(123456, 65432))
    d.samples([0.0, 4.0, 8.0, 12.0, 16.0, 20.0, 24.0])
    assert d.saves == [] and d.totals() == (123456 + 2400, 65432)
    d.sample(28.0)
    assert d.saves == [(123456 + 2800, 65432)]
    ok += 1; print("ok  totals carried over from the saved trip; save baseline set at first sample")

    # --- garbage in the struct (> 1,000,000 m) is cleared ---------------------------------
    d = fresh(totals=(0xDEADBEEF, 5))
    d.sample(100.0)
    assert d.totals() == (0, 0) and d.st()["garbage_resets"] == 1
    ok += 1; print("ok  implausible totals cleared")

    # --- saturates instead of wrapping ----------------------------------------------------
    d = fresh(totals=(100000000 - 100, 0))
    d.samples([0.0, 10.0])
    assert d.totals() == (100000000, 0)
    ok += 1; print("ok  totals saturate at 1,000,000 m")

    # --- long random walk vs a Python model of the algorithm -------------------------
    import random
    rnd = random.Random(40)
    d = fresh(totals=(5000, 7000))
    m = Model(5000, 7000)
    elev, src = 1500.0, BARO
    for i in range(3000):
        if rnd.random() < 0.003:
            src = rnd.choice((GPS, BARO, BARO_AUTO, 0))
        r = rnd.random()
        dt = rnd.choice((0, 0.2, 1, 1, 1, 1, 2, 5, 30))
        if r < 0.01:
            elev += rnd.uniform(-200, 200)                 # calibration jump
        else:
            elev += rnd.uniform(-1.5, 1.6) * max(dt, 0.5) + rnd.gauss(0, 2 if src != GPS else 6)
        elev = round(elev, 2)
        d.source = src
        d.sample(elev, dt)
        m.sample(cm(elev), src, int(dt * TICKS_PER_S))
        assert d.totals() == (m.up, m.down), (i, d.totals(), m.up, m.down)
    assert d.saves == m.saves
    print("    %d samples: ascent %.2f m, descent %.2f m, %d jumps, %d saves"
          % (3000, m.up / 100, m.down / 100, d.st()["jumps"], len(d.saves)))
    ok += 1; print("ok  3000-sample random walk matches the reference model")

    # --- resets: dialog "Reset Trip Elevations", full reset, other checkboxes -----------
    d = fresh()
    d.samples([0.0, 10.0, 20.0, 30.0, 20.0])
    assert d.totals() == (3000, 1000)
    saves = len(d.saves)
    d.reset_dialog(elev=0, values=1)                      # odometer/times only
    assert d.totals() == (3000, 1000)
    d.reset_dialog(elev=1)
    assert d.totals() == (0, 0) and d.max_min() == (UNSET_MAX, -UNSET_MAX)
    assert d.saves[-1] == (0, 0) and len(d.saves) == saves + 2, "stock dialog save writes the cleared totals"
    assert d.st()["have_ref"] == 0
    d.sample(25.0)                                        # new reference, nothing counted vs the old one
    assert d.totals() == (0, 0)
    d.samples([30.0, 40.0])
    assert d.totals() == (1500, 0)
    n = len(d.saves)
    d.samples([40.0 + 4 * k for k in range(1, 4)])        # 27 m since reset: saves (baseline was 0)
    assert len(d.saves) == n + 1
    d.call(TRIP_RESET_ALL, (TRIP,))
    assert d.totals() == (0, 0)
    d.call(TRIP_RESET_ELEV, (TRIP,))
    assert d.totals() == (0, 0) and d.max_min() == (UNSET_MAX, -UNSET_MAX)
    ok += 1; print("ok  resets: dialog checkbox, full trip reset, direct; stock max/min reset preserved")

    # --- before boot init: sample does nothing extra, reset still clears ------------------
    d = AscentDevice()                                    # ASCENT_RAM is random, no boot()
    d.mu.mem_write(TRIP, b"\0" * 0x6C)
    d.mu.mem_write(ASCENT_RAM, b"\0" * 64)               # anything but the magic
    d.call(TRIP_RESET_ELEV, (TRIP,))
    d.set_totals(700, 800)
    d.samples([0.0, 50.0, 100.0])
    assert d.totals() == (700, 800) and d.saves == [] and d.max_min() == (100.0, 0.0)
    d.call(TRIP_RESET_ELEV, (TRIP,))
    assert d.totals() == (0, 0) and d.max_min() == (UNSET_MAX, -UNSET_MAX)
    assert d.mu.mem_read(ASCENT_RAM, 64) == b"\0" * 64, "reset before init must not touch mod RAM"
    ok += 1; print("ok  inert before init (stock elevation code unaffected); reset before init safe")

    # --- the fields: real spec init + fmt_elevation ----------------------------------------
    d = fresh()
    feet = [(0, "0"), (40, "1"), (10000, "328"), (123456, "4050"), (3047980, "99999"),
            (3048000, "100000"), (100000000, "3280840")]
    metres = [(0, "0"), (49, "0"), (51, "1"), (10000, "100"), (123456, "1235"), (9999949, "99999"),
              (9999950, "100000"), (100000000, "1000000")]
    for meas, unit, cases in ((0, " ft", feet), (1, " m", metres), (2, " ft", feet), (3, " m", metres)):
        d.mu.mem_write(PREFS + 0x90, struct.pack("<I", meas))   # Measures: statute, km, NM/ft, NM/m
        for c, text in cases:
            for which, tot in (("ascent", (c, 0)), ("descent", (0, c))):
                d.set_totals(*tot)
                assert d.field(which) == (text, unit, 1, (1, 0)), (meas, which, c, d.field(which))
    d.set_totals(100000001, 0)
    assert d.field("ascent") == "dashes" and d.field("descent") == ("0", " m", 1, (1, 0))
    ok += 1; print("ok  fields format through the stock fmt_elevation (ft/m, up to 1,000,000 m), dashes for garbage")

    # --- the fields are registered as ids 162/163 ---------------------------------------
    tab = SYMS["pane_table"]
    ids = struct.unpack("<164I", bytes(d.mu.mem_read(tab, 164 * 4)))
    assert ids[161] == SYMS["battfield_desc"] and ids[162] == SYMS["ascent_desc"] and ids[163] == SYMS["descent_desc"]
    for i, name in ((162, "Elevation: Total Ascent"), (163, "Elevation: Total Descent")):
        fn, flags, long_p, short_p = struct.unpack("<IIII", bytes(d.mu.mem_read(ids[i], 16)))
        assert flags & 0xFF == 1 and (flags >> 8) & 0xFF == 0 and d.cstr(long_p) == name
    assert struct.unpack("<I", bytes(d.mu.mem_read(0x3011BA04, 4)))[0] == 0xE35500A4
    d.call(0x30090928, (1, 0xFFFF))                       # static ctor that fills the stock descriptors (RAM)
    nfields = sum(d.mu.mem_read(p + 4, 1)[0] for p in ids)
    assert nfields == 48, nfields                          # 45 stock + battery + ascent + descent
    assert nfields <= 50, "field picker holds 50 entries"
    assert d.cstr(struct.unpack("<I", bytes(d.mu.mem_read(ids[12] + 8, 4)))[0]) == "Elevation: Maximum"
    ok += 1; print("ok  pane table: ids 161-163, picker bound 164, %d of 50 picker entries" % nfields)

    print("\nall %d tests passed" % ok)


if __name__ == "__main__":
    run_tests()
