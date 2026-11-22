"""claude_limits — the account's 5-hour session and weekly plan limits (percent used and reset time).

Opt-in: only used when the config sets "plan_limits": true. It reads the Claude Code login token from
~/.claude/.credentials.json (or "credentials_file") and asks Anthropic's OAuth usage endpoint
(https://api.anthropic.com/api/oauth/usage). That endpoint is not documented, so any failure just means no limits are returned.
The token is held in memory for the call only: never logged, written or sent to a client. Standard library only.
"""
import json, os, sys, time, urllib.request
from datetime import datetime

URL = "https://api.anthropic.com/api/oauth/usage"
WINDOWS = ("five_hour", "seven_day")


def _token(path):
    with open(os.path.expanduser(path)) as f:
        oauth = json.load(f).get("claudeAiOauth") or {}
    expires = oauth.get("expiresAt")  # milliseconds; Claude Code refreshes the file while it runs
    if expires and expires / 1000 < time.time():
        raise RuntimeError("Claude login token has expired (start Claude Code to refresh it)")
    return oauth["accessToken"]


def parse(doc):
    """Reduce the API response to {window: {used_pct, resets_at}} for the windows that are present."""
    out = {}
    for key in WINDOWS:
        w = (doc or {}).get(key)
        if isinstance(w, dict) and isinstance(w.get("utilization"), (int, float)) and w.get("resets_at"):
            try:
                datetime.fromisoformat(w["resets_at"])
            except ValueError:
                continue
            out[key] = {"used_pct": round(float(w["utilization"]), 1), "resets_at": w["resets_at"]}
    return out


def fetch(credentials_file="~/.claude/.credentials.json", timeout=15):
    """Return parse()d limits, or raise. Callers treat any exception as 'limits unavailable'."""
    req = urllib.request.Request(URL, headers={"Authorization": "Bearer " + _token(credentials_file),
                                               "anthropic-beta": "oauth-2025-04-20", "User-Agent": "buzz-claude-usage"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return parse(json.load(r))


def safe_fetch(cfg):
    """fetch() for the server: {} when disabled or unavailable; logs the error type, never the token."""
    if not cfg.get("plan_limits"):
        return {}
    try:
        return fetch(cfg.get("credentials_file") or "~/.claude/.credentials.json")
    except Exception as e:
        print(f"plan limits unavailable: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        return {}
