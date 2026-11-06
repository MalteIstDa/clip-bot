# ClipBot

Python-Bot mit lokalem Web-Dashboard (`app.py`, http://127.0.0.1:8765).
Pipeline: `clipbot/download.py` (yt-dlp) → `highlights.py` → `transcribe.py` (faster-whisper)
→ `subtitles.py` → `render.py` (ffmpeg, 1080x1920) → `uploaders/` (YouTube, TikTok, Instagram).

- Sprache mit dem Nutzer: Deutsch.
- Plugins: superpowers (Ablauf: Brainstorming → Plan → TDD) und hyperframes (Videos aus HTML rendern),
  aktiviert über `.claude/settings.json`.
- Niemals Zugangsdaten committen: `.env`, `client_secret.json`, `tokens/`, `data/` bleiben lokal.

- Tests: `python -m pytest -q tests` (ohne Netz und ohne Schlüssel; die KI wird in Tests durch ein Fake-Backend ersetzt).
