"""Benachrichtigungen: Eintrag im Dashboard, Mitteilung des Betriebssystems und optional aufs Handy (ntfy, Discord)."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading

log = logging.getLogger("clipbot")

# kind: live (Creator ist live) | video (neues Video) | clips (neue Clips warten) | bestof | upload | error
PUSH_KINDS = ("live", "video", "clips", "bestof", "error")

_WINDOWS_TOAST = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$texts = $xml.GetElementsByTagName('text')
$texts.Item(0).AppendChild($xml.CreateTextNode($env:CLIPBOT_TITLE)) > $null
$texts.Item(1).AppendChild($xml.CreateTextNode($env:CLIPBOT_TEXT)) > $null
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($xml))
"""


def desktop(title: str, text: str) -> None:
    """Mitteilung des Betriebssystems. Die Texte gehen als Argument bzw. Umgebungsvariable hinein, nie als Code."""
    try:
        if sys.platform == "darwin":
            subprocess.run(
                ["osascript", "-e", "on run argv", "-e",
                 'display notification (item 2 of argv) with title (item 1 of argv) sound name "Glass"',
                 "-e", "end run", title, text or " "],
                check=False, timeout=10, capture_output=True)
        elif os.name == "nt":
            env = {**os.environ, "CLIPBOT_TITLE": title, "CLIPBOT_TEXT": text or " "}
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_TOAST],
                           check=False, timeout=15, capture_output=True, env=env)
        else:
            subprocess.run(["notify-send", title, text], check=False, timeout=10, capture_output=True)
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("Mitteilung des Betriebssystems nicht moeglich: %s", exc)


def ntfy(topic: str, title: str, text: str, url: str = "") -> None:
    import requests

    payload = {"topic": topic, "title": title, "message": text or title, "tags": ["clapper"]}
    if url.startswith("https://"):
        payload["click"] = url
    requests.post("https://ntfy.sh/", json=payload, timeout=8)


def discord(webhook: str, title: str, text: str, url: str = "") -> None:
    import requests

    content = "\n".join(part for part in (f"**{title}**", text, url) if part)
    requests.post(webhook, json={"content": content[:1900]}, timeout=8)


def _push(settings: dict, title: str, text: str, url: str) -> None:
    if settings.get("notify_desktop"):
        desktop(title, text)
    for name, call in (
        ("ntfy", lambda: settings.get("notify_ntfy_topic") and ntfy(settings["notify_ntfy_topic"], title, text, url)),
        ("Discord", lambda: os.environ.get("DISCORD_WEBHOOK_URL")
            and discord(os.environ["DISCORD_WEBHOOK_URL"], title, text, url)),
    ):
        try:
            call()
        except Exception as exc:  # Benachrichtigungen duerfen nie einen Lauf abbrechen
            log.info("Benachrichtigung ueber %s fehlgeschlagen: %s", name, exc)


def send(state, settings: dict, kind: str, title: str, text: str = "", url: str = "", creator: str = "",
         background: bool = True) -> None:
    """Benachrichtigung speichern (Dashboard) und nach draussen schicken (Betriebssystem, Handy)."""
    state.add_notification(kind, title, text, url, creator)
    log.info("Benachrichtigung: %s%s", title, f" ({text})" if text else "")
    if kind not in PUSH_KINDS:
        return
    if background:
        threading.Thread(target=_push, args=(settings, title, text, url), daemon=True).start()
    else:
        _push(settings, title, text, url)
