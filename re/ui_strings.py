"""List printable strings in the firmware's UI text area with their addresses."""
import re, sys
img = open(sys.argv[1], "rb").read(); B = 0x30080000
lo, hi = int(sys.argv[2], 16), int(sys.argv[3], 16)
for m in re.finditer(rb"[\x20-\x7e\xa0-\xff]{3,}\x00", img[lo - B:hi - B]):
    s = m.group()[:-1]
    if sum(c.isalpha() for c in s.decode("latin1")) >= 3:
        print("%08X %s" % (lo + m.start(), s.decode("latin1")))
