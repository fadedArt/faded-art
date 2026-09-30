import math
import os
import uuid

import numpy as np
import stripe
from flask import Flask, jsonify, render_template, request
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps
from skimage.draw import line

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024  # max. 15 MB Upload

# Stripe-Key NICHT im Code speichern, sondern als Umgebungsvariable setzen:
#   export STRIPE_SECRET_KEY="sk_test_..."
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")

UPLOAD_FOLDER = "static"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

CALC_SIZE = 500   # Auflösung für die Berechnung (schnell)
OUT_SIZE = 1000   # Auflösung für das fertige Bild
SCALE = OUT_SIZE / CALC_SIZE

COLORS_THREAD = {
    "Tiefschwarz": (12, 12, 12),
    "Reinweiß": (250, 250, 250),
    "Champagner Gold": (212, 175, 55),
    "Silber": (195, 195, 195),
}
COLORS_BOARD = {
    "Klassik Weiß matt": (250, 250, 250),
    "MDF Schwarz matt": (18, 18, 18),
    "Eiche Natur geölt": (168, 122, 82),
    "Nussbaum Premium": (59, 39, 22),
    "Schwarze Esche": (33, 33, 33),
    "Beton-Look": (110, 110, 110),
}


def luminance(rgb):
    r, g, b = rgb
    return 0.299 * r + 0.587 * g + 0.114 * b


def prepare_target(image_path, thread_is_lighter):
    """Bild -> quadratisch, kontrastreich -> Zielwerte 0..255 (hoch = hier Faden nötig)."""
    img = Image.open(image_path)
    img = ImageOps.exif_transpose(img).convert("L")
    img = ImageOps.fit(img, (CALC_SIZE, CALC_SIZE), Image.Resampling.LANCZOS)
    img = ImageOps.autocontrast(img, cutoff=1)
    img = ImageEnhance.Contrast(img).enhance(1.6)
    img = img.filter(ImageFilter.SMOOTH).filter(ImageFilter.SHARPEN)

    arr = np.array(img, dtype=np.float32)
    # Dunkler Faden: dunkle Bildstellen brauchen Faden.
    # Heller Faden (auf dunklem Brett): helle Bildstellen brauchen Faden.
    target = arr if thread_is_lighter else 255.0 - arr

    # Nur der Kreis zählt
    r = CALC_SIZE // 2
    y, x = np.ogrid[:CALC_SIZE, :CALC_SIZE]
    target[(x - r) ** 2 + (y - r) ** 2 > r ** 2] = 0
    return target


def compute_string_art(image_path, num_pins, thickness_option, thread_color_option,
                       wood_option, job_id):
    pins_count = int(num_pins)

    thickness = str(thickness_option)
    if "0.3" in thickness:
        alpha, num_lines = 0.125, 3000
    elif "0.2" in thickness:
        alpha, num_lines = 0.09, 3600
    else:
        alpha, num_lines = 0.06, 4200

    thread_rgb = np.array(COLORS_THREAD.get(thread_color_option, (12, 12, 12)), dtype=np.float32)
    board_rgb = np.array(COLORS_BOARD.get(wood_option, (250, 250, 250)), dtype=np.float32)
    thread_is_lighter = luminance(thread_rgb) > luminance(board_rgb)

    target = prepare_target(image_path, thread_is_lighter)

    # ---- Nägel auf dem Kreis ----
    r = CALC_SIZE // 2
    pins = []
    for i in range(pins_count):
        a = 2 * math.pi * i / pins_count
        pins.append((int(r + (r - 1) * math.cos(a)), int(r + (r - 1) * math.sin(a))))

    cache = {}

    def get_line(a, b):
        key = (a, b) if a < b else (b, a)
        if key not in cache:
            (x0, y0), (x1, y1) = pins[key[0]], pins[key[1]]
            cache[key] = line(y0, x0, y1, x1)
        return cache[key]

    skip = max(10, pins_count // 25)  # Mindestabstand zwischen zwei Nägeln
    decay = 255.0 * alpha * 1.6       # so viel "Bedarf" nimmt ein Faden weg

    # ---- Greedy-Suche: immer die Linie mit dem größten Nutzen ----
    sequence = [0]
    current = 0
    for _ in range(num_lines):
        best_pin, best_score = -1, 0.0
        for nxt in range(pins_count):
            d = abs(current - nxt)
            d = min(d, pins_count - d)
            if d <= skip:
                continue
            rr, cc = get_line(current, nxt)
            score = target[rr, cc].mean()
            if score > best_score:
                best_score, best_pin = score, nxt
        if best_pin < 0 or best_score < 2:  # nichts Sinnvolles mehr zu zeichnen
            break
        rr, cc = get_line(current, best_pin)
        target[rr, cc] = np.maximum(target[rr, cc] - decay, 0)
        sequence.append(best_pin)
        current = best_pin

    # ---- Rendern in hoher Auflösung ----
    pins_out = [(int(x * SCALE), int(y * SCALE)) for x, y in pins]
    counts = np.zeros((OUT_SIZE, OUT_SIZE), dtype=np.float32)

    def compose(cnt, size=None):
        coverage = 1.0 - (1.0 - alpha) ** cnt
        rgb = board_rgb + (thread_rgb - board_rgb) * coverage[..., None]
        im = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB")
        if size:
            im = im.resize((size, size), Image.Resampling.LANCZOS)
        return im

    frames = []
    step_every = max(1, len(sequence) // 40)
    for i in range(len(sequence) - 1):
        (x0, y0), (x1, y1) = pins_out[sequence[i]], pins_out[sequence[i + 1]]
        rr, cc = line(y0, x0, y1, x1)
        rr = np.clip(rr, 0, OUT_SIZE - 1)
        cc = np.clip(cc, 0, OUT_SIZE - 1)
        counts[rr, cc] += 1
        if i % step_every == 0 or i == len(sequence) - 2:
            frames.append(compose(counts, 500).convert("P", palette=Image.ADAPTIVE))

    final = compose(counts).convert("RGBA")

    # Nägel obendrauf
    pin_color = (212, 175, 55, 255) if "Gold" in str(thread_color_option) else (210, 210, 210, 255)
    draw = ImageDraw.Draw(final)
    for px, py in pins_out:
        draw.ellipse([px - 3, py - 3, px + 3, py + 3], fill=pin_color, outline=(40, 40, 40, 255))

    png_name = f"preview_{job_id}.png"
    gif_name = f"progress_{job_id}.gif"
    final.save(os.path.join(UPLOAD_FOLDER, png_name))
    if frames:
        frames[0].save(
            os.path.join(UPLOAD_FOLDER, gif_name),
            save_all=True,
            append_images=frames[1:],
            duration=140,
            loop=0,
        )

    # Nagelreihenfolge für die Produktion
    with open(os.path.join(UPLOAD_FOLDER, f"pinfolge_{job_id}.txt"), "w") as f:
        f.write(",".join(map(str, sequence)))

    return f"/static/{png_name}", f"/static/{gif_name}"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/configure")
def configure():
    return render_template("configure.html")


@app.route("/generate", methods=["POST"])
def generate():
    uploaded = request.files.get("image")
    if not uploaded or uploaded.filename == "":
        return jsonify({"status": "error", "message": "Bitte ein Bild hochladen."}), 400

    width_cm = request.form.get("width_cm", "50")
    height_cm = request.form.get("height_cm", "50")
    wood = request.form.get("wood")
    thread_color = request.form.get("thread_color")
    thread_thickness = request.form.get("thread_thickness")
    nail = request.form.get("nail")
    num_pins = 360 if "360" in str(nail) else 240

    job_id = uuid.uuid4().hex[:10]
    temp_path = os.path.join(UPLOAD_FOLDER, f"input_{job_id}.jpg")
    uploaded.save(temp_path)

    try:
        image_url, gif_url = compute_string_art(
            temp_path, num_pins, thread_thickness, thread_color, wood, job_id
        )
    except Exception as e:
        return jsonify({"status": "error", "message": f"Bild konnte nicht verarbeitet werden: {e}"}), 500
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    return jsonify({
        "status": "success",
        "size": f"{width_cm}x{height_cm} cm",
        "wood": wood,
        "thread_color": thread_color,
        "thread_thickness": thread_thickness,
        "nail": nail,
        "image_url": image_url,
        "gif_url": gif_url,
    })


@app.route("/checkout", methods=["POST"])
def checkout():
    data = request.get_json(silent=True) or {}
    try:
        width_cm = int(data.get("width_cm", 50))
        height_cm = int(data.get("height_cm", 50))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Ungültige Größe."}), 400

    if not (10 <= width_cm <= 200 and 10 <= height_cm <= 200):
        return jsonify({"status": "error", "message": "Größe muss zwischen 10 und 200 cm liegen."}), 400

    area = width_cm * height_cm
    # Testpreis: bis 2500 cm² = 0,50 €, danach flächenbasiert (Betrag in Cent)
    unit_amount = 50 if area <= 2500 else int(50 + (area - 2500) * 2.2)

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "eur",
                    "product_data": {"name": f"Faded Art String Art - {width_cm}x{height_cm} cm"},
                    "unit_amount": unit_amount,
                },
                "quantity": 1,
            }],
            mode="payment",
            success_url=request.host_url + "?success=true",
            cancel_url=request.host_url + "?canceled=true",
            customer_email=data.get("email") or None,
        )
        return jsonify({"status": "success", "url": session.url})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
