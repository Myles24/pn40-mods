"""Build a PN-40.hex whose screenshot button dumps the whole NOR flash to SD.

usage: python make_bootdump.py <stock PN-40.hex> <output PN-40.hex>

Applies everything in ..\\patched\\make_patch.py (GPS week rollover fix) plus
one more patch. The result is for reading out the bootloader once; reinstall
patched\\PN-40.hex afterwards to get normal screenshots back.

With screenshots turned on, Hold POWER + press ENTER writes
SD:\\screenshots\\screencap-<date>.bmp as before, but the file is not a BMP:
it is the raw 8 MB NOR flash, 0x30000000-0x307FFFFF:
    0x30000000-0x3007FFFF  bootloader (not in any update file)
    0x30080000-0x306CC90B  firmware (compare against PN-40.hex to check the dump)
    0x306CC90C-0x307FFFFF  whatever lies above the firmware
The boot code maps all 8 MB of NOR as readable (cached section, manager
domain; see 0x30081B28), so nothing here writes to flash.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "patched"))
import make_patch

make_patch.PATCHES.append(
    # NOR dump in place of the screenshot BMP writer.
    #
    # 0x30086C80 is write_bmp(path, image), called only from the screenshot
    # handler at 0x30084C40, which ignores its return value. The new body
    # copies NOR to the file in 16 KB chunks through a heap buffer, using the
    # same libc calls the original used:
    #     malloc 0x30189890  free 0x30189748
    #     fopen  0x30187E80  fwrite 0x30187904  fclose 0x30187DB8
    #     push  {r4-r8, lr}
    #     mov   r5, r0                  ; path
    #     mov   r0, #0x4000
    #     bl    malloc
    #     movs  r6, r0
    #     beq   done
    #     mov   r0, r5
    #     ldr   r1, =0x30523A00         ; "w", the mode the BMP writer used
    #     bl    fopen
    #     movs  r4, r0
    #     beq   freebuf
    #     mov   r7, #0x30000000
    # chunk:
    #     mov   r0, r6
    #     mov   r1, r7
    #     mov   r2, #0x4000
    # copy:
    #     ldr   r3, [r1], #4
    #     str   r3, [r0], #4
    #     subs  r2, r2, #4
    #     bne   copy
    #     mov   r0, r6
    #     mov   r1, #1
    #     mov   r2, #0x4000
    #     mov   r3, r4
    #     bl    fwrite
    #     cmp   r0, #0x4000
    #     bne   close
    #     add   r7, r7, #0x4000
    #     cmp   r7, #0x30800000
    #     blo   chunk
    # close:
    #     mov   r0, r4
    #     bl    fclose
    # freebuf:
    #     mov   r0, r6
    #     bl    free
    # done:
    #     mov   r0, #0
    #     pop   {r4-r8, pc}
    #     .word 0x30523A00
    (
        "NOR dump on screenshot",
        0x30086C80,
        "f04f2de940d04de20040a0e10170a0e1de0a04eb000050e30040a0019800000a"
        "0090a0e10010a0e32830a0e3ba30cde1bc10cde1b821d7e1be20cde10030a0e3"
        "b031cde1ba01d7e1b201cde1b431cde10130a0e3b631cde11830a0e3b831cde1"
        "ba11cde1bc11cde18230a0e1023083e0930002e00320c2e3043082e2be31cde1"
        "2338a0e1b032cde1b212cde1b412cde1",
        "f0412de90050a0e10109a0e3ff0a04eb0060b0e11a00000a0500a0e168109fe5"
        "760404eb0040b0e11300000a0372a0e30600a0e10710a0e10129a0e3043091e4"
        "043080e4042052e2fbffff1a0600a0e10110a0e30129a0e30430a0e1080304eb"
        "010950e30200001a017987e2c20557e3eeffff3a0400a0e12e0404eb0600a0e1"
        "900a04eb0000a0e3f081bde8003a5230",
    )
)

if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    make_patch.main(sys.argv[1], sys.argv[2])
