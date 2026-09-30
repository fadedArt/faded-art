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
import stripe

app = Flask(__name__)

# Trage hier deinen Stripe Secret Key ein (z.B. sk_test_...)
stripe.api_key = "sk_test_DEIN_STRIPE_SECRET_KEY"

UPLOAD_FOLDER = 'static'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
IMAGE_SIZE = 1000

def compute_string_art(image_path, num_pins, thickness_option, thread_color_option, wood_option):
    try:
        pins_count = int(num_pins)
    except (TypeError, ValueError):
        pins_count = 240

    line_weight = 1       
    if "0.3" in str(thickness_option):
        alpha_val = 40    
    elif "0.2" in str(thickness_option):
        alpha_val = 28    
    else:
        alpha_val = 20    

    color_map = {
        "Tiefschwarz": (15, 15, 15),
        "Reinweiß": (245, 245, 245),
        "Champagner Gold": (212, 175, 55),
        "Silber": (200, 200, 200)
    }
    thread_rgb = color_map.get(thread_color_option, (15, 15, 15))

    bg_color_map = {
        "Klassik Weiß matt": (250, 250, 250, 255),
        "MDF Schwarz matt": (20, 20, 20, 255),
        "Eiche Natur geölt": (160, 115, 75, 255),
        "Nussbaum Premium": (65, 42, 25, 255),
        "Schwarze Esche": (30, 30, 30, 255),
        "Beton-Look": (90, 90, 90, 255)
    }
    canvas_bg = bg_color_map.get(wood_option, (250, 250, 250, 255))

    img = Image.open(image_path).convert('L')
    img = img.resize((IMAGE_SIZE, IMAGE_SIZE), Image.Resampling.LANCZOS)
    
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.2)  
    img_np = np.array(img, dtype=float)

    edges = canny(img_np / 255.0, sigma=1.5)
    target_img = 255.0 - ((255 - img_np) * 0.15 + (edges.astype(float) * 255 * 0.85))
    
    radius = IMAGE_SIZE // 2
    y, x = np.ogrid[:IMAGE_SIZE, :IMAGE_SIZE]
    mask = (x - radius)**2 + (y - radius)**2 > radius**2
    target_img[mask] = 255
    
    pins = []
    for i in range(pins_count):
        angle = 2 * math.pi * i / pins_count
        px = int(radius + (radius - 1) * math.cos(angle))
        py = int(radius + (radius - 1) * math.sin(angle))
        pins.append((px, py))
        
    pin_sequence = [0]
    current_pin = 0
    
    frames = []
    anim_img = Image.new('RGBA', (IMAGE_SIZE, IMAGE_SIZE), color=canvas_bg)
    anim_draw = ImageDraw.Draw(anim_img, 'RGBA')
    
    num_lines_adjusted = 4600 if pins_count > 300 else 3200 
    
    for step in range(num_lines_adjusted):
        best_pin = -1
        max_score = -1
        best_line_pixels = None
        
        skip_range = 18 if pins_count > 300 else 12
        
        for next_pin in range(pins_count):
            if abs(current_pin - next_pin) <= skip_range or abs(current_pin - next_pin) >= pins_count - skip_range:
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
        
        target_img[rr, cc] = np.clip(target_img[rr, cc] + 12, 0, 255)
        
        p1 = pins[current_pin]
        p2 = pins[best_pin]
        
        line_color_rgba = (thread_rgb[0], thread_rgb[1], thread_rgb[2], alpha_val)
        anim_draw.line([p1, p2], fill=line_color_rgba, width=line_weight)
        
        if step % 100 == 0 or step == num_lines_adjusted - 1:
            frames.append(anim_img.copy().convert('P', palette=Image.ADAPTIVE))
            
        current_pin = best_pin

    pin_color = (212, 175, 55, 255) if "Gold" in thread_color_option else (220, 220, 220, 255)
    for px, py in pins:
        anim_draw.ellipse([px-4, py-4, px+4, py+4], fill=pin_color, outline=(50, 50, 50, 255))

    output_image_path = os.path.join(UPLOAD_FOLDER, 'preview.png')
    anim_img.save(output_image_path)
    
    gif_path = os.path.join(UPLOAD_FOLDER, 'progress.gif')
    if frames:
        frames[0].save(
            gif_path, 
            save_all=True, 
            append_images=frames[1:], 
            optimize=False, 
            duration=150,  
            loop=0
        )
    
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
    - Faden: {thread_color} (Dicke: {thread_thickness} mm)
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

@app.route('/configure')
def configure():
    return render_template('configure.html')

@app.route('/generate', methods=['POST'])
def generate():
    size = request.form.get('size')
    wood = request.form.get('wood')
    thread_color = request.form.get('thread_color')
    thread_thickness = request.form.get('thread_thickness')
    nail = request.form.get('nail')
    
    num_pins = 360 if "360" in str(nail) else 240
    
    uploaded_file = request.files['image']
    temp_path = os.path.join(UPLOAD_FOLDER, 'temp_input.jpg')
    uploaded_file.save(temp_path)
    
    compute_string_art(temp_path, num_pins, thread_thickness, thread_color, wood)
    
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
    size = data.get('size')
    
    # Wenn Größe S gewählt ist, kostet es 50 Cent (50 Cents in Stripe), ansonsten Standard
    unit_amount = 50 if "S" in size else 24000 

    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'eur',
                    'product_data': {
                        'name': f'Faded Art String Art - {size}',
                    },
                    'unit_amount': unit_amount,
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=request.host_url + '?success=true',
            cancel_url=request.host_url + '?canceled=true',
            customer_email=data.get('email')
        )
        
        send_order_email(
            customer_email=data.get('email'),
            size=size,
            wood=data.get('wood'),
            thread_color=data.get('thread_color'),
            thread_thickness=data.get('thread_thickness'),
            nail=data.get('nail'),
            total_price="0,50 €" if "S" in size else "Standard"
        )
        
        return jsonify({"status": "success", "url": checkout_session.url})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
