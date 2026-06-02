# token-usage-dash

Pushes selected coding-agent subscription usage to a [dot.mindreset.tech](https://dot.mindreset.tech) e-ink display as a 296×152 image.

![Token usage on e-ink display](docs/preview.jpg)

## What it shows

- **Claude** — 5-hour and 7-day utilization (% used, % left, time to reset)
- **OpenAI Codex** — 5-hour and weekly utilization
- **Kimi Code** — 5-hour and 7-day usage windows when returned by the Kimi usage API

Configure exactly two rendered providers with `USAGE_PROVIDERS`, for example `claude,openai`, `claude,kimi`, or `openai,kimi`.

## Setup

### 1. Install dependencies

```bash
uv sync
```

### 2. Configure

```bash
cp .env.sample .env
```

Edit `.env` with your credentials:

| Key | Description |
|-----|-------------|
| `QUOTE_API_KEY` | Bearer token from dot.mindreset.tech |
| `QUOTE_DEVICE_ID` | Device serial number |
| `USAGE_PROVIDERS` | Exactly two providers to render: `claude`, `openai`, `kimi` (default: `claude,openai`) |
| `OPENAI_ENABLED` | Legacy option: set to `false` to skip OpenAI only when `USAGE_PROVIDERS` is unset |
| `UPDATE_INTERVAL` | Seconds between updates in loop mode (default: `1800`) |
| `CODEX_ACCESS_TOKEN` | Override Codex OAuth token (optional; default: read from `~/.codex/auth.json`) |
| `CODEX_ACCOUNT_ID` | Override Codex account ID (optional) |
| `KIMI_CODE_ACCESS_TOKEN` | Override Kimi Code OAuth access token (optional) |
| `KIMI_API_KEY` | Override Kimi Code API key (optional) |
| `KIMI_CODE_BASE_URL` | Override Kimi Code base URL (default: `https://api.kimi.com/coding/v1`) |

### 3. Claude auth

Claude credentials are read automatically from `~/.claude/.credentials.json` (created when you authenticate with [Claude Code](https://claude.ai/code)).

### 4. OpenAI Codex auth

Codex credentials are read automatically from `~/.codex/auth.json` (created when you authenticate with [Codex](https://github.com/openai/codex)). Run `codex` once to log in.

Alternatively, set `CODEX_ACCESS_TOKEN` in `.env` to supply the token directly.

### 5. Kimi Code auth

Kimi credentials are read automatically from `~/.kimi/credentials/kimi-code.json` after `kimi login`.

Alternatively, set `KIMI_CODE_ACCESS_TOKEN` or `KIMI_API_KEY` in `.env` to supply the token directly.

### 6. Add Image API content in Content Studio

In the dot.mindreset.tech app, add an **Image API** content slot to your device. The script targets this slot.

## Usage

```bash
# One-shot update
uv run display.py

# Loop every 30 minutes
uv run display.py --loop

# Loop with custom interval and save preview PNG
uv run display.py --loop --interval 900 --preview

# Preview image without pushing to device
uv run render.py   # saves to /tmp/usage_preview.png

# Print usage to terminal only
uv run usage.py
uv run usage.py --providers claude,kimi
uv run usage.py --claude-only
uv run usage.py --openai-only
```

## Files

| File | Purpose |
|------|---------|
| `usage.py` | Fetches and normalizes Claude, OpenAI, and Kimi usage data |
| `render.py` | Renders the 296×152 PNG image |
| `display.py` | Orchestrates fetch → render → push to device |
