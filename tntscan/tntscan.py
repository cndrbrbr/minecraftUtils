#!/usr/bin/env python3
"""
tntscan.py - findet TNT (oder andere Bloecke) in Minecraft-Welten, direkt in den
Region-Dateien (*.mca), ohne den Server zu belasten und ohne etwas zu veraendern.

Aufruf:
    python3 tntscan.py <ordner>                 # TNT-Bloecke + TNT-Loren suchen
    python3 tntscan.py <ordner> --block minecraft:lava --no-entities
    python3 tntscan.py <ordner> --gap 32 --top 50

<ordner> ist ein Weltordner oder ein Ordner, der mehrere Welten enthaelt
(z. B. /server/data/worlds). Darunter werden alle Ordner namens "region"
(Bloecke) und "entities" (Objekte wie TNT-Loren) durchsucht - also auch Nether,
End und Multiverse-Welten.

Ausgabe je Dimension: Gesamtzahl und die groessten Haeufungen ("Cluster") mit
Anzahl und Bereich (x/z in Blockkoordinaten, auf Chunkgrenzen gerundet).

Nur Python-Standardbibliothek, getestet mit Minecraft/Spigot 26.3 (laeuft auch
mit aelteren Formaten ab 1.18).

Hintergrund zum Dateiformat (stark vereinfacht):
  * Eine Region-Datei r.<rx>.<rz>.mca enthaelt bis zu 32x32 Chunks.
    Kopf: 1024 Eintraege a 4 Byte = Position des Chunks in der Datei
    (3 Byte Offset in 4-KiB-Sektoren + 1 Byte Laenge), danach 1024 Zeitstempel.
  * Jeder Chunk ist NBT (Minecrafts Binaerformat), meist zlib-komprimiert.
  * Ein Chunk besteht aus "sections" (16x16x16 Bloecke). Jede Section hat eine
    "palette" (Liste der vorkommenden Blocktypen) und "data" (gepackte Indizes
    in diese Palette, 4096 Stueck).
  * Achtung 26.x: Palette-Eintraege kommen in drei Formen vor - als einfacher
    String, als Compound {Name: ..., Properties: ...} und als Compound mit einem
    leeren Schluesselnamen {"": "minecraft:..."}. Alle drei werden unterstuetzt.
"""
import argparse
import gzip
import io
import os
import struct
import sys
import zlib


# --------------------------------------------------------------------------
# Minimaler NBT-Leser
# --------------------------------------------------------------------------
# NBT-Tag-Typen: 1 byte, 2 short, 3 int, 4 long, 5 float, 6 double,
# 7 byte[], 8 string, 9 list, 10 compound, 11 int[], 12 long[].
# Byte- und Int-Arrays werden nur uebersprungen (brauchen wir nicht),
# Long-Arrays werden gelesen, weil darin die Blockdaten stecken.

def read_tag(f, tag_type):
    """Liest den Wert eines Tags vom Typ tag_type aus dem Stream f."""
    r = f.read
    if tag_type == 1: return struct.unpack('>b', r(1))[0]
    if tag_type == 2: return struct.unpack('>h', r(2))[0]
    if tag_type == 3: return struct.unpack('>i', r(4))[0]
    if tag_type == 4: return struct.unpack('>q', r(8))[0]
    if tag_type == 5: return struct.unpack('>f', r(4))[0]
    if tag_type == 6: return struct.unpack('>d', r(8))[0]
    if tag_type == 7:                                   # byte[] - ueberspringen
        n = struct.unpack('>i', r(4))[0]; r(n); return None
    if tag_type == 8:
        n = struct.unpack('>H', r(2))[0]
        return r(n).decode('utf8', 'replace')
    if tag_type == 9:                                   # Liste: Elementtyp + Anzahl
        elem_type = r(1)[0]
        n = struct.unpack('>i', r(4))[0]
        return [read_tag(f, elem_type) for _ in range(n)]
    if tag_type == 10:                                  # Compound bis Tag 0 (End)
        d = {}
        while True:
            t = r(1)[0]
            if t == 0:
                return d
            n = struct.unpack('>H', r(2))[0]
            name = r(n).decode('utf8', 'replace')
            d[name] = read_tag(f, t)
    if tag_type == 11:                                  # int[] - ueberspringen
        n = struct.unpack('>i', r(4))[0]; r(4 * n); return None
    if tag_type == 12:                                  # long[] - Blockdaten
        n = struct.unpack('>i', r(4))[0]
        return list(struct.unpack('>%dq' % n, r(8 * n)))
    raise ValueError('unbekannter NBT-Typ %d' % tag_type)


def read_root(raw):
    """Liest das Wurzel-Compound eines (bereits entpackten) Chunks."""
    f = io.BytesIO(raw)
    tag_type = f.read(1)[0]
    name_len = struct.unpack('>H', f.read(2))[0]
    f.read(name_len)                                    # Name des Wurzel-Tags
    return read_tag(f, tag_type)


# --------------------------------------------------------------------------
# Bloecke zaehlen
# --------------------------------------------------------------------------

def palette_name(entry):
    """Blockname aus einem Palette-Eintrag (alle drei Formen, siehe oben)."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return entry.get('Name') or entry.get('')
    return None


def count_in_section(section, names):
    """Zaehlt, wie viele der 4096 Bloecke einer Section einen der Namen haben."""
    states = section.get('block_states') or {}
    palette = states.get('palette') or []
    wanted = {i for i, p in enumerate(palette) if palette_name(p) in names}
    if not wanted:
        return 0
    if len(palette) == 1:                   # ganze Section besteht aus diesem Block
        return 4096
    # Die 4096 Palette-Indizes sind in 64-Bit-Zahlen gepackt. Bits pro Index:
    # mindestens 4, sonst so viele, wie fuer die Palettengroesse noetig.
    # Seit 1.16 werden Indizes nicht ueber Long-Grenzen verteilt, die
    # restlichen Bits einer Long bleiben ungenutzt.
    bits = max(4, (len(palette) - 1).bit_length())
    per_long = 64 // bits
    mask = (1 << bits) - 1
    count = 0
    done = 0
    for value in states.get('data') or []:
        value &= (1 << 64) - 1              # Java-long ist vorzeichenbehaftet
        for _ in range(per_long):
            if done >= 4096:
                break
            if (value & mask) in wanted:
                count += 1
            value >>= bits
            done += 1
    return count


# --------------------------------------------------------------------------
# Region-Dateien lesen
# --------------------------------------------------------------------------

def iter_chunks(path):
    """Liefert (index, entpackte Chunkdaten) fuer jeden Chunk einer .mca-Datei.

    Die Datei wird einmal komplett gelesen und nicht veraendert. Laeuft der
    Server dabei, kann ein Chunk gerade halb geschrieben sein - solche Chunks
    werden einfach uebersprungen."""
    with open(path, 'rb') as fh:
        data = fh.read()
    if len(data) < 8192:                    # leere oder kaputte Datei
        return
    for i in range(1024):
        loc = struct.unpack('>I', data[i * 4:i * 4 + 4])[0]
        if not loc:                         # Chunk nie erzeugt
            continue
        offset = (loc >> 8) * 4096
        try:
            length, compression = struct.unpack('>IB', data[offset:offset + 5])
            payload = data[offset + 5:offset + 4 + length]
            if compression == 2:
                raw = zlib.decompress(payload)
            elif compression == 1:
                raw = gzip.decompress(payload)
            elif compression == 3:          # unkomprimiert
                raw = payload
            else:                           # z. B. LZ4 oder externe .mcc-Datei
                continue
        except Exception:
            continue
        yield i, raw


def scan_region_dir(directory, kind, block_names, entity_ids):
    """Durchsucht einen region- oder entities-Ordner.

    Gibt eine Liste (x, z, anzahl) je Chunk mit Treffern zurueck."""
    # Schneller Vorfilter: nur Chunks, in deren Rohdaten einer der Namen
    # ueberhaupt vorkommt, werden vollstaendig als NBT gelesen.
    needles = [n.encode() for n in (block_names if kind == 'blocks' else entity_ids)]
    hits = []
    for filename in sorted(os.listdir(directory)):
        if not filename.endswith('.mca'):
            continue
        try:
            _, rx, rz, _ = filename.split('.')
            rx, rz = int(rx), int(rz)
        except ValueError:
            continue
        for i, raw in iter_chunks(os.path.join(directory, filename)):
            if not any(n in raw for n in needles):
                continue
            chunk_x = rx * 32 + i % 32
            chunk_z = rz * 32 + i // 32
            try:
                chunk = read_root(raw)
            except Exception:
                continue
            if kind == 'blocks':
                n = sum(count_in_section(s, block_names)
                        for s in (chunk.get('sections') or []))
            else:
                n = sum(1 for e in (chunk.get('Entities') or [])
                        if isinstance(e, dict) and e.get('id') in entity_ids)
            if n:
                hits.append((chunk_x * 16, chunk_z * 16, n))
    return hits


def cluster(hits, gap):
    """Fasst Treffer-Chunks zu Haeufungen zusammen.

    Ein Chunk gehoert zu einer Haeufung, wenn er hoechstens <gap> Bloecke
    von deren bisherigem Rechteck entfernt ist. Einfach und schnell, fuer
    einen Ueberblick voellig ausreichend."""
    groups = []
    for x, z, n in sorted(hits):
        for g in groups:
            if g['x0'] - gap <= x <= g['x1'] + gap and g['z0'] - gap <= z <= g['z1'] + gap:
                g['x0'] = min(g['x0'], x); g['x1'] = max(g['x1'], x + 15)
                g['z0'] = min(g['z0'], z); g['z1'] = max(g['z1'], z + 15)
                g['n'] += n
                break
        else:
            groups.append(dict(x0=x, x1=x + 15, z0=z, z1=z + 15, n=n))
    return sorted(groups, key=lambda g: -g['n'])


# --------------------------------------------------------------------------
# Hauptprogramm
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description='Findet TNT (oder andere Bloecke) in Minecraft-Weltdateien (nur lesend).')
    ap.add_argument('folder', help='Weltordner oder Ordner mit mehreren Welten')
    ap.add_argument('--block', action='append', metavar='NAME',
                    help='gesuchter Blocktyp, mehrfach moeglich (Standard: minecraft:tnt)')
    ap.add_argument('--entity', action='append', metavar='ID',
                    help='gesuchter Objekttyp, mehrfach moeglich (Standard: minecraft:tnt_minecart)')
    ap.add_argument('--no-entities', action='store_true', help='entities-Ordner nicht durchsuchen')
    ap.add_argument('--gap', type=int, default=64,
                    help='max. Abstand in Bloecken, ab dem Treffer getrennte Haeufungen sind (Standard 64)')
    ap.add_argument('--top', type=int, default=30, help='so viele Haeufungen je Dimension zeigen (Standard 30)')
    args = ap.parse_args()

    block_names = set(args.block or ['minecraft:tnt'])
    # Kurzform ohne "minecraft:" ebenfalls akzeptieren (kommt in Paletten vor)
    block_names |= {n.split(':', 1)[1] for n in block_names if n.startswith('minecraft:')}
    entity_ids = set(args.entity or ['minecraft:tnt_minecart'])

    if not os.path.isdir(args.folder):
        sys.exit('Ordner nicht gefunden: %s' % args.folder)

    found_any = False
    for dirpath, _dirnames, files in os.walk(args.folder):
        base = os.path.basename(dirpath)
        if base not in ('region', 'entities'):
            continue
        if base == 'entities' and args.no_entities:
            continue
        if not any(f.endswith('.mca') for f in files):
            continue
        found_any = True
        kind = 'blocks' if base == 'region' else 'entities'
        hits = scan_region_dir(dirpath, kind, block_names, entity_ids)
        total = sum(h[2] for h in hits)
        label = 'Bloecke' if kind == 'blocks' else 'Objekte'
        print('== %s  [%s]  gesamt %d' % (os.path.relpath(dirpath, args.folder), label, total), flush=True)
        for g in cluster(hits, args.gap)[:args.top]:
            print('   %8d  x %d..%d  z %d..%d' % (g['n'], g['x0'], g['x1'], g['z0'], g['z1']), flush=True)

    if not found_any:
        sys.exit('Keine region-/entities-Ordner mit .mca-Dateien unter %s gefunden.' % args.folder)


if __name__ == '__main__':
    main()
