# ClipBot

ClipBot holt die neuesten Videos deiner Quellkanäle, findet die spannendsten Stellen, schneidet daraus 60–120 Sekunden lange Clips im Hochformat mit Untertiteln und lädt sie auf YouTube, TikTok und Instagram hoch.

## Dashboard

Ein Doppelklick auf `start.bat` (Windows) bzw. `./start.sh` (Mac/Linux) öffnet das Dashboard im Browser unter `http://127.0.0.1:8765`. Es läuft nur auf deinem PC und ist von außen nicht erreichbar. Das Programmfenster muss dabei offen bleiben.

- **Übersicht:** Läufe starten (nur Clips erzeugen, offene Clips hochladen oder beides), ein einzelnes Video per Adresse clippen, Protokoll mitlesen.
- **Clips:** alle Clips nach Quellvideo sortiert. Ein Klick öffnet den Clip zum Ansehen, Titel und Beschreibung ändern, einzeln je Plattform hochladen oder löschen.
- **Kanäle:** Kanäle auf YouTube suchen (nach Name oder nach den meistgesehenen Videos der letzten 7 Tage zu einem Thema), mit einem Klick als Quellkanal anlegen, bearbeiten und Upload-Konten verbinden.
- **Einstellungen:** Clip-Länge, Bildaufbau, Untertitel, Spracherkennung und Upload-Regeln.

Für Läufe ohne Oberfläche (z. B. über die Windows-Aufgabenplanung alle 6 Stunden) gibt es `auto.bat`. Solange in den Einstellungen „Zeitgesteuerte Läufe über auto.bat laden nichts hoch“ aktiv ist, erzeugt `auto.bat` nur Clips.

## Einmalige Einrichtung

1. **Python 3.10+** und **ffmpeg** installieren (Windows: `winget install Python.Python.3.12` und `winget install Gyan.FFmpeg`).
2. `start.bat` starten. Beim ersten Mal werden die Pakete installiert, danach öffnet sich das Dashboard.
3. Unter „Kanäle“ den Quellkanal anlegen und unter „Übersicht“ auf „Clips erzeugen“ klicken. Dabei wird nichts hochgeladen.
4. Clips unter „Clips“ ansehen, bei Bedarf die Einstellungen anpassen.
5. Für Uploads die Entwickler-Apps einrichten (siehe unten) und die Konten unter „Kanäle“ verbinden. Für YouTube genügt die Datei `client_secret.json` im ClipBot-Ordner; für TikTok und Instagram `.env.example` → `.env` kopieren und die Zugangsdaten eintragen.

Passwörter gehören **nicht** in die Konfiguration. Du meldest dich einmal im Browser direkt bei Google bzw. TikTok an; der Bot speichert nur die Zugriffstoken im Ordner `tokens/`.

### YouTube

- In der Google Cloud Console ein Projekt anlegen, „YouTube Data API v3“ aktivieren, OAuth-Client vom Typ „Desktop-App“ erstellen, JSON als `client_secret.json` in den Projektordner legen.
- Solange die Google-App im Status „Test“ ist, dein eigenes Google-Konto im OAuth-Zustimmungsbildschirm als Testnutzer eintragen.
- Im Dashboard unter „Kanäle“ auf „Verbinden“ klicken und im Browser mit dem passenden Kanal anmelden.
- **Wichtig:** Uploads aus ungeprüften API-Projekten sperrt YouTube auf „privat“. Öffentlich wird es erst nach dem Compliance-Audit des Projekts (Formular „YouTube API Services – Audit and Quota Extension“).

### TikTok

- Auf developers.tiktok.com eine App anlegen, „Login Kit“ und „Content Posting API“ mit „Direct Post“ hinzufügen, Scope `video.publish` beantragen, Redirect-URI eintragen.
- `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REDIRECT_URI` in `.env` eintragen.
- Im Dashboard unter „Kanäle“ auf „Verbinden“ klicken, bei TikTok zustimmen und die Adresse der Zielseite einfügen.
- **Wichtig:** Solange die App nicht von TikTok auditiert ist, landen alle Posts nur privat („Nur ich“), das Konto muss beim Posten auf privat stehen und es sind maximal 5 Konten pro 24 Stunden möglich.

### Instagram

- Das Konto muss ein Business- oder Creator-Konto sein und mit einer Facebook-Seite verknüpft sein.
- Auf developers.facebook.com eine App anlegen, Berechtigungen `instagram_basic` und `instagram_content_publish` hinzufügen, langlebigen Token erzeugen (60 Tage gültig, danach erneuern).
- Token in `.env` eintragen, Instagram-User-ID und Namen der Token-Variable im Dashboard beim Kanal eintragen, dann „Token prüfen“.
- Limit: 100 per API veröffentlichte Posts pro 24 Stunden und Konto.

## Wenn etwas nicht geht

- **Windows blockiert `start.bat` (Smart App Control):** Rechtsklick auf die heruntergeladene ZIP-Datei → Eigenschaften → unten „Zulassen“ ankreuzen → OK, dann neu entpacken.
- **Es entstehen keine Clips:** Das Protokoll unter „Übersicht“ nennt den Grund. Übersprungen werden Videos unter 150 Sekunden, Livestreams und Videos über der Höchstlänge aus den Einstellungen. Endet ein Lauf mit „Mit Fehlern beendet“, steht die Ursache in der roten Zeile darüber.
- **Clips erscheinen erst, wenn sie fertig gerendert sind.** Das dauert je nach PC einige Minuten pro Clip; beim ersten Lauf wird zusätzlich das Sprachmodell geladen.

## Ohne Dashboard

| Befehl | Wirkung |
| --- | --- |
| `auto.bat` | kompletter Lauf für alle Kanäle |
| `auto.bat --channel n3on` | nur ein Kanal |
| `auto.bat --dry-run` | Clips erzeugen, nichts hochladen |
| `auto.bat --upload-only` | nur bereits fertige Clips hochladen |

## Rechte

Lade nur Clips von Kanälen hoch, deren Inhaber das erlauben (z. B. über ein offizielles Clipper-Programm des Streamers). Ohne Erlaubnis drohen Urheberrechts-Strikes und Kontosperrungen; außerdem prüfen TikTok und YouTube beim Audit, was die App hochlädt.
