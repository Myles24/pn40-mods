r"""Render one layer of a .pm0 raster map to a PNG, placing every tile by its bounds.

    python pm0view.py map.pm0 [layer] [out.png]    (default: the finest layer)

Needs numpy and Pillow.
"""
import os, sys
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pm0


def rgb565_to_rgb(a):
    a = a.astype(np.uint32)
    r, g, b = a >> 11 & 31, a >> 5 & 63, a & 31
    return np.stack([r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2], -1).astype(np.uint8)


def render(m, k, flip=True):
    """-> RGB array of layer k at its native resolution (north up)"""
    L = m.layers[k]
    tiles = [m.tiles[p] for p in set(L.grid.pages()) if p]
    x0 = min(t.bounds[0] for t in tiles)
    y0 = min(t.bounds[1] for t in tiles)
    x1 = max(t.bounds[2] for t in tiles)
    y1 = max(t.bounds[3] for t in tiles)
    # pixel size from the tiles themselves (x varies a little with rounding)
    sx = np.median([(t.bounds[2] - t.bounds[0]) / t.width for t in tiles])
    sy = np.median([(t.bounds[3] - t.bounds[1]) / t.height for t in tiles])
    out = np.zeros((int(round((y1 - y0) / sy)), int(round((x1 - x0) / sx)), 3), np.uint8)
    for t in tiles:
        px = np.array(pm0.decode_rows(t), np.uint16)
        # stored south to north (y grows south): flip to north up
        if flip:
            px = px[::-1]
        c = int(round((t.bounds[0] - x0) / sx))
        r = int(round((t.bounds[1] - y0) / sy))
        h, w = px.shape
        out[r:r + h, c:c + w] = rgb565_to_rgb(px)[:out.shape[0] - r, :out.shape[1] - c]
    return out


def main():
    m = pm0.read(sys.argv[1])
    k = int(sys.argv[2]) if len(sys.argv) > 2 else max(range(len(m.layers)), key=lambda i: m.layers[i].level)
    out = sys.argv[3] if len(sys.argv) > 3 else os.path.splitext(os.path.basename(sys.argv[1]))[0] + "_L%d.png" % k
    Image.fromarray(render(m, k)).save(out)
    print(out)


if __name__ == "__main__":
    main()
