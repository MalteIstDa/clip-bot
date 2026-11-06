"""Skript fuer ein Erklaervideo per KI schreiben und in einem zweiten Durchgang auf Fakten pruefen."""
from __future__ import annotations

import logging

from clipbot import ai, viral

log = logging.getLogger("clipbot")

KINDS = ("hook", "fact", "number", "compare", "steps", "question")

SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "scenes": {
            "type": "array",
            "minItems": 4,
            "maxItems": 9,
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "voice": {"type": "string"},
                    "headline": {"type": "string"},
                    "sub": {"type": "string"},
                    "emoji": {"type": "string"},
                    "number": {"type": "string"},
                    "items": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
                },
                "required": ["kind", "voice", "headline", "sub", "emoji", "number", "items"],
            },
        },
        "youtube_title": {"type": "string"},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "scenes", "youtube_title", "caption", "hashtags"],
}

SYSTEM = (
    "Du schreibst Skripte fuer virale deutsche Erklaervideos im Hochformat. Gesprochen, locker, du-Form, "
    "kurze Saetze. Jede Aussage muss sachlich stimmen und dem gesicherten Wissensstand entsprechen; "
    "wenn etwas umstritten oder unsicher ist, sag das im Text. Keine erfundenen Zahlen. "
    "Antworte nur mit JSON nach dem Schema."
)


def write(settings: dict, topic: str, hook: str = "") -> dict:
    prompt = (
        f"Thema: {topic}\n" + (f"Vorgeschlagener erster Satz: {hook}\n" if hook else "") +
        "\nSchreibe ein Skript fuer 45 bis 75 Sekunden (insgesamt etwa 120 bis 180 gesprochene Woerter), "
        "5 bis 8 Szenen, eine Szene je Gedanke.\n"
        "- Szene 1 hat kind 'hook': eine Frage oder ueberraschende Aussage, die sofort neugierig macht.\n"
        "- Danach erklaerst du an einem konkreten Beispiel Schritt fuer Schritt. Nutze kind 'number' fuer eine "
        "einpraegsame Zahl (number = die Zahl mit Einheit, z. B. '37 °C'), 'compare' fuer vorher/nachher oder "
        "Mythos/Wahrheit (items = genau 2 kurze Punkte), 'steps' fuer einen Ablauf (items = 2 bis 4 Schritte), "
        "'fact' fuer eine einzelne Aussage.\n"
        "- Letzte Szene kind 'question': eine Frage an die Zuschauer, die zum Kommentieren anregt.\n"
        "- voice: der gesprochene Text der Szene, hoechstens 25 Woerter, Zahlen ausgeschrieben, wie man sie spricht.\n"
        "- headline: 2 bis 5 Woerter fuer den Bildschirm (nicht den gesprochenen Text wiederholen). "
        "sub: optional eine kurze zweite Zeile, sonst leer. emoji: ein passendes Emoji. "
        "number und items leer lassen, wenn die Szene sie nicht braucht.\n"
        "- youtube_title: hoechstens 70 Zeichen. caption: 1 bis 2 Saetze mit Frage am Ende, ohne Hashtags. "
        "hashtags: 5 bis 8 passende deutsche und breite Hashtags."
    )
    data = ai.generate_json(settings, prompt, SCRIPT_SCHEMA, SYSTEM)
    data["hashtags"] = viral.normalize_hashtags(data["hashtags"], 8)
    return data


CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["ok", "pruefen", "falsch"]},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "scene": {"type": "integer"},
                    "claim": {"type": "string"},
                    "problem": {"type": "string"},
                    "fix": {"type": "string"},
                },
                "required": ["scene", "claim", "problem", "fix"],
            },
        },
    },
    "required": ["verdict", "issues"],
}


def fact_check(settings: dict, script: dict) -> dict:
    """Zweiter Durchgang: jede Aussage pruefen. Gibt {"verdict", "issues", "summary"} zurueck."""
    lines = "\n".join(f"[{i}] {s['voice']} | Bildschirm: {s['headline']} {s['number']} {' / '.join(s['items'])}"
                      for i, s in enumerate(script["scenes"]))
    prompt = (
        f"Pruefe dieses Skript fuer ein Erklaervideo ('{script['title']}') streng auf sachliche Fehler, "
        "irrefuehrende Vereinfachungen, erfundene oder ungenaue Zahlen und einseitige Darstellungen "
        "(besonders bei Politik und Gesundheit).\n\n"
        f"{lines}\n\n"
        "verdict: 'ok', wenn nichts zu beanstanden ist; 'pruefen', wenn einzelne Aussagen unsicher oder ungenau sind; "
        "'falsch', wenn etwas eindeutig nicht stimmt. issues: jede Beanstandung mit Szenennummer, der Aussage, "
        "dem Problem und einer korrigierten Formulierung. Keine Beanstandung fuer reine Stilfragen."
    )
    data = ai.generate_json(settings, prompt, CHECK_SCHEMA)
    if not data["issues"]:
        summary = "Faktencheck: keine Beanstandung."
    else:
        label = {"ok": "Hinweise", "pruefen": "BITTE PRUEFEN", "falsch": "FEHLER GEFUNDEN"}[data["verdict"]]
        summary = f"Faktencheck ({label}): " + " | ".join(
            f"Szene {i['scene'] + 1}: {i['problem']} Vorschlag: {i['fix']}" for i in data["issues"])
    return {**data, "summary": summary}


def revise(settings: dict, script: dict, check: dict) -> dict:
    """Skript mit den Beanstandungen des Faktenchecks neu schreiben lassen (nur bei verdict 'falsch')."""
    import json
    if check.get("verdict") != "falsch" or not check.get("issues"):
        return script
    prompt = (
        "Ueberarbeite dieses Skript. Behebe jede Beanstandung des Faktenchecks, lass alles andere so nah wie "
        "moeglich am Original.\n\nSkript:\n" + json.dumps(script, ensure_ascii=False) +
        "\n\nBeanstandungen:\n" + json.dumps(check["issues"], ensure_ascii=False)
    )
    data = ai.generate_json(settings, prompt, SCRIPT_SCHEMA, SYSTEM)
    data["hashtags"] = viral.normalize_hashtags(data["hashtags"], 8)
    return data
