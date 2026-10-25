# Ghidra script: clear "no return" on every function that can actually return.
# Ghidra's Non-Returning Functions analyzer marks many of this firmware's
# struct-returning helpers as no-return, which truncates every caller's
# decompilation. Keep the flag only where the body has no return path.
# @category PN40
from ghidra.program.model.block import BasicBlockModel

fm = currentProgram.getFunctionManager()
listing = currentProgram.getListing()
cleared = kept = 0
kept_list = []
for fn in fm.getFunctions(True):
    if not fn.hasNoReturn():
        continue
    returns = False
    for ins in listing.getInstructions(fn.getBody(), True):
        ft = ins.getFlowType()
        if ft.isTerminal():
            returns = True
            break
    if returns:
        fn.setNoReturn(False)
        cleared += 1
    else:
        kept += 1
        kept_list.append("%08x" % fn.getEntryPoint().getOffset())
print("pn40_noreturn: cleared %d, kept %d: %s" % (cleared, kept, " ".join(kept_list[:40])))
