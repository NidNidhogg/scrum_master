#!/usr/bin/env bash
# install_launchagent.sh — make the scrum_master bot start at login and
# restart on crash via a macOS LaunchAgent.
#
# Usage: ./deploy/install_launchagent.sh
# Idempotent: re-running reloads the agent with the current template.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="$REPO_DIR/deploy/launchd/com.scrummaster.bot.plist.template"
PLIST="$HOME/Library/LaunchAgents/com.scrummaster.bot.plist"
LABEL="com.scrummaster.bot"
PYTHON="$REPO_DIR/.venv/bin/python"

if [[ "$(uname)" != "Darwin" ]]; then
    echo "This installer is macOS-only (launchd)." >&2
    exit 1
fi

if [[ ! -x "$PYTHON" ]]; then
    echo "Missing venv python at $PYTHON — run: python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

if [[ ! -f "$REPO_DIR/.env" ]]; then
    echo "Missing $REPO_DIR/.env — copy .env.example and fill in TG_TOKEN etc." >&2
    exit 1
fi

mkdir -p "$REPO_DIR/logs" "$HOME/Library/LaunchAgents"
sed -e "s|{{PYTHON}}|$PYTHON|g" -e "s|{{REPO_DIR}}|$REPO_DIR|g" "$TEMPLATE" > "$PLIST"
plutil -lint "$PLIST" >/dev/null

# Reload: unload if present, then load fresh
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl unload -w "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl load -w "$PLIST"

echo "✅ LaunchAgent installed and loaded:"
echo "   plist:  $PLIST"
echo "   python: $PYTHON"
echo "   logs:   $REPO_DIR/logs/bot.{out,err}.log"
echo ""
echo "Manage:"
echo "   status:    launchctl list | grep $LABEL"
echo "   stop now:  launchctl bootout gui/$(id -u) $PLIST"
echo "   uninstall: ./deploy/uninstall_launchagent.sh"
