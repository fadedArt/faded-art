(() => {
const $ = id => document.getElementById(id);
const S = 600, R = S * 0.47;
const CFG = JSON.parse($('cfg').dataset.cfg), SIZES = CFG.sizes, PRICE = CFG.pricing;
const cv = $('cv'), ctx = cv.getContext('2d');
const img = new Image();
let step = 1, view = { x: S / 2, y: S / 2, s: 1, cover: 1 }, hasImg = false, orderRef = null, busy = false;

const cfg = () => { const fmt = $('format').value, size = $('size').value, [w, h] = SIZES[fmt][size];
  return { format: fmt, size, w, h, wood: $('wood').value, color: $('color').value,
    hex: $('color').selectedOptions[0].dataset.hex,
    disc: $('circle').value, discHex: $('circle').selectedOptions[0].dataset.hex, thick: $('thick').value, pins: parseInt($('pins').value) }; };
const lum = h => { const n = parseInt(h.slice(1), 16); return .299 * (n >> 16) + .587 * ((n >> 8) & 255) + .114 * (n & 255); };
const MSG = 'Fadenfarbe und Kreisfarbe sind zu ähnlich. Bitte wähle einen stärkeren Kontrast.';
const lowContrast = () => { const c = cfg(); return Math.abs(lum(c.hex) - lum(c.discHex)) < 60; };
const msg = t => { $('msg').textContent = t || ''; };

function price() {
  const c = cfg(), cents = PRICE.base_cents + Math.max(0, c.w * c.h - PRICE.base_area) * PRICE.cents_per_cm2;
  const p = Math.round(cents) / 100 + PRICE.wood[c.wood] + PRICE.pins[c.pins];
  $('price').textContent = 'Preis: ' + p.toFixed(2).replace('.', ',') + ' €';
}

function board() {
  const c = cfg(), b = $('board');
  b.style.setProperty('--w', c.w); b.style.setProperty('--h', c.h);
  b.classList.toggle('wide', c.w >= c.h);
  b.dataset.wood = c.wood;
  $('disc').style.background = c.discHex;
  if (step >= 4) msg(lowContrast() ? MSG : '');
  [...$('size').options].forEach(o => { const [w, h] = SIZES[c.format][o.value]; o.textContent = o.value + ' (' + w + ' × ' + h + ' cm)'; });
  price(); draw();
}

function pins(c2, r, n, size) {
  c2.fillStyle = '#d4af37';
  for (let i = 0; i < n; i++) {
    const a = 2 * Math.PI * i / n;
    c2.beginPath(); c2.arc(S / 2 + r * Math.cos(a), S / 2 + r * Math.sin(a), size, 0, 7); c2.fill();
  }
}

function draw() {
  if (!hasImg) return;
  const c = cfg();
  ctx.clearRect(0, 0, S, S);
  ctx.save(); ctx.beginPath(); ctx.arc(S / 2, S / 2, R, 0, 7); ctx.clip();
  ctx.fillStyle = step >= 4 ? c.discHex : '#fff'; ctx.fillRect(0, 0, S, S);   // ab Schritt 4: kein Bild, nur Kreisfarbe
  if (step < 4) { const dw = img.width * view.s, dh = img.height * view.s; ctx.drawImage(img, view.x - dw / 2, view.y - dh / 2, dw, dh); }
  ctx.restore();
  if (step >= 4) { // Beispiel-Sehnen in Fadenfarbe (nur Look, nicht das echte Ergebnis)
    ctx.save(); ctx.strokeStyle = c.hex; ctx.globalAlpha = .5; ctx.lineWidth = Math.max(1, c.thick * 5);
    for (let i = 0; i < 120; i++) {
      const a = 2 * Math.PI * i / 120, b = a + 2.6;
      ctx.beginPath(); ctx.moveTo(S / 2 + R * Math.cos(a), S / 2 + R * Math.sin(a));
      ctx.lineTo(S / 2 + R * Math.cos(b), S / 2 + R * Math.sin(b)); ctx.stroke();
    }
    ctx.restore();
  }
  pins(ctx, R, c.pins, 2.5);
}

function fit() {
  view.cover = Math.max(2 * R / img.width, 2 * R / img.height);
  view.s = view.cover; view.x = view.y = S / 2;
  const z = $('zoom');
  z.min = view.cover * .5; z.max = view.cover * 4; z.step = view.cover / 100; z.value = view.s;
}

$('file').addEventListener('change', e => {
  const f = e.target.files[0]; if (!f) return;
  if (!/^image\/(jpeg|png)$/.test(f.type) || f.size > 15 * 1024 * 1024) { msg('Bitte JPG oder PNG bis 15 MB wählen.'); e.target.value = ''; return; }
  msg(''); const url = URL.createObjectURL(f);
  img.onload = () => { hasImg = true; fit(); $('ph').hidden = true; cv.hidden = false; $('zoomBox').hidden = false; $('result').hidden = true; orderRef = null; draw(); URL.revokeObjectURL(url); };
  img.onerror = () => msg('Das Bild konnte nicht gelesen werden.');
  img.src = url;
});

let drag = null;
cv.addEventListener('pointerdown', e => { if (step !== 1) return; drag = { x: e.clientX, y: e.clientY }; cv.setPointerCapture(e.pointerId); cv.style.cursor = 'grabbing'; });
cv.addEventListener('pointermove', e => {
  if (!drag) return;
  const k = S / cv.getBoundingClientRect().width;
  view.x += (e.clientX - drag.x) * k; view.y += (e.clientY - drag.y) * k;
  drag = { x: e.clientX, y: e.clientY }; draw();
});
['pointerup', 'pointercancel'].forEach(t => cv.addEventListener(t, () => { drag = null; cv.style.cursor = step === 1 ? 'grab' : 'default'; }));
$('zoom').addEventListener('input', e => { view.s = parseFloat(e.target.value); draw(); });
const zoomBy = f => { const z = $('zoom'); view.s = Math.min(+z.max, Math.max(+z.min, view.s * f)); z.value = view.s; draw(); };
$('zin').addEventListener('click', () => zoomBy(1.1));
$('zout').addEventListener('click', () => zoomBy(1 / 1.1));
$('center').addEventListener('click', () => { fit(); draw(); });
['format', 'size', 'wood', 'color', 'circle', 'thick', 'pins'].forEach(id => $(id).addEventListener('input', board));
$('cfg').addEventListener('submit', e => e.preventDefault());

function show(n) {
  step = n;
  document.querySelectorAll('[data-step]').forEach(s => s.hidden = +s.dataset.step !== n);
  [...$('steps').children].forEach((li, i) => {
    li.classList.toggle('done', i + 1 < n);
    i + 1 === n ? li.setAttribute('aria-current', 'step') : li.removeAttribute('aria-current');
  });
  $('back').style.visibility = n > 1 ? 'visible' : 'hidden';
  $('next').textContent = n === 5 ? 'Vorschau berechnen' : 'Weiter';
  cv.style.cursor = n === 1 ? 'grab' : 'default'; cv.style.touchAction = n === 1 ? 'none' : 'auto'; // Verschieben nur in Schritt 1
  msg(''); if (n >= 4 && lowContrast()) msg(MSG);
  draw();
}

$('back').addEventListener('click', () => step > 1 && show(step - 1));
$('next').addEventListener('click', () => {
  if (step === 1 && !hasImg) return msg('Bitte zuerst ein Foto auswählen.');
  if (step === 4 && lowContrast()) return msg(MSG);
  if (step < 5) return show(step + 1);
  generate();
});

function cropBlob() { // exakt der sichtbare Kreisausschnitt, ohne Pins
  const k = 1200 / S, o = document.createElement('canvas'); o.width = o.height = Math.round(2 * R * k);
  const c = o.getContext('2d'); c.fillStyle = '#fff'; c.fillRect(0, 0, o.width, o.height);
  const dw = img.width * view.s, dh = img.height * view.s, off = S / 2 - R;
  c.drawImage(img, (view.x - dw / 2 - off) * k, (view.y - dh / 2 - off) * k, dw * k, dh * k);
  return new Promise(r => o.toBlob(r, 'image/png'));
}

async function generate() {
  if (busy) return;
  if (lowContrast()) return msg(MSG);
  busy = true; msg(''); $('next').disabled = true;
  $('wait').textContent = 'Berechnung läuft, das dauert meist unter einer Minute …';
  $('spin').hidden = false; $('checkout').hidden = true;
  try {
    const c = cfg(), fd = new FormData();
    fd.append('image', await cropBlob(), 'crop.png');
    ['format', 'size', 'wood', 'color', 'disc', 'thick', 'pins'].forEach(k => fd.append(k, c[k]));
    const res = await fetch('/generate', { method: 'POST', body: fd });
    if (!res.ok) throw new Error('Server-Fehler ' + res.status);
    let d = await res.json();
    for (let i = 0; d.job_id && d.status !== 'done' && i < 240; i++) { // Polling bei Hintergrundjob
      await new Promise(r => setTimeout(r, 1500));
      d = await (await fetch('/status/' + d.job_id)).json();
      if (d.status === 'error') throw new Error('Berechnung fehlgeschlagen');
    }
    if (!d.preview_url) throw new Error('Die Berechnung dauert ungewöhnlich lange');
    orderRef = d.order_ref;
    $('result').src = d.preview_url; $('result').hidden = false; cv.hidden = true;
    $('checkout').hidden = false;
  } catch (err) { msg('Berechnung nicht möglich: ' + err.message + '. Bitte erneut versuchen.'); }
  finally { $('wait').textContent = ''; busy = false; $('next').disabled = false; $('spin').hidden = true; }
}

async function addToCart() {
  const res = await fetch('/cart/add', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ order_ref: orderRef }) });
  const d = await res.json();
  if (!res.ok) throw new Error(d.message || 'Unbekannt');
  return d;
}
$('addcart').addEventListener('click', async () => {
  if (!orderRef) return msg('Bitte zuerst die Vorschau berechnen.');
  try {
    const d = await addToCart();
    $('cartinfo').innerHTML = 'Im Warenkorb (' + d.count + '). <a href="/warenkorb">Zum Warenkorb</a> oder <a href="/configure">weiteres Bild konfigurieren</a>';
  } catch (err) { msg('Warenkorb nicht möglich: ' + err.message); }
});
$('buy').addEventListener('click', async () => {
  if (!orderRef) return msg('Bitte zuerst die Vorschau berechnen.');
  try { await addToCart(); location.href = '/warenkorb'; }
  catch (err) { msg('Kauf nicht möglich: ' + err.message); }
});

show(1); board();
})();

