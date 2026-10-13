"""Faded Art Engine 2: Schwarz + Weiß gemischt (zwei durchgehende Fäden, beliebig oft gewechselt).

Testsieger aus dem Vergleich vom 13.10.2026 (15 Porträts x 6 Versionen): deutlich detailreicher als ein einzelner
schwarzer Faden. Physikalisches Mischmodell: jeder Faden zieht die Zellen, die er quert, ein Stück in seine Farbe
(c += a * w * (Farbe - c)). Pro Schritt wird die Farbe und Sehne mit dem größten Fehlerrückgang gewählt.
Wechsel-Bremse: die andere Farbe kommt nur dran, wenn sie deutlich mehr bringt (gleiche Qualität, ~60 % weniger Wechsel).

Speichersparend für Render Free (512 MB): Sehnen nur einmal je Nagel abgelegt, Pixelindex uint16, Deckung uint8.
"""
import threading
import numpy as np
from PIL import Image, ImageFilter, ImageOps

RASTER = 180          # Rechenraster in Pixeln (220 bringt kaum mehr, kostet aber deutlich Zeit und Speicher)
SWITCH_PEN = 100.0    # Wechsel-Bremse (0 = aus)
PATIENCE = 400

_cache, _cache_lock = {}, threading.Lock()


def _line(p0, p1, size):
    d = p1 - p0
    n = max(int(np.hypot(*d)), 2)
    t = np.linspace(0, 1, n)
    x, y = p0[0] + d[0] * t, p0[1] + d[1] * t
    steep = abs(d[1]) > abs(d[0])
    main, frac = (np.round(y).astype(int), x) if steep else (np.round(x).astype(int), y)
    f0 = np.floor(frac).astype(int)
    w1 = frac - f0
    if steep:
        xs, ys = np.concatenate([f0, f0 + 1]), np.concatenate([main, main])
    else:
        xs, ys = np.concatenate([main, main]), np.concatenate([f0, f0 + 1])
    ws = np.concatenate([1 - w1, w1])
    ok = (xs >= 0) & (xs < size) & (ys >= 0) & (ys < size) & (ws > 0.02)
    idx, ws = ys[ok] * size + xs[ok], ws[ok]
    u, inv = np.unique(idx, return_inverse=True)
    return u.astype(np.uint16), (np.minimum(np.bincount(inv, ws), 1.0) * 255 + .5).astype(np.uint8)


class Lines:
    """Alle erlaubten Sehnen, je Nagel flach hintereinander (für eine Rechnung über alle Kandidaten auf einmal)."""
    def __init__(self, n, size, min_gap):
        self.n, self.size = n, size
        c = (size - 1) / 2
        t = 2 * np.pi * np.arange(n) / n
        pins = np.stack([c + (c - 1) * np.cos(t), c + (c - 1) * np.sin(t)], 1)
        segs = {}
        for i in range(n):
            for j in range(i + 1, n):
                if min(j - i, n - (j - i)) >= min_gap:
                    segs[(i, j)] = _line(pins[i], pins[j], size)
        self.per_pin = []
        for i in range(n):
            js, ids, ws, st, pos = [], [], [], [], 0
            for j in range(n):
                s = segs.get((min(i, j), max(i, j)))
                if s is not None:
                    js.append(j); st.append(pos); ids.append(s[0]); ws.append(s[1]); pos += len(s[0])
            st.append(pos)
            st = np.array(st)
            self.per_pin.append((np.array(js), np.concatenate(ids), np.concatenate(ws),
                                 st[:-1].copy(), st, (1 / np.sqrt(np.diff(st))).astype(np.float32)))
        del segs

    def seg(self, a, b):
        js, ids, ws, st, st_full, _ = self.per_pin[a]
        i = int(np.searchsorted(js, b))
        return ids[st_full[i]:st_full[i + 1]], ws[st_full[i]:st_full[i + 1]].astype(np.float32) / 255


def lines_for(n):
    """Sehnen je Nagelzahl; nur eine Nagelzahl im Speicher (Render Free)."""
    with _cache_lock:
        if n not in _cache:
            _cache.clear()
            _cache[n] = Lines(n, RASTER, max(8, n // 16))
        return _cache[n]


def clahe(g, clip, tiles=8):
    """Lokaler Kontrast (wie in app.py). g: uint8 -> float 0..1."""
    H, W_ = g.shape
    th, tw = H // tiles, W_ // tiles
    luts = np.empty((tiles, tiles, 256), np.float32)
    for i in range(tiles):
        for j in range(tiles):
            t = g[i * th:(i + 1) * th, j * tw:(j + 1) * tw]
            h = np.bincount(t.ravel(), minlength=256).astype(np.float64)
            lim = max(1.0, clip * t.size)
            h = np.minimum(h, lim) + np.maximum(h - lim, 0).sum() / 256
            c = np.cumsum(h)
            luts[i, j] = (c - c[0]) / max(c[-1] - c[0], 1)
    yc, xc = (np.arange(H) + 0.5) / th - 0.5, (np.arange(W_) + 0.5) / tw - 0.5
    y0, x0 = np.clip(np.floor(yc).astype(int), 0, tiles - 1), np.clip(np.floor(xc).astype(int), 0, tiles - 1)
    y1, x1 = np.minimum(y0 + 1, tiles - 1), np.minimum(x0 + 1, tiles - 1)
    wy, wx = np.clip(yc - y0, 0, 1)[:, None], np.clip(xc - x0, 0, 1)[None, :]
    Y0, X0, Y1, X1 = y0[:, None], x0[None, :], y1[:, None], x1[None, :]
    top = (1 - wx) * luts[Y0, X0, g] + wx * luts[Y0, X1, g]
    bot = (1 - wx) * luts[Y1, X0, g] + wx * luts[Y1, X1, g]
    return ((1 - wy) * top + wy * bot).astype(np.float32)


def _blur(a, r):
    im = Image.fromarray((np.clip(a, 0, 1) * 255 + .5).astype(np.uint8))
    return np.asarray(im.filter(ImageFilter.GaussianBlur(r)), np.float32) / 255


def target(path, size=RASTER, gamma=1.0, clip=0.008, edges=False, bg=0.12):
    """Zielhelligkeit 0..1 und Gewichtung. Freigestellter Hintergrund (Transparenz) wird gleichmäßig dunkel."""
    src = Image.open(path)
    alpha = None
    if src.mode in ("RGBA", "LA"):
        src = src.convert("RGBA")
        alpha = np.asarray(src.getchannel("A").resize((size, size), Image.Resampling.LANCZOS), np.float32) / 255
        src = Image.alpha_composite(Image.new("RGBA", src.size, (255, 255, 255, 255)), src)
    g8 = np.asarray(ImageOps.autocontrast(src.convert("L"), cutoff=1).resize((size, size), Image.Resampling.LANCZOS))
    g = clahe(g8, clip) if clip else g8.astype(np.float32) / 255
    m = alpha if alpha is not None else np.ones_like(g)
    v = g[m > 0.5] if (m > 0.5).sum() > 50 else g.ravel()
    lo, hi = np.percentile(v, [2, 98])
    g = np.clip((g - lo) / max(hi - lo, 1e-3), 0, 1) ** gamma
    if edges:                                             # Konturen schärfen
        g = np.clip(g + 0.6 * (g - _blur(g, size / 110)), 0, 1)
    t = m * g + (1 - m) * bg
    yy, xx = np.mgrid[:size, :size]
    r = np.hypot(xx - size / 2, yy - size / 2) / (size / 2)
    w = (0.5 + 1.0 * m) * np.clip(1.2 - 0.35 * r ** 2, 0.7, 1.2)   # Motiv und Bildmitte zählen mehr
    if edges:
        e = np.hypot(*np.gradient(_blur(g, 1.0)))
        w = w * (1 + 1.2 * np.clip(e / (np.percentile(e, 99) + 1e-6), 0, 1))
    return t.astype(np.float32), w.astype(np.float32)


def solve(t, w, L, colors, board, a, max_lines, progress=None, pen=SWITCH_PEN):
    """Gibt die Spannreihenfolge [(Farbe, von, nach), ...] zurück."""
    size, n, K = L.size, L.n, len(colors)
    lo, hi = min(min(colors), board), max(max(colors), board)
    t = (lo + (hi - lo) * np.clip(t, 0, 1)).ravel().astype(np.float32)      # Ziel in den erreichbaren Bereich
    c = np.full_like(t, board)
    yy, xx = np.mgrid[:size, :size]
    W = (w * (np.hypot(xx - (size - 1) / 2, yy - (size - 1) / 2) < size / 2 - 1)).ravel().astype(np.float32)
    cur = []
    for col in colors:                                    # Startnagel: bester erster Zug je Farbe
        best = (-np.inf, 0)
        for p in range(0, n, 3):
            js, ids, ws, st, _, _ = L.per_pin[p]
            cv = c[ids]; nv = cv + a * (ws / 255) * (col - cv); tv = t[ids]
            g = np.add.reduceat(W[ids] * ((cv - tv) ** 2 - (nv - tv) ** 2), st).max()
            best = max(best, (g, p))
        cur.append(best[1])
    used = np.zeros((K, n, n), bool)
    order, err, best, dead, last = [], 0.0, (0.0, 0), [0] * K, 0
    for step in range(max_lines):
        cand = []
        for k in range(K):
            if dead[k] > PATIENCE:
                continue
            js, ids, ws, st, _, inv = L.per_pin[cur[k]]
            cv = c[ids]; nv = cv + a * (ws / 255) * (colors[k] - cv); tv = t[ids]
            sc = np.add.reduceat(W[ids] * ((cv - tv) ** 2 - (nv - tv) ** 2), st) * inv
            sc[used[k, cur[k], js]] = -np.inf
            i = int(np.argmax(sc))
            cand.append((float(sc[i]), k, int(js[i])))
        if not cand:
            break
        g, k, j = max(cand, key=lambda x: x[0] if (x[1] == last or x[0] <= 0) else x[0] / (1 + pen))
        if g == -np.inf:
            break
        if g <= 0:
            dead[k] += 1
            if all(d > PATIENCE for d in dead):
                break
        else:
            dead[k] = 0
        p = cur[k]
        used[k, p, j] = used[k, j, p] = True
        ids, ws = L.seg(p, j)
        cv = c[ids]; nv = cv + a * ws * (colors[k] - cv); tv = t[ids]
        err -= float(np.sum(W[ids] * ((cv - tv) ** 2 - (nv - tv) ** 2)))
        c[ids] = nv
        order.append((k, p, j)); cur[k] = j; last = k
        if err < best[0]:
            best = (err, len(order))
        if progress and step % 200 == 0:
            progress(step / max_lines, order)
    return order[:best[1]]


def runs_of(order):
    """[(Farbe, [Pins])] in Spannreihenfolge, wie color_runs in app.py: jede Farbe ist ein eigener durchgehender Faden."""
    out = []
    for k, a, b in order:
        if out and out[-1][0] == k:
            out[-1][1].append(b)
        else:
            out.append((k, [a, b]))
    return out
