#!/usr/bin/env python3
"""Tokemon — macOS menu bar + floating overlay app."""

import json
import queue
import subprocess
import threading
import traceback
from pathlib import Path

import requests
import rumps
from AppKit import (
    NSBackingStoreBuffered,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSFloatingWindowLevel,
    NSScreen,
    NSTextField,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectStateActive,
    NSVisualEffectView,
    NSWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorIgnoresCycle,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
)
from Foundation import NSMakeRect, NSMutableAttributedString, NSOperationQueue

NSVisualEffectMaterialHUDWindow = 6

CONFIG_PATH   = Path.home() / ".config" / "tokemon" / "config.json"
POSITION_PATH = Path.home() / ".config" / "tokemon" / "window_pos.json"
REFRESH_INTERVAL = 60

ROW_H     = 20
PAD_X     = 14
PAD_TOP   = 10
PAD_BOT   = 10
FONT_SIZE = 11.0

COL_WHITE = NSColor.whiteColor()
COL_DIM   = NSColor.colorWithCalibratedWhite_alpha_(0.45, 1.0)
COL_ERROR = NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.38, 0.38, 1.0)


def _hex_color(h: str) -> NSColor:
    h = h.lstrip("#")
    r, g, b = int(h[0:2], 16)/255, int(h[2:4], 16)/255, int(h[4:6], 16)/255
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0)


# Colored dot (●) per service id — None = no dot
SERVICE_DOT_COLORS: dict[str, NSColor] = {}   # populated after NSApp init

SERVICE_DOT_HEX = {
    "claude_5h":  "#d97757",
    "claude_7d":  "#d97757",
    "openrouter": "#94a3b8",
    "amp":          "#b9f7ce",
    "amp_credits":  "#7ce8a0",
    "codex":        "#6b95c7",   # muted #024ede
}


# ─── Service registry ────────────────────────────────────────────────────────
#
# Built-in services always available; extra_services in config adds more.
# Each entry: {"id": str, "label": str, "type": "claude"|"openrouter"|"generic"}
# Generic entries also carry: endpoint, auth {type, token|cookie}, fields {used,limit,reset}

BUILTIN_SERVICES = [
    {"id": "claude_5h",   "label": "Claude cur",  "type": "claude_5h"},
    {"id": "claude_7d",   "label": "Claude wk",   "type": "claude_7d"},
    {"id": "openrouter",  "label": "OpenRouter",  "type": "openrouter"},
    {"id": "amp",          "label": "Amp",         "type": "amp"},
    {"id": "amp_credits", "label": "Amp cr",      "type": "amp_credits"},
    {"id": "codex",       "label": "Codex",       "type": "codex"},
]


def load_services(cfg: dict) -> list[dict]:
    seen: set[str] = set()
    services: list[dict] = []
    for svc in list(BUILTIN_SERVICES) + cfg.get("extra_services", []):
        if svc.get("id") and svc.get("label") and svc["id"] not in seen:
            seen.add(svc["id"])
            services.append({**svc, "type": svc.get("type", "generic")})
    return services


def label_width(services: list[dict]) -> int:
    """Overlay window width: scales with longest service label."""
    longest = max(len(s["label"]) for s in services)
    # "● " (2) + label + "  " (2) + bar (10) + " 74% (15h54m)" (14)
    # Block/circle chars render wider than ASCII; 20px safety margin
    chars = 2 + longest + 2 + 10 + 14
    return PAD_X * 2 + int(chars * 6.8)


# ─── Config ──────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def ensure_config():
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        example = Path(__file__).parent / "config.example.json"
        if example.exists():
            import shutil
            shutil.copy(example, CONFIG_PATH)


# ─── Service: Claude.ai ──────────────────────────────────────────────────────

CLAUDE_HEADERS = {
    "anthropic-client-platform": "web_claude_ai",
    "anthropic-client-version": "1.0.0",
    "content-type": "application/json",
    "Referer": "https://claude.ai/settings/usage",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    ),
}


def get_claude_cookie(cfg: dict) -> str | None:
    if manual := cfg.get("claude", {}).get("session_cookie"):
        return manual
    try:
        import browser_cookie3
        for loader_name in cfg.get("claude", {}).get("browsers", ["chrome", "firefox"]):
            try:
                loader = getattr(browser_cookie3, loader_name)
                jar = {c.name: c.value for c in loader(domain_name=".claude.ai")}
                for key in ("sessionKey", "__Secure-next-auth.session-token",
                            "next-auth.session-token"):
                    if key in jar:
                        return f"{key}={jar[key]}"
                if jar:
                    return "; ".join(f"{k}={v}" for k, v in jar.items())
            except Exception:
                continue
    except ImportError:
        pass
    return None


import time as _time
_claude_cache: list = [0.0, {}]   # [timestamp, data]


def fetch_claude(cfg: dict, _svc: dict) -> dict:
    org_id = cfg.get("claude", {}).get("org_id", "")
    if not org_id:
        return {"_error": "no org_id in config", "_unconfigured": True}
    cookie = get_claude_cookie(cfg)
    if not cookie:
        return {"_error": "open claude.ai in Chrome first", "_unconfigured": True}
    headers = {**CLAUDE_HEADERS, "Cookie": cookie}
    for h in ("anthropic-anonymous-id", "anthropic-device-id"):
        if val := cfg.get("claude", {}).get(h):
            headers[h] = val
    try:
        r = requests.get(
            f"https://claude.ai/api/organizations/{org_id}/usage",
            headers=headers, timeout=10,
        )
        r.raise_for_status()
        result = r.json()
        _claude_cache[0] = _time.monotonic()
        _claude_cache[1] = result
        return result
    except requests.HTTPError as e:
        return {"_error": f"HTTP {e.response.status_code}"}
    except Exception as e:
        return {"_error": str(e)[:50]}


def fetch_claude_cached(cfg: dict, _svc: dict) -> dict:
    """Return cached Claude usage; re-fetch only if cache is older than 55s."""
    if _time.monotonic() - _claude_cache[0] < 55 and _claude_cache[1]:
        return _claude_cache[1]
    return fetch_claude(cfg, _svc)


def _fmt_reset(iso: str) -> str:
    """'2026-04-11T08:00:00+00:00' → 'in 1w2d' / 'in 6d20h' / 'in 3h 22m'"""
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        secs = int((dt - datetime.now(timezone.utc)).total_seconds())
        if secs < 0:
            return "soon"
        short = _fmt_secs_short(secs)
        return f"in {short}" if short != "now" else "now"
    except Exception:
        return iso[:16]


def _fmt_reset_short(iso: str) -> str:
    """'2026-04-11T08:00:00+00:00' → '1w2d' / '6d20h' / '3h22m' / '45m'"""
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        secs = int((dt - datetime.now(timezone.utc)).total_seconds())
        return _fmt_secs_short(secs)
    except Exception:
        return "?"


def _fmt_reset_human_short(text: str) -> str:
    """'Apr 18, 2026 2:14 PM' → '7d3h' / '3h22m'."""
    try:
        from datetime import datetime
        dt = datetime.strptime(text.strip(), "%b %d, %Y %I:%M %p")
        return _fmt_secs_short(int(dt.timestamp() - _time.time()))
    except Exception:
        return "?"


def _codex_reset_strings(data: dict) -> tuple[str, str]:
    """Return (short, long) reset strings from whichever Codex field is present."""
    pw = (data.get("rate_limit") or {}).get("primary_window") or {}
    raw = (
        pw.get("reset_after_seconds"),
        pw.get("reset_at"),
        pw.get("resets_at"),
        pw.get("resets"),
        (data.get("rate_limit") or {}).get("reset_at"),
        (data.get("rate_limit") or {}).get("resets_at"),
        data.get("reset_at"),
        data.get("resets_at"),
    )

    secs = next((v for v in raw if isinstance(v, (int, float))), None)
    if secs is not None:
        short = _fmt_secs_short(int(secs))
        return short, f"in {short}" if short != "now" else "now"

    text = next((v for v in raw if isinstance(v, str) and v.strip()), "")
    if not text:
        return "", ""

    short = _fmt_reset_short(text)
    if short != "?":
        return short, _fmt_reset(text)

    short = _fmt_reset_human_short(text)
    if short != "?":
        return short, text

    return "", text


def _claude_common(data: dict) -> tuple[bool, str]:
    """Returns (has_error, error_msg). Call at top of each split parser."""
    if data.get("_unconfigured") or data.get("_error"):
        return True, data.get("_error", "not configured")
    return False, ""


def parse_claude_5h(data: dict, _svc: dict) -> tuple[str, str, list[str]]:
    err, msg = _claude_common(data)
    if err:
        return f"✗ {msg}", "–", [f"Error: {msg}"]
    fh  = data.get("five_hour") or {}
    pct = fh.get("utilization")
    if pct is None:
        return "no data", "–", []
    rst_short = _fmt_reset_short(fh["resets_at"]) if fh.get("resets_at") else ""
    rst_long  = _fmt_reset(fh["resets_at"])       if fh.get("resets_at") else ""
    bar_lbl = f"{_bar(int(pct))} {int(pct)}% ({rst_short})" if rst_short else f"{_bar(int(pct))} {int(pct)}%"
    return bar_lbl, f"C↑{int(pct)}%", [f"{_bar(int(pct))} {int(pct)}%  {rst_long}"]


def parse_claude_7d(data: dict, _svc: dict) -> tuple[str, str, list[str]]:
    err, msg = _claude_common(data)
    if err:
        return f"✗ {msg}", "–", [f"Error: {msg}"]
    sd  = data.get("seven_day") or {}
    pct = sd.get("utilization")
    if pct is None:
        return "no data", "–", []
    rst_short = _fmt_reset_short(sd["resets_at"]) if sd.get("resets_at") else ""
    rst_long  = _fmt_reset(sd["resets_at"])       if sd.get("resets_at") else ""
    bar_lbl = f"{_bar(int(pct))} {int(pct)}% ({rst_short})" if rst_short else f"{_bar(int(pct))} {int(pct)}%"
    return bar_lbl, f"C↗{int(pct)}%", [f"{_bar(int(pct))} {int(pct)}%  {rst_long}"]


# ─── Service: OpenRouter ─────────────────────────────────────────────────────

def fetch_openrouter(cfg: dict, _svc: dict) -> dict:
    key = cfg.get("openrouter", {}).get("api_key", "")
    if not key:
        return {"_error": "no api_key in config", "_unconfigured": True}
    headers = {"Authorization": f"Bearer {key}"}
    try:
        # Key metadata (usage + optional spending limit)
        r1 = requests.get("https://openrouter.ai/api/v1/auth/key",
                          headers=headers, timeout=10)
        r1.raise_for_status()
        data = r1.json().get("data", r1.json())

        # Credit balance (prepaid accounts) — may 404 on free tier, that's fine
        try:
            r2 = requests.get("https://openrouter.ai/api/v1/credits",
                              headers=headers, timeout=10)
            if r2.ok:
                credits = r2.json()
                # Response shape: {"data": {"total_credits": X, "total_usage": Y}}
                #                 or {"total_credits": X, ...}
                cd = credits.get("data", credits)
                data["_credits_total"] = cd.get("total_credits")
                data["_credits_usage"] = cd.get("total_usage")
        except Exception:
            pass

        return data
    except requests.HTTPError as e:
        return {"_error": f"HTTP {e.response.status_code}"}
    except Exception as e:
        return {"_error": str(e)[:50]}


def parse_openrouter(data: dict, _svc: dict) -> tuple[str, str, list[str]]:
    if data.get("_unconfigured"):
        return "not configured", "–", ["Set api_key in config"]
    if err := data.get("_error"):
        return f"✗ {err}", "✗", [f"Error: {err}"]

    key_label = data.get("label", "")
    details   = ([f"Key: {key_label}"] if key_label else [])

    # Prefer prepaid credit balance if available
    total   = data.get("_credits_total")
    cr_used = data.get("_credits_usage")
    if total is not None and cr_used is not None:
        remaining = total - cr_used
        pct_used  = int(100 * cr_used / total) if total else 0
        return (
            f"{_bar(pct_used)} ${remaining:.2f}",
            f"OR ${remaining:.2f}",
            [f"Remaining: ${remaining:.4f}", f"Used: ${cr_used:.4f} / ${total:.4f}"] + details,
        )
    if total is not None:
        return f"${total:.4f} credits", f"OR ${total:.4f}", [f"Credits: ${total:.4f}"] + details

    # Fall back to key-level usage/limit
    usage = data.get("usage")
    limit = data.get("limit")
    if limit:
        remaining = limit - (usage or 0)
        pct_used  = int(100 * (usage or 0) / limit)
        return (
            f"{_bar(pct_used)} ${remaining:.2f}",
            f"OR ${remaining:.2f}",
            [f"Remaining: ${remaining:.4f}", f"Used: ${usage:.4f} / ${limit:.4f}"] + details,
        )
    if usage is not None:
        return f"${usage:.4f} used", f"OR ${usage:.4f}", [f"Used: ${usage:.4f}  (no limit)"] + details
    return "? (see details)", "OR ?", [f"Raw: {json.dumps(data)[:80]}"]


# ─── Service: Generic (config-driven) ────────────────────────────────────────
#
# Config shape for an extra_services entry:
#   {
#     "id":       "codex",
#     "label":    "Codex",
#     "type":     "generic",
#     "endpoint": "https://...",
#     "auth":     {"type": "bearer", "token": "sk-..."},
#                  or  {"type": "cookie", "value": "session=..."}
#     "fields":  {            <- dot-notation paths into the JSON response
#       "used":   "usage",
#       "limit":  "limit",
#       "reset":  "reset_at",
#       "unit":   "$"         <- optional prefix/suffix hint: "$" or "tok"
#     }
#   }

def fetch_generic(cfg: dict, svc: dict) -> dict:
    endpoint = svc.get("endpoint", "")
    if not endpoint:
        return {"_error": "no endpoint", "_unconfigured": True}
    headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
    auth = svc.get("auth", {})
    if auth.get("type") == "bearer":
        headers["Authorization"] = f"Bearer {auth.get('token', '')}"
    elif auth.get("type") == "cookie":
        headers["Cookie"] = auth.get("value", "")
    try:
        r = requests.get(endpoint, headers=headers, timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        return {"_error": f"HTTP {e.response.status_code}"}
    except Exception as e:
        return {"_error": str(e)[:50]}


def _dot_get(data: dict, path: str):
    """Traverse dot-notation path: 'data.usage' → data['data']['usage']."""
    val = data
    for part in path.split("."):
        if not isinstance(val, dict):
            return None
        val = val.get(part)
    return val


def parse_generic(data: dict, svc: dict) -> tuple[str, str, list[str]]:
    if data.get("_unconfigured"):
        return "not configured", "–", [f"Set endpoint in config ({svc['id']})"]
    if err := data.get("_error"):
        return f"✗ {err}", "✗", [f"Error: {err}"]

    fields = svc.get("fields", {})
    unit   = fields.get("unit", "")          # "$" → dollar prefix, else suffix
    used   = _dot_get(data, fields["used"])   if "used"  in fields else None
    limit  = _dot_get(data, fields["limit"])  if "limit" in fields else None
    reset  = _dot_get(data, fields["reset"])  if "reset" in fields else None

    def fmt_val(v):
        if v is None:
            return "?"
        if unit == "$":
            return f"${float(v):.2f}"
        if unit:
            return f"{_n(v)} {unit}"
        return _n(v)

    short = svc["label"][:3].upper()

    if limit and used is not None:
        try:
            pct = int(100 * float(used) / float(limit))
            bar_lbl = f"{_bar(pct)} {fmt_val(limit - used if unit == '$' else used)}"
            mb_lbl  = f"{short} {pct}%"
            details = [f"Used: {fmt_val(used)} / {fmt_val(limit)}"]
        except (TypeError, ZeroDivisionError):
            bar_lbl, mb_lbl, details = fmt_val(used), short, [f"Used: {fmt_val(used)}"]
    elif used is not None:
        bar_lbl = fmt_val(used)
        mb_lbl  = f"{short} {fmt_val(used)}"
        details = [f"Used: {fmt_val(used)}"]
    else:
        return "? (see details)", f"{short} ?", [f"Raw: {json.dumps(data)[:80]}"]

    if reset:
        details.append(f"Resets: {reset}")
    return bar_lbl, mb_lbl, details


# ─── Service: Amp ────────────────────────────────────────────────────────────
#
# Amp uses SvelteKit's devalue compressed format.
# Response: {"type":"result","result":"[{schema}, val0, val1, ...]"}
# Decoded:  {"bucket":"ubi", "quota":1500, "used":1060.5, "hourlyReplenishment":63, ...}
# Units:    quota/used are in cents  (1500 = $15.00)

AMP_DEFAULT_ENDPOINT = "https://ampcode.com/_app/remote/w6b2h6/getFreeTierUsage"


def _decode_sveltekit(data) -> dict:
    """Recursively expand SvelteKit devalue compressed array into nested dicts/lists.
    Accepts a JSON string or an already-parsed list."""
    arr = json.loads(data) if isinstance(data, str) else data
    if not isinstance(arr, list) or not arr:
        return {}

    def _resolve(idx, seen=None):
        if not isinstance(idx, int) or idx >= len(arr):
            return idx
        if seen is None:
            seen = set()
        if idx in seen:
            return arr[idx]
        seen = seen | {idx}
        val = arr[idx]
        if isinstance(val, dict):
            return {k: _resolve(v, seen) for k, v in val.items()}
        if isinstance(val, list):
            return [_resolve(v, seen) for v in val]
        return val

    return _resolve(0)


def fetch_amp(cfg: dict, _svc: dict) -> dict:
    amp_cfg  = cfg.get("amp", {})
    cookie   = amp_cfg.get("session_cookie", "")
    endpoint = amp_cfg.get("endpoint", AMP_DEFAULT_ENDPOINT)

    if not cookie:
        return {"_error": "no session cookie", "_unconfigured": True}

    headers = {
        "Cookie": cookie,
        "content-type": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": "https://ampcode.com/settings",
        "x-sveltekit-pathname": "/settings",
    }
    try:
        r = requests.get(endpoint, headers=headers, timeout=10)
        r.raise_for_status()
        outer = r.json()
        if isinstance(outer, dict) and outer.get("type") == "error":
            msg = (outer.get("error") or {}).get("message", "unknown")
            return {"_error": msg}
        if isinstance(outer, dict) and outer.get("type") == "result":
            return _decode_sveltekit(outer["result"])
        return outer
    except requests.HTTPError as e:
        code = e.response.status_code
        if code == 404:
            return {"_error": "endpoint stale — update amp.endpoint in config"}
        return {"_error": f"HTTP {code}"}
    except Exception as e:
        return {"_error": str(e)[:60]}


def parse_amp(data: dict, _svc: dict) -> tuple[str, str, list[str]]:
    if data.get("_unconfigured"):
        return "not configured", "–", ["Set amp.session_cookie in config"]
    if err := data.get("_error"):
        return f"✗ {err}", "✗", [f"Error: {err}"]

    quota  = data.get("quota")            # cents, e.g. 1500 = $15.00
    used   = data.get("used")             # cents, e.g. 1060.5
    hourly = data.get("hourlyReplenishment")  # cents/hr, e.g. 63 = $0.63/hr

    if quota is None or used is None:
        return "? (see details)", "Amp ?", [f"Raw: {json.dumps(data)[:80]}"]

    quota_usd     = quota / 100
    used_usd      = used / 100
    remaining_usd = quota_usd - used_usd
    pct_used      = int(100 * used / quota) if quota else 0

    details = [
        f"${remaining_usd:.2f} left of ${quota_usd:.2f}",
        f"Used: ${used_usd:.2f}",
    ]
    if hourly:
        details.append(f"Replenishes +${hourly/100:.2f}/hr")

    data["_display_label"] = "Amp fr"
    return (
        f"{_bar(pct_used)} ${remaining_usd:.2f}",
        f"Amp ${remaining_usd:.2f}",
        details,
    )


# ─── Service: Amp Credits ────────────────────────────────────────────────────
#
# Fetches credit balance from the settings __data.json page-load data.
# The devalue-encoded node contains: credits.paid.{used, available} in cents.

AMP_SETTINGS_DATA_URL = "https://ampcode.com/settings/__data.json"


def fetch_amp_credits(cfg: dict, _svc: dict) -> dict:
    amp_cfg  = cfg.get("amp", {})
    cookie   = amp_cfg.get("session_cookie", "")

    if not cookie:
        return {"_error": "no session cookie", "_unconfigured": True}

    headers = {
        "Cookie": cookie,
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": "https://ampcode.com/settings",
        "x-sveltekit-pathname": "/settings",
    }
    try:
        r = requests.get(AMP_SETTINGS_DATA_URL, headers=headers, timeout=10)
        r.raise_for_status()
        outer = r.json()
        # Find the node whose schema has a "credits" key
        for node in outer.get("nodes", []):
            if not isinstance(node, dict) or node.get("type") != "data":
                continue
            data_arr = node.get("data", [])
            if not data_arr or not isinstance(data_arr[0], dict):
                continue
            if "credits" in data_arr[0]:
                return _decode_sveltekit(data_arr)
        return {"_error": "credits node not found"}
    except requests.HTTPError as e:
        return {"_error": f"HTTP {e.response.status_code}"}
    except Exception as e:
        return {"_error": str(e)[:60]}


def parse_amp_credits(data: dict, _svc: dict) -> tuple[str, str, list[str]]:
    if data.get("_unconfigured"):
        return "not configured", "–", ["Set amp.session_cookie in config"]
    if err := data.get("_error"):
        return f"✗ {err}", "✗", [f"Error: {err}"]

    credits = data.get("credits")
    if not isinstance(credits, dict):
        return "? (see details)", "AC ?", [f"Raw: {json.dumps(data)[:80]}"]

    paid       = credits.get("paid", {})
    free       = credits.get("free", {})
    paid_avail = (paid.get("available") or 0) / 100   # cents → dollars
    paid_used  = (paid.get("used") or 0) / 100
    free_avail = (free.get("available") or 0) / 100
    # When free tier is overdrawn, the deficit comes out of paid credits
    balance    = paid_avail + min(0, free_avail)
    total      = paid_used + paid_avail
    pct_used   = int(100 * paid_used / total) if total else 0

    details = [
        f"Balance: ${balance:.2f}",
        f"Spent: ${paid_used:.2f} / ${total:.2f}",
    ]

    return f"{_bar(pct_used)} ${balance:.2f}", f"AC ${balance:.2f}", details


# ─── Service: Codex ─────────────────────────────────────────────────────────

def fetch_codex(cfg: dict, _svc: dict) -> dict:
    bearer = cfg.get("codex", {}).get("bearer_token", "")
    if not bearer:
        return {"_error": "no bearer_token in config", "_unconfigured": True}
    try:
        r = requests.get(
            "https://chatgpt.com/backend-api/wham/usage",
            headers={
                "Authorization": f"Bearer {bearer}",
                "Referer": "https://chatgpt.com/codex/cloud/settings/usage",
                "x-openai-target-path": "/backend-api/wham/usage",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            },
            timeout=10,
        )
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        return {"_error": f"HTTP {e.response.status_code}"}
    except Exception as e:
        return {"_error": str(e)[:50]}


def _fmt_secs_short(secs: int) -> str:
    """604428 → '1w2d' / '6d20h' / '3h20m'"""
    if secs <= 0:
        return "now"
    d, rem = divmod(int(secs), 86400)
    h, m   = divmod(rem // 60, 60)
    w, d   = divmod(d, 7)
    if w:
        return f"{w}w{d}d" if d else f"{w}w"
    if d:
        return f"{d}d{h}h" if h else f"{d}d"
    return f"{h}h{m}m" if m else f"{h}h"


def parse_codex(data: dict, _svc: dict) -> tuple[str, str, list[str]]:
    if data.get("_unconfigured"):
        return "not configured", "–", ["Set codex.bearer_token in config"]
    if err := data.get("_error"):
        return f"✗ {err}", "✗", [f"Error: {err}"]

    plan  = data.get("plan_type", "?")
    pw    = (data.get("rate_limit") or {}).get("primary_window") or {}
    pct   = pw.get("used_percent")

    if pct is None:
        return f"({plan}) ?", "CX ?", [f"Plan: {plan}"]

    reset_str, reset_long = _codex_reset_strings(data)
    bar       = _bar(int(pct))
    bar_lbl   = f"{bar} {int(pct)}% ({reset_str})" if reset_str else f"{bar} {int(pct)}%"
    mb_lbl    = f"CX {int(pct)}%"
    details   = [f"{bar} {int(pct)}%  ↺{reset_long}" if reset_long else f"{bar} {int(pct)}%",
                 f"Plan: {plan}"]
    # Signal the overlay to use "Codex (fr)" as the row label (abbreviated to keep alignment)
    data["_display_label"] = f"Codex {plan[:2]}"
    return bar_lbl, mb_lbl, details


# ─── Dispatch table ──────────────────────────────────────────────────────────

HANDLERS = {
    "claude_5h":   (fetch_claude_cached, parse_claude_5h),
    "claude_7d":   (fetch_claude_cached, parse_claude_7d),
    "openrouter":  (fetch_openrouter,    parse_openrouter),
    "amp":          (fetch_amp,           parse_amp),
    "amp_credits":  (fetch_amp_credits,  parse_amp_credits),
    "codex":        (fetch_codex,        parse_codex),
    "generic":     (fetch_generic,       parse_generic),
}


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _pick(d: dict, *keys):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _bar(pct: int, w: int = 6) -> str:
    filled = round(max(0, min(pct, 100)) / 100 * w)
    return "▕" + "█" * filled + "░" * (w - filled) + "▏"


def _n(n) -> str:
    if isinstance(n, float): return f"{n:,.2f}"
    if isinstance(n, int):   return f"{n:,}"
    return str(n)


def _on_main(fn):
    NSOperationQueue.mainQueue().addOperationWithBlock_(fn)


# ─── Overlay window ──────────────────────────────────────────────────────────

def _make_label(frame, text: str, color=None) -> NSTextField:
    lbl = NSTextField.alloc().initWithFrame_(frame)
    lbl.setEditable_(False)
    lbl.setBordered_(False)
    lbl.setDrawsBackground_(False)
    lbl.setSelectable_(False)
    lbl.setStringValue_(text)
    lbl.setTextColor_(color or COL_WHITE)
    lbl.setFont_(NSFont.monospacedSystemFontOfSize_weight_(FONT_SIZE, 0.0))
    return lbl


class DraggableWindow(NSWindow):
    def mouseDown_(self, event):
        pass

    def mouseDragged_(self, event):
        self.performWindowDragWithEvent_(event)

    def mouseUp_(self, event):
        origin = self.frame().origin
        try:
            POSITION_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(POSITION_PATH, "w") as f:
                json.dump({"x": origin.x, "y": origin.y}, f)
        except Exception:
            pass


class Overlay:
    def __init__(self, services: list[dict]):
        self._services = services
        self._label_col_w = max(len(s["label"]) for s in services)
        win_w = label_width(services)
        win_h = PAD_TOP + len(services) * ROW_H + PAD_BOT
        x, y  = self._initial_position(win_w)

        win = DraggableWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, win_w, win_h),
            NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered,
            False,
        )
        win.setBackgroundColor_(NSColor.clearColor())
        win.setOpaque_(False)
        win.setHasShadow_(True)
        win.setLevel_(NSFloatingWindowLevel)
        win.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorIgnoresCycle
        )

        bg = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, win_w, win_h))
        bg.setMaterial_(NSVisualEffectMaterialHUDWindow)
        bg.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        bg.setState_(NSVisualEffectStateActive)
        bg.setWantsLayer_(True)
        bg.layer().setCornerRadius_(0)
        bg.layer().setMasksToBounds_(True)
        win.setContentView_(bg)
        win.setAlphaValue_(0.93)

        # Build dot color table now that NSApp is initialised
        for svc_id, hex_val in SERVICE_DOT_HEX.items():
            SERVICE_DOT_COLORS[svc_id] = _hex_color(hex_val)

        self._labels: dict[str, NSTextField] = {}
        for i, svc in enumerate(services):
            y_pos = win_h - PAD_TOP - (i + 1) * ROW_H
            padded = svc["label"].ljust(self._label_col_w)
            lbl = _make_label(
                NSMakeRect(PAD_X, y_pos, win_w - PAD_X * 2, ROW_H),
                f"● {padded}  · · ·",
                COL_DIM,
            )
            bg.addSubview_(lbl)
            self._labels[svc["id"]] = lbl

        win.orderFrontRegardless()
        self._win = win

    def update_row(self, svc_id: str, text: str, state: str = "ok",
                   display_label: str | None = None):
        if svc_id not in self._labels:
            return
        text_color = {"ok": COL_WHITE, "error": COL_ERROR, "unconfigured": COL_DIM}[state]
        label_name  = display_label or next(s["label"] for s in self._services if s["id"] == svc_id)
        padded      = label_name.ljust(self._label_col_w)
        full        = f"● {padded}  {text}"
        dot_color   = SERVICE_DOT_COLORS.get(svc_id)
        lbl         = self._labels[svc_id]

        if dot_color:
            font     = NSFont.monospacedSystemFontOfSize_weight_(FONT_SIZE, 0.0)
            astr     = NSMutableAttributedString.alloc().initWithString_(full)
            full_rng = (0, len(full))
            astr.addAttribute_value_range_(NSFontAttributeName,            font,       full_rng)
            astr.addAttribute_value_range_(NSForegroundColorAttributeName, text_color, full_rng)
            astr.addAttribute_value_range_(NSForegroundColorAttributeName, dot_color,  (0, 1))
            lbl.setAttributedStringValue_(astr)
        else:
            lbl.setStringValue_(full)
            lbl.setTextColor_(text_color)

    def toggle(self):
        if self._win.isVisible():
            self._win.orderOut_(None)
        else:
            self._win.orderFrontRegardless()

    @property
    def visible(self) -> bool:
        return self._win.isVisible()

    @staticmethod
    def _initial_position(win_w: int) -> tuple[float, float]:
        try:
            pos = json.loads(POSITION_PATH.read_text())
            return pos["x"], pos["y"]
        except Exception:
            pass
        sr = NSScreen.mainScreen().visibleFrame()
        return sr.origin.x + sr.size.width - win_w - 20, sr.origin.y + 20


# ─── Rumps app ───────────────────────────────────────────────────────────────

class TokemonApp(rumps.App):
    def __init__(self):
        cfg = load_config()
        self._services = load_services(cfg)

        self._svc_items: dict[str, list[rumps.MenuItem]] = {
            svc["id"]: [
                rumps.MenuItem(f"  {svc['label']}  loading…"),
                rumps.MenuItem(f"  {svc['label']}  —"),
            ]
            for svc in self._services
        }
        self._toggle_item = rumps.MenuItem("Hide overlay", callback=self.toggle_overlay)

        menu = []
        for svc in self._services:
            menu += self._svc_items[svc["id"]]
            menu.append(None)
        menu += [
            self._toggle_item,
            rumps.MenuItem("Refresh now",  callback=self.refresh_now),
            rumps.MenuItem("Edit config…", callback=self.open_config),
            None,
            rumps.MenuItem("Quit", callback=self._quit),
        ]

        super().__init__(name="Tokemon", title="⬡", menu=menu, quit_button=None)
        ensure_config()

        self._overlay = None  # created on first _apply_pending tick
        self._pending = queue.Queue()
        self._inited = False

    def _quit(self, _):
        if self._overlay is not None:
            self._overlay._win.orderOut_(None)
        rumps.quit_application()

    @rumps.timer(1)
    def _apply_pending(self, _):
        """Runs on rumps' main thread — safe for all UI work."""
        if not self._inited:
            self._inited = True
            try:
                self._overlay = Overlay(self._services)
            except Exception:
                traceback.print_exc()
            threading.Thread(target=self._fetch_all, daemon=True).start()
            return

        try:
            updates = self._pending.get_nowait()
        except queue.Empty:
            return

        for svc_id, bar_lbl, mb_lbl, state, details, svc, disp_label in updates:
            if self._overlay is not None:
                self._overlay.update_row(svc_id, bar_lbl, state, display_label=disp_label)
            if svc_id in self._svc_items:
                items = self._svc_items[svc_id]
                items[0].title = f"  {svc['label']}  {mb_lbl}"
                detail_text = "  " + "   ".join(d for d in details if d) if details else "  —"
                items[1].title = detail_text[:70]

    @rumps.timer(REFRESH_INTERVAL)
    def on_tick(self, _):
        threading.Thread(target=self._fetch_all, daemon=True).start()

    def refresh_now(self, _):
        threading.Thread(target=self._fetch_all, daemon=True).start()

    def toggle_overlay(self, _):
        if self._overlay is None:
            return
        self._overlay.toggle()
        self._toggle_item.title = "Hide overlay" if self._overlay.visible else "Show overlay"

    def open_config(self, _):
        ensure_config()
        subprocess.run(["open", str(CONFIG_PATH)])

    def _fetch_all(self):
        cfg      = load_config()
        services = load_services(cfg)   # pick up any config changes
        updates  = []

        for svc in services:
            svc_id   = svc["id"]
            svc_type = svc.get("type", "generic")
            fetcher, parser = HANDLERS.get(svc_type, HANDLERS["generic"])
            try:
                data                     = fetcher(cfg, svc)
                bar_lbl, mb_lbl, details = parser(data, svc)
                unconfigured             = bool(data.get("_unconfigured"))
                is_error                 = bool(data.get("_error")) and not unconfigured
            except Exception:
                bar_lbl, mb_lbl = "✗ crash", "✗"
                details         = [traceback.format_exc().splitlines()[-1][:60]]
                unconfigured    = False
                is_error        = True

            state = "error" if is_error else ("unconfigured" if unconfigured else "ok")
            disp_label = data.get("_display_label")
            updates.append((svc_id, bar_lbl, mb_lbl, state, details, svc, disp_label))

        self._pending.put(updates)


# ─── Entry ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    TokemonApp().run()
