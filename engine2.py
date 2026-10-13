"""Faded Art Engine 2: Schwarz + Weiß gemischt (zwei durchgehende Fäden).

v2 (13.10.2026, Vergleich 15 Porträts): Der Fehler wird im leicht weichgezeichneten Bild gemessen, also so, wie das Auge
das Fadenbild aus Abstand sieht. Ergebnis gegenüber v1: Ähnlichkeit 98,1 -> 98,45 (bei allen 15 Fotos besser),
etwa 20 % weniger Faden und rund 45 statt 500 Farbwechsel.

Mischmodell: jeder Faden zieht die Zellen, die er quert, ein Stück in seine Farbe (c += a * w * (Farbe - c)).
Gewinn eines Fadens (linearisiert): -sum(d * (2R + q d)), R = G*G*(c - Ziel), alle EVERY Fäden exakt neu berechnet.
Rechenkern mit numba (wie in app.py); fehlt numba, rechnet dieselbe Logik mit numpy (langsamer).
Speichersparend für Render (512 MB): alle Sehnen einmal je Nagel in einem flachen Array, Pixelindex uint16, Deckung uint8.
"""
import threading
import numpy as np
from PIL import Image, ImageFilter, ImageOps

RASTER = 180          # Rechenraster in Pixeln
SIGMA_MM = 2.3        # Weichzeichnung in mm auf dem Brett (Betrachtungsabstand ca. 1-2 m)
EVERY = 10            # so oft wird der weichgezeichnete Fehler exakt neu berechnet
SWITCH_PEN = 100.0    # Wechsel-Bremse (0 = aus)
DEAD = 8              # so viele Abschnitte ohne Verbesserung, dann Stopp

_cache, _cache_lock = {}, threading.Lock()

try:
    from numba import njit
    _NUMBA = True
except Exception:                                          # pragma: no cover
    _NUMBA = False


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
    """Alle erlaubten Sehnen als flache Liste (CSR): je Nagel p die Kandidaten cand_off[p]..cand_off[p+1]."""
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
        off, cj, cs, ce, ids, ws, pos = [0], [], [], [], [], [], 0
        for i in range(n):
            for j in range(n):
                s = segs.get((min(i, j), max(i, j)))
                if s is not None:
                    cj.append(j); cs.append(pos); ids.append(s[0]); ws.append(s[1]); pos += len(s[0]); ce.append(pos)
            off.append(len(cj))
        del segs
        self.ids, self.ws = np.concatenate(ids), np.concatenate(ws)
        self.cand_off, self.cand_j = np.array(off, np.int64), np.array(cj, np.int64)
        self.cand_s, self.cand_e = np.array(cs, np.int64), np.array(ce, np.int64)
        self.cand_inv = (1 / np.sqrt(self.cand_e - self.cand_s)).astype(np.float32)


def lines_for(n):
    """Sehnen je Nagelzahl; nur eine Nagelzahl im Speicher."""
    with _cache_lock:
        if n not in _cache:                               # 240 und 360 Nägel passen beide in den Speicher (~35 + 70 MB)
            _cache[n] = Lines(n, RASTER, max(8, n // 16))
        return _cache[n]


def _gauss_matrix(size, sigma):
    r = np.arange(size)
    G = np.exp(-(r[:, None] - r[None, :]) ** 2 / (2 * sigma ** 2))
    return (G / G.sum(1, keepdims=True)).astype(np.float32)


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
    w = (0.75 + 0.5 * m) * np.clip(1.15 - 0.3 * r ** 2, 0.8, 1.15)   # Motiv und Mitte etwas wichtiger
    if edges:
        e = np.hypot(*np.gradient(_blur(g, 1.0)))
        w = w * (1 + 0.8 * np.clip(e / (np.percentile(e, 99) + 1e-6), 0, 1))
    return t.astype(np.float32), w.astype(np.float32)


def _steps_py(nsteps, cur, last, used, c, R, q, a, cols, off, cj, cs, ce, cinv, ids, ws, out, pen):
    """Bis zu nsteps Fäden wählen (numpy-Fassung). Gibt die Anzahl zurück; out[i] = (Farbe, von, nach, Gewinn>0)."""
    K = len(cols)
    for step in range(nsteps):
        best = (-1e30, -1, -1, 0, 0, 0.0)
        for k in range(K):
            p = cur[k]
            o0, o1 = off[p], off[p + 1]
            s0, s1 = cs[o0], ce[o1 - 1]
            px = ids[s0:s1].astype(np.int64)
            d = a * (ws[s0:s1] / 255.0) * (cols[k] - c[px])
            g = np.add.reduceat(-d * (2 * R[px] + q * d), cs[o0:o1] - s0) * cinv[o0:o1]
            g[used[k, p, cj[o0:o1]]] = -np.inf
            i = int(np.argmax(g))
            v = float(g[i])
            eff = v if (k == last[0] or v <= 0) else v / (1 + pen)
            if eff > best[0]:
                best = (eff, k, int(cj[o0 + i]), int(cs[o0 + i]), int(ce[o0 + i]), v)
        _, k, j, s, e, v = best
        if k < 0 or v == -np.inf:
            return step
        p = cur[k]
        px = ids[s:e].astype(np.int64)
        dv = a * (ws[s:e] / 255.0) * (cols[k] - c[px])
        R[px] += q * dv
        c[px] += dv
        used[k, p, j] = used[k, j, p] = True
        out[step, 0], out[step, 1], out[step, 2], out[step, 3] = k, p, j, 1 if v > 0 else 0
        cur[k] = j
        last[0] = k
    return nsteps


if _NUMBA:
    @njit(cache=True, nogil=True)
    def _steps_nb(nsteps, cur, last, used, c, R, q, a, cols, off, cj, cs, ce, cinv, ids, ws, out, pen):
        K = cols.shape[0]
        for step in range(nsteps):
            beff, bk, bj, bs, be, bv = -1e30, -1, -1, 0, 0, 0.0
            for k in range(K):
                p = cur[k]
                for ci in range(off[p], off[p + 1]):
                    j = cj[ci]
                    if used[k, p, j]:
                        continue
                    s = 0.0
                    for x in range(cs[ci], ce[ci]):
                        px = ids[x]
                        d = a * (ws[x] / 255.0) * (cols[k] - c[px])
                        s -= d * (2.0 * R[px] + q * d)
                    v = s * cinv[ci]
                    eff = v if (k == last[0] or v <= 0) else v / (1.0 + pen)
                    if eff > beff:
                        beff, bk, bj, bs, be, bv = eff, k, j, cs[ci], ce[ci], v
            if bk < 0:
                return step
            p = cur[bk]
            for x in range(bs, be):
                px = ids[x]
                dv = a * (ws[x] / 255.0) * (cols[bk] - c[px])
                R[px] += q * dv
                c[px] += dv
            used[bk, p, bj] = True
            used[bk, bj, p] = True
            out[step, 0] = bk
            out[step, 1] = p
            out[step, 2] = bj
            out[step, 3] = 1 if bv > 0 else 0
            cur[bk] = bj
            last[0] = bk
        return nsteps


def warmup():
    """numba-Übersetzung vorab (beim Serverstart im Hintergrund)."""
    if not _NUMBA:
        return
    try:
        z = np.zeros(4, np.float64)
        _steps_nb(1, np.zeros(1, np.int64), np.zeros(1, np.int64), np.zeros((1, 2, 2), np.bool_), z, z.copy(), 0.1, 0.1,
                  np.zeros(1, np.float64), np.array([0, 1, 2], np.int64), np.array([1, 0], np.int64), np.array([0, 1], np.int64),
                  np.array([1, 2], np.int64), np.ones(2, np.float32), np.zeros(2, np.uint16), np.zeros(2, np.uint8),
                  np.zeros((1, 4), np.int64), 0.0)
    except Exception:
        pass


def solve(t, w, L, colors, board, a, max_lines, progress=None, pen=SWITCH_PEN, sigma_px=None, board_mm=450.0):
    """Gibt die Spannreihenfolge [(Farbe, von, nach), ...] zurück. progress(anteil, order) wird regelmäßig gerufen."""
    size, n, K = L.size, L.n, len(colors)
    sigma = sigma_px or max(0.6, SIGMA_MM * size / board_mm)
    lo, hi = min(min(colors), board), max(max(colors), board)
    tt = (lo + (hi - lo) * np.clip(t, 0, 1)).astype(np.float32)          # Ziel in den erreichbaren Bereich
    yy, xx = np.mgrid[:size, :size]
    Wm = (w * (np.hypot(xx - (size - 1) / 2, yy - (size - 1) / 2) < size / 2 - 1)).astype(np.float32)
    Wm = Wm / max(float(Wm[Wm > 0].mean()), 1e-6)
    G = _gauss_matrix(size, sigma)
    imp = np.zeros((size, size), np.float32); imp[size // 2, size // 2] = 1
    q = float(((G @ imp @ G.T) ** 2).sum())
    c = np.full(size * size, board, np.float64)
    cols = np.array(colors, np.float64)

    def resid():
        e = (G @ (c.reshape(size, size).astype(np.float32) - tt) @ G.T) * Wm
        return (G @ (e * Wm) @ G.T).ravel().astype(np.float64), float((e ** 2).sum())

    R, E = resid()
    cur = np.zeros(K, np.int64)
    for k in range(K):                                   # Startnagel: bester erster Zug je Farbe
        best = (-np.inf, 0)
        for p in range(0, n, 3):
            o0, o1 = L.cand_off[p], L.cand_off[p + 1]
            s0, s1 = L.cand_s[o0], L.cand_e[o1 - 1]
            px = L.ids[s0:s1].astype(np.int64)
            d = a * (L.ws[s0:s1] / 255.0) * (cols[k] - c[px])
            g = np.add.reduceat(-d * (2 * R[px] + q * d), L.cand_s[o0:o1] - s0).max()
            best = max(best, (g, p))
        cur[k] = best[1]
    used = np.zeros((K, n, n), np.bool_)
    last = np.zeros(1, np.int64)
    out = np.zeros((EVERY, 4), np.int64)
    step_fn = _steps_nb if _NUMBA else _steps_py
    order, best, dead = [], (E, 0), 0
    while len(order) < max_lines:
        args = (min(EVERY, max_lines - len(order)), cur, last, used, c, R, q, a, cols, L.cand_off, L.cand_j,
                L.cand_s, L.cand_e, L.cand_inv, L.ids, L.ws, out, float(pen))
        try:
            got = step_fn(*args)
        except Exception:                                 # numba-Problem auf dem Server: mit numpy weiterrechnen
            if step_fn is _steps_py:
                raise
            step_fn = _steps_py
            got = step_fn(*args)
        order += [(int(k), int(p0), int(p1)) for k, p0, p1, _ in out[:got]]
        R, E = resid()
        if E < best[0]:
            best = (E, len(order))
        dead = 0 if (got and out[:got, 3].any()) else dead + 1
        if got == 0 or dead > DEAD:
            break
        if progress and (len(order) <= 300 or len(order) % 100 < EVERY):   # am Anfang oft: Zeichnen startet sofort
            progress(len(order) / max_lines, order)
    return order[:best[1]] if best[1] else order


def runs_of(order):
    """[(Farbe, [Pins])] in Spannreihenfolge, wie color_runs in app.py: jede Farbe ist ein eigener durchgehender Faden."""
    out = []
    for k, a, b in order:
        if out and out[-1][0] == k:
            out[-1][1].append(b)
        else:
            out.append((k, [a, b]))
    return out


def live_seq(order):
    """Pinfolge im Format der Live-Ansicht (seq, cseq): beim ersten Faden einer Farbe kommt deren Startnagel davor."""
    seq, cseq, started = [], [], set()
    for k, a, b in order:
        if k not in started:
            started.add(k); seq.append(a); cseq.append(k)
        seq.append(b); cseq.append(k)
    return seq, cseq
