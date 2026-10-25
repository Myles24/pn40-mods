# PN-40 graphics and display

Scope: LCD driver, surfaces, drawing/text API, fonts, colours, bitmaps, backlight.
Names are listed in `graphics_names.tsv`. Almost all of the graphics library is **Thumb** code at
0x30424ED4-0x3042B060. ARM code calls it through 12-byte veneers (`ldr ip,[pc]; bx ip; .word fn|1`).
From a mod, either BL to the veneer from ARM code, or BLX the Thumb address directly.

## 1. Display hardware and buffers

### Panel interface (confirmed)
| what | value | evidence |
|---|---|---|
| Panel | 176x220 RGB565, has its own GRAM (no LCD controller scanning RAM) | `lcd_init` 30428b10 register sequence; all pixel output is CPU writes to a port |
| Command/register port | `*(u16*)0x38020000` (pointer at RAM 00460878) | ram_data initial value; the register index and its value are both written here: `*p=0x12; *p=x;` |
| GRAM data port | `0x38000000` (pointer at 0046087c). Any address 0x38000000-0x3801FFFF is the data port, so `lcd_write_pixels` 30429a7c (ARM) streams with incrementing addresses | 30429a7c increments both pointers |
| Window registers | 0x12 = x start, 0x13 = y start, 0x15 = x end, 0x16 = y end, 0x18 = entry mode (3 = normal, 0 = flipped, 0x0B = read-back) | `lcd_fill_rect_direct` 30425d50, `win_push_rect_to_lcd` 30428f2c |
| Other registers | 0x80 = display on(1)/off(0); 0x10=0x48, 0x11=0, 0x1C=0, 0x03=0x10, 0x88=2, 0x7E=4/5, 0x7F=1 at init; 0xFD/0xFE/0xE0/0xEE/0xEF power-up and OTP poll (busy bit 0x80 of reg 0xEE) | `lcd_init` 30428b10, `lcd_off` 30428a04 |
| Orientation | `lcd_orientation` 003ea630: 0 = default; the panel is mounted rotated, so the driver writes `0xAF-x`, `0xDB-y`. 2 = flipped 180 degrees (direct coordinates). Heading code 300b8bf0 adds 180 deg when it is 2 | 30425230, 30425708 |
| Read-back | `lcd_read_rect` 304250f4 sets entry mode 0x0B and reads GRAM; pixels arrive rotated by 1 bit and are fixed with `((v>>1|v<<15)&0x1F000)>>1 | ...&0x7FF` | 304250f4 |
| DMA | **none** on the flush path: every pixel is a CPU store (30428f2c -> 30429a7c) | |
| Bus counter | `lcd_bus_count` 003ea638 += N per register burst (statistics only) | |

The panel controller chip is not identified (register map does not match ILI/HX/S6D parts I know): **open**.

### RAM buffers (confirmed)
Three full-screen RGB565 buffers, 176*220*2 = 0x12E80 bytes each, back to back:

| address | role | evidence |
|---|---|---|
| 0x000E4368 | `popup_buf`: buffer of every type-1 (popup) window | all popup `win_create(1, &0xE4368, ...)` calls (decomp 13599, 45392, 62795, ...) |
| 0x000F71E8 | `shadow_buf` (`*00460880`): save-under copy of the panel area covered by a popup | `gfx_init` 300c5bf0 sets 00460880; `win_create` type 1 calls `lcd_read_rect`; `win_destroy` calls `lcd_write_from_shadow` |
| 0x0010A068 | `screen_buf`: back buffer of the main screen window | `gfx_init`: `g_screen_win = win_create(0, 0x10A068, 0,0, 175,219, 176,220, 0,0, 0xFFFF)` |

So the scheme is: **draw into a RAM window buffer, then push it to the panel GRAM** with `win_update`.
The panel GRAM is the only front buffer. There is no page flipping.
Boot (main 30081d64, around line 559): `gfx_init()`, then a second type-0 window on buffer 0x00030054
gets the splash bitmap (`gfx_blit` of 304d567c PN-40, or 304d56ac PN-30 if `*(cfg+0x3B)==0xCC`),
then `win_update` and `backlight_set_level(0x7F)`.

### Window / surface struct (0x38 bytes, table `win_table` 0x003EF778, 16 slots) (confirmed)
The screenshot "image struct" uses the same layout (take_screenshot memsets 0x38 bytes).
| off | type | meaning |
|---|---|---|
| +0x00 | u16 | in use |
| +0x02 | u16 | type: 0 = normal surface (screen), 1 = popup/overlay |
| +0x04 | u16 | overlap code against the topmost popup (0 = none, 5 = fully covered, 1-15 = which edges); used by `win_update` |
| +0x08/+0x0A | u16 | outer width/height (x2-x1+1, y2-y1+1); `gfx_fill_rect` bounds-checks against these |
| +0x0C..+0x12 | u16 x4 | x1, y1, x2, y2 on the panel (inclusive) |
| +0x18/+0x1A | u16 | buffer width (= stride in pixels) / buffer height; all primitives clip to these |
| +0x1C/+0x1E | u16 | client width/height (outer minus border) |
| +0x20..+0x26 | u16 x4 | client x1, y1, x2, y2 on the panel |
| +0x28/+0x2A | u16 | scroll: buffer x/y that maps to client x1/y1 |
| +0x2C,+0x2E,+0x30,+0x32 | u16 | overlap rect with the popup: x1, x2, y1, y2 |
| +0x34 | u16* | pixel buffer, row-major RGB565, pixel (x,y) at `buf[y*stride + x]` |

Drawing coordinates are always **buffer-relative** (0,0 = top-left of the window buffer).

### Window and flush functions
| addr (veneer) | signature | notes |
|---|---|---|
| 304258e0 (304479ac) | `win* win_create(type, u16 *buf, x1, y1, x2, y2, buf_w, buf_h, border_w, unused, border_color)` | type 1 saves the panel under it into shadow, computes overlap codes, draws the border straight to the panel. Returns 0 if no free slot or x2>=176/y2>=220. Example: 30097ae0 `win_create(1,&0xE4368,0,0x6E,0xAF,0xDB,0xB0,0x6E,0,c,c)` |
| 304253f0 (304479a0) | `win_destroy(win, 0)` | popup: copy shadow back to the panel |
| 3042907c (304478ec) | `win_update(win)` | push client area (from buffer at scroll +0x28/+0x2A) to the panel; parts covered by the popup are written to the shadow buffer instead |
| 30429da8 (30447b80) | `win_update_rect(win, x, y, w, h)` | partial flush; example 30111988 |
| 30428f2c (304478bc) | `win_push_rect_to_lcd(win, sx, sy, lcd_x, lcd_y, w, h)` | raw push, no overlap logic |
| 30428e20 (30447a30) | `gfx_copy_rect(src_win, dst_win, sx, sy, dx, dy, w, h)` | buffer to buffer |
| 304263d0 (304478a4) | `lcd_capture_screen(image*)` | screenshot: fills +0x18..+0x2A for 176x220 and reads the panel into image+0x34 |

### How the UI requests a flush (confirmed)
- `ui_main_loop` 30085e14: after each `ui_dispatch_event`, if `ui_screen_dirty` (00053b5c) then
  `win_update(g_screen_win)`; if `ui_dirty_win` (00053b60) and the app has a popup layer
  (`FUN_3009b5cc(app)`), `win_update(that window)`.
- A handler requests it by returning (or passing to `ui_handle_result`) result **{0x7D4, win}**:
  win = 0 or `g_screen_win` sets the screen flag, any other window is stored in 00053b60
  (`ui_result_redraw` 300e3a64). Result {0x7D5, x} redraws the current page.
  Example: 30096ac8 builds `{0x7D4, popup}` and calls `ui_handle_result`.
- Page show (30093604): page+0x34 = surface; page handler gets event 0x7DC, each widget 0x7D9
  (draw), then `win_update(surface)`, then 0x7DD / 0x7DA / 0x7D7. Page hide (3009351c): 0x7DE / 0x7DB.

## 2. Drawing API (Thumb, all confirmed from code)
There is no graphics-context object in the UI: every call takes the window, and font and colours
explicitly. Colours are RGB565 in the low 16 bits. Coordinates are u16, rectangles are **inclusive**
(x1,y1,x2,y2). Functions return 0 or -1 (out of bounds; most primitives draw nothing at all if any
endpoint is outside the buffer, they do not clip).

| addr (veneer) | signature | example caller |
|---|---|---|
| 30425e64 (30447bc8) | `gfx_put_pixel(win, x, y, color)` | 3010edec |
| 30426b60 (30447988) | `gfx_hline(win, x1, x2, y, color)` | 300ba344 |
| 30425e98 (30447958) | `gfx_vline(win, x, y1, y2, color)` | 300ba344 |
| 3042792c (3044791c) | `gfx_fill_rect(win, x1, y1, x2, y2, color)`; 101 call sites | 301390dc: `gfx_fill_rect(g_screen_win,0,0,0x58,0x6E,0)` |
| 30428750 (30447a0c) | `gfx_rect(win, x1, y1, x2, y2, color)` outline | 300d7828 |
| 304287ec (30447b44) | `gfx_line(win, x1, y1, x2, y2, color)` | 30122620 |
| 30427414 (none) | `gfx_thick_line(win, x1, y1, x2, y2, width, color)`, round caps | internal |
| 30428974 (none) | `gfx_polygon(win, s16 pts[], color)` closed outline, pts end with -1 | internal |
| 30426c28 (none) | `gfx_fill_polygon(win, u16 pts[], color)` pts end with 0xFFFF | 30427414 |
| 304261a4 (30447b20) | `gfx_circle(win, cx, cy, r, color)` | 30122a8c |
| 304272a4 (30447b14) | `gfx_fill_circle(win, cx, cy, r, color)` | 301235d4 |
| 30429884 (304478d4) | `gfx_blit(win, bitmap*, x, y)`: clipped, transparent key | 3008f048 |
| 30429974 (30447dc0) | `gfx_blit_clipped(win, bitmap*, x, y, cx1, cy1, cx2, cy2)` | 3032d1c0 |
| 30428d3c (304479b8) | `sprite_draw(win, sprite*, x, y)` saves background first | 3009ec4c (map cursor) |
| 30428cb8 (304479c4) | `sprite_erase(win, sprite*)` restores background | 3009ed00 |
| 30426394 (30447ab4) | `u16 rgb_to_565(r, g, b)` (8-bit inputs) | 3032dc20 |
| 304263ac (30447910) | `rgb565_to_rgb(c, u8 *r, u8 *g, u8 *b)` | `write_bmp` 30086c80 |
| 300ba344 (ARM) | `ui_draw_frame(win, x1, y1, x2, y2, selected, ...)` bevelled box: outline theme colour 2, inner highlight colour 0, colour 4 when selected | 30096ac8 |

### Text
`fg == bg` means a transparent background; otherwise each glyph cell `(x-1, y-1)..(x+w, y+line_h)`
is filled with bg first. Characters advance by glyph width + 1. y is the top of the text line.
Bytes 0x80-0x9F are mapped to ASCII look-alikes (`cp1252_to_ascii` 3042b000); '\n', '\r' and '\0'
have width 0 (except in the symbol font).

| addr (veneer) | signature | example caller |
|---|---|---|
| 30427e54 (30447970) | `int gfx_draw_string(win, font, x, y, str, fg, bg)` -> end x | 3009618c: `gfx_draw_string(g_screen_win, *0x3F2368, 0x11, y, "Display Info at Startup", fg, bg)` |
| 30427bc8 (30447a78) | `int gfx_draw_char(win, font, x, y, ch, fg, bg)` -> width | 3012a588 |
| 3042799c (30447a54) | `gfx_draw_glyph(...)` same as above but no cp1252 mapping | |
| 3042809c (304478e0) | `gfx_draw_string_centered(win, font, x, y, width, str, fg, bg, cursor_idx)`; uses the ellipsis variant if too wide | 300d840c, 30084ff8 |
| 30427ee4 (30447934) | `gfx_draw_string_ellipsis(win, font, x, y, maxw, str, fg, bg, cursor_idx)` | 3011a74c |
| 30427c1c (30447964) | `gfx_draw_string_cursor(win, font, x, y, str, fg, bg, cursor_idx, max_chars)`; draws a 1-px caret before char cursor_idx; '\n' -> next line (y += line_h+1) | 300fe34c (text entry) |
| 3042813c (304479f4) | `gfx_draw_string_ex(win, font, x, y, str, fg, halo_color, halo_w, spacing, rotation 0-3)` | 300a710c |
| 304266b4 (30447dcc) | `gfx_draw_char_ex(win, font, x, y, ch, fg, halo_color, halo_w, rotation)` | 3032d364 |
| 30428460 (30447a18) | `gfx_draw_string_ex` truncated to a width with "..." | none |
| 30426a48 / 30426ad8 | halo-1 string / centered halo string | |
| 304286a4 (30447a60) | `gfx_draw_text(win, font, x, y, str, fg, bg)`; supports raw 1bpp fonts (font+0xC == 0) | 30137e70 |

Measuring:
| addr (veneer) | signature |
|---|---|
| 30426414 (30447a6c) | `int font_char_width(font, ch)` |
| 30426634 (30447994) | `int font_string_width(font, str)` = sum(w) + (n-1) |
| 30426648 (30447a00) | `int font_string_width_ex(font, str, halo, spacing)` = sum(w) + (n-1)*spacing + 2*halo |
| 30426620 (30447b74) | width of the first line (up to '\n') |
| 304265a8 | `font_width_until(font, str, ndelim, delims)` |
| 30426430 (30447a40) | `char *font_fit_chars(font, str, maxw)` first char that does not fit |
| 30426488 (3044797c) | `bool font_text_fits(font, str, maxw)` |
| 304264a0 (3044794c) | `char *font_wrap_line(font, str, maxw)` start of the next word-wrapped line |
| font+0x12 | line height in pixels (UI code reads `*(u16*)(font+0x12)` directly) |

## 3. Fonts (confirmed; glyphs rendered from flash to check)
Descriptors are RAM structs filled by `gfx_init_tables` 30424ed4; glyph pointer tables (256 x u32)
are in .data (initial values in ram_data.bin), glyph data in flash 0x30699DD4-~0x3069F000.

Font struct:
| off | meaning |
|---|---|
| +0x00/+0x02 | cell width/height (32x32 for text fonts) |
| +0x04 | glyph table size in bytes (0x400) |
| +0x06 | first char code |
| +0x08 | 0x30 (unknown) |
| +0x0A | number of glyphs (0x100) |
| +0x0C | glyph header size: 1, 2 or 4 bytes (0 = raw 1bpp font, `gfx_draw_text` path) |
| +0x0E | top trim: glyph row 0 is drawn at y + top - trim |
| +0x10 | baseline / ascent (likely; not read by the library) |
| +0x12 | line height |
| +0x14 | u32 -> glyph pointer table |

Glyph: header then a 1bpp bitstream, MSB first, rows packed without padding
(width*height bits). Header size 4: bytes {left, top, right, bottom} blank margins of the cell;
glyph w = cell_w - left - right, h = cell_h - top - bottom. Size 2: nibbles {left|top, right|bottom}.
Size 1: 2-bit fields {left:7-6, top:5-4, right:3-2, bottom:1-0}.

| font struct | glyph table | size | style | `font_table` slots (0x003F2348 + 4*i) |
|---|---|---|---|---|
| 0x003EFB58 `font_10` | 0x004608D0 | line 10, trim 2 | regular | 3, 6, 8, 10, 11, 12 (slot 3: 168 refs, slot 8: 105) |
| 0x003EFAF8 `font_10b` | 0x004610D0 | line 10, trim 2 | bold | 4, 13 |
| 0x003EFB28 `font_9b` | 0x00460CD0 | line 9, trim 1 | bold small | 0 |
| 0x003EF730 `font_13b` | 0x004614D0 | line 13, trim 2 | bold | 5, 7, 9 (slot 9: 69 refs) |
| 0x003EF760 `font_16b` | 0x004618D0 | line 16, trim 3 | bold | 1 |
| 0x003EFB40 `font_23b` | 0x00461CD0 | line 23, trim 5 | bold | 2 |
| 0x003EFB10 `font_sym16` | 0x0046088C | 16x16, 17 glyphs (codes 0-16), header 2 | symbols | used directly (10 code refs) |

Selection: UI code loads a slot global (e.g. `*(font**)0x003F2368`) and passes it as the font
argument. The slots are never written at runtime (no "large font" setting found).

## 4. Colours and themes (confirmed)
- API colours are RGB565 u16 values (white 0xFFFF, black 0, red 0xF800).
- UI theme colours come from `theme_color(settings=0x0012B76C, idx)` 300d0090:
  scheme = settings+0x38 (0x0012B7A4); if settings+0x128 ("Use Midnight at sunset", 0x0012B894)
  and the after-sunset flag 0x0004326A are set, scheme 4 (Midnight) is used. Table
  `color_scheme_table` 0x003F23A4 (5 pointers to u16[8] in flash):

| idx | name (strings in 300c3df4) | table | colours 0..7 |
|---|---|---|---|
| 0 | Spring | 304d49a0 | CFF9 C7EF 778E 9709 0C41 FFFF 0000 FFFF (greens) |
| 1 | Summer | 304d49b0 | FFF3 FF6E FE65 FE6C CB20 FFFF 0000 FFFF (yellows) |
| 2 | Autumn | 304d49c0 | FE6C FC67 FAA0 CB20 6000 FFFF 0000 FFFF (oranges) |
| 3 | Winter | 304d49d0 | 9E7F 7DFF 34DF 03F9 018C FFFF 0000 FFFF (blues) |
| 4 | Midnight | 304d49e0 | CE79 8C51 000C 4A69 033F 0000 FFFF 0000 (greys, inverted text) |

Accessors (ARM, no args): idx0 300d0114, idx2 300d0144, idx3 300d018c, idx4 300d012c,
idx5 300d015c, idx6 300d00e4 (text, 63 callers), idx7 300d00fc. Effective scheme index: 300d01a4.
Slot meaning (likely): 0-4 light to dark fills, 5 background, 6 text, 7 highlight text.
The map engine has its own 8-bit palette conversion (30335218 / 303354a4).

## 5. Bitmaps and sprites (confirmed; rendered from flash to check)
Bitmap struct (0x18 bytes, flash):
| off | meaning |
|---|---|
| +0x00 | u32 width |
| +0x04 | u32 height |
| +0x08 | u32 transparent key (0xF81F magenta in 459 of 489 found; a value > 0xFFFF makes it opaque, likely) |
| +0x0C | u16* raw RGB565 pixels, row-major, uncompressed |
| +0x10/+0x14 | u32 hotspot x/y (e.g. 7,7 for 16x16 symbols, 7,14 for pins); not used by `gfx_blit` |

About 460 headers with key 0xF81F: 0x30526E88-0x30527BD8 (139: UI icons, 15x15 heading arrows table
at `*0x003F2880`, 100x126 compass-calibration pictures at 0x305274A0..), 0x306A0814-0x306A1A8C
(198, mostly 16x16 waypoint/map symbols), 0x306BA6A4-0x306C86EC (114; e.g. default symbol 0x306BE7FC).
Full screens: 304d567c PN-40 splash, 304d5694 PN-40 dimmed, 304d56ac PN-30 splash, 304d56c4 PN-30
dimmed, 304d773c Navionics logo 148x28. No compression and no resource IDs: code references
the structs by address (e.g. `gfx_blit(g_screen_win, &0x306BE7FC, x, y)` in 3008f048).

Sprite (map cursor) struct, 0x18 bytes in RAM: +0 w, +2 h, +4/+6 saved x/y, +8 pixels (flash,
raw RGB565), +0xC save-under buffer, +0x10 0, +0x14 key 0x1010. Four of them: 003ef6e8 (12x12,
type 1), 003ef748 (24x24, type 2), 003ef700 (type 3), 003ef718 (type 4); chosen by 3009c450.

## 6. Backlight (confirmed)
- `backlight_set_level(u16 level)` 30428a6c (veneer 304478f8): 0 = GPIO 13 output low (off);
  >= 255 = GPIO 13 high (full); 1-254 = pin 13 switched to alt function 2 and the PWL PWM duty set
  to level. Level stored in 003ea634, read back by `backlight_get_level` 30425454.
- PWL register at 0x101E8100 (pointer 003ebd74): bits 0-7 duty, bit 8 enable (30441e10/54/bc).
- Levels used: 0x7F at boot, 0x33 and 0xB2 in the power/charger logic 300857a8 (exact trigger: likely USB/charger state), 100 (300d3350),
  sweep 1..254 in a factory test (301391a4). "Backlight Intensity" setting UI: 30129260 (not traced).

## 7. Mod recipe (likely, from the above)
```c
win *scr = *(win**)0x000E435C;                      // main screen surface
font *f  = *(font**)0x003F2354;                     // regular 10 px UI font
gfx_fill_rect(scr, 0, 0, 175, 20, theme_color4());   // via veneer 0x3044791C
gfx_draw_string(scr, f, 4, 5, "Hello", 0xFFFF, 0xFFFF); // fg==bg: transparent (veneer 0x30447970)
win_update(scr);                                    // veneer 0x304478EC; or return {0x7D4, 0}
```
For a temporary overlay use `win_create(1, (u16*)0xE4368, x1,y1,x2,y2, w,h, border, 0, color)`,
draw into it with buffer-relative coordinates, `win_update`, then `win_destroy(w, 0)`.

## Open questions
- Panel controller part number (register set 0x10-0x18, 0x7E-0x88, 0xE0-0xFE).
- Font +0x08 (0x30) and +0x10 meaning; what the 17 `font_sym16` glyphs are used for.
- Who sets orientation 2 (only caller of 30424eb4 passes 0; 300d0fe8 toggles setting 0x0012BB28).
- 30424b98..30424e6c (same Thumb block) are an unlock-code base32/CRC16 routine, not graphics.
