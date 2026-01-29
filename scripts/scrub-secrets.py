#!/usr/bin/env python3
"""
scrub-secrets.py — Remove likely secrets from state files before committing.

Runs after sync, before aggregate. Replaces patterns that GitHub push
protection (and common sense) would flag.

These are mirrors of already-public data, but we don't want to be the
permanent git archive of other people's leaked credentials.
"""

import re
import sys
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parent.parent / "state"

# Patterns: (regex, replacement, description)
PATTERNS = [
    # Google OAuth client secrets
    (r'GOCSPX-[A-Za-z0-9_-]+', 'GOCSPX-[REDACTED]', 'Google OAuth client secret'),
    # Google OAuth client IDs (full form)
    (r'[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com', '[REDACTED].apps.googleusercontent.com', 'Google OAuth client ID'),
    # GitHub personal access tokens
    (r'ghp_[A-Za-z0-9]{36,}', 'ghp_[REDACTED]', 'GitHub PAT'),
    # GitHub OAuth tokens
    (r'gho_[A-Za-z0-9]{36,}', 'gho_[REDACTED]', 'GitHub OAuth token'),
    # GitHub App tokens
    (r'ghs_[A-Za-z0-9]{36,}', 'ghs_[REDACTED]', 'GitHub App token'),
    (r'ghu_[A-Za-z0-9]{36,}', 'ghu_[REDACTED]', 'GitHub user token'),
    # OpenAI API keys
    (r'sk-[A-Za-z0-9]{20,}', 'sk-[REDACTED]', 'OpenAI-style API key'),
    # Google API keys
    (r'AIza[A-Za-z0-9_-]{35,}', 'AIza[REDACTED]', 'Google API key'),
    # AWS access keys
    (r'AKIA[A-Z0-9]{16,}', 'AKIA[REDACTED]', 'AWS access key'),
    # AWS pre-signed URLs (the whole credential param)
    (r'X-Amz-Credential=[^&\s]+', 'X-Amz-Credential=[REDACTED]', 'AWS pre-signed credential'),
    (r'X-Amz-Signature=[a-f0-9]+', 'X-Amz-Signature=[REDACTED]', 'AWS pre-signed signature'),
    # Slack tokens
    (r'xox[bsrp]-[A-Za-z0-9-]+', 'xox?-[REDACTED]', 'Slack token'),
    # Anthropic API keys
    (r'sk-ant-[A-Za-z0-9_-]+', 'sk-ant-[REDACTED]', 'Anthropic API key'),
    # Generic "Bearer" tokens in code blocks
    (r'Bearer [A-Za-z0-9_-]{20,}', 'Bearer [REDACTED]', 'Bearer token'),
    # Telegram bot tokens
    (r'[0-9]{8,10}:[A-Za-z0-9_-]{35}', '[REDACTED]:TELEGRAM_TOKEN', 'Telegram bot token'),
    # Discord bot tokens (base64-ish)
    (r'[MN][A-Za-z0-9]{23,}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}', '[REDACTED_DISCORD_TOKEN]', 'Discord bot token'),
]

def scrub_file(filepath):
    """Scrub secrets from a single file. Returns (changed, details)."""
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False, []

    original = text
    details = []

    for pattern, replacement, description in PATTERNS:
        matches = re.findall(pattern, text)
        if matches:
            text = re.sub(pattern, replacement, text)
            details.append(f"{description}: {len(matches)} occurrence(s)")

    if text != original:
        filepath.write_text(text, encoding="utf-8")
        return True, details
    return False, []


def main():
    total_files = 0
    scrubbed_files = 0

    for subdir in ["issues", "prs"]:
        d = STATE_DIR / subdir
        if not d.exists():
            continue
        for f in d.glob("*.md"):
            total_files += 1
            changed, details = scrub_file(f)
            if changed:
                scrubbed_files += 1
                print(f"[scrub] {f.name}: {'; '.join(details)}", file=sys.stderr)

    print(f"[scrub] Scanned {total_files} files, scrubbed {scrubbed_files}", file=sys.stderr)


if __name__ == "__main__":
    main()
