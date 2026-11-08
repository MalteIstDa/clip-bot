# ClipBot unter Windows einrichten

Diese Anleitung ist für einen Windows-PC (Windows 10 oder 11). Du brauchst keine Vorkenntnisse. Der erste Start dauert 5 bis 15 Minuten, weil ClipBot dabei alles selbst installiert.

## 1. ClipBot herunterladen

1. Öffne https://github.com/MalteIstDa/clip-bot
2. Klicke auf den grünen Knopf **Code** und dann auf **Download ZIP**.
3. **Wichtig, bevor du entpackst:** Rechtsklick auf die heruntergeladene ZIP-Datei → **Eigenschaften** → unten bei „Sicherheit“ **Zulassen** ankreuzen → **OK**. Sonst blockiert Windows später den Start.
4. Rechtsklick auf die ZIP-Datei → **Alle extrahieren…** und als Ziel z. B. `Dokumente\ClipBot` wählen.

Im Ordner liegen jetzt unter anderem `ClipBot.bat`, `app.py` und `.env.example`.

## 2. Eigene Schlüssel eintragen (.env)

ClipBot läuft mit **deinen eigenen** Konten und Schlüsseln. Gib diese Datei niemals weiter und lade sie nirgends hoch.

1. Öffne `.env.example` mit dem **Editor** (Rechtsklick → Öffnen mit → Editor).
2. Trage hinter dem `=` ein, was du brauchst (Leerzeilen dürfen leer bleiben):
   - `ANTHROPIC_API_KEY=` Schlüssel von console.anthropic.com, nur nötig, wenn die KI-Texte von Claude kommen sollen. Kostenlose Alternative ohne Schlüssel: Ollama (siehe README).
   - `ELEVENLABS_API_KEY=` von elevenlabs.io, nur für Erklärvideos (Sprecherstimme).
   - `TIKTOK_…` und `IG_TOKEN_…` nur, wenn du auf TikTok bzw. Instagram hochladen willst.
3. **Datei → Speichern unter…**, unten bei „Dateityp“ **Alle Dateien (\*.\*)** wählen und als Dateiname genau `.env` eintippen. Speichern im ClipBot-Ordner.
   (Ohne „Alle Dateien“ macht der Editor daraus `.env.txt`, dann findet ClipBot die Datei nicht.)

Für YouTube-Uploads kommt zusätzlich die Datei `client_secret.json` von Google in den ClipBot-Ordner. Wie du die bekommst, steht im README unter „YouTube“.

Du kannst diesen Schritt auch erst einmal überspringen: Clips erzeugen und ansehen geht ohne Schlüssel.

## 3. Starten

Doppelklick auf **ClipBot.bat**.

- Meldet Windows „Der Computer wurde durch Windows geschützt“: auf **Weitere Informationen** → **Trotzdem ausführen** klicken.
- Beim ersten Start installiert ClipBot automatisch Python, ffmpeg und Node.js (über winget, das bei Windows 10/11 dabei ist) und danach die Python-Pakete. Fragt Windows nach Erlaubnis für eine Installation, bestätige mit **Ja**. Lass das schwarze Fenster offen, bis sich der Browser öffnet.
- Danach öffnet sich das Dashboard im Browser unter http://127.0.0.1:8765. Es läuft nur auf deinem PC und ist von außen nicht erreichbar.
- Auf dem Desktop liegt jetzt eine Verknüpfung **ClipBot**. Ab jetzt reicht ein Doppelklick darauf.

**Beenden:** im Dashboard unten links auf **ClipBot beenden**, oder das Fenster „ClipBot“ in der Taskleiste schließen.

## 4. Erste Schritte im Dashboard

1. Unter **Kanäle** einen Quellkanal suchen und anlegen.
2. Unter **Übersicht** auf **Clips erzeugen** klicken. Dabei wird nichts hochgeladen.
3. Die Clips unter **Clips** ansehen. Hochgeladen wird erst, wenn du sie unter **Freigabe** freigibst.

Mehr zu allen Funktionen steht im [README](README.md).

## Wenn etwas nicht geht

- **Das Fenster meldet einen Fehler:** Die Meldung nennt meistens schon die Lösung. Alle Details stehen in `data\clipbot.log` im ClipBot-Ordner. Schau vor dem Weiterschicken kurz hinein, ob dort ein Schlüssel steht.
- **„winget fehlt“:** Im Microsoft Store **App Installer** installieren und ClipBot.bat erneut starten. Oder Python 3.12 von python.org (beim Installieren „Add python.exe to PATH“ ankreuzen) und ffmpeg mit `winget install Gyan.FFmpeg` von Hand installieren.
- **Python wurde gerade installiert, wird aber nicht gefunden:** Einmal ab- und wieder anmelden (oder neu starten) und ClipBot.bat erneut starten.
- **Alles neu einrichten:** den Ordner `.venv` im ClipBot-Ordner löschen und ClipBot.bat starten.

## ClipBot aktualisieren

Neue ZIP herunterladen (wie in Schritt 1) und entpacken. Aus dem alten Ordner kopierst du diese Dateien und Ordner in den neuen: `.env`, `client_secret.json`, `config.yaml`, `tokens`, `data`. Danach den alten Ordner löschen und die Desktop-Verknüpfung löschen; beim nächsten Start von ClipBot.bat wird sie neu angelegt.
