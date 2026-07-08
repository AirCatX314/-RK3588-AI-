# LabSafe Open Source Package

This package has been sanitized for public release.

## Before Running

1. Copy `config.example.json` to `config.json`.
2. Fill in local-only values in `config.json`:
   - AI provider API keys
   - SMTP sender, receiver, and app password
   - emergency administrator phone number
   - camera device paths or stream URLs
   - modem AT port if emergency calling is enabled
3. Set `LABSAFE_SECRET_KEY` in the runtime environment for stable Flask sessions.

## Redacted From This Export

- Real AI provider API keys
- Real SMTP sender, receiver, and authorization code
- Real emergency administrator phone number
- Runtime chat and alert history
- Runtime schedule state
- Python bytecode caches
- Compiled local sensor binary
- Large model artifacts and generated databases

## Files Intentionally Not Tracked

The `.gitignore` excludes runtime configuration and state files such as
`config.json`, `messages.json`, `schedule.json`, `agent_state.sqlite3`,
`uploads/`, `logs/`, and model binaries. Keep secrets in local configuration or
environment variables only.

