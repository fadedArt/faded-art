import hashlib, hmac, json, math, os, re, shutil, smtplib, threading, time, uuid
from email.message import EmailMessage
from datetime import datetime
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import stripe
from flask import Flask, abort, jsonify, render_template, render_template_string, request, send_file, session
from werkzeug.middleware.proxy_fix import ProxyFix
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps
from fpdf import FPDF
from numba import njit

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)   # hinter Render: richtige https-Adresse für Stripe
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
# SECRET_KEY unbedingt als Umgebungsvariable setzen (sonst gehen Warenkörbe bei Neustart verloren)
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(24).hex()
app.permanent_session_lifetime = timedelta(days=7)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
Image.MAX_IMAGE_PIXELS = 50_000_000

# Geheimnisse NUR über Umgebungsvariablen setzen, nie im Code
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "").strip().strip("\"'")      # Leerzeichen und Anführungszeichen aus Versehen entfernen
WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip().strip("\"'")
DATA_DIR = os.environ.get("DATA_DIR", "data")
os.makedirs(DATA_DIR, exist_ok=True)

pool = ThreadPoolExecutor(max_workers=1)   # eine Berechnung gleichzeitig (schont den Arbeitsspeicher)
REF_RE = re.compile(r"^[0-9a-f]{32}$")

# Schlüssel = Werte aus dem Konfigurator; (Anzeigename, RGB, Aufpreis in €)
WOOD = {
    "weiss": ("Klassik Weiß matt", (250, 250, 250), 0),
    "mdf": ("MDF Schwarz matt", (18, 18, 18), 0),
    "eiche": ("Eiche Natur geölt", (168, 122, 82), 50),
    "nussbaum": ("Nussbaum Premium", (59, 39, 22), 80),
    "esche": ("Schwarze Esche", (33, 33, 33), 90),
    "beton": ("Beton-Look", (110, 110, 110), 60),
}
COLORS = {
    "schwarz": ("Tiefschwarz", (12, 12, 12)),
    "weiss": ("Reinweiß", (250, 250, 250)),
    "gold": ("Champagner Gold", (212, 175, 55)),
    "silber": ("Silber", (195, 195, 195)),
}
MULTI = "mehrfarbig"   # Wert im Konfigurator: Fadenfarben werden automatisch aus dem Foto gewählt
MULTI_COLORS = {       # Fäden, aus denen die Mehrfarb-Berechnung wählt (nur echte, kaufbare Garnfarben eintragen)
    "schwarz": ("Tiefschwarz", (12, 12, 12)),
    "weiss": ("Reinweiß", (250, 250, 250)),
    "rot": ("Rot", (185, 28, 36)),
    "gelb": ("Gelb", (242, 196, 22)),
    "blau": ("Königsblau", (28, 58, 150)),
    "gruen": ("Grün", (24, 120, 62)),
    "hautton": ("Apricot", (228, 168, 128)),
    "braun": ("Braun", (110, 66, 36)),
}
MULTI_MAX, MULTI_EUR = 4, 0   # höchstens so viele Fadenfarben; Aufpreis für Mehrfarbig in € (noch festlegen!)
BLOCK = 150                   # Mehrfarbig: so viele Linien am Stück mit einer Farbe, dann wird ggf. gewechselt
CHROMA = 0.7                  # Gewicht der Farbabweichung gegenüber der Helligkeit (Mehrfarbig)

def thread_name(k):
    return (COLORS.get(k) or MULTI_COLORS[k])[0]

def thread_rgb(k):
    return (COLORS.get(k) or MULTI_COLORS[k])[1]

DISC = {  # Farbe des Kreises in der Mitte
    "weiss": ("Weiß", (250, 250, 250)), "creme": ("Creme", (236, 224, 200)),
    "braun": ("Braun", (110, 74, 45)), "anthrazit": ("Anthrazit", (45, 45, 48)),
    "schwarz": ("Schwarz", (14, 14, 14)), "marine": ("Marineblau", (24, 36, 66)),
}
THICKS = ("0.15", "0.20", "0.30", "0.40", "0.60")   # echte Fadendicke in mm
E_REF, D0_CM = 0.274, 45.0     # Referenz: 0,20 mm Faden auf einem Bild von 45 cm Durchmesser (50-cm-Brett)
ROUND_FORMATS = {"rund"}; ROUND_AREA = 0.785   # Rundes Holz: Fläche = Kreis, Preis nach Fläche
PINCOL = {  # Farbe der Nägel
    "gold": ("Gold", (212, 175, 55)), "silber": ("Silber", (205, 205, 205)),
    "schwarz": ("Schwarz", (30, 30, 30)), "weiss": ("Weiß", (245, 245, 245)),
    "kupfer": ("Kupfer", (184, 115, 51)),
}
LINES = {240: 3500, 360: 4500}   # Fäden je Pin-Zahl (Einfarbig)
LINES_MULTI = {240: 8000, 360: 10000}   # Höchstzahl für Mehrfarbig
MAX_USE = 1                   # Einfarbig: so oft darf dieselbe Sehne gezogen werden
SMOOTH = 3                    # Einfarbig: Glättung des Zielbilds in px (bei 1000 px)
WEIGHT = 0.9                  # Einfarbig: wie viel Dunkelheit eine Linie im Zielbild "abarbeitet" (relativ zur Fadendeckung)
VARIANTS = {  # drei Stile, die bei jeder Berechnung erzeugt werden; der Kunde wählt seinen Favoriten
    "a": dict(name="Ausgewogen", clahe=0.02, tone=1.0),      # clahe: Stärke des lokalen Kontrasts (Augen, Mund, Konturen)
    "b": dict(name="Kontrastreich", clahe=0.035, tone=1.25),  # tone > 1: Mitteltöne bekommen mehr Fäden
    "c": dict(name="Weich", clahe=0.01, tone=0.85),
}
LINE_W = 3.5                      # Fadenbreite in Pixeln (bei 1000 px Bildbreite)
MIN_GAP = 25                      # Mindestabstand in Nägeln (bei 360 Pins)
CELL = 2                          # Rastergröße der Bewertung in px (bei 1000 px)
OVERSHOOT = 0.6                   # Mehrfarbig: Strafe für Fäden durch Stellen, die schon fertig sind (< 1: mehr Linien, mehr Kontrast)
MIN_CONTRAST = 60

def lum(rgb):
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
PINS = {240: 0, 360: 70}                       # Pins -> Aufpreis in €
BUILD = "b22"   # Versionsnummer: app.py, configure.html, configure.js und style.css müssen dieselbe tragen
TEST_QUAD_CENTS = int(os.environ.get("TESTPREIS_QUAD_CENTS", "50"))   # Testpreis für ALLE quadratischen Bretter; 0 = aus (Live-Betrieb!)
BASE_CENTS, BASE_AREA, CENTS_PER_CM2 = 50, 2500, 2.5  # 0,50 € ist noch der Testpreis!
# Feste Größen (Breite x Höhe in cm) je Format. Einzige Quelle: Frontend liest sie von hier.
SIZES = {
    "rund": {"S": (30, 30), "M": (50, 50), "L": (70, 70), "XL": (100, 100)},   # Durchmesser in cm
    "quad": {"S": (30, 30), "M": (50, 50), "L": (70, 70), "XL": (100, 100)},
    "hoch": {"S": (30, 40), "M": (45, 60), "L": (60, 80), "XL": (90, 120)},
    "quer": {"S": (40, 30), "M": (60, 45), "L": (80, 60), "XL": (120, 90)},
}


# ---------- Bestell-Speicher (eine Datei pro Bestellung, kein Datei-Chaos) ----------
def odir(ref):
    return os.path.join(DATA_DIR, ref)

def load(ref):
    if not REF_RE.match(ref or ""):
        return None
    try:
        with open(os.path.join(odir(ref), "meta.json")) as f:
            return json.load(f)
    except OSError:
        return None

_meta_lock = threading.Lock()
LIVE_SEQ = {}   # laufende Berechnungen: Pinfolge je Variante, damit der Browser jede Linie live zeichnen kann

def _write(ref, m):
    p = os.path.join(odir(ref), "meta.json")
    with open(p + ".tmp", "w") as f:
        json.dump(m, f)
    os.replace(p + ".tmp", p)

def update(ref, **kw):
    with _meta_lock:
        m = load(ref) or {}
        m.update(kw)
        _write(ref, m)

def update_variant(ref, key, pct=None, live=None, ready=False, used=None):
    """Fortschritt, Live-Bildnummer, verwendete Fadenfarben und Fertig-Meldung EINER Variante (drei laufen gleichzeitig)."""
    with _meta_lock:
        m = load(ref) or {}
        if used is not None:
            m.setdefault("used", {})[key] = used
        if pct is not None:
            m.setdefault("prog", {})[key] = pct
        if live is not None:
            m.setdefault("live", {})[key] = live
        if ready and key not in (m.get("ready") or []):
            m["ready"] = (m.get("ready") or []) + [key]
        m["beat"] = time.time()
        if m.get("prog"):
            m["progress"] = int(sum(m["prog"].values()) / len(VARIANTS))
        _write(ref, m)

def ring_cm(m):
    """Durchmesser des Nagelkreises: bei rundem Holz sitzen die Nägel direkt am Rand (95 %), sonst 90 % der kürzeren Seite."""
    return (0.95 if m.get("fmt") in ROUND_FORMATS else 0.9) * min(m["w"], m["h"])

def board_label(m):
    return f"rund, Durchmesser {m['w']} cm" if m.get("fmt") in ROUND_FORMATS else f"{m['w']} x {m['h']} cm"

def price_cents(m):
    if TEST_QUAD_CENTS and m.get("fmt") == "quad":
        return TEST_QUAD_CENTS
    area = m["w"] * m["h"] * (ROUND_AREA if m.get("fmt") in ROUND_FORMATS else 1)
    extra = max(0, area - BASE_AREA) * CENTS_PER_CM2
    return BASE_CENTS + round(extra) + (WOOD[m["wood"]][2] + PINS[m["pins"]] + (MULTI_EUR if m.get("color") == MULTI else 0)) * 100


# ---------- String-Art-Berechnung (dein Algorithmus, pro Bestellung isoliert) ----------
W_IMG, S_RENDER = 1000, 2000      # Rechenauflösung, Render-Auflösung (danach auf 1000 px verkleinert)
_GEOM, _geom_lock = {}, threading.Lock()
_OPP = (np.array([[.299, .587, .114], [-.169, -.331, .5], [.5, -.419, -.081]]) * [[1], [CHROMA], [CHROMA]]).astype(np.float32)

def preview_img(img, size=800):
    """Vorschau in 800 px (das gekaufte Bild und die Pinfolge haben volle Auflösung)."""
    return img.convert("RGB").resize((size, size), Image.Resampling.LANCZOS)

def opp(rgb):
    """RGB (0..255) -> Helligkeit + gewichtete Farbanteile. Abstände hier entsprechen grob dem, was das Auge als Unterschied sieht."""
    return (np.asarray(rgb, np.float32) / 255) @ _OPP.T

def geometry(n):
    """Nagelkreis, Sehnenlängen und erlaubte Sehnen (Mindestabstand der Nägel); einmal je Pin-Zahl berechnet."""
    with _geom_lock:
        if n not in _GEOM:
            rp = W_IMG / 2 - 9
            ang = 2 * np.pi * np.arange(n) / n
            px, py = W_IMG / 2 + rp * np.cos(ang), W_IMG / 2 + rp * np.sin(ang)
            L = np.hypot(px[:, None] - px, py[:, None] - py).astype(np.float32)   # Sehnenlängen
            nsc = np.maximum(2, (L / CELL).astype(np.int32) + 1)     # Abtastpunkte je Sehne: einer je Rasterzelle
            # (feste Punktzahl wäre schneller, legt die Punkte aller Durchmesser aber auf dieselben Ringe um die Mitte -> Ringmuster)
            gap = np.abs(np.arange(n)[:, None] - np.arange(n))
            ok = np.minimum(gap, n - gap) > max(8, round(MIN_GAP * n / 360) - 1)
            _GEOM[n] = dict(px=px, py=py, rp=rp, L=L, nsc=nsc, ok=ok)
        return _GEOM[n]

# Rechenkerne mit numba: werden beim ersten Aufruf in Maschinencode übersetzt (gespeichert in __pycache__) und sind dann
# um ein Vielfaches schneller als numpy, weil pro Faden nur wenige Hundert Zellen angefasst werden.
@njit(cache=True, nogil=True)
def _cell_gain(c, C, T, Co, Gk, B, ov, q):
    """Gewinn (Fehlerverringerung je Pixel Fadenweg) von Zelle c für jede Fadenfarbe, siehe compute_string_art Schritt 2."""
    for k in range(Co.shape[0]):
        dr = dd = 0.0
        for ch in range(3):
            dk = Co[k, ch] - C[c, ch]
            dr += dk * (T[c, ch] - C[c, ch])
            dd += dk * dk
        if dr < 0:
            dr *= ov                                   # Faden durch schon fertige Stellen: mildere Strafe
        Gk[k, c] = (2 * B * dr - B * B * dd) / q

@njit(cache=True, nogil=True)
def _init_gain(C, T, Co, Gk, B, ov, q):
    for c in range(C.shape[0]):
        _cell_gain(c, C, T, Co, Gk, B, ov, q)

@njit(cache=True, nogil=True)
def _best(k, p, px, py, ln, ok, used, Gk, q, Wc, nsc, floor, maxuse):
    """Beste Sehne der Farbe k ab Pin p: (Pin, Gewinn), Pin = -1, wenn keine einen Gewinn über floor bringt.
    Gewinn = Summe über nsc[p, c] Abtastpunkte (etwa einer je Rasterzelle) mal Gewicht ln[p, c] je Punkt."""
    best, bg = -1, floor
    for c in range(len(px)):
        if ok[p, c] and used[k, p, c] < maxuse:
            iq = 1.0 / q                               # Zelle = Pixel (gerundet) / q, als eine Multiplikation
            ns = nsc[p, c]
            x0, y0 = (px[p] + 0.5) * iq, (py[p] + 0.5) * iq
            dx, dy = (px[c] - px[p]) * iq / (ns - 1), (py[c] - py[p]) * iq / (ns - 1)
            g = 0.0
            for j in range(ns):
                g += Gk[k, int(y0 + dy * j) * Wc + int(x0 + dx * j)]
            g *= ln[p, c]
            if g > bg:
                best, bg = c, g
    return best, bg

@njit(cache=True, nogil=True)
def _lines(k, p, count, out, ln, ok, used, Gk, C, T, Co, px, py, B, ov, q, Wc, nsc, coef, floor, lin, maxuse):
    """Zieht bis zu count Fäden der Farbe k ab Pin p, jeweils die beste Sehne; die Pins landen in out.
    lin > 0: lineares Modell (Einfarbig), Gk ist die restliche Dunkelheit, jede Linie zieht lin je Zelle ab.
    lin = 0: Farbmodell (Mehrfarbig), siehe _cell_gain. Gibt die Anzahl zurück (weniger als count = Auto-Stopp)."""
    for i in range(count):
        nxt, g = _best(k, p, px, py, ln, ok, used, Gk, q, Wc, nsc, floor, maxuse)
        if nxt < 0:
            return i
        ex, ey = px[nxt] - px[p], py[nxt] - py[p]
        N = max(1, int(math.sqrt(ex * ex + ey * ey)))
        prev, run = -1, 0
        for s in range(N + 2):                         # Zellen der Geraden pixelgenau, je Zelle Länge des Fadenwegs
            c = -2
            if s <= N:
                c = (int(py[p] + 0.5 + ey * s / N) // q) * Wc + int(px[p] + 0.5 + ex * s / N) // q
            if c != prev:
                if prev >= 0 and lin > 0:
                    Gk[k, prev] -= lin
                elif prev >= 0:
                    beta = 1 - math.exp(coef * run)    # Deckung dieses Fadens in der Zelle
                    for ch in range(3):
                        C[prev, ch] += beta * (Co[k, ch] - C[prev, ch])
                    _cell_gain(prev, C, T, Co, Gk, B, ov, q)
                prev, run = c, 0
            run += 1
        used[k, p, nxt] += 1
        used[k, nxt, p] += 1
        out[i] = nxt
        p = nxt
    return count

@njit(cache=True, nogil=True)
def _render_run(P, pins, S, lw, lut, col, cnt, canvas):
    """Malt einen Abschnitt (eine Farbe) auf die Leinwand: Fäden zählen, dann Deckung 1 - (1 - a)^Anzahl überlagern.
    Je Pixel der Hauptachse ein Punkt, quer zur Richtung auf lw px verbreitert (keine Doppelzählung innerhalb eines Fadens)."""
    for i in range(len(pins) - 1):
        u, v = pins[i], pins[i + 1]
        dx, dy = P[v, 0] - P[u, 0], P[v, 1] - P[u, 1]
        horiz = abs(dx) >= abs(dy)
        N = int(abs(dx)) if horiz else int(abs(dy))
        step = S if horiz else 1
        for s in range(N + 1):
            t = s / max(1, N)
            x = min(max(int(round(P[u, 0] + dx * t)), 0), S - 2)
            y = min(max(int(round(P[u, 1] + dy * t)), 0), S - 2)
            for o in range(-(lw // 2), lw - lw // 2):
                j = y * S + x + o * step
                if 0 <= j < S * S:
                    cnt[j] += 1
    for j in range(S * S):
        if cnt[j]:
            cov = lut[min(cnt[j], len(lut) - 1)]
            for ch in range(3):
                canvas[j, ch] = int(canvas[j, ch] + (col[ch] - canvas[j, ch]) * cov + 0.5)
            cnt[j] = 0

def _warmup():
    """Übersetzt die Rechenkerne gleich beim Serverstart (im Hintergrund), nicht erst bei der ersten Bestellung."""
    try:
        n, K = 4, 2
        ln, ok, used = np.ones((n, n), np.float32), np.ones((n, n), bool), np.zeros((K, n, n), np.uint8)
        C, T, Co, Gk = np.zeros((4, 3), np.float32), np.ones((4, 3), np.float32), np.ones((K, 3), np.float32), np.zeros((K, 4), np.float32)
        _init_gain(C, T, Co, Gk, 0.1, 0.5, 2)
        _lines(0, 0, 1, np.empty(1, np.int64), ln, ok, used, Gk, C, T, Co, np.arange(n) * 3.0, np.zeros(n), 0.1, 0.5, 2, 2, np.full((n, n), 4, np.int32), -0.1, 0.0, 0.0, 1)
        _render_run(np.ones((n, 2)), np.arange(2, dtype=np.int64), 4, 3, np.ones(4, np.float32), np.ones(3, np.float32), np.zeros(16, np.uint16), np.zeros((16, 3), np.uint8))
    except Exception:
        app.logger.exception("numba-Vorübersetzung fehlgeschlagen")

_warm = []

def warm_up_once():
    if not _warm:
        _warm.append(pool.submit(_warmup))   # über die Rechen-Warteschlange: die erste Bestellung wartet ggf. kurz dahinter

def _simplex(v):
    """Projiziert jede Zeile auf 'alle Werte >= 0, Summe 1'."""
    u = -np.sort(-v, axis=1)
    css = np.cumsum(u, 1) - 1
    rho = (u - css / np.arange(1, v.shape[1] + 1) > 0).sum(1)
    return np.maximum(v - (css[np.arange(len(v)), rho - 1] / rho)[:, None], 0)

def mix_error(P, V):
    """Wie gut lassen sich die Farben P aus den Farben V mischen? (aus der Ferne mischt das Auge Fäden und Untergrund)"""
    V = np.asarray(V, np.float32)
    lam = np.full((len(P), len(V)), 1 / len(V), np.float32)
    step = 1 / (np.linalg.norm(V @ V.T, 2) + 1e-6)
    for _ in range(100):
        lam = _simplex(lam - step * ((lam @ V - P) @ V.T))
    return float(((lam @ V - P) ** 2).sum(1).mean())

def clahe(g, clip, tiles=8):
    """Lokaler Kontrast (CLAHE): Histogramm-Angleichung je Kachel, Spitzen auf clip gekappt, Kacheln weich überblendet.
    Holt Augen, Mund und Konturen aus flauen Bereichen heraus, ohne dass helle Flächen ausbrennen. g: uint8 -> float 0..1."""
    H, W_ = g.shape
    th, tw = H // tiles, W_ // tiles
    luts = np.empty((tiles, tiles, 256), np.float32)
    for i in range(tiles):
        for j in range(tiles):
            t = g[i * th:(i + 1) * th, j * tw:(j + 1) * tw]
            h = np.bincount(t.ravel(), minlength=256).astype(np.float64)
            lim = max(1.0, clip * t.size)
            h = np.minimum(h, lim) + np.maximum(h - lim, 0).sum() / 256   # Überschuss gleichmäßig verteilen
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

def pick_palette(path, disc):
    """Mehrfarbig: wählt nacheinander die Fäden, die das Foto am besten nachmischen (höchstens MULTI_MAX).
    Eine weitere Farbe kommt nur dazu, wenn sie das Ergebnis deutlich verbessert: weniger Farben = weniger Fadenwechsel."""
    img = np.asarray(Image.open(path).convert("RGB").resize((32, 32), Image.Resampling.BOX), np.float32)
    yy, xx = np.mgrid[:32, :32]
    P = opp(img[(xx - 15.5) ** 2 + (yy - 15.5) ** 2 <= 16 ** 2])
    d0 = opp(disc)
    keys = [k for k, v in MULTI_COLORS.items() if np.abs(opp(v[1]) - d0).sum() > 0.25]   # zu nah an der Kreisfarbe: unsichtbar
    chosen, err = [], mix_error(P, [d0])
    while len(chosen) < MULTI_MAX:
        errs = {k: mix_error(P, [d0] + [opp(thread_rgb(c)) for c in chosen + [k]]) for k in keys if k not in chosen}
        if not errs:
            break
        k = min(errs, key=errs.get)
        if chosen and errs[k] > 0.85 * err:
            break
        chosen.append(k)
        err = errs[k]
    return chosen or [max(MULTI_COLORS, key=lambda c: np.abs(opp(thread_rgb(c)) - d0).sum())]

def color_runs(seq, cseq):
    """Abschnitte [(Farbe, [Pins])] in Fädel-Reihenfolge. Jede Farbe ist ein eigener durchgehender Faden:
    ein Abschnitt beginnt an dem Pin, an dem diese Farbe zuletzt aufgehört hat."""
    last, out = {}, []
    for p, k in zip(seq, cseq):
        if k in last:
            if not out or out[-1][0] != k:
                out.append((k, [last[k]]))
            out[-1][1].append(p)
        last[k] = p
    return out

def compute_string_art(ref, m, key="a"):
    """Gierige String-Art: pro Schritt wird ab dem aktuellen Nagel die beste Sehne gewählt.
    Einfarbig (lineares Modell, liefert die erkennbarsten Gesichter): Zielbild = gewünschte Dunkelheit je Rasterzelle;
    gewählt wird die Sehne mit der größten durchschnittlichen Rest-Dunkelheit, danach zieht sie WEIGHT x Fadendeckung ab.
    Dunkle Stellen bekommen so Fäden proportional zu ihrer Dunkelheit, und kurze Sehnen können Details (Augen) treffen.
    Mehrfarbig (Farbmodell): jede Zelle hat eine Farbe, jeder Faden mischt seine Farbe hinein; gewählt wird der größte
    Fehlerrückgang. In beiden Fällen werden nach jeder Linie nur die Zellen dieser Linie neu bewertet."""
    var = VARIANTS[key]                        # Stil: lokaler Kontrast, Tonwerte
    LW = LINE_W
    d, n = odir(ref), m["pins"]
    pal = m["palette"] if m["color"] == MULTI else [m["color"]]
    cols = [thread_rgb(c) for c in pal]
    K, disc, bg = len(cols), DISC[m["disc"]][1], WOOD[m["wood"]][1]
    W, S, q = W_IMG, S_RENDER, CELL
    Wc = W // q
    # Maßstab: derselbe Faden wirkt auf einem größeren Bild feiner, auf einem kleineren kräftiger
    disc_cm = max(10.0, ring_cm(m))
    t_mm = float(m["thick"])
    a = min(0.4, max(0.02, E_REF * (t_mm / 0.20) * (D0_CM / disc_cm) / LW))     # Deckkraft pro Faden
    n_lines = int((LINES if K == 1 else LINES_MULTI)[n] * min(1.6, max(1.0, (0.20 / t_mm) * (disc_cm / D0_CM))))   # feinerer Faden braucht mehr Linien
    geo = geometry(n)
    px, py, rp, ok, nsc = geo["px"], geo["py"], geo["rp"], geo["ok"], geo["nsc"]
    ln = ((geo["L"] if K > 1 else 1) / nsc).astype(np.float32)   # Mehrfarbig: Gewinn über die ganze Sehne, Einfarbig: Durchschnitt

    # 1) Bild vorbereiten, Ziel je Rasterzelle (2x2 px: so sieht das Auge das Bild aus Distanz)
    rgb = Image.open(os.path.join(d, "input.png")).convert("RGB").resize((W, W), Image.Resampling.LANCZOS)
    gray = ImageOps.autocontrast(rgb.convert("L"), cutoff=1.5)
    yy, xx = np.ogrid[:W, :W]
    inside = (xx - W / 2) ** 2 + (yy - W / 2) ** 2 <= (W / 2 - 2) ** 2
    cells = lambda x: x.reshape(Wc, q, Wc, q, *x.shape[2:]).mean(axis=(1, 3)).reshape(Wc * Wc, *x.shape[2:])
    d0, Co = opp(disc), opp(cols)
    logk = math.log1p(-a)
    B = -math.expm1(logk * LW / q)                           # Deckung, die ein Faden beim Queren einer Zelle bringt
    if K == 1:
        nrm = clahe(np.asarray(gray), var["clahe"]) if var["clahe"] else np.asarray(gray, np.float32) / 255
        if SMOOTH:                                           # winzige Glanzlichter glätten: sonst weichen ihnen alle Linien aus (weiße "Löcher")
            nrm = np.asarray(Image.fromarray((nrm * 255 + 0.5).astype(np.uint8)).filter(ImageFilter.GaussianBlur(SMOOTH)), np.float32) / 255
        c = 1.0 - nrm if lum(cols[0]) < lum(disc) else nrm    # heller Faden auf dunklem Grund: helle Stellen = Faden
        c = 1.0 - (1.0 - c) ** var["tone"]
        Gk = cells(np.where(inside, c, 0).astype(np.float32))[None].copy()   # restliche Dunkelheit je Zelle
        C = T = np.zeros((1, 3), np.float32)                 # (nur im Farbmodell gebraucht)
        lin = WEIGHT * B
    else:                                                    # Mehrfarbig: Helligkeit wie oben, Farbe aus dem Foto (etwas kräftiger)
        nrm = np.asarray(gray.filter(ImageFilter.UnsharpMask(radius=3, percent=140, threshold=2)), np.float32) / 255
        x = opp(np.asarray(ImageEnhance.Color(rgb).enhance(1.25).resize((Wc, Wc), Image.Resampling.BOX), np.float32)).reshape(-1, 3)
        x[:, 0] = cells(nrm ** var["tone"])                # gleich in Rasterauflösung: spart Arbeitsspeicher
        T = np.ascontiguousarray(np.where(cells(inside.astype(np.float32))[:, None] > 0.5, x, d0), np.float32)
        C = np.repeat(d0[None], Wc * Wc, 0)                  # aktuelle Farbe jeder Zelle
        Gk = np.empty((K, Wc * Wc), np.float32)              # Gewinn je Zelle und Fadenfarbe (Fehlerverringerung |T - C|²)
        _init_gain(C, T, Co, Gk, B, OVERSHOOT, q)
        lin = 0.0

    # Live-Ansicht: Fäden werden parallel auf eine kleine Leinwand gezeichnet und regelmäßig als Bild gespeichert
    LIVE = 600
    lk = LIVE / W
    live = Image.new("RGB", (LIVE, LIVE), disc)
    ld = ImageDraw.Draw(live, "RGBA")
    lwid = max(1, round(LW * lk))
    lalpha = min(255, int(255 * a * LW * lk / lwid))
    pcol = PINCOL[m.get("pin", "gold")][1]
    for x0, y0 in zip(px, py):
        ld.ellipse([x0 * lk - 2, y0 * lk - 2, x0 * lk + 2, y0 * lk + 2], fill=pcol)
    live_n = 0

    def save_live(pct):
        nonlocal live_n
        live_n += 1
        live.save(os.path.join(d, f"live_{key}.tmp.jpg"), quality=80)
        os.replace(os.path.join(d, f"live_{key}.tmp.jpg"), os.path.join(d, f"live_{key}.jpg"))   # atomar: der Browser sieht nie halbe Bilder
        update_variant(ref, key, pct=pct, live=live_n)

    save_live(0)
    used = np.zeros((K, n, n), np.uint8)                  # wie oft jede Sehne schon gezogen wurde (je Farbe)
    maxuse = MAX_USE if K == 1 else 1

    # 2) Gierige Auswahl. Mehrfarbig: in Abschnitten von BLOCK Linien mit der Farbe, die gerade am meisten bringt.
    cur = [round(i * n / K) % n for i in range(K)]       # jede Farbe beginnt an einem anderen Pin
    seq, cseq, started = [], [], set()
    for r in [r for r, v in list(LIVE_SEQ.items()) if time.time() - v.get("t", 0) > 3600]:
        LIVE_SEQ.pop(r, None)
    LIVE_SEQ.setdefault(ref, {"t": time.time()})[key] = {"seq": seq, "cseq": cseq, "hex": ["#%02x%02x%02x" % c for c in cols],
                                                         "alpha": round(lalpha / 255, 4), "lw": lwid, "n": n}
    buf, coef = np.empty(100, np.int64), logk * LW / (q * q)
    floor = -1e30 if K == 1 else 0.0                     # Einfarbig: immer die volle Linienzahl (das Bild entsteht aus der Dichte)

    def draw(k, count):                                  # count Fäden der Farbe k ziehen und live zeichnen
        got = _lines(k, cur[k], count, buf, ln, ok, used, Gk, C, T, Co, px, py, B, OVERSHOOT, q, Wc, nsc, coef, floor, lin, maxuse)
        if got and k not in started:                      # Startpin dieser Farbe
            started.add(k)
            seq.append(cur[k])
            cseq.append(k)
        for nxt in buf[:got].tolist():
            p0 = cur[k]
            ld.line([(px[p0] * lk, py[p0] * lk), (px[nxt] * lk, py[nxt] * lk)], fill=cols[k] + (lalpha,), width=lwid)
            seq.append(nxt)
            cseq.append(k)
            cur[k] = nxt
        return got

    k, left, lines = 0, n_lines if K == 1 else 0, 0
    while lines < n_lines:
        if left <= 0:                                     # nächster Abschnitt: Farbe mit dem größten Gewinn
            gains = [_best(i, cur[i], px, py, ln, ok, used, Gk, q, Wc, nsc, -1e30, maxuse)[1] for i in range(K)]
            k = int(np.argmax(gains))
            if gains[k] <= 0:
                break                                     # Auto-Stopp: kein Faden verbessert das Bild mehr
            left = BLOCK
        want = min(100 - lines % 100, left, n_lines - lines)
        got = draw(k, want)
        lines, left = lines + got, left - got
        if got and lines % 100 == 0:                      # Live-Bild, Fortschritt und Lebenszeichen
            save_live(min(99, int(100 * lines / n_lines)))
        if got < want:
            if K == 1:
                break
            left = 0

    # 3) Rendern in 2000 px, Abschnitt für Abschnitt (spätere Fäden liegen oben); Deckkraft überlagert sich physikalisch
    runs = color_runs(seq, cseq)
    P = np.ascontiguousarray(np.stack([px, py], 1) * (S / W))
    ra = min(0.95, 2 * a * LW)                               # Deckung je Pixel einer 1 px breiten Linie: wirkt wie echter Faden
    canvas = np.empty((S * S, 3), np.uint8)                # 8 Bit je Farbe: wenig Speicher (Render Free hat nur 512 MB)
    canvas[:] = disc
    cnt = np.zeros(S * S, np.uint16)
    lut = (1 - (1 - ra) ** np.arange(4096)).astype(np.float32)   # Deckung bei n Fäden übereinander
    for k, pins_ in runs:
        _render_run(P, np.asarray(pins_, np.int64), S, 1, lut, np.asarray(cols[k], np.float32), cnt, canvas)
    del cnt
    art = Image.fromarray(canvas.reshape(S, S, 3))
    del canvas
    pin_col = PINCOL[m.get("pin", "gold")][1]
    edge = (40, 40, 40) if lum(pin_col) > 90 else (170, 170, 170)
    dr = ImageDraw.Draw(art)
    for x, y in P:
        dr.ellipse([x - 5, y - 5, x + 5, y + 5], fill=pin_col, outline=edge)
    art = art.reduce(2)

    mask = Image.new("L", (W, W), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, W, W], fill=255)
    final = Image.new("RGB", (W, W), bg)
    final.paste(art, (0, 0), mask)
    final.save(os.path.join(d, f"final_{key}.png"), compress_level=3)              # privat, nur nach Zahlung nutzbar (Stufe 3: schneller und kleiner als Standard)
    preview_img(final).save(os.path.join(d, f"preview_{key}.png"), compress_level=3)   # Vorschau ohne Wasserzeichen
    with open(os.path.join(d, f"pinfolge_{key}.txt"), "w") as f:
        if K == 1:
            f.write(",".join(map(str, seq)))
        else:                                                 # Mehrfarbig: eine Zeile je Abschnitt
            f.write("\n".join(f"{thread_name(pal[k])}: " + ",".join(map(str, p)) for k, p in runs))
    update_variant(ref, key, used=[pal[k] for k in sorted(set(cseq))])   # Mehrfarbig: nicht jede gewählte Farbe wird gebraucht
    write_instructions(ref, m, runs, pal, px, py, rp, key, n_lines)

def write_instructions(ref, m, runs, pal, px, py, rp, key, n_lines):
    """Anleitung mit allen Einstellungen und der kompletten Pinfolge (für die Fertigung)."""
    n, disc_cm = m["pins"], round(ring_cm(m))
    multi = m["color"] == MULTI
    scale = (disc_cm / 2 / rp) / 100
    meters = {}
    for k, p in runs:
        p = np.array(p)
        meters[k] = meters.get(k, 0.0) + float(np.hypot(np.diff(px[p]), np.diff(py[p])).sum()) * scale
    total = sum(len(p) - 1 for _, p in runs)
    if multi:
        colors = ", ".join(thread_name(pal[k]) for k in sorted(meters))
        length = "\n".join(f"  {thread_name(pal[k])}: ca. {v:.2f} Meter" for k, v in sorted(meters.items()))
        starts = {}
        for k, p in runs:
            starts.setdefault(k, p[0])
        blocks, i = [], 0
        for b, (k, p) in enumerate(runs):
            lines = "\n".join(f"Schritt {i + j + 1}: Pin {x}" for j, x in enumerate(p[1:]))
            blocks.append(f"--- Abschnitt {b + 1} von {len(runs)}: Faden {thread_name(pal[k])}, weiter ab Pin {p[0]} ({len(p) - 1} Linien) ---\n{lines}")
            i += len(p) - 1
        steps = "\n\n".join(blocks)
        how = ("3. Jede Farbe ist ein eigener Faden. Startpins: "
               + ", ".join(f"{thread_name(pal[k])} an Pin {p}" for k, p in starts.items()) + "\n"
               "4. Die Abschnitte der Reihe nach fädeln. Bei einem Farbwechsel den Faden am letzten Pin hängen lassen\n"
               "   (mit einer Klammer sichern) und die nächste Farbe dort aufnehmen, wo sie zuletzt aufgehört hat")
        plain = "\n".join(f"{thread_name(pal[k])}: " + ",".join(map(str, p)) for k, p in runs)
    else:
        colors = thread_name(m["color"])
        length = f"ca. {sum(meters.values()):.2f} Meter"
        seq = runs[0][1] if runs else [0]
        steps = "\n".join(f"Schritt {i + 1}: Pin {p}" for i, p in enumerate(seq))
        how = f"3. Faden an Pin {seq[0]} befestigen\n4. Der Pinfolge oben Schritt für Schritt folgen: jeden Pin mit dem nächsten verbinden"
        plain = ",".join(map(str, seq))
    txt = f"""FADED ART - Pinfolge und Anleitung
Erstellt am: {datetime.now():%d.%m.%Y um %H:%M:%S}
Bestellnummer: {ref}
Stil der Berechnung: {VARIANTS[key]['name']}

=== PROJEKT-EINSTELLUNGEN ===
Anzahl Pins: {n}
Maximale Linien: {n_lines}
Verwendete Linien: {total} ({"Maximum erreicht" if total >= n_lines else "Auto-Stopp, sobald kein Faden das Bild mehr verbessert"})
Fadendicke: {m['thick'].replace('.', ',')} mm (Maßstab: Bild {disc_cm} cm)
Fadenfarbe{"n" if multi else ""}: {colors}
Farbe des Kreises: {DISC[m['disc']][0]}
Nagelfarbe: {PINCOL[m.get('pin', 'gold')][0]}
Untergrund: {WOOD[m['wood']][0]}, {board_label(m)}
Kreisdurchmesser (Pins): ca. {disc_cm} cm
Fadenlänge: {length}

=== PINFOLGE ===
Gesamtschritte: {total}{f" in {len(runs)} Abschnitten" if multi else ""}

{steps}

=== ANLEITUNG ===
1. Nagelkreis mit {n} gleichmäßig verteilten Nägeln setzen (Durchmesser ca. {disc_cm} cm)
2. Nägel von 0 bis {n - 1} nummerieren: Pin 0 sitzt rechts auf 3 Uhr, die Nummern steigen im Uhrzeigersinn
{how}
5. Faden straff, aber nicht zu fest ziehen, damit sich das Brett nicht verzieht
6. Weitermachen, bis alle {total} Verbindungen fertig sind

=== PINFOLGE ALS ZAHLENLISTE ===
{plain}
"""
    with open(os.path.join(odir(ref), f"anleitung_{key}.txt"), "w", encoding="utf-8") as f:
        f.write(txt)
    write_pdf(ref, m, runs, pal, meters, total, n_lines, key)

def write_pdf(ref, m, runs, pal, meters, total, n_lines, key):
    """Fertigungsanleitung als PDF: Seite 1 Vorschau und alle Einstellungen, danach die Pinfolge zum Abhaken."""
    n, disc_cm, multi = m["pins"], round(ring_cm(m)), m["color"] == MULTI
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(False)
    pdf.set_title(f"Faded Art Anleitung {ref}")
    gold, grey = (176, 141, 36), (110, 110, 110)

    def footer():
        pdf.set_xy(12, 287)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*grey)
        pdf.cell(186, 4, f"Faded Art  |  Bestellnummer {ref}  |  Seite {pdf.page_no()}", align="C")
        pdf.set_text_color(0, 0, 0)

    # Seite 1: Vorschau und Einstellungen
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(*gold)
    pdf.cell(0, 10, "FADED ART", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(0, 6, f"Fertigungsanleitung  |  Stil: {VARIANTS[key]['name']}  |  erstellt am {datetime.now():%d.%m.%Y %H:%M}",
             new_x="LMARGIN", new_y="NEXT")
    pdf.image(os.path.join(odir(ref), f"preview_{key}.png"), x=12, y=30, w=88)
    if multi:
        thread = ", ".join(thread_name(pal[k]) for k in sorted(meters))
        length = "\n".join(f"{thread_name(pal[k])}: ca. {v:.0f} m" for k, v in sorted(meters.items()))
    else:
        thread, length = thread_name(m["color"]), f"ca. {sum(meters.values()):.0f} m"
    rows = [("Brett", f"{board_label(m)}, Größe {m.get('size', '')}"), ("Untergrund", WOOD[m["wood"]][0]),
            ("Kreis (Bildfläche)", f"{DISC[m['disc']][0]}, Nagelkreis ca. {disc_cm} cm Durchmesser"),
            ("Nägel", f"{n} Stück, {PINCOL[m.get('pin', 'gold')][0]}, gleichmäßig auf dem Kreis"),
            ("Faden" + ("farben" if multi else "farbe"), thread), ("Fadendicke", f"{m['thick'].replace('.', ',')} mm"),
            ("Linien", f"{total} von max. {n_lines}" + (f", {len(runs)} Abschnitte" if multi else "")), ("Fadenlänge", length)]
    pdf.set_xy(106, 30)
    for label, val in rows:
        pdf.set_x(106)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*grey)
        pdf.cell(0, 5, label.upper(), new_x="LMARGIN", new_y="NEXT")
        pdf.set_x(106)
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(0, 0, 0)
        pdf.multi_cell(92, 5.5, val, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1.5)
    if multi:
        starts = {}
        for k, p in runs:
            starts.setdefault(k, p[0])
        start = ("Jede Farbe ist ein eigener Faden. Startpins: "
                 + ", ".join(f"{thread_name(pal[k])} an Pin {p}" for k, p in starts.items()) + ".")
        work = ("Die Abschnitte der Reihe nach fädeln. Bei einem Farbwechsel den Faden am letzten Pin hängen lassen "
                "(mit einer Klammer sichern) und die nächste Farbe dort aufnehmen, wo sie zuletzt aufgehört hat.")
    else:
        start = f"Faden an Pin {runs[0][1][0] if runs else 0} befestigen."
        work = "Der Pinfolge Schritt für Schritt folgen: jeden Pin mit dem nächsten verbinden."
    steps = [f"Nagelkreis mit {n} gleichmäßig verteilten Nägeln setzen (Durchmesser ca. {disc_cm} cm).",
             f"Nägel von 0 bis {n - 1} nummerieren: Pin 0 sitzt rechts auf 3 Uhr, die Nummern steigen im Uhrzeigersinn.",
             start, work, "Faden straff, aber nicht zu fest ziehen, damit sich das Brett nicht verzieht.",
             f"Weitermachen, bis alle {total} Verbindungen fertig sind. Erledigte Schritte in der Liste abhaken."]
    pdf.set_xy(12, max(pdf.get_y(), 124) + 4)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 7, "Anleitung", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10.5)
    for i, t in enumerate(steps):
        pdf.multi_cell(186, 5.5, f"{i + 1}.  {t}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)
    footer()

    # Pinfolge: 8 Spalten, Kästchen zum Abhaken; bei Mehrfarbig je Abschnitt eine Überschrift in Fadenfarbe
    cols, cw, rh, top, bottom = 8, 186 / 8, 5.2, 24, 282
    y, c, step = bottom, 0, 0

    def new_page():
        nonlocal y, c
        if pdf.page_no() > 1:
            footer()
        pdf.add_page()
        pdf.set_xy(12, 12)
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 7, "Pinfolge", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*grey)
        pdf.cell(0, 4, "Kästchen  |  Schritt  |  nächster Pin (fett)", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        y, c = top, 0

    for b, (k, p) in enumerate(runs):
        if multi:
            if c:
                y, c = y + rh, 0
            if y + 2 * rh + 2 > bottom:
                new_page()
            y += 1.5
            pdf.set_fill_color(*thread_rgb(pal[k]))
            pdf.set_draw_color(150, 150, 150)
            pdf.rect(12, y + 0.6, 6, 4, style="DF")
            pdf.set_xy(20, y)
            pdf.set_font("Helvetica", "B", 9.5)
            pdf.cell(0, rh, f"Abschnitt {b + 1} von {len(runs)}: Faden {thread_name(pal[k])}, weiter ab Pin {p[0]}  ({len(p) - 1} Linien)")
            y += rh + 0.5
        for x in p[1:]:
            step += 1
            if y + rh > bottom:
                new_page()
            x0 = 12 + c * cw
            pdf.set_draw_color(120, 120, 120)
            pdf.rect(x0, y + 1.1, 2.8, 2.8)
            pdf.set_xy(x0 + 3.6, y)
            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(*grey)
            pdf.cell(9, rh, str(step), align="R")
            pdf.set_font("Helvetica", "B", 9.5)
            pdf.set_text_color(0, 0, 0)
            pdf.cell(9, rh, str(x))
            c += 1
            if c == cols:
                y, c = y + rh, 0
    footer()
    pdf.output(os.path.join(odir(ref), f"anleitung_{key}.pdf"))

def run_job(ref):
    try:
        update(ref, status="running", beat=time.time(), progress=0, ready=[], prog={}, live={})
        meta = load(ref)
        if meta["color"] == MULTI:                   # Fadenfarben einmal für alle drei Varianten wählen
            update(ref, palette=pick_palette(os.path.join(odir(ref), "input.png"), DISC[meta["disc"]][1]))
            meta = load(ref)

        def one(key):                                # alle drei Varianten laufen gleichzeitig, jede mit Live-Bild
            try:
                compute_string_art(ref, meta, key)
                update_variant(ref, key, pct=100, ready=True)
            except Exception:
                app.logger.exception("Variante %s fehlgeschlagen (%s)", key, ref)

        with ThreadPoolExecutor(max_workers=len(VARIANTS)) as ex:
            list(ex.map(one, list(VARIANTS)))
        if not load(ref).get("ready"):
            raise RuntimeError("keine Variante berechnet")
        update(ref, status="done", progress=100)
    except Exception:
        app.logger.exception("Berechnung fehlgeschlagen (%s)", ref)
        update(ref, status="error", message="Die Berechnung ist fehlgeschlagen.")


# ---------- Admin: bezahlte Bestellungen mit Anleitung ansehen (nur mit ADMIN_KEY) ----------
def admin_ok():
    key = os.environ.get("ADMIN_KEY", "")
    return bool(key) and hmac.compare_digest(request.args.get("key", ""), key)

@app.get("/admin")
def admin():
    if not admin_ok():
        abort(404)
    rows = []
    for ref in os.listdir(DATA_DIR):
        m = load(ref)
        if m and m.get("status") == "done":
            rows.append(dict(ref=ref, paid=m.get("paid", False), email=m.get("email") or "", title=item_title(m),
                             cents=price_cents(m), created=time.strftime("%d.%m.%Y %H:%M", time.localtime(m.get("created", 0)))))
    rows.sort(key=lambda r: (not r["paid"], r["created"]), reverse=False)
    return render_template_string("""<!doctype html><meta name=viewport content="width=device-width,initial-scale=1"><title>Bestellungen</title>
<body style="font-family:system-ui;background:#111;color:#eee;padding:1rem"><h1>Bestellungen</h1>
{% for r in rows %}<p style="border-bottom:1px solid #333;padding:.6rem 0"><b>{{ "BEZAHLT" if r.paid else "nicht bezahlt" }}</b> {{ r.created }}<br>
{{ r.title }}, {{ (r.cents / 100)|round(2) }} €<br>{{ r.email }}<br>
<a style="color:#e6ca65" href="/admin/{{ r.ref }}/anleitung.pdf?key={{ key }}">Anleitung (PDF)</a> |
<a style="color:#e6ca65" href="/admin/{{ r.ref }}/anleitung.txt?key={{ key }}">als Text</a> |
<a style="color:#e6ca65" href="/admin/{{ r.ref }}/final.png?key={{ key }}">Bild in voller Größe</a></p>{% else %}<p>Noch keine Bestellungen.</p>{% endfor %}""",
        rows=rows, key=request.args.get("key"))

@app.get("/admin/stripe-check")
def stripe_check():
    """Prüft, ob der Stripe-Schlüssel stimmt: /admin/stripe-check?key=DEIN_ADMIN_KEY"""
    if not admin_ok():
        abort(404)
    k = stripe.api_key or ""
    info = {"Stripe-Schlüssel gesetzt": bool(k), "beginnt mit": (k[:8] + "...") if k else "-",
            "Webhook-Schlüssel gesetzt": bool(WEBHOOK_SECRET), "Webhook beginnt mit": (WEBHOOK_SECRET[:6] + "...") if WEBHOOK_SECRET else "-"}
    try:
        stripe.Balance.retrieve()
        info["Verbindung zu Stripe"] = "OK"
    except Exception as e:
        info["Verbindung zu Stripe"] = "FEHLER: " + str(getattr(e, "user_message", None) or e)[:300]
    return jsonify(info)

@app.get("/admin/<ref>/<name>")
def admin_file(ref, name):
    if not admin_ok() or name not in ("anleitung.pdf", "anleitung.txt", "final.png", "pinfolge.txt") or not load(ref) or not os.path.exists(os.path.join(odir(ref), name)):
        abort(404)
    return send_file(os.path.join(odir(ref), name), as_attachment=(name != "final.png"), max_age=0)


# ---------- Seiten ----------
@app.get("/")
def index():
    return render_template("index.html")

@app.get("/configure")
def configure():
    warm_up_once()                          # Rechenkerne übersetzen, während der Kunde sein Foto auswählt
    cfg = {"sizes": SIZES, "pricing": {
        "test_quad_cents": TEST_QUAD_CENTS, "round_factor": ROUND_AREA, "base_cents": BASE_CENTS, "base_area": BASE_AREA, "cents_per_cm2": CENTS_PER_CM2,
        "wood": {k: v[2] for k, v in WOOD.items()}, "pins": PINS, "multi": MULTI_EUR}}
    return render_template("configure.html", cfg=cfg)

@app.get("/ueber-uns")
def about():
    return render_template("about.html")

@app.get("/success")
def success():
    session.pop("cart", None)
    sid = request.args.get("session_id", "")
    if sid.startswith("cs_") and stripe.api_key:      # Zahlung direkt bei Stripe prüfen: klappt auch ohne eingerichteten Webhook
        try:
            handle_checkout(stripe.checkout.Session.retrieve(sid))
        except Exception:
            app.logger.exception("Stripe-Sitzung %s konnte nicht geprüft werden", sid)
    return render_template("success.html")

for _p in ("impressum", "datenschutz", "agb", "widerruf"):
    app.add_url_rule(f"/{_p}", _p, lambda p=_p: render_template(f"{p}.html"))


# ---------- API ----------
def clamp(v, lo, hi):
    try:
        return min(hi, max(lo, int(float(v))))
    except (TypeError, ValueError):
        return lo

@app.post("/generate")
def generate():
    f, form = request.files.get("image"), request.form
    if not f:
        return jsonify(message="Kein Bild übermittelt"), 400
    try:
        Image.open(f.stream).verify()
        f.stream.seek(0)
        img = Image.open(f.stream).convert("RGB")
    except Exception:
        return jsonify(message="Ungültiges Bild"), 400

    wood, color, thick = form.get("wood"), form.get("color"), form.get("thick")
    pins = clamp(form.get("pins"), 0, 10000)
    disc, pin = form.get("disc") or "weiss", form.get("pin") or "gold"   # fehlt ein Feld (alte Seitenversion), gelten Standardwerte
    if pin not in PINCOL:
        return jsonify(message="Ungültige Nagelfarbe"), 400
    if disc not in DISC:
        return jsonify(message="Ungültige Kreisfarbe"), 400
    if color in COLORS and abs(lum(COLORS[color][1]) - lum(DISC[disc][1])) < MIN_CONTRAST:   # mehrfarbig: Farben passend zum Kreis gewählt
        return jsonify(message="Fadenfarbe und Kreisfarbe sind zu ähnlich"), 400
    fmt, size = form.get("format") or "quad", form.get("size") or "M"
    if fmt not in SIZES or size not in SIZES[fmt]:
        return jsonify(message="Ungültige Größe"), 400
    if wood not in WOOD or (color not in COLORS and color != MULTI) or thick not in THICKS or pins not in PINS:
        return jsonify(message="Ungültige Auswahl"), 400

    ref = uuid.uuid4().hex
    os.makedirs(odir(ref))
    img.save(os.path.join(odir(ref), "input.png"))
    update(ref, fmt=fmt, size=size, w=SIZES[fmt][size][0], h=SIZES[fmt][size][1], wood=wood,
           color=color, disc=disc, pin=pin, thick=thick, pins=pins, status="queued", paid=False, created=time.time())
    pool.submit(run_job, ref)
    return jsonify(order_ref=ref, job_id=ref, status="queued"), 202

@app.get("/version")
def version():
    """Zeigt, welche Dateien auf dem Server liegen (Größe und Prüfsumme). Hilft, unvollständig kopierte Dateien zu finden."""
    root, files = os.path.dirname(os.path.abspath(__file__)), {}
    for rel in ("app.py", "static/style.css", "static/configure.js", "templates/configure.html", "templates/base.html", "templates/cart.html"):
        try:
            with open(os.path.join(root, rel), "rb") as f:
                b = f.read()
            files[rel] = {"bytes": len(b), "pruefsumme": hashlib.sha1(b).hexdigest()[:8]}
        except OSError:
            files[rel] = "FEHLT"
    return jsonify(build=BUILD, dateien=files)

@app.get("/health")
def health():
    return jsonify(ok=True)

@app.get("/status/<ref>")
def status(ref):
    m = load(ref)
    if not m:
        abort(404)
    if m["status"] in ("queued", "running") and time.time() - max(m.get("beat", 0), m.get("created", 0)) > 300:
        update(ref, status="error", message="Die Berechnung wurde unterbrochen (Server-Neustart). Bitte erneut versuchen.")
        m = load(ref)
    out = {"status": m["status"], "order_ref": ref, "job_id": ref, "progress": m.get("progress", 0),
           "prog": m.get("prog", {}), "live": m.get("live", {}), "lines": {}}
    for k, v in (LIVE_SEQ.get(ref) or {}).items():           # neue Linien seit der letzten Abfrage (?sa=, ?sb=, ?sc=)
        if k in VARIANTS:
            since = max(0, request.args.get("s" + k, 0, type=int))
            end = min(len(v["cseq"]), since + 800)           # cseq wird nach seq ergänzt: so passen beide immer zusammen
            out["lines"][k] = {"from": since, "pins": v["seq"][since:end], "cols": v["cseq"][since:end], "hex": v["hex"],
                               "alpha": v["alpha"], "lw": v["lw"], "n": v["n"]}
    if m["status"] == "error":
        out["message"] = m.get("message", "Die Berechnung ist fehlgeschlagen.")
    ready = m.get("ready") or []
    out["variants"] = [{"key": k, "name": VARIANTS[k]["name"], "url": f"/preview/{ref}/{k}.png"} for k in VARIANTS if k in ready]
    if m["status"] == "done" and out["variants"]:
        out["preview_url"] = out["variants"][0]["url"]
    return jsonify(out)

@app.get("/live/<ref>/<key>.jpg")
def live_view(ref, key):
    p = os.path.join(odir(ref), f"live_{key}.jpg")
    if not load(ref) or key not in VARIANTS or not os.path.exists(p):
        abort(404)
    return send_file(p, mimetype="image/jpeg", max_age=0)

@app.get("/preview/<ref>/<key>.png")
def preview_variant(ref, key):
    p = os.path.join(odir(ref), f"preview_{key}.png")
    if not load(ref) or key not in VARIANTS or not os.path.exists(p):
        abort(404)
    return send_file(p, mimetype="image/png", max_age=0)

@app.get("/preview/<ref>.png")
def preview(ref):
    return preview_variant(ref, "a")

MAX_CART = 10

def choose_variant(ref, key):
    """Die gewählte Variante wird zur Bestellung: Bild, Pinfolge und Anleitung erhalten die festen Dateinamen."""
    for src, dst in ((f"final_{key}.png", "final.png"), (f"pinfolge_{key}.txt", "pinfolge.txt"),
                     (f"anleitung_{key}.txt", "anleitung.txt"), (f"anleitung_{key}.pdf", "anleitung.pdf"), (f"preview_{key}.png", "preview.png")):
        shutil.copyfile(os.path.join(odir(ref), src), os.path.join(odir(ref), dst))
    update(ref, variant=key)

def thread_label(m):
    if m["color"] == MULTI:                  # Farben der gewählten Variante (oder alle vorgeschlagenen, solange keine gewählt ist)
        cols = (m.get("used") or {}).get(m.get("variant")) or m.get("palette")
        return "Mehrfarbig" + (f" ({', '.join(thread_name(c) for c in cols)})" if cols else "")
    return COLORS[m["color"]][0]

def item_title(m):
    return (f"String Art {m.get('size', '')} ({board_label(m)}), {WOOD[m['wood']][0]}, "
            f"{thread_label(m)} auf {DISC[m['disc']][0]}, {m['pins']} Pins ({PINCOL[m.get('pin', 'gold')][0]})"
            + (f", Stil {VARIANTS[m['variant']]['name']}" if m.get("variant") in VARIANTS else ""))

def cart_items():
    items, keep = [], []
    for ref in session.get("cart", []):
        m = load(ref)
        if m and m["status"] == "done":
            keep.append(ref)
            items.append({"ref": ref, "title": item_title(m), "cents": price_cents(m), "variant": m.get("variant", "a")})
    session["cart"] = keep
    return items

@app.template_filter("euro")
def euro(cents):
    return f"{cents / 100:.2f}".replace(".", ",") + " €"

def asset_v(name):
    """Änderungszeit der Datei als Versionsnummer: erzwingt, dass der Browser neue CSS/JS-Dateien lädt."""
    try:
        return int(os.path.getmtime(os.path.join(app.static_folder, name)))
    except OSError:
        return 0

@app.context_processor
def inject_cart():
    return {"cart_count": len(session.get("cart", [])), "asset_v": asset_v}

@app.errorhandler(413)
def too_big(e):
    return jsonify(message="Das Bild ist zu groß (maximal 16 MB)"), 413

@app.errorhandler(400)
def bad_request(e):
    return jsonify(message="Ungültige Anfrage. Bitte Seite neu laden und erneut versuchen."), 400

@app.get("/warenkorb")
def cart():
    items = cart_items()
    return render_template("cart.html", items=items, total=sum(i["cents"] for i in items))

@app.post("/cart/add")
def cart_add():
    body = request.get_json(silent=True) or {}
    ref, key = body.get("order_ref"), body.get("variant") or "a"
    m = load(ref)
    if not m or m["status"] != "done" or key not in (m.get("ready") or []):
        return jsonify(message="Bestellung nicht gefunden"), 400
    choose_variant(ref, key)
    cart = session.get("cart", [])
    if ref not in cart:
        if len(cart) >= MAX_CART:
            return jsonify(message=f"Der Warenkorb ist voll (max. {MAX_CART} Motive)"), 400
        cart.append(ref)
    session["cart"], session.permanent = cart, True
    return jsonify(count=len(cart))

@app.post("/cart/remove")
def cart_remove():
    ref = (request.get_json(silent=True) or {}).get("order_ref")
    session["cart"] = [r for r in session.get("cart", []) if r != ref]
    return jsonify(count=len(session["cart"]))

@app.post("/checkout")
def checkout():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip()
    items = cart_items()
    if not items:
        return jsonify(message="Der Warenkorb ist leer"), 400
    if not re.match(r"^\S+@\S+\.\S+$", email) or data.get("agb") is not True:
        return jsonify(message="E-Mail oder AGB-Zustimmung fehlt"), 400
    if not stripe.api_key:
        return jsonify(message="Zahlung ist noch nicht eingerichtet (STRIPE_SECRET_KEY fehlt bei Render)"), 500
    if not stripe.api_key.startswith(("sk_", "rk_")):
        return jsonify(message="Der Stripe-Schlüssel bei Render ist falsch: Er muss der GEHEIME Schlüssel sein und mit sk_test_ beginnen "
                               "(nicht pk_ und nicht whsec_)"), 500
    try:
        s = stripe.checkout.Session.create(
            mode="payment", locale="de", customer_email=email,   # Zahlarten legt das Stripe-Dashboard fest (payment_method_types gibt es nicht mehr)
            line_items=[{"quantity": 1, "price_data": {
                "currency": "eur", "unit_amount": i["cents"],   # Preise kommen nur vom Server
                "product_data": {"name": i["title"]}}} for i in items],
            shipping_address_collection={"allowed_countries": ["DE", "AT", "CH"]},
            metadata={"order_refs": ",".join(i["ref"] for i in items)},
            success_url=request.host_url + "success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=request.host_url + "warenkorb")
        return jsonify(url=s.url)
    except Exception as e:
        app.logger.exception("Stripe-Fehler")
        detail = ""
        if stripe.api_key.startswith(("sk_test", "rk_test")):     # nur im Testmodus die genaue Stripe-Meldung zeigen
            detail = ": " + str(getattr(e, "user_message", None) or e)[:300]
        return jsonify(message="Checkout konnte nicht gestartet werden" + detail), 500

def notify_owner(ref):
    """Schickt dir bei jeder bezahlten Bestellung Anleitung und Bild per E-Mail (sichert die Daten, falls der Server-Speicher gelöscht wird)."""
    try:
        host, to = os.environ.get("SMTP_HOST"), os.environ.get("ORDER_MAIL_TO")
        if not (host and to):
            app.logger.warning("Bestellung %s bezahlt. SMTP_HOST/ORDER_MAIL_TO fehlen, keine E-Mail. Daten liegen in %s", ref, odir(ref))
            return
        m = load(ref)
        msg = EmailMessage()
        msg["Subject"], msg["To"], msg["From"] = f"Neue Bestellung: {item_title(m)}", to, os.environ.get("SMTP_USER", to)
        msg.set_content(f"Bezahlt: {price_cents(m) / 100:.2f} EUR\nKunden-E-Mail: {m.get('email')}\nBestellnummer: {ref}\n"
                        "Im Anhang: Anleitung als PDF (Maße, Einstellungen, Pinfolge zum Abhaken), das Bild und die Anleitung als Text.\n"
                        "Die Lieferadresse steht im Stripe-Dashboard.")
        for name, kind in (("anleitung.pdf", "application/pdf"), ("final.png", "image/png"), ("anleitung.txt", "text/plain")):
            with open(os.path.join(odir(ref), name), "rb") as f:
                msg.add_attachment(f.read(), maintype=kind.split("/")[0], subtype=kind.split("/")[1], filename=name)
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=30) as s:
            s.starttls()
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
            s.send_message(msg)
    except Exception:
        app.logger.exception("Bestell-E-Mail konnte nicht gesendet werden (%s)", ref)

@app.post("/stripe-webhook")
def stripe_webhook():
    try:
        ev = stripe.Webhook.construct_event(
            request.data, request.headers.get("Stripe-Signature", ""), WEBHOOK_SECRET)
    except Exception:
        return "", 400
    if ev["type"] == "checkout.session.completed":
        handle_checkout(ev["data"]["object"])
    return "", 200

def handle_checkout(s):
    """Bezahlte Checkout-Sitzung: Bestellungen als bezahlt markieren und dir die Unterlagen schicken.
    Webhook und Erfolgsseite können dieselbe Zahlung melden; gemailt wird nur beim ersten Mal."""
    if s["payment_status"] != "paid":           # Klammer-Zugriff statt .get(): läuft mit alten und neuen stripe-Versionen
        return
    meta = s["metadata"] if "metadata" in s else {}
    refs = meta["order_refs"] if "order_refs" in meta else ""
    email = s["customer_email"] if "customer_email" in s else None
    for ref in refs.split(","):
        with _meta_lock:
            m = load(ref)
            if not m or m.get("paid"):
                continue
            m.update(paid=True, stripe_session=s["id"], email=email)
            _write(ref, m)
        threading.Thread(target=notify_owner, args=(ref,), daemon=True).start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

