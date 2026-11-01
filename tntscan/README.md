# tntscan

Findet TNT in Minecraft-Welten – oder jeden anderen Blocktyp. Das Skript liest
die Region-Dateien (`*.mca`) direkt von der Festplatte. Es braucht also weder
einen Spieler vor Ort noch WorldEdit, belastet den Server nicht und **verändert
nichts**.

Entstanden ist es, als auf einem Survival-Server eine TNT-Pyramide mit über
166.000 Blöcken 600.000 Blöcke vom Spawn entfernt auftauchte. WorldEdit kann
dort nur in kleinen Bereichen zählen und bringt den Server dabei ins Stocken.
`tntscan` durchsucht dagegen eine ganze Welt (13 GB) in wenigen Minuten.

## Was es findet

- **TNT-Blöcke** in allen Dimensionen (Overworld, Nether, End) und in allen
  Welten unterhalb des angegebenen Ordners, also auch Multiverse-Welten
- **TNT-Loren** (`minecraft:tnt_minecart`) aus den `entities`-Ordnern
- auf Wunsch andere Blöcke oder Objekte (`--block`, `--entity`)

Die Treffer werden zu Häufungen zusammengefasst. Für jede Häufung gibt das
Skript die Anzahl und den Bereich in Blockkoordinaten aus.

## Voraussetzungen

- Python 3.8 oder neuer, nur die Standardbibliothek
- Lesezugriff auf den Weltordner
- getestet mit Spigot 26.3, funktioniert mit dem Weltformat ab 1.18

## Benutzung

```bash
# alle Welten eines Servers
python3 tntscan.py /pfad/zum/server/worlds

# nur eine Welt
python3 tntscan.py /pfad/zum/server/worlds/world

# anderer Block, ohne Objekte
python3 tntscan.py /pfad/zur/welt --block minecraft:lava --no-entities

# mehrere Blöcke, feinere Häufungen, mehr Zeilen
python3 tntscan.py /pfad/zur/welt --block minecraft:tnt --block minecraft:end_crystal --gap 32 --top 50
```

Bei einem laufenden Server ist das gefahrlos, weil nur gelesen wird. Chunks,
die der Server gerade schreibt, werden übersprungen. Bei sehr großen Welten
empfiehlt sich `nice -n 15 ionice -c3 python3 tntscan.py …`, damit der Server
Vorrang behält.

Mit Docker liegt der Weltordner meist in einem Volume, zum Beispiel
`/var/lib/docker/volumes/<projekt>_mc4_data/_data/data/worlds`.

| Option | Bedeutung | Standard |
|---|---|---|
| `--block NAME` | gesuchter Blocktyp, mehrfach möglich | `minecraft:tnt` |
| `--entity ID` | gesuchter Objekttyp, mehrfach möglich | `minecraft:tnt_minecart` |
| `--no-entities` | `entities`-Ordner auslassen | aus |
| `--gap N` | Treffer, die weiter als N Blöcke auseinanderliegen, werden getrennte Häufungen | 64 |
| `--top N` | so viele Häufungen je Dimension anzeigen | 30 |

## Ausgabe lesen

```
== world/dimensions/minecraft/overworld/region  [Bloecke]  gesamt 166714
     166650  x -1520..-1329  z 625088..625263
          9  x -8592..-8577  z -8096..-8081
          9  x -1504..-1489  z 420128..420143
          1  x -528..-513  z -8608..-8593
== world/dimensions/minecraft/overworld/entities  [Objekte]  gesamt 0
```

- Eine Zeile `==` steht für eine Dimension und gibt die Gesamtzahl an.
- Darunter folgt je Häufung die Anzahl der Blöcke sowie der x- und z-Bereich.
  Die Bereiche sind auf Chunkgrenzen (16 Blöcke) gerundet.
- **Genau 9 TNT** an einer Stelle ist fast immer die natürliche Falle unter
  einem **Wüstentempel** und kein Spieler-TNT.
- Weiter vorgehen kannst du zum Beispiel mit WorldEdit von der Konsole aus:
  `/world world`, `/pos1 x,y,z`, `/pos2 x,y,z`, dann `/count tnt` und
  `/replace tnt air`. TNT explodiert beim Ersetzen nicht.

## Wie es funktioniert

1. Alle Ordner namens `region` (Blöcke) und `entities` (Objekte) unterhalb des
   Startordners werden gesucht.
2. Jede `.mca`-Datei enthält bis zu 32×32 Chunks. Der Kopf der Datei sagt, wo
   jeder Chunk steht. Die Chunks werden einzeln entpackt (zlib).
3. **Vorfilter:** Kommt der gesuchte Name in den entpackten Rohdaten gar nicht
   vor, wird der Chunk übersprungen. Das macht das Skript schnell.
4. Treffer-Chunks werden als NBT gelesen. Jede 16×16×16-Section hat eine
   **Palette** (Liste der vorkommenden Blöcke) und gepackte Indizes. Das Skript
   entpackt die Indizes und zählt die Bloecke, deren Palette-Eintrag passt.
5. Die Treffer werden zu Häufungen zusammengefasst und ausgegeben.

Hinweis zum Format von 26.x: Palette-Einträge kommen in drei Formen vor, als
einfacher Text, als `{Name: …}` und als Compound mit leerem Schlüssel
`{"": "minecraft:…"}`. Wer nur eine Form prüft, übersieht einen Teil der Blöcke.
Eine erste Version dieses Skripts hat so rund 10 % der Pyramide übersehen.

## Grenzen

- Gezählt wird, was auf der Festplatte steht. Änderungen aus den letzten
  Minuten, die der Server noch nicht gespeichert hat, fehlen. Ein `save-all`
  vorher hilft.
- TNT in Truhen, Shulkerkisten oder Inventaren wird nicht gezählt, nur
  platzierte Blöcke und TNT-Loren.
- Das Skript sagt nicht, **wer** das TNT gesetzt hat. Das wissen nur
  Protokoll-Plugins wie BlockWatch oder CoreProtect.
