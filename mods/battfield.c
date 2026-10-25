/* "Battery Voltage" data field (field id 161).
 *
 * Shows the battery voltage the way DeLorme's Support Mode "Debug Battery"
 * screen does: the Battery Charger task's latest reading (float volts at
 * 0x003F4FB0) + 0.13 V, here as "2.84 V". Dashes until the first real reading
 * replaces the 3.5 V placeholder the firmware starts with.
 *
 * The field is registered by a 162-entry copy of the firmware's 161-entry
 * pane class pointer table (pane_table in fields.S) with this descriptor
 * appended; build.py points the firmware's three table users at the copy.
 */
#include "fw.h"

#define BATT_PLACEHOLDER 0x40600000u    /* 3.5f: initial .data value, before any reading */
#define BATT_OFFSET_MV   130            /* battery_volts() 0x301A8AD0 adds 0.13 V */

struct ui_result *field_battery_voltage(struct ui_result *res, const struct ui_result *ev, int page, int cell);
int float_bits_to_mv(unsigned b);
int battery_mv(void);
int field_picker_index(const struct pane_desc *desc);

const struct pane_desc battfield_desc = {
    field_battery_voltage, 1, 0, {0, 0}, "Battery Voltage", "Battery Volts",
};

/* v / 10 without libgcc (exact for all 32-bit v) */
static unsigned div10(unsigned v)
{
    return (unsigned)(((unsigned long long)v * 0xCCCCCCCDu) >> 35);
}

/* IEEE single (bits) -> millivolts, rounded; -1 for negative/NaN/huge */
int float_bits_to_mv(unsigned b)
{
    unsigned e = (b >> 23) & 0xFF, m;
    int sh;

    if (b >> 31 || e == 0xFF)
        return -1;
    if (e == 0)
        return 0;
    m = (b & 0x7FFFFF) | 0x800000;      /* value = m * 2^(e-150) */
    sh = 147 - (int)e;                  /* mV = m*125 * 2^(e-147) */
    if (sh <= 0)
        return -1;
    if (sh > 31)
        return 0;
    return (int)((m * 125u + (1u << (sh - 1))) >> sh);
}

/* battery voltage in mV, or -1 if there is no reading yet */
int battery_mv(void)
{
    unsigned bits = fw_battery_raw_bits();
    int mv;

    if (bits == BATT_PLACEHOLDER)
        return -1;
    mv = float_bits_to_mv(bits);
    return mv < 0 ? -1 : mv + BATT_OFFSET_MV;
}

/* "2.84": centivolts with two decimals */
static void format_volts(char *p, int mv)
{
    unsigned cv = div10((unsigned)mv + 5), whole = div10(div10(cv)), frac = cv - whole * 100, t;

    if (whole >= 10) {
        t = div10(whole);
        *p++ = (char)('0' + t);
        whole -= t * 10;
    }
    *p++ = (char)('0' + whole);
    *p++ = '.';
    t = div10(frac);
    *p++ = (char)('0' + t);
    *p++ = (char)('0' + (frac - t * 10));
    *p = 0;
}

/* Same steps as the stock fields (e.g. Battery Life 0x300B621C). */
struct ui_result *field_battery_voltage(struct ui_result *res, const struct ui_result *ev, int page, int cell)
{
    struct ui_result e = *ev, tmp, flush = {0x7D4, 0};
    unsigned char rect[8], ctx[32];
    char value[8];
    int mv;

    if (fw_cell_obscured(fw_page_objs[page], cell) && ev->code == 1)
        goto not_handled;
    if (!fw_field_needs_redraw(&e))
        goto not_handled;
    fw_rect_copy(rect, (char *)fw_page_objs[page] + cell * 4);
    fw_rect_init(ctx, rect);
    fw_draw_field_label(ctx, page, cell, 0);
    mv = battery_mv();
    if (mv < 500) {                     /* no reading, or nonsense */
        fw_draw_field_dashes(ctx, 1, 1);
    } else {
        format_volts(value, mv);
        fw_draw_field_value(ctx, value, "V", 1, 1, 0);
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

/* Replacement for the "Change Info Fields" preselect loop (0x300930AC): the
 * position of desc in the picker list, which holds every pane class with
 * is_field set, minus barometer fields on units without one. The stock loop
 * counted all classes, which only worked while every field id was < 45. */
int field_picker_index(const struct pane_desc *desc)
{
    int baro = fw_has_barometer(), n = 0, i;

    for (i = 0; i < PANE_TABLE_LEN; i++) {
        const struct pane_desc *d = pane_table[i];
        if (!d->is_field || (d->needs_baro && !baro))
            continue;
        if (d == desc)
            return n;
        n++;
    }
    return 0;
}
