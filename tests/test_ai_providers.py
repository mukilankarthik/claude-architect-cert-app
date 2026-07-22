"""Corner-case coverage for ai_providers.py's provider-agnostic dispatch and
per-provider error wrapping. Each provider SDK is mocked so no network calls
or real API keys are involved."""

import httpx
import pytest

from ai_providers import PROVIDERS, AIProviderError, generate_text


def _http_response(status_code=401):
    request = httpx.Request("POST", "https://example.invalid")
    return httpx.Response(status_code, request=request)


# ─── generate_text() dispatch ───────────────────────────────────────────────


def test_generate_text_raises_for_unknown_provider():
    with pytest.raises(AIProviderError, match="Unknown provider"):
        generate_text("not-a-real-provider", "key", "model", "sys", "user")


def test_providers_registry_has_expected_shape():
    for key, meta in PROVIDERS.items():
        assert "label" in meta
        assert "default_model" in meta
        assert "env_var" in meta
        assert "key_help" in meta


# ─── Anthropic ──────────────────────────────────────────────────────────────


def test_generate_anthropic_returns_text_on_success(monkeypatch):
    import anthropic

    class FakeContentBlock:
        text = "hello from claude"

    class FakeMessages:
        def create(self, **kwargs):
            self.kwargs = kwargs
            return type("M", (), {"content": [FakeContentBlock()]})()

    class FakeClient:
        def __init__(self, api_key):
            self.api_key = api_key
            self.messages = FakeMessages()

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)

    result = generate_text("anthropic", "sk-test", "claude-sonnet-4-6", "sys prompt", "user prompt")
    assert result == "hello from claude"


def test_generate_anthropic_wraps_authentication_error(monkeypatch):
    import anthropic

    class FakeClient:
        def __init__(self, api_key):
            raise anthropic.AuthenticationError("bad key", response=_http_response(), body=None)

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)

    with pytest.raises(AIProviderError, match="Invalid Anthropic API key"):
        generate_text("anthropic", "sk-bad", "claude-sonnet-4-6", "sys", "user")


def test_generate_anthropic_wraps_generic_error(monkeypatch):
    import anthropic

    class FakeClient:
        def __init__(self, api_key):
            raise ValueError("network exploded")

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)

    with pytest.raises(AIProviderError, match="Anthropic request failed"):
        generate_text("anthropic", "sk-test", "claude-sonnet-4-6", "sys", "user")


# ─── OpenAI ─────────────────────────────────────────────────────────────────


def test_generate_openai_returns_text_on_success(monkeypatch):
    import openai

    class FakeMessage:
        content = "hello from gpt"

    class FakeChoice:
        message = FakeMessage()

    class FakeCompletions:
        def create(self, **kwargs):
            return type("R", (), {"choices": [FakeChoice()]})()

    class FakeChat:
        def __init__(self):
            self.completions = FakeCompletions()

    class FakeClient:
        def __init__(self, api_key):
            self.chat = FakeChat()

    monkeypatch.setattr(openai, "OpenAI", FakeClient)

    result = generate_text("openai", "sk-test", "gpt-4.1", "sys", "user")
    assert result == "hello from gpt"


def test_generate_openai_wraps_authentication_error(monkeypatch):
    import openai

    class FakeClient:
        def __init__(self, api_key):
            raise openai.AuthenticationError("bad key", response=_http_response(), body=None)

    monkeypatch.setattr(openai, "OpenAI", FakeClient)

    with pytest.raises(AIProviderError, match="Invalid OpenAI API key"):
        generate_text("openai", "sk-bad", "gpt-4.1", "sys", "user")


def test_generate_openai_wraps_generic_error(monkeypatch):
    import openai

    class FakeClient:
        def __init__(self, api_key):
            raise RuntimeError("boom")

    monkeypatch.setattr(openai, "OpenAI", FakeClient)

    with pytest.raises(AIProviderError, match="OpenAI request failed"):
        generate_text("openai", "sk-test", "gpt-4.1", "sys", "user")


# ─── Gemini ─────────────────────────────────────────────────────────────────


def test_generate_gemini_returns_text_on_success(monkeypatch):
    import google.generativeai as genai

    class FakeModel:
        def __init__(self, model, system_instruction=None):
            pass

        def generate_content(self, prompt):
            return type("R", (), {"text": "hello from gemini"})()

    monkeypatch.setattr(genai, "configure", lambda **kwargs: None)
    monkeypatch.setattr(genai, "GenerativeModel", FakeModel)

    result = generate_text("gemini", "key", "gemini-2.5-flash", "sys", "user")
    assert result == "hello from gemini"


def test_generate_gemini_wraps_any_error_since_sdk_has_no_auth_error_class(monkeypatch):
    import google.generativeai as genai

    def raise_configure(**kwargs):
        raise Exception("invalid api key")

    monkeypatch.setattr(genai, "configure", raise_configure)

    with pytest.raises(AIProviderError, match="Gemini request failed"):
        generate_text("gemini", "bad-key", "gemini-2.5-flash", "sys", "user")
