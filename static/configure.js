(() => {
const $ = id => document.getElementById(id);
const S = 600, R = S * 0.47;
const PRICE = { base: 0.5, area: 2500, perCm2: 0.025,
  wood: { weiss: 0, mdf: 0, eiche: 50, nussbaum: 80, esche: 90, beton: 60 }, pins: { 240: 0, 360: 70 } };
const cv = $('cv'), ctx = cv.getContext('2d');
const img = new Image();
let step = 1, view = { x: S / 2, y: S / 2, s: 1, cover: 1 }, hasImg = false, orderRef = null, busy = false;

const num = (el, lo, hi) => Math.min(hi, Math.max(lo, parseInt(el.value) || lo));
const cfg = () => ({ w: num($('w'), 30, 200), h: num($('h'), 30, 200), wood: $('wood').value,
  color: $('color').value, hex: $('color').selectedOptions[0].dataset.hex,
  thick: $('thick').value, pins: parseInt($('pins').value) });
const msg = t => { $('msg').textContent = t || ''; };

function price() {
  const c = cfg(), a = c.w * c.h;
  const p = PRICE.base + Math.max(0, a - PRICE.area) * PRICE.perCm2 + PRICE.wood[c.wood] + PRICE.pins[c.pins];
  $('price').textContent = 'Preis: ' + p.toFixed(2).replace('.', ',') + ' €';
}

function board() {
  const c = cfg(), b = $('board');
  b.style.setProperty('--w', c.w); b.style.setProperty('--h', c.h);
  b.classList.toggle('wide', c.w >= c.h);
  b.dataset.wood = c.wood;
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
  ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, S, S);
  const dw = img.width * view.s, dh = img.height * view.s;
  ctx.drawImage(img, view.x - dw / 2, view.y - dh / 2, dw, dh);
  ctx.restore();
  if (step >= 4) { // Beispiel-Sehnen in Fadenfarbe (nur Look, nicht das echte Ergebnis)
    ctx.save(); ctx.strokeStyle = c.hex; ctx.globalAlpha = .35; ctx.lineWidth = c.thick * 4;
    for (let i = 0; i < 60; i++) {
      const a = 2 * Math.PI * i / 60, b = a + 2.4;
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
cv.addEventListener('pointerdown', e => { drag = { x: e.clientX, y: e.clientY }; cv.setPointerCapture(e.pointerId); cv.style.cursor = 'grabbing'; });
cv.addEventListener('pointermove', e => {
  if (!drag) return;
  const k = S / cv.getBoundingClientRect().width;
  view.x += (e.clientX - drag.x) * k; view.y += (e.clientY - drag.y) * k;
  drag = { x: e.clientX, y: e.clientY }; draw();
});
['pointerup', 'pointercancel'].forEach(t => cv.addEventListener(t, () => { drag = null; cv.style.cursor = 'grab'; }));
$('zoom').addEventListener('input', e => { view.s = parseFloat(e.target.value); draw(); });
$('center').addEventListener('click', () => { fit(); draw(); });
['w', 'h', 'wood', 'color', 'thick', 'pins'].forEach(id => $(id).addEventListener('input', board));
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
  msg(''); draw();
}

$('back').addEventListener('click', () => step > 1 && show(step - 1));
$('next').addEventListener('click', () => {
  if (step === 1 && !hasImg) return msg('Bitte zuerst ein Foto auswählen.');
  if (step === 2) { const c = cfg(); $('w').value = c.w; $('h').value = c.h; board(); }
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
  if (busy) return; busy = true; msg(''); $('next').disabled = true;
  $('spin').hidden = false; $('checkout').hidden = true;
  try {
    const c = cfg(), fd = new FormData();
    fd.append('image', await cropBlob(), 'crop.png');
    ['w', 'h', 'wood', 'color', 'thick', 'pins'].forEach(k => fd.append(k, c[k]));
    const res = await fetch('/generate', { method: 'POST', body: fd });
    if (!res.ok) throw new Error('Server-Fehler ' + res.status);
    let d = await res.json();
    for (let i = 0; d.job_id && d.status !== 'done' && i < 80; i++) { // Polling bei Hintergrundjob
      await new Promise(r => setTimeout(r, 1500));
      d = await (await fetch('/status/' + d.job_id)).json();
      if (d.status === 'error') throw new Error('Berechnung fehlgeschlagen');
    }
    if (!d.preview_url) throw new Error('Keine Vorschau erhalten');
    orderRef = d.order_ref;
    $('result').src = d.preview_url; $('result').hidden = false; cv.hidden = true;
    $('checkout').hidden = false;
  } catch (err) { msg('Berechnung nicht möglich: ' + err.message + '. Bitte erneut versuchen.'); }
  finally { busy = false; $('next').disabled = false; $('spin').hidden = true; }
}

$('pay').addEventListener('click', async () => {
  const email = $('email').value.trim();
  if (!/^\S+@\S+\.\S+$/.test(email)) return msg('Bitte eine gültige E-Mail-Adresse eingeben.');
  if (!$('agb').checked) return msg('Bitte AGB und Widerrufsbelehrung bestätigen.');
  if (!orderRef) return msg('Bitte zuerst die Vorschau berechnen.');
  $('pay').disabled = true;
  try {
    const res = await fetch('/checkout', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ order_ref: orderRef, email, agb: true }) });
    const d = await res.json();
    if (d.url) location.href = d.url; else throw new Error(d.message || 'Unbekannt');
  } catch (err) { msg('Checkout nicht möglich: ' + err.message); $('pay').disabled = false; }
});

show(1); board();
})();

