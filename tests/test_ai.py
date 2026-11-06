import json

import pytest

from clipbot import ai, config

SCHEMA = {"type": "object", "properties": {
    "title": {"type": "string"},
    "hashtags": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3},
}, "required": ["title", "hashtags"]}


def settings(**kw):
    return {**config.DEFAULTS, "ai_backend": "fake", **kw}


@pytest.fixture
def fake(monkeypatch):
    answers = []
    calls = []

    def backend(s, system, prompt, schema):
        calls.append(prompt)
        return answers.pop(0)

    monkeypatch.setitem(ai._CALLS, "fake", backend)
    return answers, calls


def test_valid_answer(fake):
    answers, _ = fake
    answers.append(json.dumps({"title": "Krass", "hashtags": ["#fyp"]}))
    assert ai.generate_json(settings(), "p", SCHEMA) == {"title": "Krass", "hashtags": ["#fyp"]}


def test_retry_after_broken_json(fake):
    answers, calls = fake
    answers += ["kein json", json.dumps({"title": "ok", "hashtags": ["#a"]})]
    assert ai.generate_json(settings(), "p", SCHEMA)["title"] == "ok"
    assert len(calls) == 2


def test_gives_up_after_two_bad_answers(fake):
    answers, _ = fake
    answers += [json.dumps({"title": 1, "hashtags": []}), json.dumps({"title": "x"})]
    with pytest.raises(ai.AIError):
        ai.generate_json(settings(), "p", SCHEMA)


def test_off_raises():
    assert not ai.enabled(settings(ai_backend="off"))
    with pytest.raises(ai.AIError):
        ai.generate_json(settings(ai_backend="off"), "p", SCHEMA)


def test_validate_limits():
    with pytest.raises(ai.AIError):
        ai.validate({"title": "x", "hashtags": ["a", "b", "c", "d"]}, SCHEMA)
    with pytest.raises(ai.AIError):
        ai.validate({"n": True}, {"type": "object", "properties": {"n": {"type": "integer"}}})


def test_strict_schema_adds_additional_properties():
    strict = ai._strict(SCHEMA)
    assert strict["additionalProperties"] is False
    assert strict["required"] == ["title", "hashtags"]


def test_claude_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with pytest.raises(ai.AIError, match="ANTHROPIC_API_KEY"):
        ai.generate_json(settings(ai_backend="claude"), "p", SCHEMA)


def test_ollama_unreachable():
    with pytest.raises(ai.AIError, match="Ollama"):
        ai.generate_json(settings(ai_backend="ollama", ai_ollama_url="http://127.0.0.1:9"), "p", SCHEMA)


def test_yaml_off_is_read_as_off(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("settings:\n  ai_backend: off\nchannels: []\n", encoding="utf-8")
    assert config.load_raw(cfg)["settings"]["ai_backend"] == "off"
