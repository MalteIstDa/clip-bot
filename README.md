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

## KI für Titel, Captions und Hashtags

Unter **Einstellungen → KI für Texte** wählst du, wer die Texte schreibt:

- **Aus:** Titel und Captions kommen wie bisher aus den Vorlagen des Kanals.
- **Claude:** Schlüssel auf console.anthropic.com anlegen und in `.env` als `ANTHROPIC_API_KEY=...` eintragen.
- **Ollama (kostenlos, lokal):** Ollama von ollama.com installieren, im Terminal `ollama pull qwen3:8b` ausführen.

Mit **KI testen** prüfst du nach dem Speichern, ob die Verbindung klappt.

## Viralere Clips (mit KI)

Ist unter „KI für Texte“ Claude oder Ollama gewählt, macht der Bot pro Video Folgendes:

1. Er sucht mehr mögliche Stellen (Einstellung „so viele Stellen je Video bewerten“, Standard 6) und lässt die KI sie nach Viral-Potenzial bewerten. Die besten werden geschnitten. Den Grund schreibt er ins Protokoll.
2. Die KI schreibt Titel, Captions und Hashtags **je Plattform** (YouTube, TikTok, Instagram). Die Caption enthält immer den Credit aus den Kanal-Einstellungen.
3. In den ersten Sekunden steht oben eine Hook-Zeile im Bild (abschaltbar unter „Untertitel“).

Änderst du Titel oder Beschreibung eines Clips im Dashboard, gilt deine Fassung für alle Plattformen.

## Freigabe vor dem Hochladen

Neue Clips landen unter **Freigabe** im Dashboard. Dort siehst du Video, Hook-Zeile, die Begründung der KI und die Texte je Plattform (umschalten über YouTube / TikTok / Instagram) und kannst sie ändern.

- **Freigeben:** Der Clip wird beim nächsten Upload-Lauf hochgeladen (Knopf „Offene Clips hochladen“ oder `auto.bat`).
- **Freigeben und hochladen:** sofort hochladen.
- **Neu texten (KI):** Titel, Captions und Hashtags neu schreiben lassen.
- **Verwerfen:** Der Clip bleibt unter „Clips“, wird aber nie hochgeladen.

Willst du ohne Prüfung posten, setze `require_approval: false` in der `config.yaml`.

## Erklärvideos (KI, animiert)

ClipBot kann eigene deutsche Erklärvideos bauen, z. B. „Was passiert, wenn du Plastik verschluckst?“.

1. Unter **Kanäle** einen Kanal mit der Art **„Eigene Erklärvideos“** anlegen (keine Quelladresse nötig) und die Konten verbinden.
2. Unter **Einstellungen** eine KI wählen (Claude oder Ollama) und bei „Erklärvideos“ die Stimme einstellen.
   - **ElevenLabs:** `ELEVENLABS_API_KEY` in `.env` eintragen (elevenlabs.io → Profil → API Keys). Die Voice-ID findest du in deiner Stimmen-Bibliothek.
   - **Stumm:** kostenlose Vorschau ohne Sprecher, um Aussehen und Ablauf zu prüfen.
3. **Node.js 22 oder neuer** installieren (nodejs.org). hyperframes wird beim ersten Video automatisch über `npx` geladen.
4. Im Bereich **Erklärvideos** auf „Trends suchen“ klicken: ClipBot zeigt die meistgesehenen kurzen Erklärvideos dieses Monats auf YouTube und lässt die KI daraus eigene Themen ableiten. Eine Idee übernehmen oder ein eigenes Thema eintippen und „Video erstellen“ klicken.

Länge: Die Videos sind standardmäßig 65 bis 90 Sekunden lang (Einstellungen → Erklärvideos), weil z. B. TikTok nur Videos über einer Minute vergütet. Ist ein Skript zu kurz, lässt der Bot es einmal ausbauen. Bleibt das fertige Video trotzdem unter einer Minute, steht eine Warnung in der Freigabe.

Ablauf: Skript (KI) → Faktencheck (zweiter KI-Durchgang; bei Fehlern wird das Skript korrigiert) → Stimme mit Wortzeiten → Animation mit hyperframes (1080x1920) → das Video wartet unter **Freigabe**. Dort steht das Ergebnis des Faktenchecks. Lies es, bevor du freigibst, besonders bei Gesundheit und Politik.
