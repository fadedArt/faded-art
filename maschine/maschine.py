#!/usr/bin/env python3
"""Faded Art Spannmaschine: holt einen bezahlten Auftrag von der Website und spannt ihn.

Aufruf (auf dem Raspberry Pi neben der Maschine):
    python3 maschine.py liste            # bezahlte Aufträge anzeigen
    python3 maschine.py 3fa2c1           # Auftrag 3fa2c1 spannen (Bestellnummer = erste 6 Zeichen)
    python3 maschine.py 3fa2c1 --weiter  # nach Pause/Fadenriss an der gespeicherten Stelle weitermachen
    python3 maschine.py 3fa2c1 --trocken # nur G-Code erzeugen und Dauer schätzen, Maschine bewegt sich nicht

Mechanik (siehe Bauplan): Brett liegt auf einem Drehteller (Achse X = Drehwinkel in Grad).
Der Nadelschlitten steht still auf einer Radialschiene (Achse Y = Abstand der Nadeln von der Mitte in mm)
und hat zwei Nadeln, je Fadenfarbe eine (Achse Z = Nadel Schwarz, Achse A = Nadel Weiß, je ein RC-Servo).
Ein Faden wird so um Nagel q gelegt: Nadel hoch, Teller dreht q kurz vor die Nadel, Nadel runter,
Teller dreht ein Stück weiter (Faden legt sich um den Nagel), Nadel hoch. Die Sehne zum vorherigen Nagel entsteht dabei von selbst.

Steuerung: FluidNC (ESP32) per USB. Einstellungen der Achsen stehen in fluidnc_config.yaml.
"""
import argparse, json, math, os, sys, time, urllib.request

SITE = os.environ.get("FADED_SITE", "https://faded-art.onrender.com")   # Adresse der Website
KEY = os.environ.get("FADED_KEY", "")                                     # derselbe ADMIN_KEY wie bei Render
PORT = os.environ.get("FADED_PORT", "/dev/ttyUSB0")

# --- Maschinenwerte (beim Einrichten einmal einmessen) ---
Z_UP, Z_DOWN = 0.0, 18.0      # Servo-Stellung "Nadel oben" (über den Nagelköpfen) / "unten" (unter dem Nagelkopf), in mm
NEEDLE_OUT_MM = 6.0           # Nadel steht so weit außerhalb des Nagelkreises
WRAP_DEG = None               # Drehung beim Umschlingen; None = 0,6 x Nagelabstand
OFFSET_DEG = {"schwarz": 0.0, "weiss": 4.0}   # Winkelversatz der zweiten Nadel gegenüber der ersten (einmessen!)
NEEDLE_AXIS = {"schwarz": "Z", "weiss": "A"}
FEED_ROT = 40000              # Drehgeschwindigkeit in Grad/min (≈ 670 °/s Spitze, FluidNC begrenzt mit Beschleunigung)
FEED_Y = 3000
SAVE = os.path.expanduser("~/.faded_fortschritt.json")


def api(path, method="GET"):
    req = urllib.request.Request(f"{SITE}{path}{'&' if '?' in path else '?'}key={KEY}", method=method,
                                 data=b"" if method == "POST" else None)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def gcode(job):
    """Erzeugt den kompletten G-Code: eine Liste (Schritt-Nr, Befehle)."""
    n = job["pins"]
    pitch = 360 / n
    wrap = WRAP_DEG or 0.6 * pitch
    radius = job["ring_cm"] * 10 / 2 + NEEDLE_OUT_MM
    out = [(0, ["$H", "G21 G90", f"G1 Y{radius:.2f} F{FEED_Y}", "G1 Z0 A0 F20000"], 0.0)]   # Referenzfahrt, Nadelschlitten an den Kreis
    angle = 0.0          # Tellerwinkel 0..360 vor dem jeweiligen Schritt (für Weitermachen nach Pause)
    step = 0
    for run in job["runs"]:
        col = run["color"] if run["color"] in NEEDLE_AXIS else "schwarz"
        ax, off = NEEDLE_AXIS[col], OFFSET_DEG.get(col, 0.0)
        for q in run["pins"][1:]:
            step += 1
            # Nagel q soll knapp vor der Nadel stehen. Pin 0 sitzt auf 3 Uhr, Nummern im Uhrzeigersinn (wie in der Anleitung)
            target = (-q * pitch + off - wrap / 2) % 360
            d = (target - angle) % 360          # immer in dieselbe Richtung drehen (Faden wickelt sich sauber); X relativ (G91)
            out.append((step, [f"G90 G0 {ax}{Z_UP}", f"G91 G1 X{d:.3f} F{FEED_ROT}", f"G90 G0 {ax}{Z_DOWN}",
                               f"G91 G1 X{wrap:.3f} F{FEED_ROT}", f"G90 G0 {ax}{Z_UP}"], angle))
            angle = (target + wrap) % 360
    out.append((step + 1, ["G90 G0 Z0 A0"], angle))
    return out


def estimate(prog):
    """Grobe Dauer: Drehung mit ~300 °/s im Schnitt, je Nadelhub 0,25 s."""
    t = 0.0
    for _, cmds, _a in prog:
        for c in cmds:
            if c.startswith("G91 G1 X"):
                t += float(c.split()[2][1:]) / 300 + 0.05
            elif c.startswith("G90 G0 Z") or c.startswith("G90 G0 A"):
                t += 0.25
    return t


class Grbl:
    """Sendet Zeile für Zeile und wartet jeweils auf 'ok' (einfach und robust)."""
    def __init__(self, port):
        import serial                                    # pip install pyserial
        self.s = serial.Serial(port, 115200, timeout=2)
        time.sleep(2)
        self.s.reset_input_buffer()

    def send(self, line):
        self.s.write((line + "\n").encode())
        while True:
            r = self.s.readline().decode(errors="replace").strip()
            if r == "ok":
                return
            if r.startswith("error") or r.startswith("ALARM"):
                raise RuntimeError(f"Maschine meldet {r} bei: {line}")


def main():
    ap = argparse.ArgumentParser(description="Faded Art Spannmaschine")
    ap.add_argument("auftrag", help="'liste' oder Bestellnummer (erste 6 Zeichen)")
    ap.add_argument("--weiter", action="store_true", help="an der gespeicherten Stelle weitermachen")
    ap.add_argument("--trocken", action="store_true", help="nichts bewegen, nur prüfen")
    a = ap.parse_args()
    if not KEY:
        sys.exit("FADED_KEY fehlt (ADMIN_KEY der Website). Beispiel: export FADED_KEY=...")
    if a.auftrag == "liste":
        for o in api("/api/maschine"):
            print(f"{o['nr']}  {'gespannt' if o['done'] else 'offen   '}  {o['created']}  {o['title']}")
        return
    job = api(f"/api/maschine/{a.auftrag}")
    prog = gcode(job)
    print(f"Auftrag {job['nr']}: {job['title']}")
    print(f"{job['pins']} Nägel, Kreis {job['ring_cm']} cm, {job['lines']} Fäden, "
          f"{sum(1 for r in job['runs'])} Abschnitte, Farben: {', '.join(sorted({r['color'] for r in job['runs']}))}")
    print(f"Geschätzte Dauer: {estimate(prog) / 3600:.1f} Stunden")
    if a.trocken:
        with open(f"auftrag_{job['nr']}.gcode", "w") as f:
            f.write("\n".join(c for _, cmds, _a in prog for c in cmds))
        print(f"G-Code gespeichert: auftrag_{job['nr']}.gcode")
        return
    start = 0
    if a.weiter and os.path.exists(SAVE):
        sv = json.load(open(SAVE))
        if sv.get("ref") == job["ref"]:
            start = sv["schritt"]
            print(f"Weiter ab Faden {start}")
    input("Brett mit Nägeln auf den Teller legen, Nagel 0 auf die Markierung (3 Uhr), Fäden an den Startnägeln "
          "befestigen. Dann Enter drücken: Abfahrt.")
    m = Grbl(PORT)
    t0 = time.time()
    try:
        for c in prog[0][1]:                                  # Referenzfahrt
            m.send(c)
        for s, cmds, ang in prog[1:]:
            if s <= start:
                continue
            if start and s == start + 1:                      # nach Pause: Teller auf den Winkel von damals drehen
                m.send(f"G91 G1 X{ang:.3f} F{FEED_ROT}")
            for c in cmds:
                m.send(c)
            if s and s % 50 == 0:
                json.dump({"ref": job["ref"], "schritt": s}, open(SAVE, "w"))
                done = s / max(1, job["lines"])
                rest = (time.time() - t0) / max(1e-6, (s - start) / max(1, job["lines"])) * (1 - done) if s > start else 0
                print(f"\r{s}/{job['lines']} Fäden ({100 * done:.0f} %), noch ca. {rest / 60:.0f} min   ", end="", flush=True)
    except KeyboardInterrupt:
        print("\nPause. Weitermachen mit: python3 maschine.py", job["nr"], "--weiter")
        return
    except RuntimeError as e:
        print("\nFehler:", e, "\nFaden prüfen, dann: python3 maschine.py", job["nr"], "--weiter")
        return
    print("\nFertig! Fäden an den letzten Nägeln verknoten.")
    try:
        api(f"/api/maschine/{job['ref']}/fertig", "POST")
    except Exception:
        pass
    if os.path.exists(SAVE):
        os.remove(SAVE)


if __name__ == "__main__":
    main()
