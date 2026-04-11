#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# <xbar.title>Tokmon</xbar.title>
# <xbar.version>v0.1</xbar.version>
# <xbar.refreshTime>5m</xbar.refreshTime>
# <xbar.dependencies>python3,browser-cookie3,requests</xbar.dependencies>

"""
Tokmon — SwiftBar/xbar menu bar plugin
Shows remaining credits/usage for Claude.ai, Amp, and OpenRouter.

Config file: ~/.config/tokmon/config.json
See config.example.json alongside this script for the schema.
"""

import json
import os
import sys
import traceback
from pathlib import Path

import requests

CONFIG_PATH = Path.home() / ".config" / "tokmon" / "config.json"


# ─── Config ──────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH) as f:
        return json.load(f)


# ─── Claude.ai ───────────────────────────────────────────────────────────────

CLAUDE_BASE_HEADERS = {
    "anthropic-client-platform": "web_claude_ai",
    "anthropic-client-version": "1.0.0",
    "content-type": "application/json",
    "Referer": "https://claude.ai/settings/usage",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    ),
}


def get_claude_session_cookie(cfg: dict) -> str | None:
    # 1. Manual override in config
    if manual := cfg.get("claude", {}).get("session_cookie"):
        return manual

    # 2. Extract from browser
    try:
        import browser_cookie3  # type: ignore

        for loader_name in cfg.get("claude", {}).get("browsers", ["chrome", "firefox"]):
            try:
                loader = getattr(browser_cookie3, loader_name)
                cookies = loader(domain_name=".claude.ai")
                jar = {c.name: c.value for c in cookies}
                # Find the session token — name varies by auth provider
                for key in ("sessionKey", "__Secure-next-auth.session-token",
                            "next-auth.session-token", "CF_Authorization"):
                    if key in jar:
                        return f"{key}={jar[key]}"
                # Fall back: send all cookies for the domain
                if jar:
                    return "; ".join(f"{k}={v}" for k, v in jar.items())
            except Exception:
                continue
    except ImportError:
        pass

    return None


def fetch_claude_usage(cfg: dict) -> dict | None:
    claude_cfg = cfg.get("claude", {})
    org_id = claude_cfg.get("org_id", "")
    if not org_id:
        return None

    session_cookie = get_claude_session_cookie(cfg)
    if not session_cookie:
        return None

    headers = {
        **CLAUDE_BASE_HEADERS,
        "Cookie": session_cookie,
    }
    # Optional: pass through device/anonymous IDs if you have them
    for h in ("anthropic-anonymous-id", "anthropic-device-id"):
        if val := claude_cfg.get(h):
            headers[h] = val

    url = f"https://claude.ai/api/organizations/{org_id}/usage"
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        return {"error": f"HTTP {e.response.status_code}"}
    except Exception as e:
        return {"error": str(e)}


def format_claude(data: dict) -> tuple[str, list[str]]:
    """Returns (menu_bar_label, [dropdown_lines])"""
    if "error" in data:
        return f"Claude: ✗ {data['error']}", []

    # The usage response shape — adapt as needed once you see real data
    # Common fields: used, limit, reset_at, model_usage, etc.
    used = data.get("used") or data.get("tokens_used") or data.get("usage")
    limit = data.get("limit") or data.get("tokens_limit")
    reset_at = data.get("reset_at") or data.get("resets_at") or data.get("next_reset")

    if limit and used is not None:
        pct = int(100 * used / limit)
        bar = _progress_bar(pct)
        label = f"Claude {bar} {pct}%"
        lines = [
            f"Claude Code Usage | size=13",
            f"Used:  {_fmt_num(used)} / {_fmt_num(limit)}",
        ]
    elif used is not None:
        label = f"Claude: {_fmt_num(used)} used"
        lines = [f"Claude Code Usage | size=13", f"Used: {_fmt_num(used)}"]
    else:
        # Unknown shape — dump keys so you can adapt the parser
        label = "Claude: ?"
        lines = [f"Claude raw: {json.dumps(data)[:120]}"]

    if reset_at:
        lines.append(f"Resets: {reset_at}")

    return label, lines


# ─── OpenRouter ──────────────────────────────────────────────────────────────

def fetch_openrouter(cfg: dict) -> dict | None:
    key = cfg.get("openrouter", {}).get("api_key", "")
    if not key:
        return None
    try:
        r = requests.get(
            "https://openrouter.ai/api/v1/auth/key",
            headers={"Authorization": f"Bearer {key}"},
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("data", r.json())
    except requests.HTTPError as e:
        return {"error": f"HTTP {e.response.status_code}"}
    except Exception as e:
        return {"error": str(e)}


def format_openrouter(data: dict) -> tuple[str, list[str]]:
    if "error" in data:
        return f"OR: ✗ {data['error']}", []

    usage = data.get("usage")          # credits used
    limit = data.get("limit")          # None = unlimited / free tier
    label_str = data.get("label", "")

    if limit:
        remaining = limit - (usage or 0)
        pct_used = int(100 * (usage or 0) / limit)
        bar = _progress_bar(pct_used)
        label = f"OR {bar} ${remaining:.2f}"
        lines = [
            f"OpenRouter | size=13",
            f"Remaining: ${remaining:.4f}",
            f"Used: ${usage:.4f} / ${limit:.4f}",
        ]
    elif usage is not None:
        label = f"OR: ${usage:.4f} used"
        lines = [f"OpenRouter | size=13", f"Used: ${usage:.4f}  (no limit set)"]
    else:
        label = "OR: ?"
        lines = [f"OpenRouter raw: {json.dumps(data)[:120]}"]

    if label_str:
        lines.append(f"Key: {label_str}")

    return label, lines


# ─── Amp ─────────────────────────────────────────────────────────────────────

def fetch_amp(cfg: dict) -> dict | None:
    amp_cfg = cfg.get("amp", {})

    # TODO: fill in once you identify the endpoint.
    # Options observed so far:
    #   - session cookie approach (like Claude)
    #   - API key header
    #   - CLI: `amp usage` or similar
    api_key = amp_cfg.get("api_key")
    session_cookie = amp_cfg.get("session_cookie")
    endpoint = amp_cfg.get("endpoint")  # e.g. https://ampcode.com/api/usage

    if not endpoint:
        return None

    headers = {"User-Agent": "Mozilla/5.0"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if session_cookie:
        headers["Cookie"] = session_cookie

    try:
        r = requests.get(endpoint, headers=headers, timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        return {"error": f"HTTP {e.response.status_code}"}
    except Exception as e:
        return {"error": str(e)}


def format_amp(data: dict) -> tuple[str, list[str]]:
    if "error" in data:
        return f"Amp: ✗ {data['error']}", []

    # Adapt once you know the response shape
    used = data.get("used") or data.get("tokens_used")
    limit = data.get("limit") or data.get("tokens_limit")
    resets_in = data.get("resets_in") or data.get("next_topup")

    if limit and used is not None:
        pct = int(100 * used / limit)
        bar = _progress_bar(pct)
        label = f"Amp {bar} {pct}%"
        lines = [f"Amp | size=13", f"Used: {_fmt_num(used)} / {_fmt_num(limit)}"]
    elif used is not None:
        label = f"Amp: {_fmt_num(used)} used"
        lines = [f"Amp | size=13", f"Used: {_fmt_num(used)}"]
    else:
        label = "Amp: ?"
        lines = [f"Amp raw: {json.dumps(data)[:120]}"]

    if resets_in:
        lines.append(f"Next top-up: {resets_in}")

    return label, lines


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _progress_bar(pct: int, width: int = 8) -> str:
    filled = round(pct / 100 * width)
    return "[" + "█" * filled + "░" * (width - filled) + "]"


def _fmt_num(n) -> str:
    if isinstance(n, float):
        return f"{n:,.2f}"
    if isinstance(n, int) and n >= 1_000:
        return f"{n:,}"
    return str(n)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    cfg = load_config()

    results = {}
    labels = []
    dropdown = []

    # Fetch all three
    for name, fetcher, formatter in [
        ("claude", fetch_claude_usage, format_claude),
        ("openrouter", fetch_openrouter, format_openrouter),
        ("amp", fetch_amp, format_amp),
    ]:
        try:
            data = fetcher(cfg)
            if data is None:
                continue
            bar_label, detail_lines = formatter(data)
        except Exception:
            bar_label = f"{name}: ✗ crash"
            detail_lines = [traceback.format_exc().splitlines()[-1]]

        labels.append(bar_label)
        if detail_lines:
            dropdown.extend(detail_lines)
            dropdown.append("---")

    # Menu bar: all labels on one line, separated by  |
    print("  ".join(labels))
    print("---")
    for line in dropdown:
        print(line)

    # Refresh action
    print("---")
    print("Refresh | refresh=true")
    print(f"Edit config | bash=open param1={CONFIG_PATH} terminal=false")


if __name__ == "__main__":
    main()
