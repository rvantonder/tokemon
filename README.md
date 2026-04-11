# Tokemon

macOS menu bar + floating overlay for monitoring token usage across LLM services.

![Tokemon overlay](screenshot.png)

Tracks usage for **Claude**, **OpenRouter**, **Amp**, and **Codex** in a single always-on-top window.

## Install

Download the latest `Tokemon.app` from [Releases](https://github.com/rvantonder/tokemon/releases) and drag it to your Applications folder.

### Build from source

```bash
pip install requests rumps pyobjc browser-cookie3 pyinstaller
make build
open dist/Tokemon.app
```

## Configuration

Tokemon reads `~/.config/tokemon/config.json` (created from `config.example.json` on first run). You can also open it from the menu bar → **Edit config…**.

```json
{
  "claude": {
    "org_id": "<your-org-id>",
    "session_cookie": "<your-session-cookie>"
  },
  "openrouter": {
    "api_key": "sk-or-v1-..."
  },
  "amp": {
    "session_cookie": "<your-session-cookie>"
  },
  "codex": {
    "bearer_token": "<your-bearer-token>"
  }
}
```

### Setup

Paste the following prompt into your favorite AI coding agent and follow it to populate `~/.config/tokemon/config.json`:

````text
Help me set up Tokemon by extracting credentials for the services I use.
Write them to ~/.config/tokemon/config.json using this template:

{
  "claude": {
    "org_id": "<org-id>",
    "session_cookie": "<session-cookie>"
  },
  "openrouter": {
    "api_key": "sk-or-v1-..."
  },
  "amp": {
    "session_cookie": "<session-cookie>"
  },
  "codex": {
    "bearer_token": "<bearer-token>"
  }
}

For each service:

**Claude** — I need org_id and session_cookie from claude.ai.
Use Playwright to:
1. Open https://claude.ai/settings/usage (I should already be logged in)
2. Intercept the network request to /api/organizations/<org-id>/usage
3. Extract the <org-id> from the URL
4. Extract the Cookie header value as the session_cookie

**OpenRouter** — I need an API key.
Use Playwright to:
1. Open https://openrouter.ai/settings/keys (I should already be logged in)
2. Copy an existing API key, or create one and copy it

**Amp** — I need a session cookie from ampcode.com.
Use Playwright to:
1. Open https://ampcode.com/settings (I should already be logged in)
2. Extract the cookie header from any network request to ampcode.com

**Codex** — I need a bearer token from chatgpt.com.
Use Playwright to:
1. Open https://chatgpt.com (I should already be logged in)
2. Intercept any request to chatgpt.com/backend-api/*
3. Extract the Authorization header value (without the "Bearer " prefix)

Only configure the services I tell you I use. Skip the rest.
````

### Extra services

You can add custom services via the `extra_services` array in config. Each entry needs an endpoint, auth, and field mappings:

```json
{
  "extra_services": [
    {
      "id":       "my-service",
      "label":    "My LLM",
      "type":     "generic",
      "endpoint": "https://api.example.com/usage",
      "auth": {
        "type":  "bearer",
        "token": "sk-..."
      },
      "fields": {
        "used":  "usage",
        "limit": "limit",
        "reset": "reset_at",
        "unit":  "$"
      }
    }
  ]
}
```
