#!/bin/sh
# Create the Ghidra project re/proj from fw_stock.bin and run auto-analysis.
# Takes about an hour. Afterwards run run_export.sh to produce out/decomp.c.
#   GHIDRA=/path/to/ghidra_11.1.2_PUBLIC sh analyze.sh
set -e
: "${GHIDRA:?set GHIDRA to the Ghidra install folder}"
RE=$(cd "$(dirname "$0")" && pwd)
GH="$GHIDRA/support/analyzeHeadless"
[ -f "$GH" ] || GH="$GH.bat"
# Windows Ghidra wants Windows paths; elsewhere this is a no-op
w() { cygpath -w "$1" 2>/dev/null || echo "$1"; }
cd "$RE"
mkdir -p proj out
# raw ARM v5t image at its NOR address; pn40_setup adds the RAM blocks
# (ram_code.bin, ram_data.bin) and the function seeds before analysis
MAXMEM=4G "$GH" "$(w "$RE/proj")" pn40 -import "$(w "$RE/fw_stock.bin")" \
    -loader BinaryLoader -loader-baseAddr 0x30080000 -processor ARM:LE:32:v5t \
    -scriptPath "$(w "$RE/ghidra_scripts")" -preScript pn40_setup.py > out/analyze.log 2>&1
grep -h "pn40_setup\|REPORT\|Traceback" out/analyze.log
# Thumb functions behind ARM->Thumb veneers
MAXMEM=4G "$GH" "$(w "$RE/proj")" pn40 -process fw_stock.bin -noanalysis \
    -scriptPath "$(w "$RE/ghidra_scripts")" -postScript pn40_thumb.py > out/thumb.log 2>&1
grep -h "pn40_thumb\|Traceback" out/thumb.log
