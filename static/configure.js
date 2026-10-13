(() => {
const BUILD = 'b26';
window.addEventListener('error', e => { const m = document.getElementById('msg'); if (m && !m.textContent && e.message) m.textContent = 'Technischer Fehler: ' + e.message; });
const $ = id => document.getElementById(id);
const S = 600, R = S * 0.49;     // Nagelring sitzt fast am Rand des Bildkreises (wie im berechneten Ergebnis)
const CFG = JSON.parse($('cfg').dataset.cfg), SIZES = CFG.sizes, PRICE = CFG.pricing;
const cv = $('cv'), ctx = cv.getContext('2d');
const img = new Image();
let step = 1, view = { x: S / 2, y: S / 2, s: 1, cover: 1 }, hasImg = false, orderRef = null, busy = false;
let cut = null, segPromise = null, cutToken = 0;   // cut: freigestelltes Foto (Canvas mit transparentem Hintergrund)

const cfg = () => { const fmt = $('format').value, size = $('size').value, [w, h] = SIZES[fmt][size];
  return { format: fmt, size, w, h, wood: $('wood').value, color: $('color').value,
    hex: $('color').selectedOptions[0].dataset.hex,
    pin: $('pincolor').value, pinHex: $('pincolor').selectedOptions[0].dataset.hex,
    disc: $('circle').value, discHex: $('circle').selectedOptions[0].dataset.hex, thick: $('thick').value, pins: parseInt($('pins').value),
    multi: $('color').value === 'mehrfarbig', sw: $('color').value === 'sw' }; };
const DEMO = ['#111111', '#b91c24', '#f2c416', '#1c3a96'];   // Beispielfarben für die Vorschau "Mehrfarbig" (echte Farben wählt der Server)
const lum = h => { const n = parseInt(h.slice(1), 16); return .299 * (n >> 16) + .587 * ((n >> 8) & 255) + .114 * (n & 255); };
const MSG = 'Fadenfarbe und Kreisfarbe sind zu ähnlich. Bitte wähle einen stärkeren Kontrast.';
const lowContrast = () => { const c = cfg(); return !c.multi && !c.sw && Math.abs(lum(c.hex) - lum(c.discHex)) < 60; };   // mehrfarbig: Server wählt passende Farben
const msg = t => { $('msg').textContent = t || ''; };

function price() {
  const c = cfg();
  if (PRICE.test_quad_cents && c.format === 'quad') {   // Testpreis für quadratische Bretter
    $('price').textContent = 'Preis: ' + (PRICE.test_quad_cents / 100).toFixed(2).replace('.', ',') + ' € (Testpreis)'; return;
  }
  const area = c.w * c.h * (c.format === 'rund' ? PRICE.round_factor : 1);
  const cents = PRICE.base_cents + Math.max(0, area - PRICE.base_area) * PRICE.cents_per_cm2;
  const p = Math.round(cents) / 100 + PRICE.wood[c.wood] + PRICE.pins[c.pins] + (c.multi ? PRICE.multi || 0 : 0) + (c.sw ? PRICE.sw || 0 : 0);
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
  $('multiHint').hidden = !c.multi; $('swHint').hidden = !c.sw;
  if (step >= 4) msg(lowContrast() ? MSG : '');
  [...$('size').options].forEach(o => { const [w, h] = SIZES[c.format][o.value]; o.textContent = o.value + ' (' + w + ' × ' + h + ' cm)'; });
  recommendThread();
  price(); draw(); saveState();
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
  if (step < 4) { const dw = img.width * view.s, dh = img.height * view.s; ctx.drawImage(cut || img, view.x - dw / 2, view.y - dh / 2, dw, dh); }
  ctx.restore();
  if (step >= 4) { // Beispiel-Sehnen in Fadenfarbe (nur Look, nicht das echte Ergebnis)
    ctx.save(); ctx.strokeStyle = c.hex; ctx.globalAlpha = .5; ctx.lineWidth = Math.max(1, c.thick * 5);
    for (let i = 0; i < 120; i++) {
      const a = 2 * Math.PI * i / 120, b = a + 2.6;
      if (c.multi) ctx.strokeStyle = DEMO[i % DEMO.length];
      else if (c.sw) ctx.strokeStyle = i % 2 ? '#f2f0ea' : '#111111';
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
  useImage(url, null).then(() => { URL.revokeObjectURL(url); storeImage(); saveState({ job: null, variant: null }); });
});
function useImage(url, v) {                                   // Foto anzeigen; v = gespeicherter Ausschnitt (nach Zurück-Taste)
  return new Promise(res => {
    img.onload = () => { hasImg = true; fit(); if (v) { view.x = v.x; view.y = v.y; view.s = v.s; $('zoom').value = v.s; }
      $('ph').hidden = true; cv.hidden = false; $('zoomBox').hidden = false; $('bgBox').hidden = false; $('result').hidden = true; orderRef = null;
      cut = null; draw(); if ($('bgcut').checked) freistellen(); res(); };
    img.onerror = () => { msg('Das Bild konnte nicht gelesen werden.'); res(); };
    img.src = url;
  });
}

// ---- Zustand merken: Zurück-Taste / Wischgeste am iPhone darf nichts löschen ----
const SKEY = 'fa-konfig-v1', SEL = ['format', 'size', 'wood', 'color', 'circle', 'thick', 'pincolor', 'pins'];
function saveState(extra) {
  try {
    const st = JSON.parse(sessionStorage.getItem(SKEY) || '{}');
    st.step = step; st.sel = {}; SEL.forEach(id => st.sel[id] = $(id).value); st.bgcut = $('bgcut').checked;
    if (hasImg) st.view = { x: view.x, y: view.y, s: view.s };
    Object.assign(st, extra || {});
    sessionStorage.setItem(SKEY, JSON.stringify(st));
  } catch (e) { /* privater Modus o. ä.: dann eben ohne Merken */ }
}
function storeImage() {                                        // Foto verkleinert merken (passt in den Sitzungsspeicher)
  for (const max of [1600, 1100, 800]) {
    try {
      const k = Math.min(1, max / Math.max(img.width, img.height)), c = document.createElement('canvas');
      c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
      c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
      const st = JSON.parse(sessionStorage.getItem(SKEY) || '{}');
      st.img = c.toDataURL('image/jpeg', 0.88); st.imgScale = k;
      sessionStorage.setItem(SKEY, JSON.stringify(st)); return;
    } catch (e) { /* zu groß: kleiner versuchen */ }
  }
}

// Hintergrund entfernen: MediaPipe (Google, Apache 2.0) läuft nur hier im Browser, Dateien liegen auf unserem Server (static/mp)
function segmenter() {
  if (!segPromise) segPromise = (async () => {
    const { ImageSegmenter } = await import('/static/mp/vision_bundle.mjs');
    return ImageSegmenter.createFromOptions(
      { wasmLoaderPath: '/static/mp/vision_wasm_internal.js', wasmBinaryPath: '/static/mp/vision_wasm_internal.wasm' },
      { baseOptions: { modelAssetPath: '/static/mp/selfie_segmenter.tflite', delegate: 'CPU' }, runningMode: 'IMAGE', outputConfidenceMasks: true, outputCategoryMask: false });
  })().catch(e => { segPromise = null; throw e; });
  return segPromise;
}
async function freistellen() {
  const token = ++cutToken, box = $('bgcut');
  $('bgHint').dataset.text = $('bgHint').dataset.text || $('bgHint').textContent;
  $('bgHint').textContent = 'Freistellen läuft …';
  try {
    const seg = await segmenter();
    const sc = Math.min(1, 1024 / Math.max(img.width, img.height));          // große Fotos verkleinert auswerten
    const c = document.createElement('canvas'); c.width = Math.round(img.width * sc); c.height = Math.round(img.height * sc);
    const x = c.getContext('2d'); x.drawImage(img, 0, 0, c.width, c.height);
    const r = seg.segment(c), m = r.confidenceMasks[0], a = m.getAsFloat32Array();
    const mc = document.createElement('canvas'); mc.width = m.width; mc.height = m.height;
    const mx = mc.getContext('2d'), id = mx.createImageData(m.width, m.height);
    for (let i = 0; i < a.length; i++) id.data[4 * i + 3] = Math.round(255 * Math.min(1, Math.max(0, a[i])));
    r.confidenceMasks.forEach(q => q.close());
    mx.putImageData(id, 0, 0);
    x.globalCompositeOperation = 'destination-in'; x.drawImage(mc, 0, 0, c.width, c.height);   // Maske = Deckung des Fotos
    if (token !== cutToken || !box.checked) return;                          // inzwischen neues Foto oder ausgeschaltet
    cut = c; $('bgHint').textContent = $('bgHint').dataset.text; draw();
  } catch (e) {
    if (token !== cutToken) return;
    box.checked = false; cut = null; $('bgHint').textContent = $('bgHint').dataset.text; draw();
    msg('Freistellen ist auf diesem Gerät leider nicht möglich (' + (e.message || e) + ').');
  }
}
$('bgcut').addEventListener('change', e => {
  if (e.target.checked) return freistellen();
  cutToken++; cut = null; if ($('bgHint').dataset.text) $('bgHint').textContent = $('bgHint').dataset.text; draw();
});

let drag = null;
cv.addEventListener('pointerdown', e => { if (step !== 1) return; drag = { x: e.clientX, y: e.clientY }; cv.setPointerCapture(e.pointerId); cv.style.cursor = 'grabbing'; });
cv.addEventListener('pointermove', e => {
  if (!drag) return;
  const k = S / cv.getBoundingClientRect().width;
  view.x += (e.clientX - drag.x) * k; view.y += (e.clientY - drag.y) * k;
  drag = { x: e.clientX, y: e.clientY }; draw();
});
['pointerup', 'pointercancel'].forEach(t => cv.addEventListener(t, () => { if (drag) saveState(); drag = null; cv.style.cursor = step === 1 ? 'grab' : 'default'; }));
$('zoom').addEventListener('input', e => { view.s = parseFloat(e.target.value); draw(); saveState(); });
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
  $('next').textContent = n === 5 ? 'Varianten berechnen' : 'Weiter';
  cv.style.cursor = n === 1 ? 'grab' : 'default'; cv.style.touchAction = n === 1 ? 'none' : 'auto'; // Verschieben nur in Schritt 1
  msg(''); if (n >= 4 && lowContrast()) msg(MSG);
  draw(); saveState();
}

$('back').addEventListener('click', () => step > 1 && show(step - 1));
$('next').addEventListener('click', () => {
  if (step === 1 && !hasImg) return msg('Bitte zuerst ein Foto auswählen.');
  if (step === 4 && lowContrast()) return msg(MSG);
  if (step < 5) return show(step + 1);
  generate();
});

function cropBlob() { // exakt der sichtbare Kreisausschnitt, ohne Pins; freigestellt: Hintergrund bleibt transparent
  const k = 1200 / S, o = document.createElement('canvas'); o.width = o.height = Math.round(2 * R * k);
  const c = o.getContext('2d'); if (!cut) { c.fillStyle = '#fff'; c.fillRect(0, 0, o.width, o.height); }
  const dw = img.width * view.s, dh = img.height * view.s, off = S / 2 - R;
  c.drawImage(cut || img, (view.x - dw / 2 - off) * k, (view.y - dh / 2 - off) * k, dw * k, dh * k);
  return new Promise(r => o.toBlob(r, cut ? 'image/png' : 'image/jpeg', 0.92));   // ohne Freistellung: JPEG, viel kleiner
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
const KEYS = ['a', 'b', 'c'], SKEYS = ['d', 'e', 'f'], ALL = KEYS.concat(SKEYS);
let curRef = null, nlines = {};
const fmtN = x => Number(x).toLocaleString('de-DE');
let prices = {}, basePrice = null;
const eur = c => (c / 100).toFixed(2).replace('.', ',') + ' €';
const tile = k => document.querySelector('.vopt[data-k="' + k + '"]');
const pinXY = (i, n) => { const t = 2 * Math.PI * i / n, r = 300 * 0.982; return [300 + r * Math.cos(t), 300 + r * Math.sin(t)]; };
const statusUrl = id => '/status/' + id + '?' + ALL.map(k => 's' + k + '=' + (L[k] ? L[k].got : 0)).join('&');

function paintBase(x, bg, pinHex, n) {
  x.globalAlpha = 1; x.fillStyle = bg; x.fillRect(0, 0, 600, 600); x.fillStyle = pinHex;
  for (let i = 0; i < n; i++) { const [px, py] = pinXY(i, n); x.beginPath(); x.arc(px, py, 2, 0, 7); x.fill(); }
}
function initLive() {                                          // leere Leinwände mit Kreisfarbe und Nägeln
  const c = cfg();
  ALL.forEach(k => {
    const cvs = tile(k).querySelector('canvas'), x = cvs.getContext('2d');
    x.globalAlpha = 1; x.fillStyle = c.discHex; x.fillRect(0, 0, 600, 600); x.fillStyle = c.pinHex;
    for (let i = 0; i < c.pins; i++) { const [px, py] = pinXY(i, c.pins); x.beginPath(); x.arc(px, py, 2, 0, 7); x.fill(); }
    L[k] = { x, cvs, got: 0, queue: [], last: {}, prev: null, n: c.pins, bg: c.discHex, hex: [c.hex], alpha: 0.08, lw: 2, dirty: false };   // last: letzter Pin je Fadenfarbe
  });
  if (!animOn) { animOn = true; requestAnimationFrame(anim); }
}
function feed(d) {                                             // neue Linien aus der Statusantwort übernehmen
  if (d.order_ref) curRef = d.order_ref;
  ALL.forEach(k => {
    const s = L[k], ln = d.lines && d.lines[k];
    if (!s || !ln || ln.from > s.got) return;
    if (s.got === 0 && ((ln.bg && ln.bg !== s.bg) || (ln.n && ln.n !== s.n))) {   // Vorschlag: andere Kreisfarbe/Nagelzahl
      s.bg = ln.bg || s.bg; s.n = ln.n || s.n; paintBase(s.x, s.bg, cfg().pinHex, s.n);
    }
    s.alpha = ln.alpha; s.lw = ln.lw; s.n = ln.n || s.n; if (ln.hex) s.hex = ln.hex;
    const off = s.got - ln.from, add = ln.pins.slice(off), cols = (ln.cols || []).slice(off);
    s.got += add.length; s.queue.push(...add.map((p, i) => [p, cols[i] || 0]));   // [Pin, Fadenfarbe]
  });
}
function anim() {                                              // jede Linie einzeln zeichnen, gleichmäßig verteilt
  ALL.forEach(k => {
    const s = L[k];
    if (!s || !s.queue.length) return;
    const m = Math.max(1, Math.ceil(s.queue.length / 80));
    s.x.globalAlpha = s.alpha; s.x.lineWidth = s.lw;
    for (let i = 0; i < m && s.queue.length; i++) {
      const [p, col] = s.queue.shift(), last = s.last[col];   // jede Farbe ist ein eigener Faden mit eigenem letzten Pin
      if (last !== undefined) {
        const [x0, y0] = pinXY(last, s.n), [x1, y1] = pinXY(p, s.n);
        s.x.strokeStyle = s.hex[col] || s.hex[0];
        s.x.beginPath(); s.x.moveTo(x0, y0); s.x.lineTo(x1, y1); s.x.stroke(); s.prev = [last, p];
      }
      s.last[col] = p;
    }
    s.dirty = true; tile(k).classList.remove('wait'); tile(k).classList.add('drawing');
  });
  const s = variantKey && L[variantKey];
  if (s && s.dirty && !fin[variantKey]) drawBig(s);
  ALL.forEach(k => { if (L[k]) L[k].dirty = false; });
  requestAnimationFrame(anim);
}
function drawBig(s) {
  if (!$('big')) return;                                          // große Bühne: Kopie der Leinwand + die gerade gezogene Linie in Gold
  const b = $('big'), x = b.getContext('2d');
  x.globalAlpha = 1; x.drawImage(s.cvs, 0, 0);
  if (s.prev) {
    const [x0, y0] = pinXY(s.prev[0], s.n), [x1, y1] = pinXY(s.prev[1], s.n);
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
  saveState({ variant: k });
  if (SKEYS.includes(k)) { if (prices[k]) $('price').textContent = 'Preis mit diesem Vorschlag: ' + eur(prices[k]); }
  else if (basePrice != null) $('price').textContent = 'Preis: ' + eur(basePrice);
  syncBig(); bigInfo();
}
function bigInfo() {                                             // unter dem großen Bild: Name, Anzahl Fäden, Zoom
  const k = variantKey, b = k && tile(k);
  if (!b) { $('bigInfo').hidden = true; return; }
  $('bigInfo').hidden = false;
  const name = b.querySelector('span').textContent.replace(' …', '');
  $('bigLines').textContent = name + ': ' + (nlines[k] ? fmtN(nlines[k]) + ' Fäden (Handgriffe)' : 'wird noch gezeichnet …');
  $('zoomBtn').hidden = !fin[k];
}
function showSugg(d) {                                           // drei Vorschläge: Fortschritt, fertiges Bild, Aufpreis
  if (!d.suggestions) return;
  $('sugg').hidden = false;
  if (d.cents != null) basePrice = d.cents;
  d.suggestions.forEach(sg => {
    const b = tile(sg.key); if (!b) return;
    prices[sg.key] = sg.cents;
    b.querySelector('.xp').textContent = sg.extra_cents > 0 ? '+ ' + eur(sg.extra_cents) : 'ohne Aufpreis';
    if (sg.lines) nlines[sg.key] = sg.lines;
    if (sg.url && !fin[sg.key]) {
      fin[sg.key] = true; b.querySelector('img').src = sg.url; b.classList.add('fin'); b.classList.remove('wait');
      b.querySelector('.vp').textContent = sg.lines ? fmtN(sg.lines) + ' Fäden' : 'fertig ✓';
      if (variantKey === sg.key) { syncBig(); bigInfo(); }
    } else if (!sg.url) {
      b.querySelector('.vp').textContent = sg.pct ? sg.pct + ' %' : 'wartet';
      if (!(d.lines && d.lines[sg.key]) && d.live && d.live[sg.key] && liveSeen[sg.key] !== d.live[sg.key]) {           // Zwischenbild, solange gerechnet wird
        liveSeen[sg.key] = d.live[sg.key];
        const im = new Image(), cvs = b.querySelector('canvas');
        im.onload = () => { const x = cvs.getContext('2d'); x.globalAlpha = 1; x.drawImage(im, 0, 0, 600, 600); b.classList.add('drawing'); };
        im.src = '/live/' + d.order_ref + '/' + sg.key + '.jpg?v=' + d.live[sg.key];
      }
    }
  });
}
function showVariants(d) {
  $('variants').hidden = false;
  feed(d);
  KEYS.forEach(k => {
    const b = tile(k), vp = b.querySelector('.vp'), done = (d.variants || []).find(v => v.key === k);
    if (done) {
      if (done.lines) nlines[k] = done.lines;
      if (!fin[k]) { fin[k] = true; b.querySelector('img').src = done.url; b.classList.add('fin'); b.classList.remove('wait');
        vp.textContent = done.lines ? fmtN(done.lines) + ' Fäden' : 'fertig ✓'; if (variantKey === k) { syncBig(); bigInfo(); } }
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
  if (!variantKey && !tile('a').classList.contains('wait')) pick('a'); else bigInfo();
  if ((d.variants || []).length || Object.keys(d.lines || {}).length || Object.keys(d.live || {}).length) $('spin').hidden = true;
}
document.querySelectorAll('.vopt').forEach(b => b.addEventListener('click', () => pick(b.dataset.k)));

function resetResults() {
  $('spin').hidden = false; $('checkout').hidden = true; $('variants').hidden = false; $('sugg').hidden = true; prices = {}; basePrice = null;
  $('vtext').textContent = 'Deine drei Varianten werden gerade live gezeichnet. Tippe auf eine, um sie groß zu sehen.';
  variantKey = null; fin = {}; liveSeen = {}; nlines = {}; $('bigInfo').hidden = true;
  $('big').hidden = true;
  $('result').hidden = true; cv.hidden = false;
  document.querySelectorAll('.vopt').forEach(x => { x.classList.add('wait'); x.classList.remove('sel'); x.classList.remove('fin'); x.querySelector('img').removeAttribute('src'); x.querySelector('.vp').textContent = ''; x.classList.remove('drawing'); const xp = x.querySelector('.xp'); if (xp) xp.textContent = ''; });
  initLive();
}
async function followJob(jobId, d, want) {                     // Berechnung verfolgen bis fertig (auch nach Zurück-Taste)
  let fails = 0;
  for (let i = 0; jobId && d.status !== 'done' && i < 600; i++) {   // Polling: erst schnell, damit das Zeichnen sofort startet
    await new Promise(r => setTimeout(r, i < 40 ? 600 : 1200));
    try {
      const r = await fetch(statusUrl(jobId));
      if (r.status === 404) throw new Error('Die Berechnung ist nicht mehr auf dem Server (Neustart). Bitte neu berechnen');
      d = await r.json(); fails = 0;
    } catch (e) { if (e.message.startsWith('Die Berechnung ist nicht')) throw e; if (++fails > 40) throw new Error('Die Verbindung zum Server ist abgebrochen'); continue; }
    if (d.status === 'error') throw new Error(d.message || 'Berechnung fehlgeschlagen');
    $('wait').textContent = 'Die Fäden werden gezogen, Linie für Linie … ' + (d.progress || 0) + ' %';
    showVariants(d);
  }
  if (!d.variants || !d.variants.length) throw new Error('Die Berechnung dauert ungewöhnlich lange');
  orderRef = d.order_ref;
  showVariants(d); showSugg(d);
  if (want && tile(want) && !tile(want).classList.contains('wait')) pick(want);
  $('vtext').textContent = 'Fertig! Tippe auf deine Lieblingsvariante und kaufe sie. Darunter rechnen wir noch drei Vorschläge für dich.';
  $('checkout').hidden = false;
  pollSugg(jobId, want);
}
async function resume(jobId, want) {                           // nach Zurück-Taste: Ergebnis wieder anzeigen statt neu rechnen
  busy = true; $('next').disabled = true; resetResults();
  try {
    const r = await fetch(statusUrl(jobId));
    if (r.status === 404) { saveState({ job: null }); $('variants').hidden = true; $('spin').hidden = true; cv.hidden = !hasImg; return; }
    await followJob(jobId, await r.json(), want);
  } catch (err) { msg('Berechnung nicht möglich: ' + err.message + '. Bitte erneut versuchen.'); }
  finally { $('wait').textContent = ''; busy = false; $('next').disabled = false; $('spin').hidden = true; }
}
async function generate() {
  if (busy) return;
  if (lowContrast()) return msg(MSG);
  busy = true; msg(''); $('next').disabled = true;
  $('wait').textContent = 'Berechnung läuft, das dauert meist unter einer Minute …';
  resetResults();
  try {
    const [blob] = await Promise.all([cropBlob(), fetchRetry('/health', {}, 10)]);   // Bild vorbereiten, während der Server aufwacht
    $('wait').textContent = 'Berechnung läuft, für drei Varianten kann das einige Minuten dauern …';
    const c = cfg(), fd = new FormData();
    fd.append('image', blob, cut ? 'crop.png' : 'crop.jpg');
    ['format', 'size', 'wood', 'color', 'disc', 'thick', 'pin', 'pins'].forEach(k => fd.append(k, c[k]));
    const res = await fetchRetry('/generate', { method: 'POST', body: fd });
    if (!res.ok) { let m = ''; try { m = (await res.json()).message; } catch (e) {} throw new Error(m || 'Server-Fehler ' + res.status); }
    const d = await res.json();
    saveState({ job: d.job_id, variant: null });             // Job merken: nach Zurück-Taste wird nicht neu gerechnet
    await followJob(d.job_id, d, null);
  } catch (err) { msg('Berechnung nicht möglich: ' + err.message + '. Bitte erneut versuchen.'); }
  finally { $('wait').textContent = ''; busy = false; $('next').disabled = false; $('spin').hidden = true; }
}

async function pollSugg(id, want) {                             // Vorschläge laufen nach den drei Varianten weiter
  for (let i = 0; i < 600; i++) {
    let d;
    try { d = await (await fetch(statusUrl(id))).json(); } catch (e) { await new Promise(r => setTimeout(r, 4000)); continue; }
    feed(d); showSugg(d);
    if (want && variantKey !== want && fin[want]) pick(want);
    if (d.sugg === 'done' || d.status === 'error') return;
    await new Promise(r => setTimeout(r, 1500));
  }
}

// Zoom: fertiges Bild in voller Auflösung, einzelne Fäden sichtbar
let zl = 2;
const zset = () => { $('zimg').style.width = (zl * 100) + '%'; };
$('zoomBtn').addEventListener('click', () => {
  if (!variantKey || !curRef) return;
  $('zimg').src = '/zoom/' + curRef + '/' + variantKey + '.png'; zl = 2; zset(); $('zoomOv').hidden = false;
  const sc = $('zscroll'); setTimeout(() => { sc.scrollLeft = (sc.scrollWidth - sc.clientWidth) / 2; sc.scrollTop = (sc.scrollHeight - sc.clientHeight) / 2; }, 300);
});
$('zplus').addEventListener('click', () => { zl = Math.min(8, zl * 1.5); zset(); });
$('zminus').addEventListener('click', () => { zl = Math.max(1, zl / 1.5); zset(); });
$('zclose').addEventListener('click', () => { $('zoomOv').hidden = true; });

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

const SAVED = (() => { try { return JSON.parse(sessionStorage.getItem(SKEY) || 'null'); } catch (e) { return null; } })();
show(1); board();

(async function restore() {                                     // gespeicherten Zustand wiederherstellen
  const st = SAVED;
  if (!st || !st.img) return;
  const keep = st;                                              // board()/show() speichern zwischendurch, deshalb Kopie
  Object.entries(keep.sel || {}).forEach(([id, v]) => { const el = $(id); if (el && [...el.options].some(o => o.value === v)) el.value = v; });
  if (keep.sel && keep.sel.thick) threadTouched = true;
  $('bgcut').checked = !!keep.bgcut;
  const v = keep.view && keep.imgScale ? { x: keep.view.x, y: keep.view.y, s: keep.view.s / keep.imgScale } : null;
  await useImage(keep.img, v);
  board(); show(keep.step || 1);
  try { const s2 = JSON.parse(sessionStorage.getItem(SKEY) || '{}'); s2.img = keep.img; s2.imgScale = 1; s2.job = keep.job; s2.variant = keep.variant; sessionStorage.setItem(SKEY, JSON.stringify(s2)); } catch (e) {}
  if (keep.job && (keep.step || 1) === 5) resume(keep.job, keep.variant);
})();

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

