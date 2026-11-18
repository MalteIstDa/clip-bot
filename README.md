# ClipBot

ClipBot behält deine Lieblings-YouTuber und -Streamer im Blick, erinnert dich, wenn jemand live geht oder ein Video hochlädt, schneidet aus neuen Videos und Streams automatisch Clips im Hochformat mit Untertiteln, baut daraus tägliche oder wöchentliche Best-ofs und lädt alles zu festen Uhrzeiten auf deine YouTube- und TikTok-Konten (auf Wunsch auch Instagram).

## Dashboard

**Mac:** Doppelklick auf **ClipBot.app** im ClipBot-Ordner (du kannst sie auch ins Dock ziehen, die App muss aber im Ordner bleiben). Beim ersten Start richtet sie alles ein, das dauert ein paar Minuten. Beendet wird ClipBot im Dashboard unten links mit „ClipBot beenden“. Das Protokoll steht in `data/clipbot.log`. Falls macOS meldet, die App sei von einem unbekannten Entwickler: Rechtsklick auf die App → Öffnen → Öffnen. Für Untertitel braucht ClipBot ein ffmpeg mit libass. Das normale `brew install ffmpeg` hat das nicht mehr, deshalb einmal im Terminal `brew install ffmpeg-full` eingeben. ClipBot findet es von selbst. Ohne libass entstehen die Clips ohne Untertitel.

**Windows:** Doppelklick auf **ClipBot.bat**. Beim ersten Start installiert es Python, ffmpeg und Node.js (über winget) sowie die Pakete und legt eine Verknüpfung „ClipBot“ auf dem Desktop an. Bei jedem Start holt es sich außerdem die neueste Version von GitHub (`update.ps1`); eigene Dateien wie `.env`, `config.yaml`, `tokens/` und `data/` bleiben dabei erhalten. In einem Git-Checkout (z. B. GitHub Desktop) wird das übersprungen. Danach öffnet sich das Dashboard. Beendet wird ClipBot wie auf dem Mac im Dashboard oder durch Schließen des Fensters „ClipBot“ in der Taskleiste. Schritt für Schritt: [ANLEITUNG-WINDOWS.md](ANLEITUNG-WINDOWS.md).

Alternativ öffnet `start.bat` (Windows) bzw. `./start.sh` (Mac/Linux) das Dashboard im Browser unter `http://127.0.0.1:8765`. Es läuft nur auf deinem PC und ist von außen nicht erreichbar. Das Programmfenster muss dabei offen bleiben.

- **Übersicht:** Automatik ein- und ausschalten, sehen, was als Nächstes ansteht, Neuigkeiten und Protokoll mitlesen, Läufe von Hand starten.
- **Freigabe:** neue Clips, Best-ofs und Erklärvideos prüfen, Texte ändern, freigeben oder verwerfen.
- **Clips:** alle Videos nach Quelle sortiert, mit dem Upload-Stand je Konto.
- **Erklärvideos:** eigene animierte Videos aus einem Thema bauen.
- **Watchlist:** YouTuber und Streamer hinzufügen (auch viele auf einmal), Reminder und Auto-Clip je Creator schalten.
- **Live & Vault:** wer gerade mitgeschnitten wird, wie stark der Chat läuft und welche Live-Clips im Vault aufs Posten warten.
- **Konten:** Upload-Konten anlegen, verbinden und ihnen Upload-Zeiten geben.
- **Kanäle:** Creator mit Konten verknüpfen, Sprache und Best-of festlegen.
- **Einstellungen:** Automatik und Zeitplan, Creator-Tag, Best-of, Benachrichtigungen, Clip-Länge, Bild, Untertitel und KI.

Das Dashboard ist dunkel gestaltet, mit einem animierten Sternenhimmel samt Galaxie. Bremst das einen älteren Rechner, schaltest du die Bewegung unten links mit „Sterne anhalten“ ab.

## So hängt alles zusammen

- Ein **Creator** ist ein YouTuber oder Streamer auf deiner Watchlist.
- Ein **Konto** ist einer deiner Accounts auf YouTube oder TikTok, auf den hochgeladen wird.
- Ein **Kanal** verknüpft beides: aus welchen Creatorn Videos entstehen, in welcher Sprache die Texte sind und auf welche Konten sie gehen. Ein Kanal kann beliebig viele Creator und Konten haben, und ein Creator oder Konto kann zu mehreren Kanälen gehören.

Beispiel: Der Kanal `streamer_de` clippt Gtasty und Papaplatte auf Deutsch und lädt auf ein YouTube- und ein TikTok-Konto hoch. Der Kanal `streams_us` clippt drei amerikanische Streamer auf Englisch und verteilt die Clips abwechselnd auf zwei TikTok-Konten.

Eine `config.yaml` aus einer älteren Version wird beim Start automatisch umgestellt: Aus jedem alten Kanal werden ein Creator, die Konten und ein Kanal. Beim ersten Speichern legt ClipBot die Sicherung `config.yaml.vor-konten.bak` an.

## Watchlist und Reminder

Unter **Watchlist** trägst du Adressen ein, eine je Zeile, zum Beispiel `https://www.youtube.com/@Gtasty` oder `https://www.twitch.tv/papaplatte`. Ein YouTube-Name wie `@N3on` genügt auch. Über die Suche rechts findest du Kanäle auf YouTube.

ClipBot prüft die Liste alle 5 Minuten (einstellbar) und meldet sich,

- wenn jemand **live geht**,
- wenn jemand ein **Video hochlädt**,
- wenn die **Aufzeichnung eines Streams** fertig ist.

Die Reminder erscheinen im Dashboard (Glocke oben rechts und „Neuigkeiten“), als Mitteilung des Betriebssystems und auf Wunsch auf dem Handy:

- **Handy (ntfy):** die kostenlose App „ntfy“ installieren, ein Thema mit einem schwer zu erratenden Namen abonnieren (z. B. `clipbot-max-7f3k`) und denselben Namen unter Einstellungen → Benachrichtigungen eintragen. Wer den Namen kennt, kann mitlesen, deshalb keinen einfachen Namen nehmen.
- **Discord:** im gewünschten Discord-Kanal einen Webhook anlegen und die Adresse in `.env` als `DISCORD_WEBHOOK_URL=...` eintragen.

Reminder kommen nur, solange ClipBot läuft. Beim ersten Durchgang merkt sich ClipBot, was es schon gibt; gemeldet wird erst Neues. Geclippt wird von YouTube und Twitch. Bei Kick und anderen Seiten gibt es nur den Live-Reminder. Videos unter 150 Sekunden (Shorts) werden übersprungen.

## Automatik

Die Automatik schaltest du oben rechts oder unter „Übersicht“ ein. Dann erledigt ClipBot der Reihe nach, was fällig ist:

1. **Hochladen nach Zeitplan:** je Konto und Uhrzeit ein freigegebenes Video (siehe unten).
2. **Automatisch clippen:** neue Videos und fertige Stream-Aufzeichnungen aller Creator mit „Auto-Clip“, für jeden Kanal, zu dem der Creator gehört. Bei Livestreams wartet ClipBot, bis die Aufzeichnung verfügbar ist.
3. **Best-ofs bauen:** täglich oder wöchentlich für die Kanäle, bei denen das eingestellt ist.

Es läuft immer nur eine Aufgabe gleichzeitig. War die Automatik länger aus, clippt sie nichts nach, was schon mehr als drei Tage wartet. Neue Videos warten weiterhin unter **Freigabe**, bis du sie freigibst (abschaltbar unter Einstellungen → Automatik und Zeitplan). Solange dort der **Probemodus** an ist, clippt die Automatik, lädt aber nichts hoch. Der Rechner muss an sein und ClipBot laufen.

### Upload-Zeiten

Unter Einstellungen → Automatik und Zeitplan trägst du die Uhrzeiten ein, z. B. `12:00, 17:30, 20:00`. Zu jeder Uhrzeit lädt jedes Konto **ein** freigegebenes Video hoch, das älteste zuerst. Einzelne Konten können unter „Konten“ eigene Zeiten bekommen. War der Rechner zur Uhrzeit aus, holt ClipBot den Upload bis zu 60 Minuten später nach (einstellbar). „Freigeben und sofort hochladen“ und „Offene Clips hochladen“ laden unabhängig vom Zeitplan sofort hoch.

### Mehrere Konten

Lege unter **Konten** jeden deiner Accounts einmal an, verbinde ihn und ordne ihn einem oder mehreren Kanälen zu. Hat ein Kanal mehrere Konten derselben Plattform, wählst du beim Kanal:

- **Jedes Konto bekommt jeden Clip**, oder
- **Clips abwechselnd verteilen:** jeder Clip geht nur auf eines der Konten, damit nicht überall dasselbe steht.

Eine Google-App und eine TikTok-App reichen für alle deine Konten. Beim Verbinden meldest du dich jeweils mit dem passenden Account an.

## Creator-Tag oben im Video

Oben im Clip steht eine Zeile, die den Creator nennt, z. B. „Gtastys unglaublicher Stunt“. Mit KI schreibt ClipBot eine passende Zeile mit dem Namen; ohne KI steht dort der Name des Creators. Den Namen änderst du in der Watchlist über „Bearbeiten“. Unter Einstellungen → Creator-Tag legst du fest, wie lange die Zeile stehen bleibt (0 = der ganze Clip), oder schaltest sie ab.

## Best-ofs

Beim Clippen merkt sich ClipBot aus jedem Video die stärksten kurzen Momente (je 8 bis 22 Sekunden). Ist bei einem Kanal „Best-of“ auf täglich oder wöchentlich gestellt, setzt ClipBot daraus ein Video von 1 bis 2 Minuten zusammen: Momente verschiedener Creator hintereinander, über jedem steht der Name. Der stärkste Moment kommt zuerst. Mit KI wählt und betitelt die KI die Momente, ohne KI zählen Lautstärke und „Most replayed“.

Das Best-of entsteht ab der eingestellten Uhrzeit (Standard 18:00, wöchentlich am Sonntag) und wartet dann unter **Freigabe**. Mit „Best-of bauen“ unter „Übersicht“ oder beim Kanal startest du es sofort. Jeder Moment wird je Kanal nur einmal verwendet. Ein Kanal kann auch nur Best-ofs erzeugen: dann beim Kanal „Einzelne Clips aus jedem Video erzeugen“ abwählen.

## Live-Clips aus Chat-Peaks und Vault

Geht ein Creator aus der Watchlist live, schneidet ClipBot den Stream mit (es bleiben nur die letzten Minuten auf der Platte) und liest den Chat mit. Jede Nachricht zählt, Lachen („KEKW“, „LUL“, „xD“, „haha“, 😂) und Hype („W“, „Pog“, „omg“, „Clip!“) zählen mehr. Rastet der Chat im Vergleich zu den Minuten davor aus, wird die Szene davor ausgeschnitten (Standard: 40 Sekunden vor bis 12 Sekunden nach dem Peak) und wie jeder Clip mit Untertiteln und Creator-Tag gerendert.

- **Twitch:** Der Chat wird anonym gelesen, ohne Konto und ohne Schlüssel.
- **YouTube:** Der Live-Chat läuft über das Paket `chat-downloader`, eine inoffizielle Schnittstelle. Ändert YouTube etwas, kann sie ausfallen.
- **Kick und andere Seiten, oder wenn der Chat nicht lesbar ist:** ClipBot clippt nach der Lautstärke des Streams.

**Clips über 1 Minute:** Standardmäßig wird aus jedem Chat-Peak zuerst ein kurzer Moment (etwa 20 Sekunden, vor allem die Sekunden vor dem Peak, weil der Chat erst nach dem Ereignis reagiert). Hat ein Streamer genug Momente, setzt ClipBot seine stärksten zu einem Clip von 65 bis 90 Sekunden zusammen: der stärkste zuerst, der zweitstärkste am Schluss, jeder mit eigener Zeile oben. Streamer werden nicht gemischt. TikTok zahlt erst für Videos über 1 Minute. Abschalten oder die Längen ändern kannst du unter Einstellungen → „Live-Clips und Vault“.

**Spannender:** Bei Gaming sucht ClipBot die Facecam im Stream (Gesichtserkennung mit OpenCV) und zeigt sie oben groß, das Spiel darunter, der Hook sitzt auf der Kante. Ohne erkennbare Facecam bleibt das ganze Bild. An den lautesten Stellen zoomt das Bild kurz hinein (Punch-in, abschaltbar unter „Bild“). Die KI bekommt zu jedem Live-Moment mit, was der Chat beim Peak geschrieben hat, und wählt und betitelt die Momente danach.

Beschreibungen enthalten keine Quellenzeile („Clip aus …“, „Credit …“) und keinen Hashtag mit dem Namen des Creators.

Die Live-Clips landen im **Vault** des Kanals (Bereich „Live & Vault“). Sind 10 drin (einstellbar), werden sie freigegeben und sofort nacheinander auf jedes Konto des Kanals gepostet, ohne Freigabe und ohne Upload-Zeiten. Mit „Jetzt posten“ geht das auch früher; schlechte Clips löschst du vorher im Vault. Lehnt eine Plattform ab (YouTube erlaubt mit einem Google-Projekt nur etwa 6 Uploads am Tag), versucht ClipBot dieses Konto nach 6 Stunden erneut. Im Probemodus wird nichts hochgeladen.

Voraussetzungen: Die Automatik ist an, der Creator hat „Auto-Clip“ und gehört zu einem Kanal, und ClipBot läuft, solange der Stream läuft. Gleichzeitig werden höchstens 6 Streams mitgeschnitten (einstellbar). Wurde ein Stream schon live geclippt, wird seine Aufzeichnung danach nicht noch einmal geclippt, außer ein Kanal braucht sie für Best-ofs. Alles Weitere unter Einstellungen → „Live-Clips und Vault“.

## Gaming, Just Chatting und IRL

ClipBot erkennt die Art des Streams an seiner Kategorie: bei Twitch „Just Chatting“, „IRL“ oder der Name des Spiels (live wird sie alle 5 Minuten neu gelesen, Aufzeichnungen nehmen das Kapitel an der Stelle des Clips), bei YouTube die Videokategorie wie „Gaming“. Ohne Kategorie hilft der Titel („IRL durch Tokyo“, „Ranked bis Platin“).

- **Gaming:** Das ganze Spielbild bleibt sichtbar (Video mittig, Hintergrund unscharf). Der Hook nennt den Spielmoment und das Spiel, dazu Hashtags wie `#fortnite #gaming`.
- **Just Chatting:** bildfüllend auf den Streamer. Der Hook greift die Reaktion oder Aussage auf.
- **IRL:** bildfüllend. Der Hook beschreibt die Situation und den Ort.

Hook und Titel passt die KI an; ohne KI kommen nur die Hashtags zur Beschreibung dazu (außer die Vorlage enthält schon eigene). Den Bildaufbau je Art stellst du unter Einstellungen → „Format je Art des Streams“ um oder schaltest die Erkennung dort ab. Die Kategorie steht im Bereich „Live & Vault“ beim Stream und bei jedem Clip.

## Sprachen

Jeder Kanal hat eine Sprache: Deutsch, Englisch (US), Französisch oder Polnisch. In dieser Sprache entstehen Titel, Captions, Hashtags, der Creator-Tag, die Credit-Zeile und die Texte der Best-ofs, bei Erklärvideo-Kanälen auch Skript und Stimme. Die Untertitel folgen dem, was im Video gesprochen wird; lass dafür unter Einstellungen → Spracherkennung die Sprache leer (automatisch), wenn du Creator in mehreren Sprachen clippst. Das Dashboard selbst bleibt deutsch.

Für Läufe ohne Oberfläche gibt es weiterhin `auto.bat` (siehe „Ohne Dashboard“). Mit der Automatik im Dashboard brauchst du das nicht mehr.

## Einmalige Einrichtung

1. **Python 3.10+** und **ffmpeg** installieren (unter Windows erledigt das `ClipBot.bat` beim ersten Start, von Hand: `winget install Python.Python.3.12` und `winget install Gyan.FFmpeg`).
2. `ClipBot.bat` (Windows) bzw. `ClipBot.app` (Mac) starten. Beim ersten Mal werden die Pakete installiert, danach öffnet sich das Dashboard.
3. Unter „Watchlist“ Creator hinzufügen und unter „Kanäle“ einen Kanal anlegen, der sie verknüpft.
4. Unter „Übersicht“ auf „Clips erzeugen“ klicken. Dabei wird nichts hochgeladen. Die Clips unter „Freigabe“ ansehen und bei Bedarf die Einstellungen anpassen.
5. Für Uploads die Entwickler-Apps einrichten (siehe unten), die Konten unter „Konten“ anlegen und verbinden und dem Kanal zuordnen.
6. Upload-Zeiten eintragen, den Probemodus ausschalten und die Automatik einschalten.

Zum Schritt 5: Für YouTube genügt die Datei `client_secret.json` im ClipBot-Ordner; für TikTok und Instagram `.env.example` → `.env` kopieren und die Zugangsdaten eintragen.

Passwörter gehören **nicht** in die Konfiguration. Du meldest dich einmal im Browser direkt bei Google bzw. TikTok an; der Bot speichert nur die Zugriffstoken im Ordner `tokens/`.

### YouTube

- In der Google Cloud Console ein Projekt anlegen, „YouTube Data API v3“ aktivieren, OAuth-Client vom Typ „Desktop-App“ erstellen, JSON als `client_secret.json` in den Projektordner legen.
- Solange die Google-App im Status „Test“ ist, dein eigenes Google-Konto im OAuth-Zustimmungsbildschirm als Testnutzer eintragen.
- Im Dashboard unter „Konten“ ein YouTube-Konto hinzufügen, auf „Verbinden“ klicken und im Browser mit dem passenden Kanal anmelden. Für jedes weitere Konto genauso, die Google-App bleibt dieselbe.
- **Wichtig:** Uploads aus ungeprüften API-Projekten sperrt YouTube auf „privat“. Öffentlich wird es erst nach dem Compliance-Audit des Projekts (Formular „YouTube API Services – Audit and Quota Extension“).

### TikTok

- Auf developers.tiktok.com eine App anlegen, „Login Kit“ und „Content Posting API“ mit „Direct Post“ hinzufügen, Scope `video.publish` beantragen, Redirect-URI eintragen.
- `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REDIRECT_URI` in `.env` eintragen.
- Im Dashboard unter „Konten“ ein TikTok-Konto hinzufügen, auf „Verbinden“ klicken, bei TikTok mit dem passenden Account zustimmen und die Adresse der Zielseite einfügen.
- **Wichtig:** Solange die App nicht von TikTok auditiert ist, landen alle Posts nur privat („Nur ich“), das Konto muss beim Posten auf privat stehen und es sind maximal 5 Konten pro 24 Stunden möglich.

### Instagram

- Das Konto muss ein Business- oder Creator-Konto sein und mit einer Facebook-Seite verknüpft sein.
- Auf developers.facebook.com eine App anlegen, Berechtigungen `instagram_basic` und `instagram_content_publish` hinzufügen, langlebigen Token erzeugen (60 Tage gültig, danach erneuern).
- Token in `.env` eintragen, Instagram-User-ID und Namen der Token-Variable im Dashboard beim Konto eintragen, dann „Token prüfen“.
- Limit: 100 per API veröffentlichte Posts pro 24 Stunden und Konto.

## Wenn etwas nicht geht

- **Windows blockiert `ClipBot.bat` (Smart App Control):** Rechtsklick auf die heruntergeladene ZIP-Datei → Eigenschaften → unten „Zulassen“ ankreuzen → OK, dann neu entpacken.
- **Es entstehen keine Clips:** Das Protokoll unter „Übersicht“ nennt den Grund. Übersprungen werden Videos unter 150 Sekunden, laufende Livestreams (geclippt wird die Aufzeichnung) und Videos über der Höchstlänge aus den Einstellungen (Standard 300 Minuten).
- **Die Automatik lädt nichts hoch:** Es müssen Upload-Zeiten eingetragen, der Probemodus aus, das Konto verbunden und ein Video freigegeben sein. Unter „Konten“ steht je Konto, wann der nächste Upload ansteht.
- **Kein Reminder:** ClipBot muss laufen. In der Watchlist zeigt eine gelbe Zeile, wenn die letzte Prüfung eines Creators fehlgeschlagen ist. Endet ein Lauf mit „Mit Fehlern beendet“, steht die Ursache in der roten Zeile darüber.
- **Clips erscheinen erst, wenn sie fertig gerendert sind.** Das dauert je nach PC einige Minuten pro Clip; beim ersten Lauf wird zusätzlich das Sprachmodell geladen.

## Ohne Dashboard

| Befehl | Wirkung |
| --- | --- |
| `auto.bat` | kompletter Lauf für alle Kanäle |
| `auto.bat --channel streamer_de` | nur ein Kanal |
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
2. Die KI schreibt Titel, Captions und Hashtags **je Plattform** in der Sprache des Kanals. Die Caption enthält immer den Credit (den Namen des Creators).
3. Oben im Bild steht eine Zeile, die neugierig macht und den Creator nennt (siehe „Creator-Tag oben im Video“).

Änderst du Titel oder Beschreibung eines Clips im Dashboard, gilt deine Fassung für alle Plattformen.

## Freigabe vor dem Hochladen

Neue Clips, Best-ofs und Erklärvideos landen unter **Freigabe** im Dashboard. Dort siehst du Video, die Zeile oben im Bild, die Begründung der KI und die Texte je Plattform (umschalten über die Plattformen, auf die der Kanal hochlädt) und kannst sie ändern.

- **Freigeben:** Die Automatik lädt das Video zur nächsten Upload-Zeit hoch. Ohne Automatik geht es mit „Offene Clips hochladen“.
- **Freigeben und sofort hochladen:** lädt gleich hoch, ohne auf die Upload-Zeit zu warten.
- **Neu texten (KI):** Titel, Captions und Hashtags neu schreiben lassen.
- **Verwerfen:** Der Clip bleibt unter „Clips“, wird aber nie hochgeladen.

Willst du ohne Prüfung posten, schalte unter Einstellungen → Automatik und Zeitplan „Neue Videos erst nach deiner Freigabe hochladen“ aus.

## Erklärvideos (KI, animiert)

ClipBot kann eigene Erklärvideos bauen, z. B. „Was passiert, wenn du Plastik verschluckst?“. Die Sprache richtet sich nach dem Kanal (Deutsch, Englisch, Französisch oder Polnisch).

1. Unter **Kanäle** einen Kanal mit der Art **„Eigene Erklärvideos“** anlegen und ihm Konten zuordnen.
2. Unter **Einstellungen** eine KI wählen (Claude oder Ollama) und bei „Erklärvideos“ die Stimme einstellen.
   - **ElevenLabs:** `ELEVENLABS_API_KEY` in `.env` eintragen (elevenlabs.io → Profil → API Keys). Die Voice-ID findest du in deiner Stimmen-Bibliothek.
   - **Stumm:** kostenlose Vorschau ohne Sprecher, um Aussehen und Ablauf zu prüfen.
3. **Node.js 22 oder neuer** installieren (nodejs.org). hyperframes wird beim ersten Video automatisch über `npx` geladen.
4. Im Bereich **Erklärvideos** auf „Trends suchen“ klicken: ClipBot zeigt die meistgesehenen kurzen Erklärvideos dieses Monats auf YouTube und lässt die KI daraus eigene Themen ableiten. Eine Idee übernehmen oder ein eigenes Thema eintippen und „Video erstellen“ klicken.

Länge: Die Videos sind standardmäßig 65 bis 90 Sekunden lang (Einstellungen → Erklärvideos), weil z. B. TikTok nur Videos über einer Minute vergütet. Ist ein Skript zu kurz, lässt der Bot es einmal ausbauen. Bleibt das fertige Video trotzdem unter einer Minute, steht eine Warnung in der Freigabe.

Ablauf: Skript (KI) → Faktencheck (zweiter KI-Durchgang; bei Fehlern wird das Skript korrigiert) → Stimme mit Wortzeiten → Animation mit hyperframes (1080x1920) → das Video wartet unter **Freigabe**. Dort steht das Ergebnis des Faktenchecks. Lies es, bevor du freigibst, besonders bei Gesundheit und Politik.
