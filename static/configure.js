(() => {
const BUILD = 'b21';
window.addEventListener('error', e => { const m = document.getElementById('msg'); if (m && !m.textContent && e.message) m.textContent = 'Technischer Fehler: ' + e.message; });
window.addEventListener('pageshow', e => { if (e.persisted) location.reload(); });   // Zurück-Taste: Seite frisch laden statt alten Zustand zeigen
const $ = id => document.getElementById(id);
const S = 600, R = S * 0.49;     // Nagelring sitzt fast am Rand des Bildkreises (wie im berechneten Ergebnis)
const CFG = JSON.parse($('cfg').dataset.cfg), SIZES = CFG.sizes, PRICE = CFG.pricing;
const cv = $('cv'), ctx = cv.getContext('2d');
const img = new Image();
let step = 1, view = { x: S / 2, y: S / 2, s: 1, cover: 1 }, hasImg = false, orderRef = null, busy = false;

const cfg = () => { const fmt = $('format').value, size = $('size').value, [w, h] = SIZES[fmt][size];
  return { format: fmt, size, w, h, wood: $('wood').value, color: $('color').value,
    hex: $('color').selectedOptions[0].dataset.hex,
    pin: $('pincolor').value, pinHex: $('pincolor').selectedOptions[0].dataset.hex,
    disc: $('circle').value, discHex: $('circle').selectedOptions[0].dataset.hex, thick: $('thick').value, pins: parseInt($('pins').value) }; };
const lum = h => { const n = parseInt(h.slice(1), 16); return .299 * (n >> 16) + .587 * ((n >> 8) & 255) + .114 * (n & 255); };
const MSG = 'Fadenfarbe und Kreisfarbe sind zu ähnlich. Bitte wähle einen stärkeren Kontrast.';
const lowContrast = () => { const c = cfg(); return Math.abs(lum(c.hex) - lum(c.discHex)) < 60; };
const msg = t => { $('msg').textContent = t || ''; };

function price() {
  const c = cfg();
  if (PRICE.test_quad_cents && c.format === 'quad') {   // Testpreis für quadratische Bretter
    $('price').textContent = 'Preis: ' + (PRICE.test_quad_cents / 100).toFixed(2).replace('.', ',') + ' € (Testpreis)'; return;
  }
  const area = c.w * c.h * (c.format === 'rund' ? PRICE.round_factor : 1);
  const cents = PRICE.base_cents + Math.max(0, area - PRICE.base_area) * PRICE.cents_per_cm2;
  const p = Math.round(cents) / 100 + PRICE.wood[c.wood] + PRICE.pins[c.pins];
  $('price').textContent = 'Preis: ' + p.toFixed(2).replace('.', ',') + ' €';
}

let threadTouched = false;
function recommendThread() {                                   // größeres Bild = derselbe Faden wirkt feiner: dickeren Faden empfehlen
  const c = cfg(), disc = (c.format === 'rund' ? 0.95 : 0.9) * Math.min(c.w, c.h), want = 0.20 * disc / 45;
  const opts = [...$('thick').options].map(o => parseFloat(o.value));
  const rec = opts.reduce((a, b) => Math.abs(b - want) < Math.abs(a - want) ? b : a);
  if (!threadTouched) $('thick').value = rec.toFixed(2);
  $('threadHint').textContent = 'Bild-Durchmesser ca. ' + Math.round(disc) + ' cm. Empfehlung: ' + rec.toFixed(2).replace('.', ',') +
    ' mm. Auf größeren Brettern wirkt derselbe Faden feiner, auf kleineren kräftiger.';
}
$('thick').addEventListener('input', () => { threadTouched = true; });

function board() {
  const c = cfg(), b = $('board');
  b.style.setProperty('--w', c.w); b.style.setProperty('--h', c.h);
  b.classList.toggle('wide', c.w >= c.h);
  b.classList.toggle('round', c.format === 'rund');
  b.dataset.wood = c.wood;
  $('disc').style.background = c.discHex;
  if (step >= 4) msg(lowContrast() ? MSG : '');
  [...$('size').options].forEach(o => { const [w, h] = SIZES[c.format][o.value]; o.textContent = o.value + ' (' + w + ' × ' + h + ' cm)'; });
  recommendThread();
  price(); draw();
}

function pins(c2, r, n, size, col) {
  c2.fillStyle = col; c2.strokeStyle = 'rgba(128,128,128,.7)'; c2.lineWidth = .8;
  for (let i = 0; i < n; i++) {
    const a = 2 * Math.PI * i / n;
    c2.beginPath(); c2.arc(S / 2 + r * Math.cos(a), S / 2 + r * Math.sin(a), size, 0, 7); c2.fill(); c2.stroke();
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
  pins(ctx, R, c.pins, 3, c.pinHex);
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
['format', 'size', 'wood', 'color', 'circle', 'thick', 'pincolor', 'pins'].forEach(id => $(id).addEventListener('input', board));
$('cfg').addEventListener('submit', e => e.preventDefault());

function show(n) {
  step = n;
  document.querySelectorAll('[data-step]').forEach(s => s.hidden = +s.dataset.step !== n);
  [...$('steps').children].forEach((li, i) => {
    li.classList.toggle('done', i + 1 < n);
    i + 1 === n ? li.setAttribute('aria-current', 'step') : li.removeAttribute('aria-current');
  });
  $('back').style.visibility = n > 1 ? 'visible' : 'hidden';
  $('next').textContent = n === 5 ? 'Drei Varianten berechnen' : 'Weiter';
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

async function fetchRetry(url, opts, tries = 4) {   // Server aufgeweckt/neu gestartet? Mehrfach versuchen
  for (let i = 1; ; i++) {
    try { return await fetch(url, opts); }
    catch (e) {
      if (i >= tries) throw new Error('Der Server ist gerade nicht erreichbar. Bitte in einer Minute erneut versuchen');
      $('wait').textContent = 'Server wird geweckt, neuer Versuch ' + i + ' von ' + (tries - 1) + ' …';
      await new Promise(r => setTimeout(r, 5000));
    }
  }
}

let variantKey = null, fin = {}, liveSeen = {}, L = {}, animOn = false;
const KEYS = ['a', 'b', 'c'];
const tile = k => document.querySelector('.vopt[data-k="' + k + '"]');
const pinXY = (i, n) => { const t = 2 * Math.PI * i / n, r = 300 * 0.982; return [300 + r * Math.cos(t), 300 + r * Math.sin(t)]; };
const statusUrl = id => '/status/' + id + '?' + KEYS.map(k => 's' + k + '=' + (L[k] ? L[k].got : 0)).join('&');

function initLive() {                                          // leere Leinwände mit Kreisfarbe und Nägeln
  const c = cfg();
  KEYS.forEach(k => {
    const cvs = tile(k).querySelector('canvas'), x = cvs.getContext('2d');
    x.globalAlpha = 1; x.fillStyle = c.discHex; x.fillRect(0, 0, 600, 600); x.fillStyle = c.pinHex;
    for (let i = 0; i < c.pins; i++) { const [px, py] = pinXY(i, c.pins); x.beginPath(); x.arc(px, py, 2, 0, 7); x.fill(); }
    L[k] = { x, cvs, got: 0, queue: [], last: null, prev: null, n: c.pins, hex: c.hex, alpha: 0.08, lw: 2, dirty: false };
  });
  if (!animOn) { animOn = true; requestAnimationFrame(anim); }
}
function feed(d) {                                             // neue Linien aus der Statusantwort übernehmen
  KEYS.forEach(k => {
    const s = L[k], ln = d.lines && d.lines[k];
    if (!s || !ln || ln.from > s.got) return;
    s.alpha = ln.alpha; s.lw = ln.lw; s.n = ln.n || s.n;
    const add = ln.pins.slice(s.got - ln.from);
    s.got += add.length; s.queue.push(...add);
  });
}
function anim() {                                              // jede Linie einzeln zeichnen, gleichmäßig verteilt
  KEYS.forEach(k => {
    const s = L[k];
    if (!s || !s.queue.length) return;
    const m = Math.max(1, Math.ceil(s.queue.length / 80));
    s.x.strokeStyle = s.hex; s.x.globalAlpha = s.alpha; s.x.lineWidth = s.lw;
    for (let i = 0; i < m && s.queue.length; i++) {
      const p = s.queue.shift();
      if (s.last !== null) {
        const [x0, y0] = pinXY(s.last, s.n), [x1, y1] = pinXY(p, s.n);
        s.x.beginPath(); s.x.moveTo(x0, y0); s.x.lineTo(x1, y1); s.x.stroke(); s.prev = s.last;
      }
      s.last = p;
    }
    s.dirty = true; tile(k).classList.remove('wait');
  });
  const s = variantKey && L[variantKey];
  if (s && s.dirty && !fin[variantKey]) drawBig(s);
  KEYS.forEach(k => { if (L[k]) L[k].dirty = false; });
  requestAnimationFrame(anim);
}
function drawBig(s) {
  if (!$('big')) return;                                          // große Bühne: Kopie der Leinwand + die gerade gezogene Linie in Gold
  const b = $('big'), x = b.getContext('2d');
  x.globalAlpha = 1; x.drawImage(s.cvs, 0, 0);
  if (s.prev !== null && s.last !== null) {
    const [x0, y0] = pinXY(s.prev, s.n), [x1, y1] = pinXY(s.last, s.n);
    x.strokeStyle = '#e6ca65'; x.globalAlpha = .95; x.lineWidth = 2.5; x.beginPath(); x.moveTo(x0, y0); x.lineTo(x1, y1); x.stroke();
  }
  b.hidden = false; $('result').hidden = true; cv.hidden = true;
}
window.__faLive = () => ({ lines: KEYS.map(k => L[k] ? L[k].got : 0), queued: KEYS.map(k => L[k] ? L[k].queue.length : 0), sel: variantKey });   // nur zur Fehlersuche
function syncBig() {
  const k = variantKey;
  if (!k) return;
  if (fin[k]) { $('result').src = tile(k).querySelector('img').src; $('result').hidden = false; $('big').hidden = true; cv.hidden = true; }
  else if (L[k]) drawBig(L[k]);
}
function pick(k) {
  const b = tile(k);
  if (!b || b.classList.contains('wait')) return;
  variantKey = k;
  document.querySelectorAll('.vopt').forEach(x => x.classList.toggle('sel', x === b));
  syncBig();
}
function showVariants(d) {
  $('variants').hidden = false;
  feed(d);
  KEYS.forEach(k => {
    const b = tile(k), vp = b.querySelector('.vp'), done = (d.variants || []).find(v => v.key === k);
    if (done) {
      if (!fin[k]) { fin[k] = true; b.querySelector('img').src = done.url; b.classList.add('fin'); b.classList.remove('wait'); vp.textContent = 'fertig ✓'; if (variantKey === k) syncBig(); }
      return;
    }
    vp.textContent = ((d.prog || {})[k] || 0) + ' %';
    if (!(d.lines && d.lines[k]) && d.live && d.live[k] && liveSeen[k] !== d.live[k] && L[k]) {   // Ersatz ohne Liniendaten: Zwischenbild
      liveSeen[k] = d.live[k];
      const im = new Image(), s = L[k];
      im.onload = () => { s.x.globalAlpha = 1; s.x.drawImage(im, 0, 0, 600, 600); s.dirty = true; b.classList.remove('wait'); };
      im.src = '/live/' + d.order_ref + '/' + k + '.jpg?v=' + d.live[k];
    }
  });
  if (!variantKey && !tile('a').classList.contains('wait')) pick('a');
  if ((d.variants || []).length || Object.keys(d.lines || {}).length || Object.keys(d.live || {}).length) $('spin').hidden = true;
}
document.querySelectorAll('.vopt').forEach(b => b.addEventListener('click', () => pick(b.dataset.k)));

async function generate() {
  if (busy) return;
  if (lowContrast()) return msg(MSG);
  busy = true; msg(''); $('next').disabled = true;
  $('wait').textContent = 'Berechnung läuft, das dauert meist unter einer Minute …';
  $('spin').hidden = false; $('checkout').hidden = true; $('variants').hidden = false;
  $('vtext').textContent = 'Deine drei Varianten werden gerade live gezeichnet. Tippe auf eine, um sie groß zu sehen.';
  variantKey = null; fin = {}; liveSeen = {};
  $('big').hidden = true;
  $('result').hidden = true; cv.hidden = false;
  document.querySelectorAll('.vopt').forEach(x => { x.classList.add('wait'); x.classList.remove('sel'); x.classList.remove('fin'); x.querySelector('img').removeAttribute('src'); x.querySelector('.vp').textContent = ''; });
  initLive();
  try {
    await fetchRetry('/health', {}, 10);                      // Server zuerst aufwecken
    $('wait').textContent = 'Berechnung läuft, für drei Varianten kann das einige Minuten dauern …';
    const c = cfg(), fd = new FormData();
    fd.append('image', await cropBlob(), 'crop.png');
    ['format', 'size', 'wood', 'color', 'disc', 'thick', 'pin', 'pins'].forEach(k => fd.append(k, c[k]));
    const res = await fetchRetry('/generate', { method: 'POST', body: fd });
    if (!res.ok) { let m = ''; try { m = (await res.json()).message; } catch (e) {} throw new Error(m || 'Server-Fehler ' + res.status); }
    let d = await res.json();
    let fails = 0;
    const jobId = d.job_id;                                  // Job-ID merken: die Status-Antwort ersetzt d
    for (let i = 0; jobId && d.status !== 'done' && i < 480; i++) {   // Polling bis zu 12 Minuten
      await new Promise(r => setTimeout(r, 1500));
      try { d = await (await fetch(statusUrl(jobId))).json(); fails = 0; }
      catch (e) { if (++fails > 40) throw new Error('Die Verbindung zum Server ist abgebrochen'); continue; }
      if (d.status === 'error') throw new Error(d.message || 'Berechnung fehlgeschlagen');
      $('wait').textContent = 'Die Fäden werden gezogen, Linie für Linie … ' + (d.progress || 0) + ' %';
      showVariants(d);
    }
    if (!d.variants || !d.variants.length) throw new Error('Die Berechnung dauert ungewöhnlich lange');
    orderRef = d.order_ref;
    showVariants(d);
    $('vtext').textContent = 'Fertig! Tippe auf deine Lieblingsvariante und kaufe sie:';
    $('checkout').hidden = false;
  } catch (err) { msg('Berechnung nicht möglich: ' + err.message + '. Bitte erneut versuchen.'); }
  finally { $('wait').textContent = ''; busy = false; $('next').disabled = false; $('spin').hidden = true; }
}

async function addToCart() {
  const res = await fetch('/cart/add', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ order_ref: orderRef, variant: variantKey }) });
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

async function checkBuild() {                                  // Selbsttest: passen alle vier Dateien zusammen?
  const bad = [], html = ($('buildinfo') || { dataset: {} }).dataset.build;
  if (html !== BUILD) bad.push('templates/configure.html');
  if (getComputedStyle(document.documentElement).getPropertyValue('--build').replace(/["'\s]/g, '') !== BUILD) bad.push('static/style.css');
  try { const v = await (await fetch('/version')).json(); if (v.build !== BUILD) bad.push('app.py'); } catch (e) { /* offline: nichts melden */ }
  if (!bad.length) return;
  const box = document.createElement('div');
  box.style.cssText = 'border:1px solid #c0392b;border-radius:12px;margin:1rem 0;padding:1rem;color:#ffb4a8;background:#2a1210';
  box.textContent = 'Versionsfehler: Diese Dateien sind veraltet oder unvollständig hochgeladen: ' + bad.join(', ') + '. Bitte mit der aktuellen Fassung ersetzen.';
  $('steps').after(box);
}
checkBuild();
})();

