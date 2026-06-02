#!/usr/bin/env python3
"""
Fetch subscription plan usage for Claude, OpenAI Codex, and Kimi Code.

Claude: uses OAuth token from ~/.claude/.credentials.json
OpenAI Codex: uses OAuth token from ~/.codex/auth.json
              or CODEX_ACCESS_TOKEN env var / .env file
              endpoint: https://chatgpt.com/backend-api/wham/usage
Kimi Code: uses KIMI_CODE_ACCESS_TOKEN / KIMI_API_KEY, or OAuth token from
           ~/.kimi/credentials/kimi-code.json
           endpoint: https://api.kimi.com/coding/v1/usages
"""

import json
import os
import platform
import socket
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import requests
from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Shared provider model
# ---------------------------------------------------------------------------

SUPPORTED_PROVIDERS = ("claude", "openai", "kimi")
DEFAULT_PROVIDERS = ("claude", "openai")


@dataclass
class UsageRow:
    label: str
    used_percent: float
    resets_at: Optional[datetime] = None
    reset_hint: Optional[str] = None


@dataclass
class UsageProviderResult:
    provider_id: str
    title: str
    rows: list[UsageRow]
    subtitle: Optional[str] = None


def parse_usage_providers(raw: Optional[str] = None) -> tuple[str, str]:
    if raw is None:
        raw = os.environ.get("USAGE_PROVIDERS", "").strip()
    if not raw:
        return DEFAULT_PROVIDERS

    providers = tuple(part.strip().lower() for part in raw.split(",") if part.strip())
    if len(providers) != 2:
        raise ValueError("USAGE_PROVIDERS must contain exactly two providers")
    if len(set(providers)) != len(providers):
        raise ValueError("USAGE_PROVIDERS must not contain duplicate providers")
    unknown = [provider for provider in providers if provider not in SUPPORTED_PROVIDERS]
    if unknown:
        supported = ", ".join(SUPPORTED_PROVIDERS)
        raise ValueError(f"Unknown usage provider {unknown[0]!r}; supported: {supported}")
    return providers  # type: ignore[return-value]

# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------

CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"
USAGE_ENDPOINT   = "https://api.anthropic.com/api/oauth/usage"
TOKEN_ENDPOINT   = "https://console.anthropic.com/v1/oauth/token"
CLIENT_ID        = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"


def load_credentials() -> dict:
    if not CREDENTIALS_PATH.exists():
        raise FileNotFoundError(
            f"No credentials found at {CREDENTIALS_PATH}. "
            "Run `claude` to authenticate first."
        )
    with open(CREDENTIALS_PATH) as f:
        data = json.load(f)
    return data["claudeAiOauth"]


def save_credentials(creds: dict) -> None:
    with open(CREDENTIALS_PATH) as f:
        data = json.load(f)
    data["claudeAiOauth"].update(creds)
    with open(CREDENTIALS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def _refresh_token(refresh_token: str) -> dict:
    resp = requests.post(
        TOKEN_ENDPOINT,
        json={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _fetch_claude_usage(access_token: str) -> dict:
    resp = requests.get(
        USAGE_ENDPOINT,
        headers={
            "Authorization": f"Bearer {access_token}",
            "anthropic-beta": "oauth-2025-04-20",
        },
        timeout=10,
    )
    if resp.status_code == 429:
        raise RuntimeError("Rate limited by usage endpoint. Try again in a few minutes.")
    resp.raise_for_status()
    return resp.json()


def get_claude_usage() -> dict:
    creds = load_credentials()
    access_token = creds["accessToken"]
    try:
        return _fetch_claude_usage(access_token)
    except requests.HTTPError as e:
        if e.response.status_code != 401:
            raise
        new = _refresh_token(creds["refreshToken"])
        save_credentials({
            "accessToken": new["access_token"],
            "refreshToken": new.get("refresh_token", creds["refreshToken"]),
        })
        return _fetch_claude_usage(new["access_token"])


# ---------------------------------------------------------------------------
# OpenAI Codex — OAuth API (token from ~/.codex/auth.json)
# ---------------------------------------------------------------------------

CODEX_AUTH_PATH  = Path.home() / ".codex" / "auth.json"
CODEX_USAGE_URL  = "https://chatgpt.com/backend-api/wham/usage"


@dataclass
class RateWindow:
    used_percent: float
    resets_at: Optional[datetime] = None


@dataclass
class OpenAIUsage:
    primary_limit: Optional[RateWindow] = None    # 5-hour
    secondary_limit: Optional[RateWindow] = None  # weekly
    credits_remaining: Optional[float] = None
    account_plan: Optional[str] = None


def _load_codex_token() -> tuple[str, str]:
    """Return (access_token, account_id). .env / env var takes priority."""
    env_token = os.environ.get("CODEX_ACCESS_TOKEN", "").strip()
    if env_token:
        account_id = os.environ.get("CODEX_ACCOUNT_ID", "").strip()
        return env_token, account_id

    if not CODEX_AUTH_PATH.exists():
        raise FileNotFoundError(
            f"No Codex credentials found at {CODEX_AUTH_PATH}. "
            "Run `codex` to authenticate first, or set CODEX_ACCESS_TOKEN in .env."
        )
    with open(CODEX_AUTH_PATH) as f:
        auth = json.load(f)
    tokens = auth["tokens"]
    return tokens["access_token"], tokens.get("account_id", "")


def get_openai_usage() -> OpenAIUsage:
    """Fetch OpenAI Codex plan usage via the OAuth API."""
    access_token, account_id = _load_codex_token()

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "User-Agent": "token-usage-dash",
    }
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id

    resp = requests.get(CODEX_USAGE_URL, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    usage = OpenAIUsage()
    usage.account_plan = data.get("plan_type")

    credits = data.get("credits", {})
    if credits.get("balance") is not None:
        usage.credits_remaining = float(credits["balance"])

    rate_limit = data.get("rate_limit", {})

    def _window(w: Optional[dict]) -> Optional[RateWindow]:
        if not w:
            return None
        used_pct = float(w.get("used_percent", 0))
        reset_ts = w.get("reset_at")
        resets_at = datetime.fromtimestamp(reset_ts, tz=timezone.utc) if reset_ts else None
        return RateWindow(used_percent=used_pct, resets_at=resets_at)

    usage.primary_limit   = _window(rate_limit.get("primary_window"))
    usage.secondary_limit = _window(rate_limit.get("secondary_window"))

    return usage


# ---------------------------------------------------------------------------
# Kimi Code — OAuth / API key usage API
# ---------------------------------------------------------------------------

KIMI_CREDENTIALS_PATH = Path.home() / ".kimi" / "credentials" / "kimi-code.json"
KIMI_DEVICE_ID_PATH = Path.home() / ".kimi" / "device_id"
KIMI_DEFAULT_BASE_URL = "https://api.kimi.com/coding/v1"
KIMI_DEFAULT_OAUTH_HOST = "https://auth.kimi.com"
KIMI_CODE_CLIENT_ID = "17e5f671-d194-4dfb-9706-5516cb48c098"


def _kimi_base_url() -> str:
    return os.environ.get("KIMI_CODE_BASE_URL", KIMI_DEFAULT_BASE_URL).strip().rstrip("/")


def _kimi_oauth_host() -> str:
    return (
        os.environ.get("KIMI_CODE_OAUTH_HOST")
        or os.environ.get("KIMI_OAUTH_HOST")
        or KIMI_DEFAULT_OAUTH_HOST
    ).strip().rstrip("/")


def _load_kimi_credentials() -> dict[str, Any]:
    if not KIMI_CREDENTIALS_PATH.exists():
        raise FileNotFoundError(
            f"No Kimi Code credentials found at {KIMI_CREDENTIALS_PATH}. "
            "Run `kimi login`, set KIMI_CODE_ACCESS_TOKEN, or set KIMI_API_KEY in .env."
        )
    with open(KIMI_CREDENTIALS_PATH) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid Kimi Code credentials at {KIMI_CREDENTIALS_PATH}")
    return data


def _save_kimi_credentials(creds: Mapping[str, Any]) -> None:
    KIMI_CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(KIMI_CREDENTIALS_PATH, "w") as f:
        json.dump(dict(creds), f, indent=2)
    try:
        os.chmod(KIMI_CREDENTIALS_PATH, 0o600)
    except OSError:
        pass


def _kimi_device_id() -> str:
    try:
        if KIMI_DEVICE_ID_PATH.exists():
            return KIMI_DEVICE_ID_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return "unknown"


def _kimi_common_headers() -> dict[str, str]:
    def _ascii(value: str) -> str:
        sanitized = value.encode("ascii", errors="ignore").decode("ascii").strip()
        return sanitized or "unknown"

    return {
        "X-Msh-Platform": "kimi_cli",
        "X-Msh-Version": "token-usage-dash",
        "X-Msh-Device-Name": _ascii(platform.node() or socket.gethostname()),
        "X-Msh-Device-Model": _ascii(f"{platform.system()} {platform.release()}"),
        "X-Msh-Os-Version": _ascii(platform.version()),
        "X-Msh-Device-Id": _ascii(_kimi_device_id()),
    }


def _refresh_kimi_token(refresh_token: str) -> dict[str, Any]:
    resp = requests.post(
        f"{_kimi_oauth_host()}/api/oauth/token",
        data={
            "client_id": KIMI_CODE_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        headers=_kimi_common_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    expires_in = float(data.get("expires_in") or 0)
    if expires_in:
        data["expires_at"] = datetime.now(timezone.utc).timestamp() + expires_in
    return data


def _load_kimi_token() -> tuple[str, Optional[dict[str, Any]]]:
    for key in ("KIMI_CODE_ACCESS_TOKEN", "KIMI_API_KEY"):
        token = os.environ.get(key, "").strip()
        if token:
            return token, None

    creds = _load_kimi_credentials()
    token = str(creds.get("access_token") or "")
    expires_at = float(creds.get("expires_at") or 0)
    refresh_token = str(creds.get("refresh_token") or "")

    if token and (not expires_at or expires_at - datetime.now(timezone.utc).timestamp() > 300):
        return token, creds
    if refresh_token:
        refreshed = _refresh_kimi_token(refresh_token)
        updated = {
            **creds,
            "access_token": refreshed["access_token"],
            "refresh_token": refreshed.get("refresh_token", refresh_token),
            "expires_at": refreshed.get("expires_at", creds.get("expires_at", 0)),
            "scope": refreshed.get("scope", creds.get("scope", "")),
            "token_type": refreshed.get("token_type", creds.get("token_type", "Bearer")),
            "expires_in": refreshed.get("expires_in", creds.get("expires_in", 0)),
        }
        _save_kimi_credentials(updated)
        return str(updated["access_token"]), updated
    if token:
        return token, creds
    raise RuntimeError("Kimi Code credentials do not contain an access token or refresh token")


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (OverflowError, TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        if "." in text and text.endswith("Z"):
            base, frac = text[:-1].split(".", 1)
            text = f"{base}.{frac[:6]}Z"
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _reset_from_kimi(data: Mapping[str, Any]) -> tuple[Optional[datetime], Optional[str]]:
    for key in ("reset_at", "resetAt", "reset_time", "resetTime"):
        dt = _parse_datetime(data.get(key))
        if dt is not None:
            return dt, None

    for key in ("reset_in", "resetIn", "ttl"):
        seconds = _to_int(data.get(key))
        if seconds is not None:
            return datetime.now(timezone.utc) + timedelta(seconds=seconds), None

    return None, None


def _kimi_label(
    item: Mapping[str, Any],
    detail: Mapping[str, Any],
    window: Mapping[str, Any],
    idx: int,
) -> str:
    raw_label = " ".join(
        str(value)
        for value in (
            item.get("name"),
            item.get("title"),
            item.get("scope"),
            detail.get("name"),
            detail.get("title"),
        )
        if value
    ).lower()
    duration = _to_int(window.get("duration") or item.get("duration") or detail.get("duration"))
    time_unit = str(window.get("timeUnit") or item.get("timeUnit") or detail.get("timeUnit") or "")

    if "5" in raw_label and ("hour" in raw_label or "5h" in raw_label):
        return "5h"
    if "week" in raw_label or "7d" in raw_label or "seven" in raw_label:
        return "7d"
    if duration:
        unit = time_unit.upper()
        if "MINUTE" in unit and duration == 300:
            return "5h"
        if "HOUR" in unit and duration == 5:
            return "5h"
        if "DAY" in unit and duration == 7:
            return "7d"
    return f"L{idx + 1}"


def _kimi_row_from_mapping(
    data: Mapping[str, Any],
    label: str,
) -> Optional[UsageRow]:
    limit = _to_int(data.get("limit"))
    used = _to_int(data.get("used"))
    remaining = _to_int(data.get("remaining"))
    if used is None and remaining is not None and limit is not None:
        used = limit - remaining
    if used is None and limit is None:
        return None

    if limit and limit > 0:
        used_percent = max(0.0, min(float(used or 0) / limit * 100.0, 100.0))
    else:
        used_percent = 0.0
    resets_at, reset_hint = _reset_from_kimi(data)
    return UsageRow(label=label, used_percent=used_percent, resets_at=resets_at, reset_hint=reset_hint)


def _parse_kimi_payload(payload: Mapping[str, Any]) -> UsageProviderResult:
    rows: list[UsageRow] = []

    raw_limits = payload.get("limits")
    if isinstance(raw_limits, Sequence) and not isinstance(raw_limits, (str, bytes)):
        for idx, item in enumerate(raw_limits):
            if not isinstance(item, Mapping):
                continue
            detail_raw = item.get("detail")
            detail = detail_raw if isinstance(detail_raw, Mapping) else item
            window_raw = item.get("window")
            window = window_raw if isinstance(window_raw, Mapping) else {}
            row = _kimi_row_from_mapping(detail, _kimi_label(item, detail, window, idx))
            if row:
                rows.append(row)

    usage = payload.get("usage")
    if isinstance(usage, Mapping):
        summary = _kimi_row_from_mapping(usage, "7d")
        if summary and not rows:
            rows.append(summary)

    return UsageProviderResult(provider_id="kimi", title="Kimi Code", rows=rows)


def get_kimi_usage() -> UsageProviderResult:
    access_token, _ = _load_kimi_token()
    resp = requests.get(
        f"{_kimi_base_url()}/usages",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "User-Agent": "token-usage-dash",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return _parse_kimi_payload(resp.json())


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def claude_to_provider_result(usage: dict) -> UsageProviderResult:
    rows: list[UsageRow] = []
    for key, label in [
        ("five_hour", "5h"),
        ("seven_day", "7d"),
        ("seven_day_sonnet", "7dS"),
        ("seven_day_opus", "7dO"),
    ]:
        window = usage.get(key)
        if not window:
            continue
        rows.append(
            UsageRow(
                label=label,
                used_percent=float(window["utilization"]),
                resets_at=_parse_datetime(window.get("resets_at")),
            )
        )
    return UsageProviderResult(provider_id="claude", title="Claude", rows=rows)


def openai_to_provider_result(usage: OpenAIUsage) -> UsageProviderResult:
    rows: list[UsageRow] = []
    if usage.primary_limit:
        rows.append(
            UsageRow("5h", usage.primary_limit.used_percent, usage.primary_limit.resets_at)
        )
    if usage.secondary_limit:
        rows.append(
            UsageRow("Wk", usage.secondary_limit.used_percent, usage.secondary_limit.resets_at)
        )

    subtitle = None
    if usage.credits_remaining is not None:
        subtitle = f"{usage.credits_remaining:.0f} cr"
    return UsageProviderResult(
        provider_id="openai",
        title="OpenAI Codex",
        subtitle=subtitle,
        rows=rows,
    )


def get_provider_usage(provider_id: str) -> UsageProviderResult:
    if provider_id == "claude":
        return claude_to_provider_result(get_claude_usage())
    if provider_id == "openai":
        return openai_to_provider_result(get_openai_usage())
    if provider_id == "kimi":
        return get_kimi_usage()
    raise ValueError(f"Unknown usage provider {provider_id!r}")


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def format_time_until(dt: Optional[datetime]) -> str:
    if dt is None:
        return "unknown"
    delta = dt - datetime.now(timezone.utc)
    seconds = int(delta.total_seconds())
    if seconds <= 0:
        return "now"
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    return f"{h}h {m}m" if h > 0 else f"{m}m"


def format_time_until_iso(iso_str: str) -> str:
    return format_time_until(datetime.fromisoformat(iso_str))


def _bar(used_pct: float, width: int = 20) -> str:
    filled = int(used_pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


def print_claude_usage(usage: dict) -> None:
    labels = {
        "five_hour":        "5-hour   ",
        "seven_day":        "7-day    ",
        "seven_day_sonnet": "7d Sonnet",
        "seven_day_opus":   "7d Opus  ",
    }
    print("Claude plan usage:")
    any_data = False
    for key, label in labels.items():
        window = usage.get(key)
        if not window:
            continue
        any_data = True
        util = window["utilization"]
        remaining = 100 - util
        resets = format_time_until_iso(window["resets_at"])
        print(f"  {label}  [{_bar(util)}] {util:5.1f}% used  {remaining:5.1f}% left  resets in {resets}")
    if not any_data:
        print("  No usage data returned.")


def print_openai_usage(usage: OpenAIUsage) -> None:
    print("OpenAI Codex plan usage:")
    if usage.account_plan:
        print(f"  Plan: {usage.account_plan}")
    if usage.credits_remaining is not None:
        print(f"  Credits remaining: {usage.credits_remaining:,.1f}")
    if usage.primary_limit:
        w = usage.primary_limit
        resets = format_time_until(w.resets_at)
        print(f"  5-hour   [{_bar(w.used_percent)}] {w.used_percent:5.1f}% used  {100-w.used_percent:5.1f}% left  resets in {resets}")
    if usage.secondary_limit:
        w = usage.secondary_limit
        resets = format_time_until(w.resets_at)
        print(f"  Weekly   [{_bar(w.used_percent)}] {w.used_percent:5.1f}% used  {100-w.used_percent:5.1f}% left  resets in {resets}")


def print_provider_usage(usage: UsageProviderResult) -> None:
    print(f"{usage.title} plan usage:")
    if usage.subtitle:
        print(f"  {usage.subtitle}")
    if not usage.rows:
        print("  No usage data returned.")
        return
    for row in usage.rows:
        resets = row.reset_hint or format_time_until(row.resets_at)
        remaining = 100 - row.used_percent
        print(
            f"  {row.label:<8}  [{_bar(row.used_percent)}] "
            f"{row.used_percent:5.1f}% used  {remaining:5.1f}% left  resets in {resets}"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Show subscription plan usage")
    parser.add_argument("--claude-only", action="store_true")
    parser.add_argument("--openai-only", action="store_true")
    parser.add_argument("--providers", help="Comma-separated providers to show, e.g. claude,kimi")
    args = parser.parse_args()

    if args.claude_only and args.openai_only:
        parser.error("--claude-only and --openai-only cannot be used together")
    if args.providers and (args.claude_only or args.openai_only):
        parser.error("--providers cannot be combined with --claude-only or --openai-only")

    if args.claude_only:
        providers = ("claude",)
    elif args.openai_only:
        providers = ("openai",)
    else:
        providers = parse_usage_providers(args.providers)

    errors = []

    for provider in providers:
        print()
        try:
            print_provider_usage(get_provider_usage(provider))
        except Exception as e:
            errors.append(str(e))
            print(f"{provider}: error — {e}")

    print()
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
