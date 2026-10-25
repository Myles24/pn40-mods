# Ghidra pre-script: PN-40 firmware 2.9.6.70319 memory map and function seeds.
# @category PN40
#
# The program is imported as raw ARM (v5t, little endian) at 0x30080000.
# This adds what the startup code (0x30081480) sets up in RAM, so pointers
# into RAM data resolve, and disassembles every BL target / code pointer.
import os
from ghidra.program.model.mem import MemoryConflictException
from java.io import FileInputStream

HERE = os.path.dirname(os.path.abspath(getSourceFile().getAbsolutePath()))
ROOT = os.path.dirname(HERE)
mem = currentProgram.getMemory()
space = currentProgram.getAddressFactory().getDefaultAddressSpace()


def addr(a):
    return space.getAddress(a)


def file_block(name, start, path, write=True, execute=False):
    stream = FileInputStream(path)
    size = os.path.getsize(path)
    b = mem.createInitializedBlock(name, addr(start), stream, size, monitor, False)
    b.setRead(True); b.setWrite(write); b.setExecute(execute)
    stream.close()


def uninit(name, start, size, volatile=False, execute=False):
    b = mem.createUninitializedBlock(name, addr(start), size, False)
    b.setRead(True); b.setWrite(True); b.setExecute(execute); b.setVolatile(volatile)


# .text ends at 0x30448080 (startup copy table: constructor lists 0x30448080-
# 0x3044822C, .data load image to 0x304B9B9C, then rodata/fonts/map tables).
# Making the rest non-executable keeps analysis from inventing ~175k
# "functions" in data.
fw = mem.getBlock(addr(0x30080000))
mem.split(fw, addr(0x30448080))
code = mem.getBlock(addr(0x30080000))
code.setName("nor_text"); code.setWrite(False); code.setExecute(True)
ro = mem.getBlock(addr(0x30448080))
ro.setName("nor_rodata"); ro.setWrite(False); ro.setExecute(False)
# RAM layout from the startup copy/zero loops
uninit("vectors", 0x0, 0x40, execute=True)
uninit("bss", 0x40, 0x3F0AAC - 0x40)
file_block("ram_code", 0x3F0AAC, os.path.join(ROOT, "ram_code.bin"), execute=True)
file_block("data", 0x3F0C14, os.path.join(ROOT, "ram_data.bin"))
uninit("noinit_heap_stacks", 0x462584, 0x800000 - 0x462584)
# on-chip memories and peripherals seen in the startup code
uninit("esram", 0xA0000000, 0x30000, execute=True)
uninit("periph_101x", 0x10100000, 0x100000, volatile=True)

# function seeds: BL targets and code pointers (precomputed in seeds.txt)
listing = currentProgram.getListing()
n = 0
for line in open(os.path.join(ROOT, "seeds.txt")):
    a = addr(int(line.strip(), 16))
    if not code.contains(a):
        continue
    if listing.getInstructionAt(a) is None:
        disassemble(a)
    if getFunctionAt(a) is None:
        createFunction(a, None)
    n += 1
print("pn40_setup: memory map added, %d seeds" % n)
