import hmac, json, math, os, re, shutil, smtplib, threading, time, uuid
from email.message import EmailMessage
from datetime import datetime
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import stripe
from flask import Flask, abort, jsonify, render_template, render_template_string, request, send_file, session
from werkzeug.middleware.proxy_fix import ProxyFix
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

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

IMAGE_SIZE = 1000
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
LINES = {240: 8000, 360: 10000}   # Anzahl Fäden je Pin-Zahl
LENPOW, BUDGET, GAMMA0, TONE = 0.25, 3.0, 1.0, 1.0
TONE_LIGHT, GAMMA_LIGHT = 1.0, 1.0   # helle Fäden auf dunklem Kreis: Fäden addieren sich optisch, daher sparsamer       # Gewicht der Sehnenlänge, Faden-Budget für die Tonwerte
LOCAL, LC_R = 0.0, 40         # Stärke und Radius des lokalen Kontrasts
VARIANTS = {  # drei Stile, die bei jeder Berechnung erzeugt werden; der Kunde wählt seinen Favoriten
    "a": dict(name="Ausgewogen", local=0.0, tone=1.0, tone_light=1.0, line_w=3.5),
    "b": dict(name="Kontrastreich", local=0.6, tone=1.4, tone_light=1.3, line_w=3.5),
    "c": dict(name="Weich", local=0.0, tone=0.8, tone_light=0.8, line_w=3.0),
}
LINE_W = 3.5                      # Fadenbreite in Pixeln (bei 1000 px Bildbreite)
AUTOSTOP, MIN_GAP = True, 25        # Auto-Stopp; Mindestabstand in Nägeln (bei 360 Pins)
TOPK = 1                          # 1 = immer die beste Sehne
CELL, SAMPLES = 2, 160            # Rastergröße der Bewertung, Abtastpunkte je Sehne
MIN_CONTRAST = 60

def lum(rgb):
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
PINS = {240: 0, 360: 70}                       # Pins -> Aufpreis in €
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

def update_variant(ref, key, pct=None, live=None, ready=False):
    """Fortschritt, Live-Bildnummer und Fertig-Meldung EINER Variante (drei laufen gleichzeitig)."""
    with _meta_lock:
        m = load(ref) or {}
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
    return BASE_CENTS + round(extra) + (WOOD[m["wood"]][2] + PINS[m["pins"]]) * 100


# ---------- String-Art-Berechnung (dein Algorithmus, pro Bestellung isoliert) ----------
def preview_img(img, size=800):
    """Vorschau in 800 px (das gekaufte Bild und die Pinfolge haben volle Auflösung)."""
    return img.convert("RGB").resize((size, size), Image.Resampling.LANCZOS)

def compute_string_art(ref, m, key="a", pbase=0.0, pscale=1.0):
    """Gierige String-Art: pro Schritt wird die Sehne gewählt, die dem Bild am meisten fehlt.
    Alles vektorisiert (numpy), Rendering mit echter Deckkraft-Überlagerung in doppelter Auflösung."""
    var = VARIANTS[key]                        # Stil: Kontrast, Tonwerte, Fadenbreite
    LW = var["line_w"]
    d, n = odir(ref), m["pins"]
    thread, disc = COLORS[m["color"]][1], DISC[m["disc"]][1]
    bg = WOOD[m["wood"]][1]
    W, S = 1000, 2000
    # Maßstab: derselbe Faden wirkt auf einem größeren Bild feiner, auf einem kleineren kräftiger
    disc_cm = max(10.0, ring_cm(m))
    t_mm = float(m["thick"])
    a = min(0.4, max(0.02, E_REF * (t_mm / 0.20) * (D0_CM / disc_cm) / LW))     # Deckkraft pro Faden
    n_lines = int(LINES[n] * min(1.6, max(1.0, (0.20 / t_mm) * (disc_cm / D0_CM))))   # feinerer Faden braucht mehr Linien

    # 1) Bild vorbereiten: Kontrast, Schärfe, dann "gewünschte Fadendichte" c (0..1)
    img = Image.open(os.path.join(d, "input.png")).convert("L").resize((W, W), Image.Resampling.LANCZOS)
    img = ImageOps.autocontrast(img, cutoff=1.5).filter(ImageFilter.UnsharpMask(radius=3, percent=140, threshold=2))
    nrm = np.asarray(img, dtype=np.float32) / 255.0
    if var["local"]:                                            # lokaler Kontrast: hebt Streifen, Augen, Ohren hervor
        blur = np.asarray(img.filter(ImageFilter.GaussianBlur(LC_R)), dtype=np.float32) / 255.0
        nrm = np.clip(nrm + var["local"] * (nrm - blur), 0, 1)
    dark_thread = lum(thread) < lum(disc)
    c = 1.0 - nrm if dark_thread else nrm    # heller Faden auf dunklem Grund: helle Stellen = Faden
    c = 1.0 - (1.0 - c) ** (var["tone"] if dark_thread else var["tone_light"])                          # TONE > 1: Mitteltöne bekommen mehr Fäden, das Bild wirkt plastischer
    yy, xx = np.mgrid[:W, :W]
    c = np.where((xx - W / 2) ** 2 + (yy - W / 2) ** 2 <= (W / 2 - 2) ** 2, c, 0).astype(np.float32)

    # 2) Pin-Positionen und Zielwerte im groben Raster (4x4 px Zellen): Fäden werden nach dem
    #    lokalen Durchschnittston bewertet, so wie das Auge das Bild aus Distanz sieht.
    rp = W / 2 - 9
    ang = 2 * np.pi * np.arange(n) / n
    px, py = W / 2 + rp * np.cos(ang), W / 2 + rp * np.sin(ang)
    q = CELL
    Wc = W // q
    logk = np.log1p(-a)
    Tl = c.reshape(Wc, q, Wc, q).mean(axis=(1, 3)).astype(np.float32)
    budget = BUDGET * n_lines * 0.65 * W * LW                   # verfügbare Faden-Pixel
    need = lambda t: float(-np.log1p(-np.minimum(t, .92)).sum() / -logk * q * q)   # benötigte Faden-Pixel
    g = GAMMA0 if dark_thread else GAMMA_LIGHT
    while need(Tl ** g) > budget and g < 4:             # Gamma: Lichter bleiben, Mitteltöne werden lichter -> mehr Kontrast
        g += 0.05
    T = np.minimum(Tl ** g, 0.92).ravel()

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

    # 3) Gierige Auswahl der Sehnen (alle Kandidaten pro Schritt gleichzeitig)
    cnt_c, cov, R = np.zeros(Wc * Wc, np.float32), np.zeros(Wc * Wc, np.float32), T.copy()
    s = np.linspace(0, 1, SAMPLES)
    skip, allp = max(8, round(MIN_GAP * n / 360) - 1), np.arange(n)     # Mindestabstand der Nägel je Sehne
    KAPPA = -logk * LW / (q * q)
    used = np.zeros((n, n), bool)                                         # jede Sehne nur einmal
    seq, cur = [0], 0
    for r in [r for r, v in list(LIVE_SEQ.items()) if time.time() - v.get("t", 0) > 3600]:
        LIVE_SEQ.pop(r, None)
    LIVE_SEQ.setdefault(ref, {"t": time.time()})[key] = {"seq": seq, "alpha": round(lalpha / 255, 4), "lw": lwid, "n": n}
    rng = np.random.default_rng(1)
    for _ in range(n_lines):
        gap = np.abs(allp - cur)
        cand = allp[(np.minimum(gap, n - gap) > skip) & ~used[cur]]
        if len(cand) == 0:
            break
        X = px[cur] + (px[cand, None] - px[cur]) * s
        Y = py[cur] + (py[cand, None] - py[cur]) * s
        ix = (np.rint(Y).astype(np.int32) // q) * Wc + np.rint(X).astype(np.int32) // q
        dc = (1 - cov[ix]) * KAPPA                            # Deckungszuwachs pro Pixel Fadenweg
        gain = (2 * R[ix] * dc - dc * dc).sum(axis=1) * np.hypot(px[cand] - px[cur], py[cand] - py[cur])   # echte Fehlerverringerung
        j = int(gain.argmax())
        if gain[j] <= 0:
            break                                          # Auto-Stopp: kein Faden verbessert das Bild mehr
        ex, ey = px[cand[j]] - px[cur], py[cand[j]] - py[cur]
        tt = np.linspace(0, 1, int(np.hypot(ex, ey)) + 1)
        cells = (np.rint(py[cur] + ey * tt).astype(np.int32) // q) * Wc + np.rint(px[cur] + ex * tt).astype(np.int32) // q
        starts = np.flatnonzero(np.r_[True, cells[1:] != cells[:-1]])      # Zellen einer Geraden sind lückenlos hintereinander
        u, k_ = cells[starts], np.diff(np.r_[starts, len(cells)])
        cov_new = 1 - np.exp(logk * (cnt_c[u] + k_) * LW / (q * q))
        cnt_c[u] += k_
        cov[u] = cov_new
        R[u] = T[u] - cov[u]
        used[cur, cand[j]] = used[cand[j], cur] = True
        p0 = (px[cur] * lk, py[cur] * lk)
        cur = int(cand[j])
        seq.append(cur)
        ld.line([p0, (px[cur] * lk, py[cur] * lk)], fill=thread + (lalpha,), width=lwid)     # Faden live zeichnen
        if len(seq) % 100 == 0:                              # Live-Bild, Fortschritt und Lebenszeichen
            save_live(min(99, int(100 * len(seq) / n_lines)))

    # 4) Rendern in 2000 px: jeder Faden ~1 px (bei 1000 px) breit, Deckkraft überlagert sich physikalisch
    k = S / W
    P = np.stack([px, py], 1) * k
    cnt = np.zeros(S * S, np.uint16)
    lw = max(1, int(round(2 * LW)))
    for u, v in zip(seq[:-1], seq[1:]):
        dx, dy = P[v, 0] - P[u, 0], P[v, 1] - P[u, 1]
        horiz = abs(dx) >= abs(dy)
        t = np.linspace(0, 1, int(abs(dx if horiz else dy)) + 1)       # genau ein Punkt je Pixel der Hauptachse: keine Doppelzählung
        x = np.clip(np.rint(P[u, 0] + dx * t), 0, S - 2).astype(np.int64)
        y = np.clip(np.rint(P[u, 1] + dy * t), 0, S - 2).astype(np.int64)
        base = y * S + x                                      # Faden ist LW px breit (bei 1000 px), quer zur Richtung verbreitert
        idx = (base[:, None] + (np.arange(lw) - lw // 2)[None, :] * (S if horiz else 1)).ravel()
        cnt[idx[(idx >= 0) & (idx < S * S)]] += 1
    d0, t0 = np.asarray(disc, np.float32), np.asarray(thread, np.float32)
    rgb = np.empty((S, S, 3), np.uint8)                     # streifenweise berechnen: spart Arbeitsspeicher (Render Free hat nur 512 MB)
    for r0 in range(0, S, 250):
        cov_s = (1 - np.power(1 - a, cnt[r0 * S:(r0 + 250) * S].astype(np.float32))).reshape(-1, S, 1)
        rgb[r0:r0 + 250] = (d0 + (t0 - d0) * cov_s).astype(np.uint8)
    art = Image.fromarray(rgb, "RGB")
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
    final.save(os.path.join(d, f"final_{key}.png"))              # privat, nur nach Zahlung nutzbar
    preview_img(final).save(os.path.join(d, f"preview_{key}.png"))   # Vorschau ohne Wasserzeichen
    with open(os.path.join(d, f"pinfolge_{key}.txt"), "w") as f:
        f.write(",".join(map(str, seq)))
    write_instructions(ref, m, seq, px, py, rp, key)

def write_instructions(ref, m, seq, px, py, rp, key="a"):
    """Anleitung mit allen Einstellungen und der kompletten Pinfolge (für die Fertigung)."""
    n, disc_cm = m["pins"], round(ring_cm(m))
    sq = np.array(seq)
    meters = float(np.hypot(np.diff(px[sq]), np.diff(py[sq])).sum()) * (disc_cm / 2 / rp) / 100
    steps = "\n".join(f"Schritt {i + 1}: Pin {p}" for i, p in enumerate(seq))
    txt = f"""FADED ART - Pinfolge und Anleitung
Erstellt am: {datetime.now():%d.%m.%Y um %H:%M:%S}
Bestellnummer: {ref}
Stil der Berechnung: {VARIANTS[key]['name']}

=== PROJEKT-EINSTELLUNGEN ===
Anzahl Pins: {n}
Maximale Linien: {LINES[n]}
Verwendete Linien: {len(seq) - 1} (Auto-Stopp, sobald kein Faden das Bild mehr verbessert)
Fadendicke: {m['thick'].replace('.', ',')} mm (Maßstab: Bild {disc_cm} cm)
Fadenfarbe: {COLORS[m['color']][0]}
Farbe des Kreises: {DISC[m['disc']][0]}
Nagelfarbe: {PINCOL[m.get('pin', 'gold')][0]}
Untergrund: {WOOD[m['wood']][0]}, {board_label(m)}
Kreisdurchmesser (Pins): ca. {disc_cm} cm
Fadenlänge: ca. {meters:.2f} Meter

=== PINFOLGE ===
Gesamtschritte: {len(seq)}

{steps}

=== ANLEITUNG ===
1. Nagelkreis mit {n} gleichmäßig verteilten Nägeln setzen (Durchmesser ca. {disc_cm} cm)
2. Nägel von 0 bis {n - 1} nummerieren: Pin 0 sitzt rechts auf 3 Uhr, die Nummern steigen im Uhrzeigersinn
3. Faden an Pin 0 befestigen
4. Der Pinfolge oben Schritt für Schritt folgen: jeden Pin mit dem nächsten verbinden
5. Faden straff, aber nicht zu fest ziehen, damit sich das Brett nicht verzieht
6. Weitermachen, bis alle {len(seq) - 1} Verbindungen fertig sind

=== PINFOLGE ALS ZAHLENLISTE ===
{",".join(map(str, seq))}
"""
    with open(os.path.join(odir(ref), f"anleitung_{key}.txt"), "w", encoding="utf-8") as f:
        f.write(txt)

def run_job(ref):
    try:
        update(ref, status="running", beat=time.time(), progress=0, ready=[], prog={}, live={})
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
<a style="color:#e6ca65" href="/admin/{{ r.ref }}/anleitung.txt?key={{ key }}">Anleitung mit Pinfolge</a> |
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
    if not admin_ok() or name not in ("anleitung.txt", "final.png", "pinfolge.txt") or not load(ref) or not os.path.exists(os.path.join(odir(ref), name)):
        abort(404)
    return send_file(os.path.join(odir(ref), name), as_attachment=(name != "final.png"), max_age=0)


# ---------- Seiten ----------
@app.get("/")
def index():
    return render_template("index.html")

@app.get("/configure")
def configure():
    cfg = {"sizes": SIZES, "pricing": {
        "test_quad_cents": TEST_QUAD_CENTS, "round_factor": ROUND_AREA, "base_cents": BASE_CENTS, "base_area": BASE_AREA, "cents_per_cm2": CENTS_PER_CM2,
        "wood": {k: v[2] for k, v in WOOD.items()}, "pins": PINS}}
    return render_template("configure.html", cfg=cfg)

@app.get("/ueber-uns")
def about():
    return render_template("about.html")

@app.get("/success")
def success():
    session.pop("cart", None)
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
    if color in COLORS and abs(lum(COLORS[color][1]) - lum(DISC[disc][1])) < MIN_CONTRAST:
        return jsonify(message="Fadenfarbe und Kreisfarbe sind zu ähnlich"), 400
    fmt, size = form.get("format") or "quad", form.get("size") or "M"
    if fmt not in SIZES or size not in SIZES[fmt]:
        return jsonify(message="Ungültige Größe"), 400
    if wood not in WOOD or color not in COLORS or thick not in THICKS or pins not in PINS:
        return jsonify(message="Ungültige Auswahl"), 400

    ref = uuid.uuid4().hex
    os.makedirs(odir(ref))
    img.save(os.path.join(odir(ref), "input.png"))
    update(ref, fmt=fmt, size=size, w=SIZES[fmt][size][0], h=SIZES[fmt][size][1], wood=wood,
           color=color, disc=disc, pin=pin, thick=thick, pins=pins, status="queued", paid=False, created=time.time())
    pool.submit(run_job, ref)
    return jsonify(order_ref=ref, job_id=ref, status="queued"), 202

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
            out["lines"][k] = {"from": since, "pins": v["seq"][since:since + 800], "alpha": v["alpha"], "lw": v["lw"], "n": v["n"]}
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
                     (f"anleitung_{key}.txt", "anleitung.txt"), (f"preview_{key}.png", "preview.png")):
        shutil.copyfile(os.path.join(odir(ref), src), os.path.join(odir(ref), dst))
    update(ref, variant=key)

def item_title(m):
    return (f"String Art {m.get('size', '')} ({board_label(m)}), {WOOD[m['wood']][0]}, "
            f"{COLORS[m['color']][0]} auf {DISC[m['disc']][0]}, {m['pins']} Pins ({PINCOL[m.get('pin', 'gold')][0]})"
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
            payment_method_types=["card"], mode="payment", locale="de", customer_email=email,
            line_items=[{"quantity": 1, "price_data": {
                "currency": "eur", "unit_amount": i["cents"],   # Preise kommen nur vom Server
                "product_data": {"name": i["title"]}}} for i in items],
            shipping_address_collection={"allowed_countries": ["DE", "AT", "CH"]},
            metadata={"order_refs": ",".join(i["ref"] for i in items)},
            success_url=request.host_url + "success",
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
                        "Anleitung mit Pinfolge und Bild im Anhang. Die Lieferadresse steht im Stripe-Dashboard.")
        for name, kind in (("anleitung.txt", "text/plain"), ("final.png", "image/png")):
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
        s = ev["data"]["object"]  # Klammer-Zugriff statt .get(): läuft mit alten und neuen stripe-Versionen
        if s["payment_status"] == "paid":
            meta = s["metadata"] if "metadata" in s else {}
            refs = meta["order_refs"] if "order_refs" in meta else ""
            email = s["customer_email"] if "customer_email" in s else None
            for ref in refs.split(","):
                if load(ref):
                    update(ref, paid=True, stripe_session=s["id"], email=email)
                    threading.Thread(target=notify_owner, args=(ref,), daemon=True).start()
    return "", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

