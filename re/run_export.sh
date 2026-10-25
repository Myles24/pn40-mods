#!/bin/sh
# Decompile the analysed project with N parallel headless Ghidra processes.
# Each worker gets its own copy of the project (Ghidra locks a project to one
# process), exports every Nth function, then merge.py joins the results.
#   GHIDRA=/path/to/ghidra_11.1.2_PUBLIC sh run_export.sh [N]
set -e
: "${GHIDRA:?set GHIDRA to the Ghidra install folder}"
N=${1:-6}
RE=$(cd "$(dirname "$0")" && pwd)
GH="$GHIDRA/support/analyzeHeadless"
[ -f "$GH" ] || GH="$GH.bat"
w() { cygpath -w "$1" 2>/dev/null || echo "$1"; }
cd "$RE"
# apply names.tsv to the master project first
MAXMEM=4G "$GH" "$(w "$RE/proj")" pn40 -process fw_stock.bin -noanalysis \
    -scriptPath "$(w "$RE/ghidra_scripts")" -postScript pn40_names.py \
    -postScript pn40_noreturn.py > out/names.log 2>&1
grep -h "pn40_names\|pn40_noreturn\|Traceback" out/names.log
export MAXMEM=2G
for k in $(seq 0 $((N - 1))); do
    rm -rf "proj_$k" && cp -r proj "proj_$k"
    "$GH" "$(w "$RE/proj_$k")" pn40 -process fw_stock.bin -noanalysis -readOnly \
        -scriptPath "$(w "$RE/ghidra_scripts")" \
        -postScript pn40_export.py "$k" "$N" > "out/export_$k.log" 2>&1 &
done
wait
grep -h "pn40_export" out/export_*.log | grep -E "code functions|done"
for k in $(seq 0 $((N - 1))); do rm -rf "proj_$k"; done
python -I merge.py "$N"
