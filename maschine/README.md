# Faded-Art-Spannmaschine

Holt bezahlte Aufträge von der Website und spannt Schwarz + Weiß (oder einfarbig) auf ein vorgenageltes Brett.

## Ablauf
1. Brett mit Nägeln (Bohrschablone für 240/360 Nägel) auf den Drehteller legen, Nagel 0 auf die Markierung (3 Uhr), Klemmen zu.
2. Fäden an den Startnägeln festknoten (zeigt das Programm an).
3. Auf dem Raspberry Pi: `python3 maschine.py liste`, dann `python3 maschine.py 3fa2c1` (erste 6 Zeichen der Bestellnummer).
4. Nach ca. 4–6 Stunden fertig. Fäden verknoten, Website wird automatisch als „gespannt“ markiert.

Pause: Strg+C. Weitermachen: `python3 maschine.py 3fa2c1 --weiter`. Probelauf ohne Bewegung: `--trocken`.

## Einrichten
- `pip install pyserial` auf dem Pi
- `export FADED_KEY=<ADMIN_KEY von Render>`, `export FADED_SITE=https://<deine-seite>.onrender.com`
- FluidNC auf das ESP32-Board flashen, `fluidnc_config.yaml` hochladen, Pins an das Board anpassen
- In `maschine.py` einmessen: `Z_UP`, `Z_DOWN`, `NEEDLE_OUT_MM`, `OFFSET_DEG["weiss"]`

## Schnittstelle der Website (nur mit ADMIN_KEY)
- `GET /api/maschine?key=…` – bezahlte Aufträge
- `GET /api/maschine/<nr>?key=…` – Nägel, Kreisdurchmesser, Abschnitte (Farbe + Nagelfolge)
- `POST /api/maschine/<ref>/fertig?key=…` – als gespannt markieren

## Offene Punkte (Prototyp)
- Umschlingen mit stehender Nadel und drehendem Brett ist das Prinzip der bekannten Dreh-Maschinen; Nadelhöhe, Fadenspannung und
  das Verhalten des geparkten zweiten Fadens müssen am Prototyp ausprobiert werden.
- Nägel setzt die Maschine nicht. Dafür eine gelaserte Bohrschablone je Größe verwenden.
