from flask import Flask, render_template, request, jsonify
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance
from skimage.draw import line
from skimage.feature import canny
import math
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = Flask(__name__)

UPLOAD_FOLDER = 'static'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

NUM_PINS = 320         # Anzahl der Pins am Kreisumfang
IMAGE_SIZE = 1000

def compute_string_art(image_path, thickness_option):
    if not thickness_option:
        thickness_option = "Ultra-Scharf (0.15 mm - Maximale Details)"

    # Fein abgestimmte Linien und Transparenz gegen das Schwarzwerden
    if "0.15" in thickness_option:
        line_weight, alpha_val = 2, 12   
    elif "0.3" in thickness_option:
        line_weight, alpha_val = 4, 20
    else:
        line_weight, alpha_val = 3, 15

    # 1. Bild laden und optimieren
    img = Image.open(image_path).convert('L')
    img = img.resize((IMAGE_SIZE, IMAGE_SIZE), Image.Resampling.LANCZOS)
    
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1.8)
    img_np = np.array(img, dtype=float)

    # 2. Kantenerkennung (Canny) für saubere Details
    edges = canny(img_np / 255.0, sigma=2.0)
    target_img = 255.0 - ((255 - img_np) * 0.2 + (edges.astype(float) * 255 * 0.8))
    
    radius = IMAGE_SIZE // 2
    y, x = np.ogrid[:IMAGE_SIZE, :IMAGE_SIZE]
    mask = (x - radius)**2 + (y - radius)**2 > radius**2
    target_img[mask] = 255
    
    # Pins im Kreis anordnen
    pins = []
    for i in range(NUM_PINS):
        angle = 2 * math.pi * i / NUM_PINS
        px = int(radius + (radius - 1) * math.cos(angle))
        py = int(radius + (radius - 1) * math.sin(angle))
        pins.append((px, py))
        
    pin_sequence = [0]
    current_pin = 0
    
    frames = []
    anim_img = Image.new('RGBA', (IMAGE_SIZE, IMAGE_SIZE), color=(255, 255, 255, 255))
    anim_draw = ImageDraw.Draw(anim_img, 'RGBA')
    
    num_lines_adjusted = 3500 
    
    for step in range(num_lines_adjusted):
        best_pin = -1
        max_score = -1
        best_line_pixels = None
        
        for next_pin in range(NUM_PINS):
            if abs(current_pin - next_pin) <= 15 or abs(current_pin - next_pin) >= NUM_PINS - 15:
                continue
            r0, c0 = pins[current_pin][1], pins[current_pin][0]
            r1, c1 = pins[next_pin][1], pins[next_pin][0]
            rr, cc = line(r0, c0, r1, c1)
            
            score = np.sum(255.0 - target_img[rr, cc])
            if score > max_score:
                max_score = score
                best_pin = next_pin
                best_line_pixels = (rr, cc)
                
        pin_sequence.append(best_pin)
        rr, cc = best_line_pixels
        
        target_img[rr, cc] = np.clip(target_img[rr, cc] + 15, 0, 255)
        
        p1 = pins[current_pin]
        p2 = pins[best_pin]
        anim_draw.line([p1, p2], fill=(15, 15, 15, alpha_val), width=1)
        
        if step % 150 == 0 or step == num_lines_adjusted - 1:
            frames.append(anim_img.copy().convert('P', palette=Image.ADAPTIVE))
            
        current_pin = best_pin

    output_image_path = os.path.join(UPLOAD_FOLDER, 'preview.png')
    anim_img.save(output_image_path)
    
    gif_path = os.path.join(UPLOAD_FOLDER, 'progress.gif')
    if frames:
        frames[0].save(gif_path, save_all=True, append_images=frames[1:], optimize=False, duration=80, loop=0)
    
    with open("produktion_pinfolge.txt", "w") as f:
        f.write(",".join(map(str, pin_sequence)))

def send_order_email(customer_email, size, wood, thread_color, thread_thickness, nail, total_price):
    sender_email = "deine-email@gmail.com"
    sender_password = "dein-app-passwort"
    receiver_email = "deine-email@gmail.com"

    try:
        with open("produktion_pinfolge.txt", "r") as f:
            pin_sequence_text = f.read()
    except FileNotFoundError:
        pin_sequence_text = "Keine Pinfolge gefunden."

    message = MIMEMultipart("alternative")
    message["Subject"] = f"Neue Faded Art Bestellung! ({size}) - {total_price}"
    message["From"] = sender_email
    message["To"] = receiver_email

    text_content = f"""
    Neue Bestellung eingegangen bei Faded Art!

    Kunden-E-Mail: {customer_email}
    Gesamtpreis: {total_price}
    
    Konfiguration:
    - Größe: {size}
    - Untergrund: {wood}
    - Faden: {thread_color} ({thread_thickness})
    - Pins/Nägel: {nail}

    -----------------------------------------
    PRODUKTIONS-PINFOLGE:
    {pin_sequence_text}
    -----------------------------------------
    """

    message.attach(MIMEText(text_content, "plain"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, receiver_email, message.as_string())
    except Exception as e:
        print(f"E-Mail Fehler: {e}")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/generate', methods=['POST'])
def generate():
    size = request.form.get('size')
    wood = request.form.get('wood')
    thread_color = request.form.get('thread_color')
    thread_thickness = request.form.get('thread_thickness')
    nail = request.form.get('nail')
    
    uploaded_file = request.files['image']
    temp_path = os.path.join(UPLOAD_FOLDER, 'temp_input.jpg')
    uploaded_file.save(temp_path)
    
    compute_string_art(temp_path, thread_thickness)
    
    return jsonify({
        "size": size,
        "wood": wood,
        "thread_color": thread_color,
        "thread_thickness": thread_thickness,
        "nail": nail,
        "image_url": "/static/preview.png",
        "gif_url": "/static/progress.gif"
    })

@app.route('/checkout', methods=['POST'])
def checkout():
    data = request.json
    send_order_email(
        customer_email=data.get('email'),
        size=data.get('size'),
        wood=data.get('wood'),
        thread_color=data.get('thread_color'),
        thread_thickness=data.get('thread_thickness'),
        nail=data.get('nail'),
        total_price=data.get('total_price')
    )
    return jsonify({"status": "success"})

if __name__ == '__main__':
    app.run(debug=True)