#!/usr/bin/env bash
# uninstall_launchagent.sh — remove the bot's LaunchAgent.
set -euo pipefail

PLIST="$HOME/Library/LaunchAgents/com.scrummaster.bot.plist"
LABEL="com.scrummaster.bot"

if [[ -f "$PLIST" ]]; then
    launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl unload -w "$PLIST" 2>/dev/null || true
    rm "$PLIST"
    echo "✅ LaunchAgent $LABEL removed."
else
    launchctl bootout "gui/$(id -u)" "$LABEL" 2>/dev/null || launchctl unload -w "$PLIST" 2>/dev/null || true
    echo "✅ LaunchAgent $LABEL stopped (no plist found)."
fi
