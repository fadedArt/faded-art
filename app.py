import json, math, os, re, time, uuid
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import stripe
from flask import Flask, abort, jsonify, render_template, request, send_file, session
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from skimage.draw import line
from skimage.feature import canny

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
# SECRET_KEY unbedingt als Umgebungsvariable setzen (sonst gehen Warenkörbe bei Neustart verloren)
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(24).hex()
app.permanent_session_lifetime = timedelta(days=7)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
Image.MAX_IMAGE_PIXELS = 50_000_000

# Geheimnisse NUR über Umgebungsvariablen setzen, nie im Code
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
DATA_DIR = os.environ.get("DATA_DIR", "data")
os.makedirs(DATA_DIR, exist_ok=True)

IMAGE_SIZE = 1000
pool = ThreadPoolExecutor(max_workers=2)
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
THICK = {"0.15": 14, "0.20": 22, "0.30": 32}  # Fadendicke -> Alpha
PINS = {240: 0, 360: 70}                       # Pins -> Aufpreis in €
BASE_CENTS, BASE_AREA, CENTS_PER_CM2 = 50, 2500, 2.5  # 0,50 € ist noch der Testpreis!
# Feste Größen (Breite x Höhe in cm) je Format. Einzige Quelle: Frontend liest sie von hier.
SIZES = {
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

def update(ref, **kw):
    m = load(ref) or {}
    m.update(kw)
    p = os.path.join(odir(ref), "meta.json")
    with open(p + ".tmp", "w") as f:
        json.dump(m, f)
    os.replace(p + ".tmp", p)

def price_cents(m):
    extra = max(0, m["w"] * m["h"] - BASE_AREA) * CENTS_PER_CM2
    return BASE_CENTS + round(extra) + (WOOD[m["wood"]][2] + PINS[m["pins"]]) * 100


# ---------- String-Art-Berechnung (dein Algorithmus, pro Bestellung isoliert) ----------
def watermark(img):
    img = img.convert("RGB").resize((600, 600))
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    try:
        font = ImageFont.load_default(size=36)
    except TypeError:
        font = ImageFont.load_default()
    for y in range(-20, 620, 120):
        for x in range(-20, 620, 220):
            d.text((x, y), "VORSCHAU", fill=(128, 128, 128, 110), font=font)
    return Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")

def compute_string_art(ref, m):
    d, n = odir(ref), m["pins"]
    alpha, thread, bg = THICK[m["thick"]], COLORS[m["color"]][1], WOOD[m["wood"]][1]
    size = (IMAGE_SIZE, IMAGE_SIZE)

    img = Image.open(os.path.join(d, "input.png")).convert("L").resize(size, Image.Resampling.LANCZOS)
    img = ImageEnhance.Contrast(img).enhance(2.2).filter(ImageFilter.SHARPEN)
    arr = np.array(img, dtype=float)
    edges = canny(arr / 255.0, sigma=1.1)
    target = 255.0 - ((255 - arr) * 0.12 + edges.astype(float) * 255 * 0.88)
    r = IMAGE_SIZE // 2
    y, x = np.ogrid[:IMAGE_SIZE, :IMAGE_SIZE]
    target[(x - r) ** 2 + (y - r) ** 2 > r ** 2] = 255

    pins = [(int(r + (r - 1) * math.cos(2 * math.pi * i / n)),
             int(r + (r - 1) * math.sin(2 * math.pi * i / n))) for i in range(n)]
    seq, cur = [0], 0
    art = Image.new("RGBA", size, (255, 255, 255, 255))
    lines_img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(lines_img, "RGBA")
    skip = 18 if n > 300 else 12

    for _ in range(4200 if n > 300 else 3000):
        best, best_score, best_px = -1, -1, None
        for nxt in range(n):
            gap = abs(cur - nxt)
            if gap <= skip or gap >= n - skip:
                continue
            rr, cc = line(pins[cur][1], pins[cur][0], pins[nxt][1], pins[nxt][0])
            score = np.sum(255.0 - target[rr, cc])
            if score > best_score:
                best, best_score, best_px = nxt, score, (rr, cc)
        seq.append(best)
        rr, cc = best_px
        target[rr, cc] = np.clip(target[rr, cc] + 15, 0, 255)
        draw.line([pins[cur], pins[best]], fill=thread + (alpha,), width=1)
        cur = best

    pin_col = (212, 175, 55, 255) if m["color"] == "gold" else (210, 210, 210, 255)
    for px, py in pins:
        draw.ellipse([px - 3, py - 3, px + 3, py + 3], fill=pin_col, outline=(40, 40, 40, 255))

    art.paste(lines_img, (0, 0), lines_img)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).ellipse([0, 0, IMAGE_SIZE, IMAGE_SIZE], fill=255)
    final = Image.new("RGBA", size, bg + (255,))
    final.paste(art, (0, 0), mask)

    final.save(os.path.join(d, "final.png"))              # privat, nur nach Zahlung nutzbar
    watermark(final).save(os.path.join(d, "preview.png"))  # öffentlich, mit Wasserzeichen
    with open(os.path.join(d, "pinfolge.txt"), "w") as f:
        f.write(",".join(map(str, seq)))

def run_job(ref):
    try:
        compute_string_art(ref, load(ref))
        update(ref, status="done")
    except Exception:
        app.logger.exception("Berechnung fehlgeschlagen (%s)", ref)
        update(ref, status="error")


# ---------- Seiten ----------
@app.get("/")
def index():
    return render_template("index.html")

@app.get("/configure")
def configure():
    cfg = {"sizes": SIZES, "pricing": {
        "base_cents": BASE_CENTS, "base_area": BASE_AREA, "cents_per_cm2": CENTS_PER_CM2,
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
    fmt, size = form.get("format"), form.get("size")
    if fmt not in SIZES or size not in SIZES[fmt]:
        return jsonify(message="Ungültige Größe"), 400
    if wood not in WOOD or color not in COLORS or thick not in THICK or pins not in PINS:
        return jsonify(message="Ungültige Auswahl"), 400

    ref = uuid.uuid4().hex
    os.makedirs(odir(ref))
    img.save(os.path.join(odir(ref), "input.png"))
    update(ref, fmt=fmt, size=size, w=SIZES[fmt][size][0], h=SIZES[fmt][size][1], wood=wood,
           color=color, thick=thick, pins=pins, status="queued", paid=False, created=time.time())
    pool.submit(run_job, ref)
    return jsonify(order_ref=ref, job_id=ref, status="queued"), 202

@app.get("/status/<ref>")
def status(ref):
    m = load(ref)
    if not m:
        abort(404)
    out = {"status": m["status"], "order_ref": ref}
    if m["status"] == "done":
        out["preview_url"] = f"/preview/{ref}.png"
    return jsonify(out)

@app.get("/preview/<ref>.png")
def preview(ref):
    m = load(ref)
    if not m or m["status"] != "done":
        abort(404)
    return send_file(os.path.join(odir(ref), "preview.png"), mimetype="image/png", max_age=0)

MAX_CART = 10

def item_title(m):
    return (f"String Art {m.get('size', '')} ({m['w']}x{m['h']} cm), {WOOD[m['wood']][0]}, "
            f"{COLORS[m['color']][0]}, {m['pins']} Pins")

def cart_items():
    items, keep = [], []
    for ref in session.get("cart", []):
        m = load(ref)
        if m and m["status"] == "done":
            keep.append(ref)
            items.append({"ref": ref, "title": item_title(m), "cents": price_cents(m)})
    session["cart"] = keep
    return items

@app.template_filter("euro")
def euro(cents):
    return f"{cents / 100:.2f}".replace(".", ",") + " €"

@app.context_processor
def inject_cart():
    return {"cart_count": len(session.get("cart", []))}

@app.get("/warenkorb")
def cart():
    items = cart_items()
    return render_template("cart.html", items=items, total=sum(i["cents"] for i in items))

@app.post("/cart/add")
def cart_add():
    ref = (request.get_json(silent=True) or {}).get("order_ref")
    m = load(ref)
    if not m or m["status"] != "done":
        return jsonify(message="Bestellung nicht gefunden"), 400
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
        return jsonify(message="Zahlung ist noch nicht eingerichtet"), 500
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
    except Exception:
        app.logger.exception("Stripe-Fehler")
        return jsonify(message="Checkout konnte nicht gestartet werden"), 500

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
            # TODO: Bestell-E-Mail an dich senden (Pinfolge liegt in data/<ref>/pinfolge.txt)
    return "", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

