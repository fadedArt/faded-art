import os as _os
for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "NUMBA_NUM_THREADS"):
    _os.environ.setdefault(_v, "1")   # Render gibt nur 0,5 CPU: sonst startet numpy Dutzende Rechen-Threads je Matrixprodukt (massiv langsamer)
import base64, hashlib, hmac, json, secrets, math, mimetypes, os, re, shutil, smtplib, threading, time, urllib.error, urllib.request, uuid
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
os.environ.setdefault("NUMBA_CACHE_DIR", os.path.join(os.environ.get("DATA_DIR", "data"), ".numba"))   # übersetzte Rechenkerne überleben Neustarts
os.environ.setdefault("FADED_CACHE", os.environ.get("DATA_DIR", "data"))                              # Sehnen-Tabellen von engine2 ebenso
from numba import njit
import engine2

mimetypes.add_type("application/wasm", ".wasm")          # Freisteller im Browser (static/mp): Browser verlangen diese Typen
mimetypes.add_type("text/javascript", ".mjs")
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
sugg_pool = ThreadPoolExecutor(max_workers=1)   # Vorschläge separat: blockieren nicht die nächste Berechnung
CANCEL = set()                             # abgebrochene Vorab-Berechnungen (Kunde hat Einstellungen geändert)

class Cancelled(Exception):
    pass

def check_cancel(ref):
    if ref in CANCEL:
        raise Cancelled()
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
    "sw": ("Schwarz + Weiß gemischt", (12, 12, 12)),
}
SHOP_COLORS = ("schwarz", "weiss", "sw")   # Fadenfarben, die der Shop gerade anbietet (Gold, Silber, Mehrfarbig: hier ergänzen)
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
MULTI_SAT, MULTI_CHROMA = 1.8, 2.5        # Mehrfarbig: Sättigung des Fotos, Gewicht der Farbe beim Zerlegen
MULTI_LINEAR, MULTI_LINES_F = True, 1.3   # Mehrfarbig nach dem linearen Modell (wie Einfarbig); Linien = LINES x Faktor
MULTI_MAX, MULTI_EUR = 4, 0   # höchstens so viele Fadenfarben; Aufpreis für Mehrfarbig in € (noch festlegen!)
SW = "sw"                     # Schwarz + Weiß gemischt: zwei Fäden, Engine 2 (engine2.py), Testsieger vom 13.10.2026
SW_EUR = 40                   # Aufpreis für Schwarz + Weiß in € (Platzhalter, bitte festlegen!)
SW_PAL = ["schwarz", "weiss"]
SW_LINES = {240: 9000, 360: 12000}   # Höchstzahl Fäden (gemischt braucht ca. 3x so viel Faden wie Einfarbig)
SW_STYLE = {"a": dict(gamma=1.0, clip=0.008), "b": dict(gamma=1.2, clip=0.012), "c": dict(gamma=0.85, clip=0.005)}
# Vorschläge: werden zusätzlich zu den drei Varianten berechnet, ohne Rücksicht auf den Preis (bestes Ergebnis).
# set = was sich an der Bestellung ändert, wenn der Kunde den Vorschlag wählt (Aufpreis ergibt sich daraus automatisch)
SUGGEST = {
    "d": dict(name="Schwarz + Weiß auf Schwarz", set=dict(color=SW, disc="schwarz", wood="mdf", pins=360), edges=False),
    "e": dict(name="Schwarz + Weiß auf Nussbaum", set=dict(color=SW, disc="braun", wood="nussbaum", pins=360), edges=False),
    "f": dict(name="Schwarz + Weiß, Konturen betont", set=dict(color=SW, disc="anthrazit", pins=360), edges=True),
}
BLOCK = 150                   # Mehrfarbig: so viele Linien am Stück mit einer Farbe, dann wird ggf. gewechselt
CHROMA = 0.7                  # Gewicht der Farbabweichung gegenüber der Helligkeit (Mehrfarbig)

def style_name(key):
    return (VARIANTS.get(key) or SUGGEST.get(key) or {"name": key})["name"]

def with_suggestion(m, key):
    """Bestellung so, wie sie mit dem Vorschlag key aussähe (ohne sie zu ändern)."""
    return dict(m, **SUGGEST[key]["set"]) if key in SUGGEST else dict(m)

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
E_PHYS = 0.444                 # physikalisch: 0,20 mm Faden / 0,45 mm Pixelbreite (45 cm auf 1000 px). Einfarbig und Schwarz + Weiß
                               # rechnen und zeichnen damit (Test 14.10.2026: Ähnlichkeit 81,9 -> 93,2, Vorschau so dunkel wie das echte Brett)
ROUND_FORMATS = {"rund"}; ROUND_AREA = 0.785   # Rundes Holz: Fläche = Kreis, Preis nach Fläche
PINCOL = {  # Farbe der Nägel
    "gold": ("Gold", (212, 175, 55)), "silber": ("Silber", (205, 205, 205)),
    "schwarz": ("Schwarz", (30, 30, 30)), "weiss": ("Weiß", (245, 245, 245)),
    "kupfer": ("Kupfer", (184, 115, 51)),
}
LINES = {240: 3500, 360: 4500}   # Fäden je Pin-Zahl (Basis für Mehrfarbig)
LINES_ONE = {240: 1600, 360: 2000}   # Einfarbig (14.10.2026): 4.500 Fäden machten das Bild zu dunkel; 2.000 mit E_PHYS ist am nächsten am Foto
LINES_MULTI = {240: 8000, 360: 10000}   # Höchstzahl für Mehrfarbig
MAX_USE = 1                   # Einfarbig: so oft darf dieselbe Sehne gezogen werden
BG_TONE = 0.0                 # freigestellter Hintergrund: gleichmäßige Dunkelheit (0 = neutral; < 0: Linien meiden ihn, wirkt fleckig)
BG_EDGE = 0.3                 # freigestellt: zusätzliche Dunkelheit entlang der Umrisslinie der Figur (klare Kontur)
BG_KEEP_CLEAN = 0.15          # (Mehrfarbig) freigestellter Hintergrund: so stark weichen Linien ihm aus
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
BUILD = "b32"   # Versionsnummer: app.py, configure.html, configure.js und style.css müssen dieselbe tragen
TEST_QUAD_CENTS = int(os.environ.get("TESTPREIS_QUAD_CENTS", "0"))   # Testpreis für ALLE quadratischen Bretter; 0 = aus (Live-Betrieb)
BASE_CENTS, BASE_AREA, CENTS_PER_CM2 = 14900, 2500, 2.5   # Vorschlag: 149 € bis 50 x 50 cm, darüber 2,5 Cent je cm² (70 x 70 = 209 €, 100 x 100 = 336 €)
PREORDER_NOTE = os.environ.get("VORBESTELL_HINWEIS", "Vorbestellung: Wir fertigen ab Februar 2027 in der Reihenfolge der Bestellungen.")

# ---------- Gutscheine (Wertgutscheine, auch zum Vorbestellen) ----------
VOUCHER_VALUES = (5000, 10000, 15000, 20000, 30000)    # wählbare Werte in Cent
VOUCHER_LIMIT = int(os.environ.get("GUTSCHEIN_LIMIT", "25"))   # höchstens so viele Gutscheine verkaufen (0 = unbegrenzt)
VOUCHER_YEARS = 3                                        # gesetzliche Verjährung: 3 Jahre zum Jahresende
VOUCHER_SALE_PCT = int(os.environ.get("GUTSCHEIN_RABATT", "20"))           # Aktion: Gutschein kostet X % weniger als sein Wert (0 = aus)
VOUCHER_SALE_UNTIL = os.environ.get("GUTSCHEIN_AKTION_BIS", "2026-12-24")  # letzter Aktionstag (danach automatisch voller Preis)

def voucher_sale_on():
    return VOUCHER_SALE_PCT > 0 and time.strftime("%Y-%m-%d") <= VOUCHER_SALE_UNTIL

def voucher_pay_cents(value):
    """Was der Käufer zahlt. Der Gutschein behält immer seinen vollen Wert."""
    return round(value * (100 - VOUCHER_SALE_PCT) / 100) if voucher_sale_on() else value

def sale_until_de():
    y, m, d = VOUCHER_SALE_UNTIL.split("-")
    return f"{int(d)}.{int(m)}."
VDIR = os.path.join(DATA_DIR, "gutscheine")
os.makedirs(VDIR, exist_ok=True)
_vlock = threading.Lock()
CODE_RE = re.compile(r"^FA-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$")

def voucher_load(code):
    code = (code or "").strip().upper()
    if not CODE_RE.match(code):
        return None
    try:
        with open(os.path.join(VDIR, code + ".json")) as f:
            return json.load(f)
    except OSError:
        return None

def voucher_save(v):
    p = os.path.join(VDIR, v["code"] + ".json")
    with open(p + ".tmp", "w") as f:
        json.dump(v, f)
    os.replace(p + ".tmp", p)

def voucher_sold():
    return sum(1 for f in os.listdir(VDIR) if f.endswith(".json"))

def voucher_valid_until(created):
    y = time.localtime(created).tm_year + VOUCHER_YEARS
    return f"31.12.{y}"

def voucher_ok(v):
    return bool(v) and v["left"] > 0 and time.time() < time.mktime((time.localtime(v["created"]).tm_year + VOUCHER_YEARS, 12, 31, 23, 59, 59, 0, 0, -1))

def voucher_create(session_id, cents, email, name, paid=None):
    """Legt den Gutschein nach bezahlter Zahlung an. Mehrfachmeldung derselben Zahlung erzeugt keinen zweiten."""
    with _vlock:
        for f in os.listdir(VDIR):
            if f.endswith(".json"):
                v = json.load(open(os.path.join(VDIR, f)))
                if v.get("session") == session_id:
                    return v
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        while True:
            code = "FA-" + "".join(secrets.choice(alphabet) for _ in range(4)) + "-" + "".join(secrets.choice(alphabet) for _ in range(4))
            if not os.path.exists(os.path.join(VDIR, code + ".json")):
                break
        v = dict(code=code, cents=cents, left=cents, paid=cents if paid is None else paid, email=email, name=name,
                 session=session_id, created=time.time(), uses=[])
        voucher_save(v)
    threading.Thread(target=voucher_mail, args=(v,), daemon=True).start()
    return v

def voucher_use(code, cents, use_id):
    """Zieht cents vom Gutschein ab (einmal je use_id)."""
    with _vlock:
        v = voucher_load(code)
        if not v or use_id in v["uses"]:
            return
        v["left"] = max(0, v["left"] - cents)
        v["uses"].append(use_id)
        voucher_save(v)
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

def update_variant(ref, key, pct=None, live=None, ready=False, used=None, lines=None):
    """Fortschritt, Live-Bildnummer, verwendete Fadenfarben und Fertig-Meldung EINER Variante (drei laufen gleichzeitig)."""
    with _meta_lock:
        m = load(ref) or {}
        if used is not None:
            m.setdefault("used", {})[key] = used
        if lines is not None:
            m.setdefault("nlines", {})[key] = lines
        if pct is not None:
            m.setdefault("prog", {})[key] = pct
        if live is not None:
            m.setdefault("live", {})[key] = live
        if ready and key not in (m.get("ready") or []):
            m["ready"] = (m.get("ready") or []) + [key]
        m["beat"] = time.time()
        if m.get("prog"):
            m["progress"] = int(sum(v for k, v in m["prog"].items() if k in VARIANTS) / len(VARIANTS))
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
    return BASE_CENTS + round(extra) + (WOOD[m["wood"]][2] + PINS[m["pins"]] + (MULTI_EUR if m.get("color") == MULTI else 0) + (SW_EUR if m.get("color") == SW else 0)) * 100


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
        engine2.warmup()
    except Exception:
        app.logger.exception("numba-Vorübersetzung fehlgeschlagen")

_warm = []

WARM = threading.Event()                     # gesetzt, sobald Rechenkerne und Tabellen bereit sind

def _prebuild():
    """Rechenkerne übersetzen und Sehnen-Tabellen laden, bevor die erste Berechnung startet (nacheinander, nie parallel)."""
    t0 = time.time()
    app.logger.warning("Vorbereitung startet")
    try:
        _warmup()
        app.logger.warning("Rechenkerne Engine 1 bereit nach %.1f s", time.time() - t0)
        for n in (360, 240):
            engine2.lines_for(n)
        engine2.warmup()
        app.logger.warning("Vorbereitung fertig nach %.1f s", time.time() - t0)
    except Exception:
        app.logger.exception("Vorbereitung fehlgeschlagen")
    finally:
        WARM.set()

_prebuild_started = []

@app.before_request
def _start_prebuild():
    """Erst nach dem Import starten (Threads während des Imports können numba blockieren)."""
    if not _prebuild_started:
        _prebuild_started.append(1)
        threading.Thread(target=_prebuild, daemon=True).start()
        threading.Thread(target=_cleanup_loop, daemon=True).start()

UPLOAD_KEEP_DAYS = int(os.environ.get("UPLOAD_LOESCHEN_TAGE", "30"))   # nicht bestellte Uploads so lange behalten (steht so in der Datenschutzerklärung)

def _cleanup_loop():
    """Einmal täglich: Uploads und Berechnungen ohne Bezahlung nach UPLOAD_KEEP_DAYS Tagen löschen. Bezahlte Bestellungen bleiben."""
    while True:
        try:
            cutoff = time.time() - UPLOAD_KEEP_DAYS * 86400
            for ref in os.listdir(DATA_DIR):
                if not REF_RE.match(ref):
                    continue
                m = load(ref)
                if m is None or m.get("paid"):
                    continue
                if m.get("created", time.time()) < cutoff:
                    shutil.rmtree(odir(ref), ignore_errors=True)
        except Exception as e:                                  # nie den Server stören
            app.logger.warning("Aufräumen fehlgeschlagen: %s", e)
        time.sleep(86400)

def warm_up_once():
    pass                                     # passiert jetzt direkt beim Serverstart (_prebuild), nicht erst beim Besuch

def _simplex(v):
    """Projiziert jede Zeile auf 'alle Werte >= 0, Summe 1'."""
    u = -np.sort(-v, axis=1)
    css = np.cumsum(u, 1) - 1
    rho = (u - css / np.arange(1, v.shape[1] + 1) > 0).sum(1)
    return np.maximum(v - (css[np.arange(len(v)), rho - 1] / rho)[:, None], 0)

def mix(P, V, iters=100):
    """Mischanteile (>= 0, Summe 1) der Farben V, die jede Farbe in P am besten nachmischen.
    Aus der Ferne mischt das Auge Fäden und Untergrund wie Farben (Mischung = gewichteter Mittelwert)."""
    V = np.asarray(V, np.float32)
    lam = np.full((len(P), len(V)), 1 / len(V), np.float32)
    step = 1 / (np.linalg.norm(V @ V.T, 2) + 1e-6)
    for _ in range(iters):
        lam = _simplex(lam - step * ((lam @ V - P) @ V.T))
    return lam

def mix_error(P, V):
    """Wie gut lassen sich die Farben P aus den Farben V mischen?"""
    lam = mix(P, V)
    return float(((lam @ np.asarray(V, np.float32) - P) ** 2).sum(1).mean())

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
    img = np.asarray(load_input(path, 32)[0], np.float32)
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

def edge_of(alpha, width=4):
    """Weicher Streifen (0..1) entlang des Umrisses einer Freistell-Maske."""
    m = Image.fromarray((alpha * 255).astype(np.uint8))
    e = np.abs(np.asarray(m.filter(ImageFilter.GaussianBlur(width)), np.float32) / 255 - alpha)
    return np.minimum(1, e * 4).astype(np.float32)

def load_input(path, size):
    """Hochgeladenes Bild auf size x size: (RGB auf Weiß, Deckung 0..1 oder None). Transparenz = im Browser freigestellter Hintergrund."""
    src = Image.open(path)
    if src.mode not in ("RGBA", "LA"):
        return src.convert("RGB").resize((size, size), Image.Resampling.LANCZOS), None
    src = src.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    alpha = np.asarray(src.getchannel("A"), np.float32) / 255
    return Image.alpha_composite(Image.new("RGBA", src.size, (255, 255, 255, 255)), src).convert("RGB"), alpha

def compute_string_art(ref, m, key="a"):
    """Gierige String-Art: pro Schritt wird ab dem aktuellen Nagel die beste Sehne gewählt.
    Einfarbig (lineares Modell, liefert die erkennbarsten Gesichter): Zielbild = gewünschte Dunkelheit je Rasterzelle;
    gewählt wird die Sehne mit der größten durchschnittlichen Rest-Dunkelheit, danach zieht sie WEIGHT x Fadendeckung ab.
    Dunkle Stellen bekommen so Fäden proportional zu ihrer Dunkelheit, und kurze Sehnen können Details (Augen) treffen.
    Mehrfarbig (Farbmodell): jede Zelle hat eine Farbe, jeder Faden mischt seine Farbe hinein; gewählt wird der größte
    Fehlerrückgang. In beiden Fällen werden nach jeder Linie nur die Zellen dieser Linie neu bewertet."""
    check_cancel(ref)
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
    a = min(0.4, max(0.02, (E_PHYS if K == 1 else E_REF) * (t_mm / 0.20) * (D0_CM / disc_cm) / LW))     # Deckkraft pro Faden
    n_lines = int((LINES[n] * MULTI_LINES_F if K > 1 and MULTI_LINEAR else (LINES_ONE if K == 1 else LINES_MULTI)[n]) * min(1.6, max(1.0, (0.20 / t_mm) * (disc_cm / D0_CM))))   # feinerer Faden braucht mehr Linien
    geo = geometry(n)
    px, py, rp, ok, nsc = geo["px"], geo["py"], geo["rp"], geo["ok"], geo["nsc"]
    ln = ((1 if K == 1 or MULTI_LINEAR else geo["L"]) / nsc).astype(np.float32)   # lineares Modell: Durchschnitt, Farbmodell: Summe

    # 1) Bild vorbereiten, Ziel je Rasterzelle (2x2 px: so sieht das Auge das Bild aus Distanz)
    rgb, alpha = load_input(os.path.join(d, "input.png"), W)
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
        if alpha is not None:                                # freigestellt: Hintergrund gleichmäßig, Kontur der Figur betont
            c = alpha * c + (1 - alpha) * BG_TONE + BG_EDGE * edge_of(alpha)
        Gk = cells(np.where(inside, c, 0).astype(np.float32))[None].copy()   # restliche Dunkelheit je Zelle
        C = T = np.zeros((1, 3), np.float32)                 # (nur im Farbmodell gebraucht)
        lin = WEIGHT * B
    elif MULTI_LINEAR:                                       # Mehrfarbig: jede Zelle in Fadenanteile zerlegen, je Farbe wie Einfarbig
        Wh = Wc // 2                                         # Zerlegung in halber Rasterauflösung (Farben ändern sich langsam)
        x = opp(np.asarray(ImageEnhance.Color(rgb).enhance(MULTI_SAT).resize((Wh, Wh), Image.Resampling.BOX), np.float32)).reshape(-1, 3)
        y = clahe(np.asarray(gray), var["clahe"]) if var["clahe"] else np.asarray(gray, np.float32) / 255
        x[:, 0] = np.asarray(Image.fromarray((y ** var["tone"] * 255).astype(np.uint8)).resize((Wh, Wh), Image.Resampling.BOX), np.float32).ravel() / 255
        wch = np.array([1, MULTI_CHROMA, MULTI_CHROMA], np.float32)          # Farbtreue stärker gewichten als beim Paletten-Vergleich
        lam = mix(x * wch, np.vstack([d0[None], Co]) * wch, 150)[:, 1:].reshape(Wh, Wh, K)   # Anteil je Fadenfarbe (ohne Untergrund)
        amt = lam.repeat(2, 0).repeat(2, 1).reshape(Wc * Wc, K) * cells(inside.astype(np.float32))[:, None]
        if alpha is not None:
            amt = amt * cells(alpha)[:, None] - (1 - cells(alpha))[:, None] * BG_KEEP_CLEAN
        Gk = np.ascontiguousarray(amt.T, np.float32)         # restlicher Anteil je Farbe und Zelle
        C = T = np.zeros((1, 3), np.float32)
        lin = WEIGHT * B
        quota = np.maximum(Gk, 0).sum(1)
        quota = quota / max(quota.sum(), 1e-6) * n_lines     # Linien je Farbe nach ihrem Anteil am Bild
    else:                                                    # Mehrfarbig (Farbmodell): Helligkeit wie oben, Farbe aus dem Foto (etwas kräftiger)
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

    last_save = [0.0]

    def save_live(pct):
        nonlocal live_n
        check_cancel(ref)
        if pct and time.time() - last_save[0] < 2.0:          # Disk nur alle 2 s (Linien gehen über LIVE_SEQ)
            return
        last_save[0] = time.time()
        live_n += 1
        live.save(os.path.join(d, f"live_{key}.tmp.jpg"), quality=80)
        os.replace(os.path.join(d, f"live_{key}.tmp.jpg"), os.path.join(d, f"live_{key}.jpg"))   # atomar: der Browser sieht nie halbe Bilder
        update_variant(ref, key, pct=pct, live=live_n)

    save_live(0)
    used = np.zeros((K, n, n), np.uint8)                  # wie oft jede Sehne schon gezogen wurde (je Farbe)
    maxuse = MAX_USE

    # 2) Gierige Auswahl. Mehrfarbig: in Abschnitten von BLOCK Linien mit der Farbe, die gerade am meisten bringt.
    cur = [round(i * n / K) % n for i in range(K)]       # jede Farbe beginnt an einem anderen Pin
    seq, cseq, started = [], [], set()
    for r in [r for r, v in list(LIVE_SEQ.items()) if time.time() - v.get("t", 0) > 3600]:
        LIVE_SEQ.pop(r, None)
    LIVE_SEQ.setdefault(ref, {"t": time.time()})[key] = {"seq": seq, "cseq": cseq, "hex": ["#%02x%02x%02x" % c for c in cols],
                                                         "alpha": round(lalpha / 255, 4), "lw": lwid, "n": n}
    buf, coef = np.empty(100, np.int64), logk * LW / (q * q)
    floor = -1e30 if lin else 0.0                        # lineares Modell: immer die volle Linienzahl (das Bild entsteht aus der Dichte)

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
    done = np.zeros(K)
    if K == 1:
        quota = np.array([n_lines])
    while lines < n_lines:
        if left <= 0 and lin:                             # nächster Abschnitt: Farbe, die am weitesten hinter ihrem Anteil liegt
            k = int(np.argmin(done / np.maximum(quota, 1e-6) + (done >= quota) * 1e9))
            if done[k] >= quota[k]:
                break
            left = BLOCK
        elif left <= 0:                                   # nächster Abschnitt: Farbe mit dem größten Gewinn
            gains = [_best(i, cur[i], px, py, ln, ok, used, Gk, q, Wc, nsc, -1e30, maxuse)[1] for i in range(K)]
            k = int(np.argmax(gains))
            if gains[k] <= 0:
                break                                     # Auto-Stopp: kein Faden verbessert das Bild mehr
            left = BLOCK
        want = min(100 - lines % 100, left, n_lines - lines)
        got = draw(k, want)
        lines, left = lines + got, left - got
        done[k] += got
        if got and lines % 100 == 0:                      # Live-Bild, Fortschritt und Lebenszeichen
            save_live(min(99, int(100 * lines / n_lines)))
        if got < want:
            if K == 1:
                break
            left = 0

    runs = color_runs(seq, cseq)
    update_variant(ref, key, lines=sum(len(p) - 1 for _, p in runs))
    render_final(ref, m, key, runs, cols, px, py, a, LW)
    with open(os.path.join(d, f"pinfolge_{key}.txt"), "w") as f:
        if K == 1:
            f.write(",".join(map(str, seq)))
        else:                                                 # Mehrfarbig: eine Zeile je Abschnitt
            f.write("\n".join(f"{thread_name(pal[k])}: " + ",".join(map(str, p)) for k, p in runs))
    update_variant(ref, key, used=[pal[k] for k in sorted(set(cseq))])   # Mehrfarbig: nicht jede gewählte Farbe wird gebraucht
    write_instructions(ref, m, runs, pal, px, py, rp, key, n_lines)

def compute_sw(ref, m, key):
    """Schwarz + Weiß gemischt (engine2.py). Für die Varianten a/b/c bei Fadenwahl "sw" und für die Vorschläge d/e/f."""
    mm = with_suggestion(m, key)
    st = SW_STYLE.get(key, SW_STYLE["a"])
    sg = SUGGEST.get(key)
    d, n, pal = odir(ref), mm["pins"], SW_PAL
    cols = [thread_rgb(c) for c in pal]
    disc = DISC[mm["disc"]][1]
    disc_cm = max(10.0, ring_cm(mm))
    t_mm = float(mm["thick"])
    a_r = t_mm * engine2.RASTER / (disc_cm * 10)            # Deckkraft eines Fadens im Rechenraster (Fadendicke / Rasterzelle)
    L = engine2.lines_for(n)
    t, w = engine2.target(os.path.join(d, "input.png"), gamma=st["gamma"], clip=st["clip"], edges=bool(sg and sg["edges"]))
    max_lines = int(SW_LINES[n] * min(1.6, max(1.0, (0.20 / t_mm) * (disc_cm / D0_CM))))
    geo = geometry(n)
    px, py, rp = geo["px"], geo["py"], geo["rp"]
    LIVE, lk = 600, 600 / W_IMG                             # Live-Bild wie bei compute_string_art
    live = Image.new("RGB", (LIVE, LIVE), disc)
    ld = ImageDraw.Draw(live, "RGBA")
    a = min(0.4, max(0.02, E_PHYS * (t_mm / 0.20) * (D0_CM / disc_cm) / LINE_W))   # Zeichnen wie Engine 2 rechnet (physikalisch)
    lalpha = min(255, int(255 * a * LINE_W * lk))
    state = {"drawn": 0, "n": 0}
    seq, cseq, started = [], [], set()                      # Live-Linien für den Browser (jede Linie einzeln gezeichnet)
    LIVE_SEQ.setdefault(ref, {"t": time.time()})[key] = {"seq": seq, "cseq": cseq, "hex": ["#%02x%02x%02x" % c for c in cols],
                                                         "alpha": round(lalpha / 255, 4), "lw": 1, "n": n,
                                                         "bg": "#%02x%02x%02x" % disc}

    def progress(f, order):
        check_cancel(ref)
        for k, p0, p1 in order[state["drawn"]:]:
            if k not in started:
                started.add(k); seq.append(p0); cseq.append(k)
            seq.append(p1); cseq.append(k)
            ld.line([(px[p0] * lk, py[p0] * lk), (px[p1] * lk, py[p1] * lk)], fill=cols[k] + (lalpha,), width=1)
        state["drawn"] = len(order)
        now = time.time()
        if now - state.get("saved", 0) < 2.0 and f < 1.0:   # Disk nur alle 2 s: die Linien gehen über den Speicher (LIVE_SEQ)
            return
        state["saved"] = now
        if state["drawn"] // 1000 > state["n"]:
            state["n"] += 1
            live.save(os.path.join(d, f"live_{key}.tmp.jpg"), quality=80)
            os.replace(os.path.join(d, f"live_{key}.tmp.jpg"), os.path.join(d, f"live_{key}.jpg"))
            update_variant(ref, key, pct=min(99, int(100 * f)), live=state["n"])
        else:
            update_variant(ref, key, pct=min(99, int(100 * f)))

    t0 = time.time()
    order = engine2.solve(t, w, L, [lum(c) / 255 for c in cols], lum(disc) / 255, a_r, max_lines, progress, board_mm=disc_cm * 10)
    app.logger.warning("Engine 2 %s/%s: %d Fäden in %.1f s (numba %s)", ref[:6], key, len(order), time.time() - t0, engine2._NUMBA)
    progress(1.0, order)
    runs = engine2.runs_of(order)
    update_variant(ref, key, lines=len(order))
    render_final(ref, mm, key, runs, cols, px, py, a, LINE_W)
    with open(os.path.join(d, f"pinfolge_{key}.txt"), "w") as f:
        f.write("\n".join(f"{thread_name(pal[k])}: " + ",".join(map(str, p)) for k, p in runs))
    update_variant(ref, key, used=list(pal))
    write_instructions(ref, mm, runs, pal, px, py, rp, key, max_lines)

def wood_texture(wood, size):
    """Holzmaserung aus static/img/holz (900 px, prozedural erzeugt) in Bildgröße; None, wenn die Datei fehlt."""
    p = os.path.join(app.static_folder, "img", "holz", f"{wood}.jpg")
    try:
        return Image.open(p).convert("RGB").resize((size, size), Image.Resampling.LANCZOS)
    except OSError:
        return None

def render_final(ref, m, key, runs, cols, px, py, a, LW):
    """Rendern in 2000 px, Abschnitt für Abschnitt (spätere Fäden liegen oben); Deckkraft überlagert sich physikalisch."""
    d, W, S = odir(ref), W_IMG, S_RENDER
    disc, bg = DISC[m["disc"]][1], WOOD[m["wood"]][1]
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
    final = wood_texture(m["wood"], W) or Image.new("RGB", (W, W), bg)   # echte Holzmaserung statt Flächenfarbe
    final.paste(art, (0, 0), mask)
    final.save(os.path.join(d, f"final_{key}.png"), compress_level=3)              # privat, nur nach Zahlung nutzbar (Stufe 3: schneller und kleiner als Standard)
    preview_img(final).save(os.path.join(d, f"preview_{key}.png"), compress_level=3)   # Vorschau ohne Wasserzeichen

def write_instructions(ref, m, runs, pal, px, py, rp, key, n_lines):
    """Anleitung mit allen Einstellungen und der kompletten Pinfolge (für die Fertigung)."""
    n, disc_cm = m["pins"], round(ring_cm(m))
    multi = len(pal) > 1
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
Stil der Berechnung: {style_name(key)}

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
    n, disc_cm, multi = m["pins"], round(ring_cm(m)), len(pal) > 1
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
    pdf.cell(0, 6, f"Fertigungsanleitung  |  Stil: {style_name(key)}  |  erstellt am {datetime.now():%d.%m.%Y %H:%M}",
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
    if ref in CANCEL:
        update(ref, status="cancelled")
        return
    if not WARM.wait(180):                           # erst rechnen, wenn die Vorbereitung durch ist
        app.logger.warning("Vorbereitung dauert > 180 s, rechne trotzdem (%s)", ref)
    if ref in CANCEL:
        update(ref, status="cancelled")
        return
    app.logger.warning("Berechnung startet (%s, Farbe %s)", ref[:6], (load(ref) or {}).get("color"))
    try:
        update(ref, status="running", beat=time.time(), progress=0, ready=[], prog={}, live={})
        meta = load(ref)
        if meta["color"] == MULTI:                   # Fadenfarben einmal für alle drei Varianten wählen
            update(ref, palette=pick_palette(os.path.join(odir(ref), "input.png"), DISC[meta["disc"]][1]))
            meta = load(ref)

        def one(key):                                # alle drei Varianten laufen gleichzeitig, jede mit Live-Bild
            try:
                (compute_sw if meta["color"] == SW else compute_string_art)(ref, meta, key)
                update_variant(ref, key, pct=100, ready=True)
            except Cancelled:
                pass
            except Exception:
                app.logger.exception("Variante %s fehlgeschlagen (%s)", key, ref)

        with ThreadPoolExecutor(max_workers=len(VARIANTS)) as ex:
            list(ex.map(one, list(VARIANTS)))
        if ref in CANCEL:
            update(ref, status="cancelled")
            LIVE_SEQ.pop(ref, None)
            return
        if not load(ref).get("ready"):
            raise RuntimeError("keine Variante berechnet")
        update(ref, status="done", progress=100, sugg="running")
        app.logger.warning("Varianten fertig (%s)", ref[:6])
    except Exception:
        app.logger.exception("Berechnung fehlgeschlagen (%s)", ref)
        update(ref, status="error", message="Die Berechnung ist fehlgeschlagen.")
        return
    sugg_pool.submit(run_suggestions, ref, meta)

def run_suggestions(ref, meta):
    """Die drei Vorschläge, einer nach dem anderen (Kunde kann schon kaufen)."""
    for key in SUGGEST:
        if ref in CANCEL:
            break
        try:
            compute_sw(ref, meta, key)
            update_variant(ref, key, pct=100, ready=True)
        except Cancelled:
            break
        except Exception:
            app.logger.exception("Vorschlag %s fehlgeschlagen (%s)", key, ref)
    update(ref, sugg="done")


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


# ---------- Gutscheine: Seiten und PDF ----------
def voucher_pdf(v):
    """Gutschein zum Ausdrucken (A5 quer)."""
    pdf = FPDF(orientation="L", format="A5")
    pdf.set_auto_page_break(False)
    pdf.add_page()
    pdf.set_fill_color(11, 11, 11)
    pdf.rect(0, 0, 210, 148, style="F")
    pdf.set_draw_color(212, 175, 55)
    pdf.set_line_width(0.8)
    pdf.ellipse(140, 24, 100, 100)
    pdf.set_text_color(212, 175, 55)
    pdf.set_font("Helvetica", "B", 26)
    pdf.set_xy(14, 18)
    pdf.cell(0, 12, "FADED ART")
    pdf.set_font("Helvetica", "", 13)
    pdf.set_text_color(240, 240, 240)
    pdf.set_xy(14, 34)
    pdf.cell(0, 8, "Gutschein für ein String-Art-Bild aus deinem Foto")
    if v.get("name"):
        pdf.set_xy(14, 46)
        pdf.cell(0, 8, f"Für: {v['name']}")
    pdf.set_font("Helvetica", "B", 40)
    pdf.set_text_color(212, 175, 55)
    pdf.set_xy(14, 60)
    pdf.cell(0, 18, euro(v["cents"]).replace("€", "EUR"))   # Standardschrift kennt kein €-Zeichen
    pdf.set_font("Courier", "B", 20)
    pdf.set_text_color(255, 255, 255)
    pdf.set_xy(14, 86)
    pdf.cell(0, 10, v["code"])
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_text_color(190, 190, 190)
    pdf.set_xy(14, 102)
    pdf.multi_cell(120, 5, f"Einlösen: Foto hochladen auf faded-art, Bild gestalten, Code im Warenkorb eingeben. "
                   f"Gültig bis {voucher_valid_until(v['created'])}. {PREORDER_NOTE} Keine Barauszahlung.")
    return bytes(pdf.output())

def send_mail(to, subject, text, files=()):
    """E-Mail über Resend (RESEND_API_KEY) oder SMTP. files: [(Name, MIME-Typ, Bytes)]."""
    key = os.environ.get("RESEND_API_KEY", "").strip()
    if key:
        body = json.dumps({"from": os.environ.get("MAIL_FROM", "Faded Art <onboarding@resend.dev>"), "to": [to], "subject": subject,
                           "text": text, "attachments": [{"filename": n, "content": base64.b64encode(b).decode()} for n, _, b in files]})
        req = urllib.request.Request("https://api.resend.com/emails", data=body.encode(), method="POST",
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "faded-art"})
        try:
            urllib.request.urlopen(req, timeout=60).read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Resend {e.code}: {e.read().decode(errors='replace')[:300]}") from None
    elif os.environ.get("SMTP_HOST"):
        msg = EmailMessage()
        msg["Subject"], msg["To"], msg["From"] = subject, to, os.environ.get("SMTP_USER", to)
        msg.set_content(text)
        for name, kind, data in files:
            msg.add_attachment(data, maintype=kind.split("/")[0], subtype=kind.split("/")[1], filename=name)
        with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587")), timeout=30) as sm:
            sm.starttls()
            sm.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
            sm.send_message(msg)
    else:
        raise RuntimeError("kein E-Mail-Versand eingerichtet")

def voucher_mail(v):
    """Gutschein als PDF an den Käufer, Kopie an dich."""
    text = (f"Vielen Dank! Dein Faded-Art-Gutschein über {euro(v['cents'])}:\n\n    {v['code']}\n\n"
            f"Gültig bis {voucher_valid_until(v['created'])}. Im Anhang als PDF zum Ausdrucken und Verschenken.\n{PREORDER_NOTE}")
    for to in filter(None, [v.get("email"), os.environ.get("ORDER_MAIL_TO")]):
        try:
            send_mail(to, f"Dein Faded-Art-Gutschein {v['code']}", text, [(f"Gutschein-{v['code']}.pdf", "application/pdf", voucher_pdf(v))])
        except Exception:
            app.logger.exception("Gutschein-Mail an %s fehlgeschlagen", to)

@app.get("/gutschein")
def voucher_page():
    left = (VOUCHER_LIMIT - voucher_sold()) if VOUCHER_LIMIT else None
    return render_template("gutschein.html", values=[(c, voucher_pay_cents(c)) for c in VOUCHER_VALUES], left=left, note=PREORDER_NOTE,
                           years=VOUCHER_YEARS, sale=VOUCHER_SALE_PCT if voucher_sale_on() else 0, until=sale_until_de())

@app.post("/gutschein/kaufen")
def voucher_buy():
    d = request.get_json(silent=True) or {}
    cents, email, name = d.get("cents"), str(d.get("email", "")).strip(), str(d.get("name", "")).strip()[:60]
    if cents not in VOUCHER_VALUES:
        return jsonify(message="Bitte einen Betrag wählen"), 400
    if not re.match(r"^\S+@\S+\.\S+$", email) or d.get("agb") is not True:
        return jsonify(message="E-Mail oder AGB-Zustimmung fehlt"), 400
    if VOUCHER_LIMIT and voucher_sold() >= VOUCHER_LIMIT:
        return jsonify(message="Die Gutscheine sind leider ausverkauft"), 400
    if not stripe.api_key:
        return jsonify(message="Zahlung ist noch nicht eingerichtet (STRIPE_SECRET_KEY fehlt bei Render)"), 500
    try:
        s = stripe.checkout.Session.create(
            mode="payment", locale="de", customer_email=email,
            line_items=[{"quantity": 1, "price_data": {"currency": "eur", "unit_amount": voucher_pay_cents(cents),
                                                         "product_data": {"name": f"Faded-Art-Gutschein im Wert von {euro(cents)}" + (f" für {name}" if name else "")
                                                                          + (f" (Aktion −{VOUCHER_SALE_PCT} %)" if voucher_sale_on() else "")}}}],
            metadata={"gutschein_kauf": str(cents), "gutschein_bezahlt": str(voucher_pay_cents(cents)), "gutschein_name": name},
            success_url=request.host_url + "gutschein/fertig?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=request.host_url + "gutschein")
        return jsonify(url=s.url)
    except Exception as e:
        app.logger.exception("Stripe-Fehler (Gutschein)")
        return jsonify(message="Checkout konnte nicht gestartet werden" +
                       (": " + str(getattr(e, "user_message", None) or e)[:300] if stripe.api_key.startswith(("sk_test", "rk_test")) else "")), 500

@app.get("/gutschein/fertig")
def voucher_done():
    sid, v = request.args.get("session_id", ""), None
    if sid.startswith("cs_") and stripe.api_key:
        try:
            v = handle_checkout(stripe.checkout.Session.retrieve(sid))
        except Exception:
            app.logger.exception("Gutschein-Sitzung %s konnte nicht geprüft werden", sid)
    return render_template("gutschein_fertig.html", v=v, until=voucher_valid_until(v["created"]) if v else "", note=PREORDER_NOTE)

@app.get("/gutschein/<code>.pdf")
def voucher_download(code):
    v = voucher_load(code)
    if not v:
        abort(404)
    return send_file(__import__("io").BytesIO(voucher_pdf(v)), mimetype="application/pdf", as_attachment=True,
                     download_name=f"Gutschein-{v['code']}.pdf")

@app.post("/cart/gutschein")
def cart_voucher():
    body = request.get_json(silent=True) or {}
    code = str(body.get("code", "")).strip().upper()
    if body.get("creator_off"):
        session.pop("creator", None)
        return jsonify(ok=True)
    if not code:
        session.pop("voucher", None)
        return jsonify(ok=True)
    if not CODE_RE.match(code):                       # kein Gutschein-Format: Rabatt-/Creator-Code?
        if cc_get(code):
            session["creator"] = code
            return jsonify(ok=True)
        return jsonify(message="Dieser Code ist ungültig."), 400
    v = voucher_load(code)
    if not voucher_ok(v):
        return jsonify(message="Dieser Gutscheincode ist ungültig, abgelaufen oder schon aufgebraucht."), 400
    session["voucher"] = v["code"]
    return jsonify(ok=True, left=v["left"])


# ---------- Rabatt- und Creator-Codes ----------
# Codes verwaltest du unter /admin/creator?key=ADMIN_KEY. Jeder Code: Rabatt für den Kunden (%) und Provision für den Creator (%).
# Creator-Link: https://faded-art.onrender.com/?code=LENA10 merkt sich den Code automatisch für den Einkauf.
CC_FILE = os.path.join(DATA_DIR, "creator_codes.json")
CC_SALES = os.path.join(DATA_DIR, "creator_sales.jsonl")
CC_RE = re.compile(r"^[A-Z0-9_-]{3,20}$")
_cclock = threading.Lock()

def cc_all():
    try:
        with open(CC_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}

def cc_get(code):
    code = (code or "").strip().upper()
    c = cc_all().get(code)
    return dict(c, code=code) if c and c.get("active", True) else None

def cc_disc(code, total):
    c = cc_get(code)
    return round(total * c["pct"] / 100) if c else 0

def cc_record(sid, code, cents):
    """Verkauf für die Provisionsabrechnung merken (einmal je Zahlung)."""
    with _cclock:
        try:
            with open(CC_SALES) as f:
                if any(json.loads(l).get("sid") == sid for l in f if l.strip()):
                    return
        except OSError:
            pass
        with open(CC_SALES, "a") as f:
            f.write(json.dumps(dict(sid=sid, code=code, cents=cents, t=time.time())) + "\n")

@app.before_request
def _creator_link():
    code = request.args.get("code", "")
    if code and request.method == "GET" and cc_get(code):
        session["creator"] = code.strip().upper()

@app.route("/admin/creator", methods=["GET", "POST"])
def admin_creator():
    if not admin_ok():
        abort(404)
    msg = ""
    if request.method == "POST":
        f = request.form
        code = f.get("code", "").strip().upper()
        with _cclock:
            cs = cc_all()
            if f.get("off") and code in cs:
                cs[code]["active"] = not cs[code].get("active", True); msg = f"{code} umgeschaltet"
            elif CC_RE.match(code):
                try:
                    cs[code] = dict(name=f.get("name", "").strip()[:60], pct=max(0, min(50, int(f.get("pct", 10)))),
                                    prov=max(0, min(50, int(f.get("prov", 10)))), active=True)
                    msg = f"{code} gespeichert"
                except ValueError:
                    msg = "Zahlen prüfen"
            else:
                msg = "Code: 3–20 Zeichen, nur A–Z, 0–9, - und _"
            with open(CC_FILE + ".tmp", "w") as fh:
                json.dump(cs, fh)
            os.replace(CC_FILE + ".tmp", CC_FILE)
    stats = {}
    try:
        with open(CC_SALES) as fh:
            for l in fh:
                if l.strip():
                    r = json.loads(l); st = stats.setdefault(r["code"], [0, 0]); st[0] += 1; st[1] += r["cents"]
    except OSError:
        pass
    rows = [dict(code=k, **v, n=stats.get(k, [0, 0])[0], umsatz=stats.get(k, [0, 0])[1],
                 provision=round(stats.get(k, [0, 0])[1] * v["prov"] / 100)) for k, v in sorted(cc_all().items())]
    return render_template_string("""<!doctype html><meta name=viewport content="width=device-width,initial-scale=1"><title>Creator-Codes</title>
<body style="font-family:system-ui;background:#111;color:#eee;padding:1rem;max-width:760px"><h1>Creator- und Rabattcodes</h1>
{% if msg %}<p style="color:#e6ca65">{{ msg }}</p>{% endif %}
<form method=post style="display:grid;gap:.4rem;grid-template-columns:1fr 1fr;margin-bottom:1.5rem">
<input name=code placeholder="CODE z.B. LENA10" required><input name=name placeholder="Creator / Zweck">
<label>Rabatt Kunde % <input name=pct type=number value=10 min=0 max=50></label><label>Provision Creator % <input name=prov type=number value=10 min=0 max=50></label>
<button style="grid-column:1/3">Code anlegen / ändern</button></form>
<table style="width:100%;border-collapse:collapse">
<tr style="text-align:left"><th>Code</th><th>Creator</th><th>Rabatt</th><th>Prov.</th><th>Verkäufe</th><th>Umsatz</th><th>Provision</th><th></th></tr>
{% for r in rows %}<tr style="border-top:1px solid #333;{{ '' if r.active else 'opacity:.4' }}"><td>{{ r.code }}</td><td>{{ r.name }}</td><td>{{ r.pct }} %</td><td>{{ r.prov }} %</td>
<td>{{ r.n }}</td><td>{{ r.umsatz|euro }}</td><td><b>{{ r.provision|euro }}</b></td>
<td><form method=post><input type=hidden name=code value="{{ r.code }}"><button name=off value=1>{{ 'deaktivieren' if r.active else 'aktivieren' }}</button></form></td></tr>
{% else %}<tr><td colspan=8>Noch keine Codes.</td></tr>{% endfor %}</table>
<p style="color:#999">Creator-Link: {{ host }}?code=CODE (Code wird beim Besuch gemerkt). Umsatz = bezahlter Bildpreis ohne Versand, nach Rabatt.</p>""",
        rows=rows, msg=msg, host=request.host_url)

# ---------- Maschine: holt bezahlte Aufträge (nur mit ADMIN_KEY) ----------
def machine_job(ref):
    """Alles, was die Spannmaschine braucht: Nagelzahl, Kreisdurchmesser und die Abschnitte [(Farbe, Pins)] in Spannreihenfolge."""
    m = load(ref)
    p = os.path.join(odir(ref), "pinfolge.txt")
    if not m or not os.path.exists(p):
        return None
    names = {thread_name(k): k for k in list(COLORS) + list(MULTI_COLORS)}
    runs = []
    for line in open(p).read().strip().splitlines():
        if ":" in line:
            name, pins_ = line.split(":", 1)
            k = names.get(name.strip(), name.strip())
        else:
            k, pins_ = m["color"], line
        runs.append({"color": k, "rgb": thread_rgb(k) if k in COLORS or k in MULTI_COLORS else None,
                     "pins": [int(x) for x in pins_.split(",") if x.strip()]})
    return {"ref": ref, "nr": ref[:6], "paid": m.get("paid", False), "title": item_title(m), "pins": m["pins"],
            "ring_cm": round(ring_cm(m), 1), "thick_mm": float(m["thick"]), "runs": runs,
            "lines": sum(len(r["pins"]) - 1 for r in runs)}

@app.get("/api/maschine")
def machine_list():
    """Bezahlte Aufträge für die Maschine: /api/maschine?key=ADMIN_KEY"""
    if not admin_ok():
        abort(404)
    out = []
    for ref in os.listdir(DATA_DIR):
        m = load(ref)
        if m and m.get("paid"):
            out.append({"nr": ref[:6], "ref": ref, "title": item_title(m), "done": bool(m.get("gespannt")),
                        "created": time.strftime("%d.%m.%Y %H:%M", time.localtime(m.get("created", 0)))})
    return jsonify(sorted(out, key=lambda r: r["created"]))

@app.get("/api/maschine/<nr>")
def machine_get(nr):
    """Ein Auftrag per Bestellnummer (die ersten 6 Zeichen reichen): /api/maschine/3fa2c1?key=ADMIN_KEY"""
    if not admin_ok() or not re.match(r"^[0-9a-f]{4,32}$", nr):
        abort(404)
    hits = [r for r in os.listdir(DATA_DIR) if r.startswith(nr) and (load(r) or {}).get("paid")]
    if len(hits) != 1:
        return jsonify(message="keine oder mehrere Bestellungen mit dieser Nummer"), 404
    return jsonify(machine_job(hits[0]))

@app.post("/api/maschine/<ref>/fertig")
def machine_done(ref):
    """Maschine meldet: Bild ist gespannt."""
    if not admin_ok() or not load(ref):
        abort(404)
    update(ref, gespannt=time.time())
    return jsonify(ok=True)


# ---------- Seiten ----------
@app.get("/")
def index():
    sizes = [(k, w, h, BASE_CENTS + round(max(0, w * h - BASE_AREA) * CENTS_PER_CM2)) for k, (w, h) in SIZES["quad"].items()]
    woods = [(k, v[0], v[2]) for k, v in WOOD.items()]
    room = {"sizes": SIZES, "base_cents": BASE_CENTS, "base_area": BASE_AREA, "cents_per_cm2": CENTS_PER_CM2,
            "round_area": ROUND_AREA, "round": sorted(ROUND_FORMATS), "wood": {k: [v[0], v[2]] for k, v in WOOD.items()}}
    return render_template("index.html", sizes=sizes, woods=woods, sw_eur=SW_EUR, pins_eur=PINS[360], note=PREORDER_NOTE, room=room)

@app.get("/configure")
def configure():
    warm_up_once()                          # Rechenkerne übersetzen, während der Kunde sein Foto auswählt
    cfg = {"sizes": SIZES, "pricing": {
        "test_quad_cents": TEST_QUAD_CENTS, "round_factor": ROUND_AREA, "base_cents": BASE_CENTS, "base_area": BASE_AREA, "cents_per_cm2": CENTS_PER_CM2,
        "wood": {k: v[2] for k, v in WOOD.items()}, "pins": PINS, "multi": MULTI_EUR, "sw": SW_EUR}}
    return render_template("configure.html", cfg=cfg)

@app.get("/ueber-uns")
def about():
    return render_template("about.html", note=PREORDER_NOTE)

@app.get("/success")
def success():
    session.pop("cart", None)
    session.pop("voucher", None)
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
        img = Image.open(f.stream)
        img = img.convert("RGBA" if img.mode in ("RGBA", "LA") or "transparency" in img.info else "RGB")   # Transparenz = freigestellt
    except Exception:
        return jsonify(message="Ungültiges Bild"), 400

    wood, color, thick = form.get("wood"), form.get("color"), form.get("thick")
    pins = clamp(form.get("pins"), 0, 10000)
    disc, pin = form.get("disc") or "weiss", form.get("pin") or "gold"   # fehlt ein Feld (alte Seitenversion), gelten Standardwerte
    if pin not in PINCOL:
        return jsonify(message="Ungültige Nagelfarbe"), 400
    if disc not in DISC:
        return jsonify(message="Ungültige Kreisfarbe"), 400
    if color in COLORS and color != SW and abs(lum(COLORS[color][1]) - lum(DISC[disc][1])) < MIN_CONTRAST:   # mehrfarbig: Farben passend zum Kreis gewählt
        return jsonify(message="Fadenfarbe und Kreisfarbe sind zu ähnlich"), 400
    fmt, size = form.get("format") or "quad", form.get("size") or "M"
    if fmt not in SIZES or size not in SIZES[fmt]:
        return jsonify(message="Ungültige Größe"), 400
    if wood not in WOOD or color not in SHOP_COLORS or thick not in THICKS or pins not in PINS:
        return jsonify(message="Ungültige Auswahl"), 400

    ref = uuid.uuid4().hex
    os.makedirs(odir(ref))
    img.save(os.path.join(odir(ref), "input.png"))
    update(ref, fmt=fmt, size=size, w=SIZES[fmt][size][0], h=SIZES[fmt][size][1], wood=wood,
           color=color, disc=disc, pin=pin, thick=thick, pins=pins, status="queued", paid=False, created=time.time())
    pool.submit(run_job, ref)
    return jsonify(order_ref=ref, job_id=ref, status="queued"), 202

@app.post("/cancel/<ref>")
def cancel(ref):
    """Vorab-Berechnung abbrechen (Kunde hat Einstellungen geändert). Bezahlte Bestellungen bleiben unberührt."""
    m = load(ref)
    if m and not m.get("paid") and m.get("status") in ("queued", "running", "done"):
        CANCEL.add(ref)
        if m.get("status") != "done":
            update(ref, status="cancelled")
    return jsonify(ok=True)

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
        if k in VARIANTS or k in SUGGEST:
            since = max(0, request.args.get("s" + k, 0, type=int))
            end = min(len(v["cseq"]), since + 800)           # cseq wird nach seq ergänzt: so passen beide immer zusammen
            out["lines"][k] = {"from": since, "pins": v["seq"][since:end], "cols": v["cseq"][since:end], "hex": v["hex"],
                               "alpha": v["alpha"], "lw": v["lw"], "n": v["n"], "bg": v.get("bg")}
    if m["status"] == "error":
        out["message"] = m.get("message", "Die Berechnung ist fehlgeschlagen.")
    ready = m.get("ready") or []
    nl = m.get("nlines") or {}
    out["variants"] = [{"key": k, "name": VARIANTS[k]["name"], "url": f"/preview/{ref}/{k}.png", "lines": nl.get(k)} for k in VARIANTS if k in ready]
    base = price_cents(m.get("orig") and dict(m, **m["orig"]) or m)
    out["suggestions"] = [{"key": k, "name": SUGGEST[k]["name"], "url": f"/preview/{ref}/{k}.png" if k in ready else None,
                           "pct": (m.get("prog") or {}).get(k, 0), "extra_cents": price_cents(with_suggestion(dict(m, **(m.get("orig") or {})), k)) - base,
                           "cents": price_cents(with_suggestion(dict(m, **(m.get("orig") or {})), k)), "lines": nl.get(k) if k in ready else None}
                          for k in SUGGEST]
    out["sugg"] = m.get("sugg", "waiting")
    out["cents"] = base
    if m["status"] == "done" and out["variants"]:
        out["preview_url"] = out["variants"][0]["url"]
    return jsonify(out)

@app.get("/live/<ref>/<key>.jpg")
def live_view(ref, key):
    p = os.path.join(odir(ref), f"live_{key}.jpg")
    if not load(ref) or (key not in VARIANTS and key not in SUGGEST) or not os.path.exists(p):
        abort(404)
    return send_file(p, mimetype="image/jpeg", max_age=0)

@app.get("/preview/<ref>/<key>.png")
def preview_variant(ref, key):
    p = os.path.join(odir(ref), f"preview_{key}.png")
    if not load(ref) or (key not in VARIANTS and key not in SUGGEST) or not os.path.exists(p):
        abort(404)
    return send_file(p, mimetype="image/png", max_age=0)

@app.get("/thumb/<ref>/<key>.jpg")
def thumb_variant(ref, key):
    """Kleines Vorschaubild (Warenkorb): schnell geladen, einmal erzeugt."""
    if not load(ref) or (key not in VARIANTS and key not in SUGGEST):
        abort(404)
    src, dst = os.path.join(odir(ref), f"preview_{key}.png"), os.path.join(odir(ref), f"thumb_{key}.jpg")
    if not os.path.exists(src):
        abort(404)
    if not os.path.exists(dst):
        im = Image.open(src).convert("RGB")
        im.thumbnail((360, 360))
        im.save(dst, quality=85)
    return send_file(dst, mimetype="image/jpeg", max_age=3600)

@app.get("/zoom/<ref>/<key>.png")
def zoom_variant(ref, key):
    """Fertiges Bild in voller Rechenauflösung (1000 px) zum Heranzoomen: einzelne Fäden sichtbar."""
    p = os.path.join(odir(ref), f"final_{key}.png")
    if not load(ref) or (key not in VARIANTS and key not in SUGGEST) or not os.path.exists(p):
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
    with _meta_lock:                         # Vorschlag gewählt: Bestellung übernimmt dessen Einstellungen (Preis passt sich an)
        m = load(ref)
        orig = m.get("orig") or {k: m[k] for k in ("color", "disc", "wood", "pins")}
        m.update(orig)
        if key in SUGGEST:
            m["orig"] = orig
            m.update(SUGGEST[key]["set"])
        else:
            m.pop("orig", None)
        m["variant"] = key
        _write(ref, m)

def thread_label(m):
    if m["color"] == MULTI:                  # Farben der gewählten Variante (oder alle vorgeschlagenen, solange keine gewählt ist)
        cols = (m.get("used") or {}).get(m.get("variant")) or m.get("palette")
        return "Mehrfarbig" + (f" ({', '.join(thread_name(c) for c in cols)})" if cols else "")
    return COLORS[m["color"]][0]

def item_title(m):
    return (f"String Art {m.get('size', '')} ({board_label(m)}), {WOOD[m['wood']][0]}, "
            f"{thread_label(m)} auf {DISC[m['disc']][0]}, {m['pins']} Pins ({PINCOL[m.get('pin', 'gold')][0]})"
            + (f", Stil {style_name(m['variant'])}" if m.get("variant") in VARIANTS or m.get("variant") in SUGGEST else ""))

def cart_items():
    items, keep = [], []
    for ref in session.get("cart", []):
        m = load(ref)
        if m and m["status"] == "done" and not m.get("paid"):   # schon bezahlte Bilder fliegen raus
            keep.append(ref)
            items.append({"ref": ref, "title": item_title(m), "cents": price_cents(m), "variant": m.get("variant", "a"),
                          "lines": (m.get("nlines") or {}).get(m.get("variant", "a"))})
    session["cart"] = keep
    return items

# ---------- Versand (europaweit). Preise in Cent je Größenklasse S/M/L/XL, nach längster Seite ----------
EU = ["AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "GR", "HU", "IE", "IT", "LV", "LT", "LU",
      "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE"]
SHIP = {   # Zone: (Anzeigename, Länder, Preise S/M/L/XL). Mit Umgebungsvariable VERSAND_JSON überschreibbar.
    "de": ("Deutschland", ["DE"], (790, 990, 1490, 3990)),
    "eu": ("EU-Ausland", EU, (1690, 1990, 2990, 6990)),
    "ch": ("Schweiz / Liechtenstein (zzgl. Zoll und Einfuhrsteuer)", ["CH", "LI"], (2490, 2990, 3990, 8990)),
}
if os.environ.get("VERSAND_JSON"):
    SHIP.update({k: tuple(v) for k, v in json.loads(os.environ["VERSAND_JSON"]).items()})
SHIP_EXTRA = 500   # je weiteres Bild im selben Paket

def ship_class(m):
    side = max(m.get("w", 50), m.get("h", 50))
    return 0 if side <= 40 else 1 if side <= 60 else 2 if side <= 80 else 3

def ship_cents(refs, zone):
    ms = [load(r) for r in refs]
    ms = [m for m in ms if m]
    if not ms or zone not in SHIP:
        return 0
    rates = SHIP[zone][2]
    return max(rates[ship_class(m)] for m in ms) + SHIP_EXTRA * (len(ms) - 1)

@app.template_filter("euro")
def euro(cents):
    return f"{cents / 100:.2f}".replace(".", ",") + " €"

def asset_v(name):
    """Änderungszeit der Datei als Versionsnummer: erzwingt, dass der Browser neue CSS/JS-Dateien lädt."""
    try:
        return int(os.path.getmtime(os.path.join(app.static_folder, name)))
    except OSError:
        return 0

SHOP = {   # Anbieterdaten für Impressum, AGB, Datenschutz. Fehlende Angaben per Umgebungsvariable bei Render nachtragen.
    "name": os.environ.get("SHOP_NAME", "Tobias Appelt"),
    "strasse": os.environ.get("SHOP_STRASSE", "Olympiastraße 12b"),
    "ort": os.environ.get("SHOP_ORT", "85622 Feldkirchen"),
    "email": os.environ.get("SHOP_EMAIL", ""),
    "telefon": os.environ.get("SHOP_TELEFON", ""),
    "ust": os.environ.get("SHOP_UST", ""),          # "klein" = Kleinunternehmer nach § 19 UStG, sonst die USt-IdNr. (DE...)
    "lieferzeit": os.environ.get("SHOP_LIEFERZEIT", ""),   # Freitext, z. B. "bis zu zwei Monate"
}

@app.context_processor
def inject_cart():
    return {"cart_count": len(session.get("cart", [])), "asset_v": asset_v, "shop": SHOP}

@app.errorhandler(413)
def too_big(e):
    return jsonify(message="Das Bild ist zu groß (maximal 16 MB)"), 413

@app.errorhandler(400)
def bad_request(e):
    return jsonify(message="Ungültige Anfrage. Bitte Seite neu laden und erneut versuchen."), 400

@app.get("/warenkorb")
def cart():
    items = cart_items()
    total = sum(i["cents"] for i in items)
    v = voucher_load(session.get("voucher"))
    if not voucher_ok(v):
        v = None
        session.pop("voucher", None)
    cc = cc_get(session.get("creator"))
    cdisc = cc_disc(cc["code"], total) if cc else 0
    total -= cdisc
    disc = min(v["left"], total) if v else 0
    refs = [i["ref"] for i in items]
    ship = {z: {"name": SHIP[z][0], "cents": ship_cents(refs, z)} for z in SHIP}
    return render_template("cart.html", items=items, total=total + cdisc, cc=cc, cdisc=cdisc, voucher=v, disc=disc, pay=total - disc, note=PREORDER_NOTE, ship=ship)

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
    zone = data.get("zone", "de")
    if zone not in SHIP:
        return jsonify(message="Bitte ein Lieferland wählen"), 400
    total = sum(i["cents"] for i in items)
    cc = cc_get(session.get("creator"))
    cdisc = cc_disc(cc["code"], total) if cc else 0
    if cdisc:                                            # Creator-Rabatt anteilig auf die Bilder
        rest = cdisc
        for n_, i in enumerate(items):
            cut = rest if n_ == len(items) - 1 else min(rest, round(cdisc * i["cents"] / total))
            i["cents"] -= cut; rest -= cut
            i["title"] += f" (Code {cc['code']} −{cc['pct']} %)"
        total -= cdisc
    ship = ship_cents([i["ref"] for i in items], zone)
    v = voucher_load(session.get("voucher"))
    disc = min(v["left"], total) if voucher_ok(v) else 0   # Gutschein gilt für die Bilder, Versand wird immer bezahlt (so kommt auch die Lieferadresse)
    if disc:                                             # Teilbetrag: Rabatt anteilig auf die Positionen verteilen
        rest = disc
        for n_, i in enumerate(items):
            cut = rest if n_ == len(items) - 1 else min(rest, round(disc * i["cents"] / total))
            cut = min(cut, i["cents"])
            i["cents"] -= cut
            i["title"] += f" (Gutschein {v['code']} abgezogen)" if cut else ""
            rest -= cut
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
                "product_data": {"name": i["title"]}}} for i in items if i["cents"] > 0] + [{"quantity": 1, "price_data": {
                "currency": "eur", "unit_amount": ship,
                "product_data": {"name": f"Versand {SHIP[zone][0]}" + (" (Bilder per Gutschein bezahlt)" if all(i["cents"] == 0 for i in items) else "")}}}],
            shipping_address_collection={"allowed_countries": SHIP[zone][1]},   # Adresse muss zur gewählten Versandzone passen
            metadata={"order_refs": ",".join(i["ref"] for i in items), "versand": zone, "versand_cents": str(ship),
                      **({"creator": cc["code"], "creator_cents": str(total)} if cc else {}),
                      **({"gutschein": v["code"], "gutschein_cents": str(disc)} if disc else {})},
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
    """Schickt dir bei jeder bezahlten Bestellung Anleitung und Bild per E-Mail (sichert die Daten, falls der Server-Speicher gelöscht wird).
    Mit RESEND_API_KEY über die Resend-API (HTTPS, funktioniert auch auf Render Free), sonst über SMTP (blockiert Render Free)."""
    try:
        to = os.environ.get("ORDER_MAIL_TO")
        key = os.environ.get("RESEND_API_KEY", "").strip()
        if not to or not (key or os.environ.get("SMTP_HOST")):
            app.logger.warning("Bestellung %s bezahlt. ORDER_MAIL_TO bzw. RESEND_API_KEY/SMTP_HOST fehlen, keine E-Mail. Daten liegen in %s", ref, odir(ref))
            return
        m = load(ref)
        subject = f"Neue Bestellung: {item_title(m)}"
        text = (f"Bezahlt: {price_cents(m) / 100:.2f} EUR\nKunden-E-Mail: {m.get('email')}\nBestellnummer: {ref}\n"
                "Im Anhang: Anleitung als PDF (Maße, Einstellungen, Pinfolge zum Abhaken), das Bild und die Anleitung als Text.\n"
                "Die Lieferadresse steht im Stripe-Dashboard.")
        files = []
        for name, kind in (("anleitung.pdf", "application/pdf"), ("final.png", "image/png"), ("anleitung.txt", "text/plain")):
            with open(os.path.join(odir(ref), name), "rb") as f:
                files.append((name, kind, f.read()))
        if key:
            body = json.dumps({"from": os.environ.get("MAIL_FROM", "Faded Art <onboarding@resend.dev>"), "to": [to], "subject": subject,
                               "text": text, "attachments": [{"filename": n, "content": base64.b64encode(b).decode()} for n, _, b in files]})
            req = urllib.request.Request("https://api.resend.com/emails", data=body.encode(), method="POST",
                                         headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "faded-art"})
            try:
                urllib.request.urlopen(req, timeout=60).read()
            except urllib.error.HTTPError as e:
                raise RuntimeError(f"Resend {e.code}: {e.read().decode(errors='replace')[:300]}") from None
        else:
            msg = EmailMessage()
            msg["Subject"], msg["To"], msg["From"] = subject, to, os.environ.get("SMTP_USER", to)
            msg.set_content(text)
            for name, kind, data in files:
                msg.add_attachment(data, maintype=kind.split("/")[0], subtype=kind.split("/")[1], filename=name)
            with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587")), timeout=30) as s:
                s.starttls()
                s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
                s.send_message(msg)
        app.logger.warning("Bestell-E-Mail für %s an %s gesendet", ref, to)
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
    email0 = s["customer_email"] if "customer_email" in s else None
    if "gutschein_kauf" in meta:                 # Gutschein gekauft
        return voucher_create(s["id"], int(meta["gutschein_kauf"]), email0,
                              meta["gutschein_name"] if "gutschein_name" in meta else "",
                              int(meta["gutschein_bezahlt"]) if "gutschein_bezahlt" in meta else None)
    if "gutschein" in meta:                      # Bestellung mit Gutschein (Teilbetrag): Guthaben abziehen
        voucher_use(meta["gutschein"], int(meta["gutschein_cents"]), s["id"])
    if "creator" in meta:                        # Creator-Provision: bezahlter Bildpreis nach Rabatt (vor Gutschein)
        cc_record(s["id"], meta["creator"], int(meta["creator_cents"]))
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

