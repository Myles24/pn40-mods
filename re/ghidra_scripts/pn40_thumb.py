# Ghidra script: disassemble the Thumb functions reached through ARM->Thumb
# interworking veneers ("ldr ip, [pc]; bx ip; .word target|1").
# @category PN40
import os
from ghidra.program.model.lang import RegisterValue
from java.math import BigInteger

HERE = os.path.dirname(os.path.abspath(getSourceFile().getAbsolutePath()))
ROOT = os.path.dirname(HERE)
prog = currentProgram
space = prog.getAddressFactory().getDefaultAddressSpace()
listing = prog.getListing()
tmode = prog.getRegister("TMode")
ctx = prog.getProgramContext()

done = fixed = 0
for line in open(os.path.join(ROOT, "thumb_seeds.txt")):
    a = space.getAddress(int(line.strip(), 16))
    if not prog.getMemory().contains(a) or not prog.getMemory().getBlock(a).isInitialized():
        continue
    ins = listing.getInstructionAt(a)
    cur = ctx.getValue(tmode, a, False)
    if ins is not None and cur is not None and int(cur) == 1:
        done += 1
        continue
    # wrong (ARM) or no disassembly here: clear, mark Thumb, redo
    fn = getFunctionAt(a)
    if fn is not None:
        removeFunction(fn)
    clearListing(a, a.add(1))
    ctx.setRegisterValue(a, a.add(1), RegisterValue(tmode, BigInteger.ONE))
    disassemble(a)
    createFunction(a, None)
    fixed += 1
print("pn40_thumb: %d already Thumb, %d fixed" % (done, fixed))
