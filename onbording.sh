#!/bin/bash

chmod +x "$0"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ZSHRC="$HOME/.zshrc"
EXPORT_LINE='export ANTHROPIC_BASE_URL=http://localhost:8082'
PORT=8082
PLIST="$HOME/Library/LaunchAgents/com.tokencost.env.plist"
PLIST_PROXY="$HOME/Library/LaunchAgents/com.tokencost.proxy.plist"
PLIST_SYNC="$HOME/Library/LaunchAgents/com.tokencost.sync.plist"
SMART_FILE="$SCRIPT_DIR/.smart_routing"

# ─── Helpers ──────────────────────────────────────────────────────────────────
smart_routing_on() {
  [ -f "$SMART_FILE" ] && [ "$(cat "$SMART_FILE" 2>/dev/null)" = "1" ]
}

proxy_in_zshrc() {
  grep -q "ANTHROPIC_BASE_URL" "$ZSHRC" 2>/dev/null
}

proxy_running() {
  lsof -i :$PORT &>/dev/null
}

stop_proxy() {
  kill $(lsof -t -i:$PORT) 2>/dev/null
  sleep 1
}

remove_from_zshrc() {
  sed -i '' '/# TokenCost/d' "$ZSHRC" 2>/dev/null
  sed -i '' '/ANTHROPIC_BASE_URL/d' "$ZSHRC" 2>/dev/null
  sed -i '' '/alias tokencost=/d' "$ZSHRC" 2>/dev/null
}

launchd_active() {
  [ -f "$PLIST" ]
}

enable_launchd() {
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" << 'PLIST_EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.tokencost.env</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/launchctl</string>
        <string>setenv</string>
        <string>ANTHROPIC_BASE_URL</string>
        <string>http://localhost:8082</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
PLIST_EOF
  launchctl load "$PLIST" 2>/dev/null
  launchctl setenv ANTHROPIC_BASE_URL http://localhost:$PORT 2>/dev/null
}

proxy_daemon_active() {
  [ -f "$PLIST_PROXY" ]
}

enable_proxy_daemon() {
  local python_bin="$SCRIPT_DIR/venv/bin/python3"
  local proxy_script="$SCRIPT_DIR/proxy.py"
  local log_out="$SCRIPT_DIR/proxy.log"
  local log_err="$SCRIPT_DIR/proxy-error.log"

  local env_block=""
  if smart_routing_on; then
    env_block="
    <key>EnvironmentVariables</key>
    <dict>
        <key>SMART_ROUTING</key>
        <string>1</string>
    </dict>"
  fi

  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST_PROXY" << PROXY_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.tokencost.proxy</string>
    <key>ProgramArguments</key>
    <array>
        <string>${python_bin}</string>
        <string>-B</string>
        <string>${proxy_script}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${log_out}</string>
    <key>StandardErrorPath</key>
    <string>${log_err}</string>
    <key>WorkingDirectory</key>
    <string>${SCRIPT_DIR}</string>${env_block}
</dict>
</plist>
PROXY_EOF
  launchctl load "$PLIST_PROXY" 2>/dev/null
}

sync_daemon_active() {
  [ -f "$PLIST_SYNC" ]
}

enable_sync_daemon() {
  local python_bin="$SCRIPT_DIR/venv/bin/python3"
  local script="$SCRIPT_DIR/import_history.py"
  local log="$SCRIPT_DIR/sync.log"
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST_SYNC" << SYNC_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.tokencost.sync</string>
    <key>ProgramArguments</key>
    <array>
        <string>${python_bin}</string>
        <string>${script}</string>
        <string>--silent</string>
    </array>
    <key>StartInterval</key>
    <integer>300</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${log}</string>
    <key>StandardErrorPath</key>
    <string>${log}</string>
    <key>WorkingDirectory</key>
    <string>${SCRIPT_DIR}</string>
</dict>
</plist>
SYNC_EOF
  launchctl load "$PLIST_SYNC" 2>/dev/null
}

disable_launchd() {
  launchctl unload "$PLIST" 2>/dev/null
  launchctl unsetenv ANTHROPIC_BASE_URL 2>/dev/null
  rm -f "$PLIST"
  launchctl unload "$PLIST_PROXY" 2>/dev/null
  rm -f "$PLIST_PROXY"
  launchctl unload "$PLIST_SYNC" 2>/dev/null
  rm -f "$PLIST_SYNC"
}

# ─── Menu ─────────────────────────────────────────────────────────────────────
show_menu() {
  clear
  echo ""
  echo -e "${BOLD}  ╔═══════════════════════════════════╗${NC}"
  echo -e "${BOLD}  ║     💰  TokenCost                 ║${NC}"
  echo -e "${BOLD}  ╚═══════════════════════════════════╝${NC}"
  echo ""

  if proxy_running; then
    if proxy_daemon_active; then
      echo -e "  Proxy:      ${GREEN}● running${NC} (daemon, autostart ✓)"
    else
      echo -e "  Proxy:      ${GREEN}● running${NC} (foreground, port $PORT)"
    fi
  else
    if proxy_daemon_active; then
      echo -e "  Proxy:      ${YELLOW}○ daemon configured but not running${NC}"
    else
      echo -e "  Proxy:      ${RED}○ stopped${NC}"
    fi
  fi

  if proxy_in_zshrc; then
    echo -e "  Terminal:   ${GREEN}✓ configured${NC} (via proxy)"
  else
    echo -e "  Terminal:   ${YELLOW}✗ not configured${NC}"
  fi

  if launchd_active; then
    echo -e "  VS Code:    ${GREEN}✓ configured${NC} (via proxy)"
  else
    echo -e "  VS Code:    ${YELLOW}✗ not configured${NC}"
  fi

  if smart_routing_on; then
    echo -e "  Optimizer:  ${GREEN}✓ enabled${NC} (SMART_ROUTING — Haiku for simple requests)"
  else
    echo -e "  Optimizer:  ${YELLOW}✗ disabled${NC} (all requests go to original model)"
  fi

  echo ""
  echo -e "  ${DIM}────────────────────────────────────${NC}"
  echo ""
  echo -e "  ${BOLD}1${NC}  Start proxy + open dashboard"
  echo -e "  ${BOLD}2${NC}  Disable proxy completely"
  echo -e "  ${BOLD}3${NC}  Exit"
  echo ""
  echo -ne "  ${BOLD}Choose [1/2/3]:${NC} "
}

# ─── Action 1: Start ──────────────────────────────────────────────────────────
action_start() {
  clear
  echo ""
  echo -e "${BOLD}  💰 TokenCost — Setup${NC}"
  echo ""

  # ── Smart model routing ───────────────────────────────────────────────────────
  echo ""
  if smart_routing_on; then
    current_smart="${GREEN}currently: enabled${NC}"
  else
    current_smart="${YELLOW}currently: disabled${NC}"
  fi
  echo -e "  ${BOLD}⚡ Smart Model Routing${NC} (SMART_ROUTING)"
  echo -e "  ${DIM}Automatically switches Opus/Sonnet → Haiku for simple requests.${NC}"
  echo -e "  ${DIM}Saves ~60% on short commands, grep, \"what is X\", build/run.${NC}"
  echo -e "  ${DIM}(${current_smart}${DIM})${NC}"
  echo ""
  echo -ne "  Enable optimizer? [y/N]: "
  read -r smart_choice
  case "$smart_choice" in
    y|Y|yes|YES)
      echo "1" > "$SMART_FILE"
      echo -e "  ${GREEN}✓${NC} Optimizer enabled"
      ;;
    *)
      echo "0" > "$SMART_FILE"
      echo -e "  ${DIM}  Optimizer disabled${NC}"
      ;;
  esac

  if proxy_daemon_active; then
    launchctl unload "$PLIST_PROXY" 2>/dev/null
    rm -f "$PLIST_PROXY"
    echo -e "  ${DIM}  Daemon will be recreated with new settings${NC}"
  fi

  echo ""
  echo -e "${CYAN}  [1/8]${NC} Checking Python..."
  if ! command -v python3 &>/dev/null; then
    echo -e "  ${RED}✗ Python3 not found.${NC}"
    echo -e "  Install with: ${YELLOW}brew install python3${NC}"
    exit 1
  fi
  echo -e "  ${GREEN}✓${NC} ${DIM}$(python3 --version)${NC}"

  echo ""
  echo -e "${CYAN}  [2/8]${NC} Dependencies..."
  if [ ! -d "venv" ]; then
    echo -e "  ${DIM}Creating virtual environment...${NC}"
    python3 -m venv venv
  fi
  source venv/bin/activate
  if python3 -c "import fastapi, uvicorn, httpx" &>/dev/null; then
    echo -e "  ${GREEN}✓${NC} ${DIM}Already installed${NC}"
  else
    echo -e "  ${DIM}Installing packages (~30 sec)...${NC}"
    pip install fastapi uvicorn httpx -q
    if [ $? -ne 0 ]; then
      echo -e "  ${RED}✗ Installation error${NC}"
      exit 1
    fi
    echo -e "  ${GREEN}✓${NC} ${DIM}Installed${NC}"
  fi

  echo ""
  echo -e "${CYAN}  [3/8]${NC} Importing history from local logs..."
  if python3 "$SCRIPT_DIR/import_history.py" 2>/dev/null; then
    echo -e "  ${GREEN}✓${NC} ${DIM}History loaded into database${NC}"
    echo -e "  ${DIM}  (sync daemon will auto-update every 5 min)${NC}"
  else
    echo -e "  ${YELLOW}⚠${NC} ${DIM}Could not import history (non-critical)${NC}"
  fi

  echo ""
  echo -e "${CYAN}  [4/8]${NC} Auto-sync logs every 5 minutes..."
  if sync_daemon_active; then
    launchctl unload "$PLIST_SYNC" 2>/dev/null
    rm -f "$PLIST_SYNC"
  fi
  enable_sync_daemon
  echo -e "  ${GREEN}✓${NC} ${DIM}Daemon registered (every 5 min)${NC}"

  echo -e "  ${DIM}  Triggering initial sync...${NC}"
  python3 "$SCRIPT_DIR/import_history.py" --silent 2>/dev/null
  echo -e "  ${GREEN}✓${NC} ${DIM}Initial sync complete${NC}"

  echo ""
  echo -e "${CYAN}  [5/8]${NC} Configuring Terminal (Claude CLI)..."
  if proxy_in_zshrc; then
    echo -e "  ${GREEN}✓${NC} ${DIM}Already configured${NC}"
  else
    echo "" >> "$ZSHRC"
    echo "# TokenCost" >> "$ZSHRC"
    echo "$EXPORT_LINE" >> "$ZSHRC"
    echo "alias tokencost='bash $SCRIPT_DIR/onbording.sh'" >> "$ZSHRC"
    echo -e "  ${GREEN}✓${NC} ${DIM}Added to ~/.zshrc (alias: tokencost)${NC}"
  fi
  # ensure alias is always up to date
  if ! grep -q "alias tokencost=" "$ZSHRC" 2>/dev/null; then
    echo "alias tokencost='bash $SCRIPT_DIR/onbording.sh'" >> "$ZSHRC"
  fi
  export ANTHROPIC_BASE_URL=http://localhost:$PORT

  echo ""
  echo -e "${CYAN}  [6/8]${NC} Configuring VS Code and GUI apps..."
  if launchd_active; then
    echo -e "  ${GREEN}✓${NC} ${DIM}Already configured${NC}"
  else
    enable_launchd
    echo -e "  ${GREEN}✓${NC} ${DIM}Global env variable set${NC}"
    echo -e "  ${DIM}  ⚠ Restart VS Code to pick up the change${NC}"
  fi

  echo ""
  echo -e "${CYAN}  [7/8]${NC} Starting proxy (autostart on login)..."

  echo -e "  ${DIM}  Stopping old proxy...${NC}"
  launchctl unload "$PLIST_PROXY" 2>/dev/null
  rm -f "$PLIST_PROXY"
  for pid in $(lsof -t -i:$PORT 2>/dev/null); do
    kill -9 "$pid" 2>/dev/null
  done
  for i in 1 2 3 4 5; do
    lsof -i:$PORT &>/dev/null || break
    sleep 1
  done

  enable_proxy_daemon

  # Wait up to 15s for proxy to become ready
  echo -ne "  ${DIM}  Waiting for proxy..."
  for i in $(seq 1 15); do
    if proxy_running; then
      echo -e " ready${NC}"
      break
    fi
    echo -ne "."
    sleep 1
  done

  if proxy_running; then
    echo -e "  ${GREEN}✓${NC} ${DIM}Daemon started and added to autostart${NC}"
    echo -e "  ${DIM}  Final log sync...${NC}"
    python3 "$SCRIPT_DIR/import_history.py" --silent 2>/dev/null && \
      echo -e "  ${GREEN}✓${NC} ${DIM}Sync complete${NC}"
  else
    echo -e "  ${YELLOW}⚠${NC} ${DIM}Daemon did not start, running in foreground...${NC}"
    echo -e "  ${DIM}  (closing this terminal will stop the proxy)${NC}"
  fi

  echo ""
  echo -e "${CYAN}  [8/8]${NC} Menubar App..."
  MENUBAR_APP=""
  if   [ -d "$HOME/Applications/TokenCostBar.app" ]; then
    MENUBAR_APP="$HOME/Applications/TokenCostBar.app"
  elif [ -d "$SCRIPT_DIR/menubar/TokenCostBar.app" ]; then
    MENUBAR_APP="$SCRIPT_DIR/menubar/TokenCostBar.app"
  fi

  if [ -n "$MENUBAR_APP" ]; then
    pkill -x "TokenCostBar" 2>/dev/null
    sleep 0.5
    open "$MENUBAR_APP" 2>/dev/null
    echo -e "  ${GREEN}✓${NC} ${DIM}Launched: $MENUBAR_APP${NC}"
  else
    echo -e "  ${YELLOW}⚠${NC} ${DIM}TokenCostBar.app not found (optional)${NC}"
  fi

  echo ""
  echo -e "  ┌─────────────────────────────────────────────────┐"
  echo -e "  │  ${GREEN}✅ Setup complete!${NC}                               │"
  echo -e "  │                                                 │"
  echo -e "  │  ${BOLD}→  http://localhost:$PORT/dashboard${NC}              │"
  echo -e "  │                                                 │"
  echo -e "  └─────────────────────────────────────────────────┘"
  echo ""
  echo -e "  ${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo -e "  ${YELLOW}⚠  What needs to be restarted?${NC}"
  echo -e "  ${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo ""
  echo -e "  ${GREEN}✓${NC}  ${BOLD}New terminal / iTerm${NC}"
  echo -e "     Open a new terminal tab — ANTHROPIC_BASE_URL"
  echo -e "     will be picked up from ~/.zshrc automatically."
  echo ""
  echo -e "  ${YELLOW}↻${NC}  ${BOLD}VS Code, Cursor, Windsurf, JetBrains, Zed — RESTART${NC}"
  echo -e "     They are already open and don't know about the new ANTHROPIC_BASE_URL."
  echo -e "     Close and reopen once — after that no restarts needed."
  echo -e "     All Claude requests will start routing through the proxy."
  echo ""
  echo -e "  ${GREEN}✓${NC}  ${BOLD}Claude Desktop (Local Agent Mode)${NC}"
  echo -e "     No restart needed — proxy reads logs directly"
  echo -e "     from ~/Library/.../local-agent-mode-sessions every 5 min."
  echo ""
  echo -e "  ${GREEN}✓${NC}  ${BOLD}OpenClaw${NC}"
  echo -e "     No restart needed if already running."
  echo -e "     Proxy reads .jsonl logs from ~/.openclaw/agents/ every 5 min."
  echo ""
  echo -e "  ${GREEN}✓${NC}  ${BOLD}Cline / Roo Code / Kilo Code (VS Code extensions)${NC}"
  echo -e "     VS Code restart covers this — after restart"
  echo -e "     new tasks will appear in the report within 5 minutes."
  echo ""
  echo -e "  ${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo ""

  sleep 1
  open "http://localhost:$PORT/dashboard" 2>/dev/null || true

  if ! proxy_running; then
    source venv/bin/activate
    if smart_routing_on; then
      SMART_ROUTING=1 python3 proxy.py
    else
      python3 proxy.py
    fi
  else
    echo -e "  ${DIM}Proxy running in background. This window can be closed.${NC}"
    echo ""
  fi
}

# ─── Action 2: Disable ────────────────────────────────────────────────────────
action_disable() {
  clear
  echo ""
  echo -e "${BOLD}  💰 TokenCost — Disable${NC}"
  echo ""

  if proxy_running; then
    echo -e "  ${CYAN}→${NC} Stopping proxy..."
    stop_proxy
    echo -e "  ${GREEN}✓${NC} Proxy stopped"
  else
    echo -e "  ${DIM}  Proxy was not running${NC}"
  fi

  if proxy_in_zshrc; then
    echo -e "  ${CYAN}→${NC} Removing from ~/.zshrc..."
    remove_from_zshrc
    echo -e "  ${GREEN}✓${NC} Removed from ~/.zshrc"
  else
    echo -e "  ${DIM}  Nothing to remove from ~/.zshrc${NC}"
  fi

  if launchd_active; then
    echo -e "  ${CYAN}→${NC} Removing global env variable (VS Code)..."
    disable_launchd
    echo -e "  ${GREEN}✓${NC} Removed from launchd"
    echo -e "  ${DIM}  ⚠ Restart VS Code for changes to take effect${NC}"
  else
    echo -e "  ${DIM}  launchd was not configured${NC}"
  fi

  unset ANTHROPIC_BASE_URL

  if pgrep -x "TokenCostBar" &>/dev/null; then
    echo -e "  ${CYAN}→${NC} Closing menubar app..."
    pkill -x "TokenCostBar" 2>/dev/null
    echo -e "  ${GREEN}✓${NC} Menubar app closed"
  else
    echo -e "  ${DIM}  Menubar app was not running${NC}"
  fi

  echo ""
  echo -e "  ${GREEN}Done. TokenCost fully disabled.${NC}"
  echo -e "  ${DIM}  Claude CLI and VS Code now connect directly to Anthropic.${NC}"
  echo ""
  echo -ne "  Press Enter..."
  read -r
}

# ─── Main ─────────────────────────────────────────────────────────────────────
show_menu
read -r choice

case "$choice" in
  1) action_start ;;
  2) action_disable ;;
  3) exit 0 ;;
  *) echo -e "  ${RED}Invalid choice${NC}"; sleep 1 ;;
esac
