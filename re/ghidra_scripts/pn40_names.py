# Ghidra script: apply re/names.tsv (addr, name, comment) to the program.
# Functions get renamed (created if missing); other addresses get a label.
# @category PN40
import os
from ghidra.program.model.symbol import SourceType
from ghidra.program.model.listing import CodeUnit

HERE = os.path.dirname(os.path.abspath(getSourceFile().getAbsolutePath()))
ROOT = os.path.dirname(HERE)
space = currentProgram.getAddressFactory().getDefaultAddressSpace()
listing = currentProgram.getListing()
n_fn = n_lbl = 0
for line in open(os.path.join(ROOT, "names.tsv")):
    if not line.strip() or line.startswith("#"):
        continue
    parts = line.rstrip("\n").split("\t")
    a = space.getAddress(int(parts[0], 16))
    name = parts[1]
    comment = parts[2] if len(parts) > 2 else ""
    fn = getFunctionAt(a)
    if fn is None and listing.getInstructionAt(a) is not None:
        fn = createFunction(a, name)
    if fn is not None:
        fn.setName(name, SourceType.USER_DEFINED)
        if comment:
            fn.setComment(comment)
        n_fn += 1
    else:
        createLabel(a, name, True, SourceType.USER_DEFINED)
        if comment:
            listing.setComment(a, CodeUnit.PLATE_COMMENT, comment)
        n_lbl += 1
print("pn40_names: %d functions, %d labels" % (n_fn, n_lbl))
