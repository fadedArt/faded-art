from flask import Flask, render_template, request, jsonify
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
from skimage.draw import line
from skimage.feature import canny
import math
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import stripe

app = Flask(__name__)

# Trage hier deinen Stripe Secret Key ein
stripe.api_key = "sk_test_DEIN_STRIPE_SECRET_KEY"

UPLOAD_FOLDER = 'static'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
IMAGE_SIZE = 1000

def compute_string_art(image_path, num_pins, thickness_option, thread_color_option, wood_option, width_cm, height_cm):
    try:
        pins_count = int(num_pins)
    except (TypeError, ValueError):
        pins_count = 240

    # Feine, realistische Fadendicken & Transparenzen
    line_weight = 1       
    if "0.3" in str(thickness_option):
        alpha_val = 32    
    elif "0.2" in str(thickness_option):
        alpha_val = 22    
    else:
        alpha_val = 14     

    color_map = {
        "Tiefschwarz": (12, 12, 12),
        "Reinweiß": (250, 250, 250),
        "Champagner Gold": (212, 175, 55),
        "Silber": (195, 195, 195)
    }
    thread_rgb = color_map.get(thread_color_option, (12, 12, 12))

    bg_color_map = {
        "Klassik Weiß matt": (250, 250, 250),
        "MDF Schwarz matt": (18, 18, 18),
        "Eiche Natur geölt": (168, 122, 82),
        "Nussbaum Premium": (59, 39, 22),
        "Schwarze Esche": (33, 33, 33),
        "Beton-Look": (110, 110, 110)
    }
    bg_rgb = bg_color_map.get(wood_option, (250, 250, 250))

    # Bildoptimierung für String Art (hoher Kontrast & Kantenerkennung)
    img = Image.open(image_path).convert('L')
    img = img.resize((IMAGE_SIZE, IMAGE_SIZE), Image.Resampling.LANCZOS)
    
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.2)  
    img = img.filter(ImageFilter.SHARPEN)
    img_np = np.array(img, dtype=float)

    edges = canny(img_np / 255.0, sigma=1.1)
    target_img = 255.0 - ((255 - img_np) * 0.12 + (edges.astype(float) * 255 * 0.88))
    
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
    
    # Rein weißer runder Hintergrund für den inneren Kreis
    anim_img = Image.new('RGBA', (IMAGE_SIZE, IMAGE_SIZE), (255, 255, 255, 255))
    
    # Maske erstellen, damit Fäden nur im Kreis gezeichnet werden
    circle_mask = Image.new('L', (IMAGE_SIZE, IMAGE_SIZE), 0)
    mask_draw = ImageDraw.Draw(circle_mask)
    mask_draw.ellipse([0, 0, IMAGE_SIZE, IMAGE_SIZE], fill=255)

    line_draw_img = Image.new('RGBA', (IMAGE_SIZE, IMAGE_SIZE), (0, 0, 0, 0))
    anim_draw = ImageDraw.Draw(line_draw_img, 'RGBA')
    
    num_lines_adjusted = 4200 if pins_count > 300 else 3000 
    
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
        
        target_img[rr, cc] = np.clip(target_img[rr, cc] + 15, 0, 255)
        
        p1 = pins[current_pin]
        p2 = pins[best_pin]
        
        line_color_rgba = (thread_rgb[0], thread_rgb[1], thread_rgb[2], alpha_val)
        anim_draw.line([p1, p2], fill=line_color_rgba, width=line_weight)
        
        if step % 100 == 0 or step == num_lines_adjusted - 1:
            temp_composite = anim_img.copy()
            temp_composite.paste(line_draw_img, (0, 0), line_draw_img)
            
            # Auf Holzhintergrund einbetten für Vorschau-Animation
            full_frame = Image.new('RGBA', (IMAGE_SIZE, IMAGE_SIZE), bg_rgb + (255,))
            full_frame.paste(temp_composite, (0, 0), circle_mask)
            frames.append(full_frame.convert('P', palette=Image.ADAPTIVE))
            
        current_pin = best_pin

    # Pins / Nägel zeichnen
    pin_color = (212, 175, 55, 255) if "Gold" in thread_color_option else (210, 210, 210, 255)
    for px, py in pins:
        anim_draw.ellipse([px-3, py-3, px+3, py+3], fill=pin_color, outline=(40, 40, 40, 255))

    # Finale Zusammenführung: Holzhintergrund + weißer Fadenkreis mit Nägeln
    final_canvas = Image.new('RGBA', (IMAGE_SIZE, IMAGE_SIZE), bg_rgb + (255,))
    inner_circle_art = anim_img.copy()
    inner_circle_art.paste(line_draw_img, (0, 0), line_draw_img)
    final_canvas.paste(inner_circle_art, (0, 0), circle_mask)

    output_image_path = os.path.join(UPLOAD_FOLDER, 'preview.png')
    final_canvas.save(output_image_path)
    
    gif_path = os.path.join(UPLOAD_FOLDER, 'progress.gif')
    if frames:
        frames[0].save(
            gif_path, 
            save_all=True, 
            append_images=frames[1:], 
            optimize=False, 
            duration=140,  
            loop=0
        )
    
    with open("produktion_pinfolge.txt", "w") as f:
        f.write(",".join(map(str, pin_sequence)))

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/configure')
def configure():
    return render_template('configure.html')

@app.route('/generate', methods=['POST'])
def generate():
    width_cm = request.form.get('width_cm', '50')
    height_cm = request.form.get('height_cm', '50')
    wood = request.form.get('wood')
    thread_color = request.form.get('thread_color')
    thread_thickness = request.form.get('thread_thickness')
    nail = request.form.get('nail')
    
    num_pins = 360 if "360" in str(nail) else 240
    
    uploaded_file = request.files['image']
    temp_path = os.path.join(UPLOAD_FOLDER, 'temp_input.jpg')
    uploaded_file.save(temp_path)
    
    compute_string_art(temp_path, num_pins, thread_thickness, thread_color, wood, width_cm, height_cm)
    
    return jsonify({
        "size": f"{width_cm}x{height_cm} cm",
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
    width_cm = int(data.get('width_cm', 50))
    height_cm = int(data.get('height_cm', 50))
    area = width_cm * height_cm
    
    # Preiskalkulation: Bis 2500 cm² (z.B. 50x50cm) der Testpreis von 0,50 €, danach flächenbasiert
    if area <= 2500:
        unit_amount = 50 
    else:
        unit_amount = int(50 + (area - 2500) * 2.2) 

    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'eur',
                    'product_data': {
                        'name': f'Faded Art String Art - {width_cm}x{height_cm} cm',
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
        
        return jsonify({"status": "success", "url": checkout_session.url})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
