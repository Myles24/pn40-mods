# PN-40 bootloader notes

Source: `nor_8MB.bin`, the full 8 MB NOR read out on 2026-10-24 with `bootdump\PN-40.hex`
(sha256 df8b5600…07fb). The firmware part matches `PN-40.hex` byte for byte, so the dump is good.

Built with ST OS20 2.06.14, GCC, Jul 21 2009. Code is ARM (not Thumb) almost everywhere.

## NOR layout (8 MB at 0x30000000)

| Range | Contents |
|---|---|
| 0x30000000-0x3007FFFF | bootloader (about 449 KB used, rest 0xFF) |
| 0x30080000-0x306CC90B | firmware; 0x30080040-0x3008107F is 0xEE fill (see below) |
| 0x307FC000-0x307FC7FF | device identity record; blank (all 0xFF) on this unit |
| 0x307FFFFC | "update finished" marker, 0x12345678 |

## Boot flow

Reset vector -> 0x3003E3F0 (runs from NOR). Uses a register that survives reset,
0x101E901C, whose top byte is the boot mode:

1. Power-on reads 0xFFFFFFFF -> set to 0xFF000000.
2. Top byte 0xB7 -> **fast path**: if word[0x30000000] == word[0x30080000] (both start
   with `ldr pc,[pc,#0x18]`) and word[0x307FFFFC] == 0x12345678, set the top byte to 0xFF
   and jump to 0x30080000. Otherwise set the top byte to 0x42 and fall through to step 3.
3. Anything else -> top byte 0xDB, full init: copy code NOR 0x300011A0 -> RAM 0x72BC
   (to 0x442E8), data NOR 0x3003E970 -> RAM 0x442E8 (to 0x4FDD4), start OS20, main at RAM 0x7674 -> 0x74A4.

main (RAM 0x74A4):
- Mode 0x42 and byte2 == 0x20 (fast path failed after an update) -> "Firmware missing/corrupt."
- Mode 0x42 otherwise -> jump to a RAM stub at 0xA0026000 (clock/SRC setup, then spins; looks like a power-down).
- Otherwise, if input 12 (GPIO 66, active-high; probably SD card detect, unconfirmed) is
  inactive and boot-reg bit 0x400000 is clear: write 0xB7 | reset-reason bits from
  0x101E0018, soft reset (write 0xF to 0x101E0018) -> fast path -> firmware.
- Otherwise run the **update path**: mount SD (`mmc:0:`), firmware update (0x88A4),
  partition update (0x8098), identity check, write 0xB7200000, soft reset.

## Firmware update (RAM 0x88A4)

- Takes the first file in `mmc:0:\update\` whose name ends in `.hex` (any name, any case).
- A file named `do_not_delete` in that folder keeps the update files afterwards. The
  bootloader then shows "Remove SD card & reboot." so the update doesn't loop.
- **Validation pass** ("Validating firmware..."): every line goes through the parser (0x842C):
  - `sscanf("%lx" x17)`: address + 1..16 bytes, each <= 0xFF; line <= 60 chars.
  - address in 0x30080000-0x307FFFFF **or 0x30000000-0x3007FFFF (the bootloader itself)**;
    end < 0x30800000.
  - addresses strictly ascending, no overlap.
  - Failure -> "Firmware file is invalid.", file renamed `<name>_invalid`.
  - Only when the identity byte (record+0x3B) is NOT 0xFF/0x96, i.e. not a PN-40: data is
    scanned for a version string `\0 2.[0-5].<build>`; build <= 165505 -> "FW not valid for this PN."
  - No checksum, no hash, no signature.
- **Programming** ("Updating firmware..."): flash init via FSMC 0x10100000 and CFI at 0x30000000.
  Marker 0x307FFFFC <- 0xFFFFFFFF first. Then per 64 KB erase block: RAM buffer at 0x51DD4
  filled with **0xEE**, hex bytes copied in, block erased and programmed. Any byte the
  file doesn't cover in a touched block becomes 0xEE (that's the 0x30080040 hole). Blocks
  the file doesn't touch are left alone. Finally marker <- 0x12345678.

## Partition update (RAM 0x8098)

`mmc:0:\update\0|1|2` -> `gen:0:|1:|2:` (= MSYS0/1/2, Internal 0/1/2): "Updating %s...",
then deletes the source ("Deleting %s...") unless `do_not_delete` is present.

## Device identity (RAM 0x406D4 / 0x4083C)

Record at 0x307FC000. All 0xFF over 0x800 bytes counts as valid (this unit). Otherwise
record+0x10 must equal a SHA-1 computed by 0x4076C. Model byte record+0x3B: 0xCC = PN-30,
0xFF/0x96 = PN-40, other = "ERR". Failure only shows "Device identity is invalid.".

## Implications for patching

- Firmware patches need nothing special: just keep the .hex format, ascending addresses, <= 16 bytes per line.
- Firmware can grow up to 0x307FBFFF. Stay clear of 0x307FC000 (identity) and the last
  block (marker), because a hex line touching a block erases the whole block.
- **Never put lines below 0x30080000 in a .hex unless you mean to rewrite the bootloader.**
  It accepts them and will rewrite itself; a mistake or power loss there leaves nothing
  to recover from except JTAG.
- If a firmware update is interrupted, the marker stays blank and the bootloader (which
  is untouched) shows "Firmware missing/corrupt." Recovery is putting a good .hex back on the SD card.
