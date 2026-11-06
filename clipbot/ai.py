"""KI-Schicht: Texte als JSON erzeugen, wahlweise mit Claude (Anthropic-API) oder lokal mit Ollama.

Aufruf ueberall gleich:  ai.generate_json(settings, prompt, schema, system=...)
Die Antwort wird gegen das JSON-Schema geprueft; bei Fehlern gibt es einen zweiten Versuch,
danach eine AIError. Aufrufer fangen AIError ab und fallen auf ihre alten Vorlagen zurueck.
"""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger("clipbot")

BACKENDS = ("off", "claude", "ollama")


class AIError(Exception):
    pass


# ---------------------------------------------------------------- Schema-Pruefung (kleine Teilmenge)
_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool,
          "integer": int, "number": (int, float)}


def validate(value, schema: dict, path: str = "$") -> None:
    """Prueft type/properties/required/items/enum/minItems/maxItems. Wirft AIError."""
    typ = schema.get("type")
    if typ:
        expected = _TYPES[typ]
        if isinstance(value, bool) and typ in ("integer", "number"):
            raise AIError(f"{path}: Zahl erwartet")
        if not isinstance(value, expected):
            raise AIError(f"{path}: {typ} erwartet")
    if "enum" in schema and value not in schema["enum"]:
        raise AIError(f"{path}: Wert nicht erlaubt")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                raise AIError(f"{path}.{key} fehlt")
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                validate(value[key], sub, f"{path}.{key}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise AIError(f"{path}: zu wenige Eintraege")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise AIError(f"{path}: zu viele Eintraege")
        for i, item in enumerate(value):
            validate(item, schema.get("items", {}), f"{path}[{i}]")


def _strict(schema: dict) -> dict:
    """Fuer die APIs: jedes Objekt bekommt additionalProperties=false, Pruef-Schluessel bleiben."""
    out = dict(schema)
    if out.get("type") == "object":
        out["additionalProperties"] = False
        out["properties"] = {k: _strict(v) for k, v in out.get("properties", {}).items()}
        out.setdefault("required", list(out["properties"]))
    if out.get("type") == "array" and "items" in out:
        out["items"] = _strict(out["items"])
    return out


# ---------------------------------------------------------------- Backends
def _claude(settings: dict, system: str, prompt: str, schema: dict) -> str:
    try:
        import anthropic
    except ImportError as exc:
        raise AIError("Paket 'anthropic' fehlt (start.bat/start.sh neu starten).") from exc
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise AIError("ANTHROPIC_API_KEY fehlt (in .env eintragen).")
    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=settings["ai_claude_model"],
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": settings["ai_effort"],
                           "format": {"type": "json_schema", "schema": _strict(schema)}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError as exc:
        raise AIError("Claude: API-Schluessel ungueltig.") from exc
    except anthropic.RateLimitError as exc:
        raise AIError("Claude: Zu viele Anfragen, spaeter erneut versuchen.") from exc
    except anthropic.APIStatusError as exc:
        raise AIError(f"Claude: Fehler {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise AIError("Claude: Keine Verbindung zur API.") from exc
    if response.stop_reason == "refusal":
        raise AIError("Claude hat die Anfrage abgelehnt.")
    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text:
        raise AIError("Claude: leere Antwort.")
    return text


def _ollama(settings: dict, system: str, prompt: str, schema: dict) -> str:
    import requests
    url = settings["ai_ollama_url"].rstrip("/") + "/api/chat"
    try:
        r = requests.post(url, timeout=600, json={
            "model": settings["ai_ollama_model"],
            "stream": False,
            "format": _strict(schema),
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
        })
    except requests.RequestException as exc:
        raise AIError(f"Ollama nicht erreichbar unter {settings['ai_ollama_url']} (laeuft Ollama?).") from exc
    if r.status_code == 404:
        raise AIError(f"Ollama: Modell '{settings['ai_ollama_model']}' fehlt "
                      f"(im Terminal: ollama pull {settings['ai_ollama_model']}).")
    if not r.ok:
        raise AIError(f"Ollama: Fehler {r.status_code}: {r.text[:200]}")
    return (r.json().get("message") or {}).get("content", "")


_CALLS = {"claude": _claude, "ollama": _ollama}


def enabled(settings: dict) -> bool:
    return settings.get("ai_backend", "off") in _CALLS


def generate_json(settings: dict, prompt: str, schema: dict, system: str = "") -> dict | list:
    """Fragt die eingestellte KI und gibt die geprueften JSON-Daten zurueck."""
    backend = settings.get("ai_backend", "off")
    if backend not in _CALLS:
        raise AIError("KI ist in den Einstellungen ausgeschaltet.")
    system = system or "Du antwortest ausschliesslich mit JSON, das dem vorgegebenen Schema entspricht."
    last = None
    for attempt in (1, 2):
        text = _CALLS[backend](settings, system, prompt, schema)
        try:
            data = json.loads(text)
            validate(data, schema)
            return data
        except (json.JSONDecodeError, AIError) as exc:
            last = exc
            log.info("KI-Antwort unbrauchbar (Versuch %d): %s", attempt, exc)
    raise AIError(f"KI-Antwort unbrauchbar: {last}")
