# Ghidra post-script: dump decompiled C, a function table and string xrefs.
# @category PN40
#
# usage (headless): -postScript pn40_export.py <slice> <nslices>
#   Functions are only exported from executable blocks (nor_text, ram_code),
#   and only those with index % nslices == slice, so several headless
#   processes (each on its own copy of the project) can split the work.
#   Slice 0 also writes strings.tsv.
# Output (in ../out):
#   decomp_<slice>.c      "/* FUNC 30085e14 size N callers N */" + C per function
#   functions_<slice>.tsv addr, name, size, n_callers, n_callees, thumb, strings used
#   strings.tsv           string addr, text, functions that reference it
import os
from ghidra.app.decompiler import DecompInterface, DecompileOptions
from ghidra.util.task import ConsoleTaskMonitor

HERE = os.path.dirname(os.path.abspath(getSourceFile().getAbsolutePath()))
OUT = os.path.join(os.path.dirname(HERE), "out")
if not os.path.isdir(OUT):
    os.makedirs(OUT)
args = getScriptArgs()
SLICE = int(args[0]) if len(args) > 0 else 0
NSLICES = int(args[1]) if len(args) > 1 else 1

prog = currentProgram
fm = prog.getFunctionManager()
refman = prog.getReferenceManager()
listing = prog.getListing()
mem = prog.getMemory()
tmode = prog.getRegister("TMode")
ctx = prog.getProgramContext()
mon = ConsoleTaskMonitor()


def users_of(a):
    """Functions referencing a, directly or through a literal-pool word."""
    users = set()
    for r in refman.getReferencesTo(a):
        fn = fm.getFunctionContaining(r.getFromAddress())
        if fn is not None:
            users.add(fn.getEntryPoint().getOffset())
            continue
        for r2 in refman.getReferencesTo(r.getFromAddress()):
            fn2 = fm.getFunctionContaining(r2.getFromAddress())
            if fn2 is not None:
                users.add(fn2.getEntryPoint().getOffset())
    return users


def clean(s):
    return s.replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


# --- strings (slice 0 only) ------------------------------------------------
func_strings = {}
string_rows = []
for d in listing.getDefinedData(True):
    if not d.hasStringValue():
        continue
    try:
        s = unicode(d.getValue())
    except Exception:
        continue
    users = users_of(d.getAddress())
    for u in users:
        func_strings.setdefault(u, []).append(s)
    string_rows.append((d.getAddress().getOffset(), s, users))
if SLICE == 0:
    with open(os.path.join(OUT, "strings.tsv"), "w") as f:
        f.write("addr\ttext\tfunctions\n")
        for a, s, users in string_rows:
            f.write("%08x\t%s\t%s\n" % (a, clean(s).encode("utf-8"), " ".join("%08x" % u for u in sorted(users))))

# --- functions ---------------------------------------------------------------
funcs = [fn for fn in fm.getFunctions(True)
         if mem.getBlock(fn.getEntryPoint()) is not None and mem.getBlock(fn.getEntryPoint()).isExecute()
         and mem.getBlock(fn.getEntryPoint()).isInitialized()]
mine = [fn for k, fn in enumerate(funcs) if k % NSLICES == SLICE]
print("pn40_export: %d code functions, slice %d/%d has %d" % (len(funcs), SLICE, NSLICES, len(mine)))

ifc = DecompInterface()
ifc.setOptions(DecompileOptions())
ifc.toggleCCode(True)
ifc.toggleSyntaxTree(False)
ifc.setSimplificationStyle("decompile")
ifc.openProgram(prog)

with open(os.path.join(OUT, "functions_%d.tsv" % SLICE), "w") as ft, \
        open(os.path.join(OUT, "decomp_%d.c" % SLICE), "w") as fc:
    for k, fn in enumerate(mine):
        ep = fn.getEntryPoint()
        size = fn.getBody().getNumAddresses()
        callers = fn.getCallingFunctions(mon)
        callees = fn.getCalledFunctions(mon)
        tv = ctx.getValue(tmode, ep, False)
        thumb = 1 if (tv is not None and int(tv) == 1) else 0
        strs = func_strings.get(ep.getOffset(), [])
        ft.write("%08x\t%s\t%d\t%d\t%d\t%d\t%s\n" % (ep.getOffset(), fn.getName(), size, len(callers), len(callees),
                 thumb, " | ".join(clean(s).encode("utf-8")[:60] for s in strs[:4])))
        res = ifc.decompileFunction(fn, 60, mon)
        fc.write("/* FUNC %08x size %d callers %d%s */\n" % (ep.getOffset(), size, len(callers),
                 " thumb" if thumb else ""))
        if res is not None and res.decompileCompleted():
            fc.write(res.getDecompiledFunction().getC().encode("utf-8"))
        else:
            fc.write("/* decompile failed: %s */\n" % (res.getErrorMessage() if res else "no result"))
        fc.write("\n")
        if k % 250 == 0:
            print("pn40_export[%d]: %d/%d" % (SLICE, k, len(mine)))
            fc.flush()
print("pn40_export[%d]: done" % SLICE)
