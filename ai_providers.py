"""Provider-agnostic text generation for the question-generation feature.

Adding a provider means adding one entry to PROVIDERS and one branch in
generate_text() — the rest of app.py only ever calls generate_text() and
handles AIProviderError, so it never needs to know which SDK produced
the answer. Each SDK is imported lazily inside its branch so installing
one provider's package doesn't require the others.
"""

PROVIDERS = {
    "anthropic": {
        "label": "Anthropic (Claude)",
        "default_model": "claude-sonnet-4-6",
        "env_var": "ANTHROPIC_API_KEY",
        "key_help": "Get your key at console.anthropic.com",
    },
    "openai": {
        "label": "OpenAI (GPT)",
        "default_model": "gpt-4.1",
        "env_var": "OPENAI_API_KEY",
        "key_help": "Get your key at platform.openai.com/api-keys",
    },
    "gemini": {
        "label": "Google (Gemini)",
        "default_model": "gemini-2.5-flash",
        "env_var": "GOOGLE_API_KEY",
        "key_help": "Get your key at aistudio.google.com/apikey",
    },
}


class AIProviderError(Exception):
    """Raised for any provider-side failure (bad key, bad request, network, etc.)."""


def generate_text(provider: str, api_key: str, model: str, system_prompt: str, user_prompt: str) -> str:
    """Send one prompt to the chosen provider and return its raw text response."""
    if provider == "anthropic":
        return _generate_anthropic(api_key, model, system_prompt, user_prompt)
    if provider == "openai":
        return _generate_openai(api_key, model, system_prompt, user_prompt)
    if provider == "gemini":
        return _generate_gemini(api_key, model, system_prompt, user_prompt)
    raise AIProviderError(f"Unknown provider: {provider}")


def _generate_anthropic(api_key: str, model: str, system_prompt: str, user_prompt: str) -> str:
    import anthropic

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return message.content[0].text
    except anthropic.AuthenticationError as exc:
        raise AIProviderError("Invalid Anthropic API key.") from exc
    except Exception as exc:
        raise AIProviderError(f"Anthropic request failed: {exc}") from exc


def _generate_openai(api_key: str, model: str, system_prompt: str, user_prompt: str) -> str:
    import openai

    try:
        client = openai.OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            max_tokens=4096,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content
    except openai.AuthenticationError as exc:
        raise AIProviderError("Invalid OpenAI API key.") from exc
    except Exception as exc:
        raise AIProviderError(f"OpenAI request failed: {exc}") from exc


def _generate_gemini(api_key: str, model: str, system_prompt: str, user_prompt: str) -> str:
    import google.generativeai as genai

    try:
        genai.configure(api_key=api_key)
        gemini_model = genai.GenerativeModel(model, system_instruction=system_prompt)
        response = gemini_model.generate_content(user_prompt)
        return response.text
    except Exception as exc:
        # The Gemini SDK doesn't expose a distinct auth-error class; a bad
        # key surfaces as a generic API error, so it's folded in here.
        raise AIProviderError(f"Gemini request failed: {exc}") from exc
