"""Write the Ghidra inputs from the stock firmware file:
fw_stock.bin (NOR 0x30080000.., gaps 0xFF), ram_code.bin and ram_data.bin
(the startup code copies them to RAM 0x3F0AAC and 0x3F0C14).

    python make_inputs.py ../update/PN-40.hex
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "mods"))
import pnhex

BASE = 0x30080000
RAM_COPIES = {"ram_code.bin": (0x30081314, 360), "ram_data.bin": (0x3044822C, 465264)}

recs = [(a, v) for _, a, v in pnhex.parse(open(sys.argv[1], "rb").read())]
end = max(a + len(v) for a, v in recs)
img = bytearray(b"\xff" * (end - BASE))
for a, v in recs:
    img[a - BASE:a - BASE + len(v)] = v
with open(os.path.join(HERE, "fw_stock.bin"), "wb") as f:
    f.write(img)
for name, (a, n) in RAM_COPIES.items():
    with open(os.path.join(HERE, name), "wb") as f:
        f.write(img[a - BASE:a - BASE + n])
print("fw_stock.bin %d bytes, %s" % (len(img), ", ".join(RAM_COPIES)))
