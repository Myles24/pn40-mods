#!/bin/sh
# print decompiled functions by address: sh fn.sh 300939d0 300e4934 ...
DECOMP="$(dirname "$0")/out/decomp.c"
for a in "$@"; do
  awk -v a="$a" 'index($0, "/* FUNC " a " ")==1 {p=1; print; next} p && index($0,"/* FUNC ")==1 {exit} p' "$DECOMP"
done
