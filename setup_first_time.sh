#!/bin/bash
#
# setup_first_time.sh — One-command setup for first-time users.
#
# Checks all prerequisites, installs what's missing (with permission),
# builds the Docker container, and starts the web UI.
#
# Usage (from the project root):
#   ./setup_first_time.sh
#
# Supports: macOS 12+, Linux (Ubuntu/Debian)
# Windows users: use WSL2 or Git Bash and run this script there.
#

set -e

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

ok()   { echo -e "${GREEN}  ✅  $*${NC}"; }
warn() { echo -e "${YELLOW}  ⚠️   $*${NC}"; }
err()  { echo -e "${RED}  ❌  $*${NC}"; }
info() { echo -e "${CYAN}  ℹ️   $*${NC}"; }
step() { echo -e "\n${BOLD}${CYAN}▶ $*${NC}"; }

clear
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║     ATS Pipeline — First-Time Setup                  ║${NC}"
echo -e "${BOLD}║     This will take 10–20 minutes on first run        ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════╝${NC}"
echo ""
echo "  This script will:"
echo "    1. Check your system prerequisites"
echo "    2. Install anything missing (with your permission)"
echo "    3. Build the pipeline environment"
echo "    4. Open the web app in your browser"
echo ""
read -p "  Press Enter to continue, or Ctrl+C to cancel... " _

# ── Detect OS ─────────────────────────────────────────────────────────────────
OS="unknown"
if [[ "$OSTYPE" == "darwin"* ]]; then
  OS="mac"
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
  OS="linux"
elif [[ "$OSTYPE" == "msys"* || "$OSTYPE" == "cygwin"* ]]; then
  OS="windows"
fi

step "Checking operating system"
case "$OS" in
  mac)     ok "macOS detected" ;;
  linux)   ok "Linux detected" ;;
  windows) warn "Windows detected — using Git Bash/WSL2 mode" ;;
  *)       warn "Unknown OS: $OSTYPE — proceeding cautiously" ;;
esac

# ── Check: Git ────────────────────────────────────────────────────────────────
step "Checking Git"
if command -v git &>/dev/null; then
  GIT_VER=$(git --version | awk '{print $3}')
  ok "Git $GIT_VER is installed"
else
  err "Git is not installed"
  if [ "$OS" = "mac" ]; then
    echo ""
    echo "  Installing Git via Xcode Command Line Tools..."
    echo "  A popup will appear — click 'Install' and wait for it to finish."
    echo "  This may take 5–10 minutes."
    echo ""
    xcode-select --install 2>/dev/null || true
    echo ""
    read -p "  Press Enter once the Xcode tools installation is complete... " _
    if ! command -v git &>/dev/null; then
      err "Git still not found. Please install from https://git-scm.com and re-run this script."
      exit 1
    fi
    ok "Git installed successfully"
  elif [ "$OS" = "linux" ]; then
    echo "  Installing Git..."
    sudo apt-get update -qq && sudo apt-get install -y git
    ok "Git installed"
  else
    err "Please download Git from https://git-scm.com/download/win and re-run this script."
    exit 1
  fi
fi

# ── Check: Docker ─────────────────────────────────────────────────────────────
step "Checking Docker"
if ! command -v docker &>/dev/null; then
  err "Docker is not installed"
  echo ""
  echo "  Docker Desktop is required. Please:"
  echo ""
  if [ "$OS" = "mac" ]; then
    echo "    1. Go to: https://www.docker.com/products/docker-desktop"
    echo "    2. Click 'Download for Mac'"
    echo "    3. Open the .dmg file and drag Docker to Applications"
    echo "    4. Open Docker from your Applications folder"
    echo "    5. Wait for the whale icon in the menu bar to show 'Running'"
    # Try to open the download page
    open "https://www.docker.com/products/docker-desktop" 2>/dev/null || true
  elif [ "$OS" = "linux" ]; then
    echo "    Installing Docker Engine..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker "$USER"
    echo ""
    warn "You need to log out and back in for Docker permissions to take effect."
    echo "  After logging back in, re-run this script."
    exit 1
  else
    echo "    1. Go to: https://www.docker.com/products/docker-desktop"
    echo "    2. Download and install Docker Desktop for Windows"
    echo "    3. Restart your computer after installation"
    start "https://www.docker.com/products/docker-desktop" 2>/dev/null || true
  fi
  echo ""
  read -p "  Press Enter once Docker Desktop is installed and running... " _
  if ! command -v docker &>/dev/null; then
    err "Docker still not found. Please install Docker Desktop and re-run this script."
    exit 1
  fi
fi

DOCKER_VER=$(docker --version | awk '{print $3}' | tr -d ',')
ok "Docker $DOCKER_VER is installed"

# ── Check: Docker is actually running ─────────────────────────────────────────
step "Checking Docker is running"
MAX_WAIT=60
WAITED=0
while ! docker info &>/dev/null; do
  if [ "$WAITED" -eq 0 ]; then
    warn "Docker is installed but not running"
    echo ""
    echo "  Please open Docker Desktop and wait for it to start."
    if [ "$OS" = "mac" ]; then
      open -a "Docker" 2>/dev/null || true
    fi
    echo ""
    echo -n "  Waiting for Docker to start"
  fi
  echo -n "."
  sleep 3
  WAITED=$((WAITED + 3))
  if [ "$WAITED" -ge "$MAX_WAIT" ]; then
    echo ""
    err "Docker did not start within ${MAX_WAIT}s."
    err "Please open Docker Desktop manually and re-run this script."
    exit 1
  fi
done
echo ""
ok "Docker is running"

# ── Check: port 5001 is available ─────────────────────────────────────────────
step "Checking port 5001"
if lsof -i :5001 &>/dev/null 2>&1; then
  BLOCKER=$(lsof -i :5001 | awk 'NR==2{print $1}' 2>/dev/null || echo "unknown")
  warn "Port 5001 is in use by: $BLOCKER"
  echo ""
  echo "  The web app uses port 5001. Close the app using that port,"
  echo "  then re-run this script. Or, to check what's using it:"
  echo "    lsof -i :5001"
  echo ""
  read -p "  Press Enter to continue anyway (the app may not start)... " _
else
  ok "Port 5001 is available"
fi

# ── Check: docker compose ─────────────────────────────────────────────────────
step "Checking Docker Compose"
if docker compose version &>/dev/null 2>&1; then
  COMPOSE_VER=$(docker compose version | awk '{print $NF}')
  ok "Docker Compose $COMPOSE_VER is available"
elif command -v docker-compose &>/dev/null; then
  ok "docker-compose (standalone) is available"
  # Alias for compatibility
  alias "docker compose"="docker-compose"
else
  err "Docker Compose not found. Please update Docker Desktop to the latest version."
  exit 1
fi

# ── Make scripts executable ───────────────────────────────────────────────────
step "Setting up permissions"
if [ -d "scripts" ]; then
  chmod +x scripts/*.sh
  ok "Scripts are executable"
else
  err "Cannot find scripts/ folder. Are you running this from the project root?"
  echo "  Try: cd ~/ats-resume-pipeline && ./setup_first_time.sh"
  exit 1
fi

# ── Check input folders exist ─────────────────────────────────────────────────
step "Setting up folders"
bash scripts/init_dirs.sh --quiet
ok "Folders ready (input/, output/, chunks/, prompts/)"

# ── Check for resume PDFs ─────────────────────────────────────────────────────
step "Checking for resume files"
PDF_COUNT=$(find input/pdf -name "*.pdf" 2>/dev/null | wc -l | tr -d ' ')
if [ "$PDF_COUNT" -eq 0 ]; then
  warn "No resume PDFs found in input/pdf/"
  echo ""
  echo "  Before using the app, copy your resume PDF files to:"
  echo "    $(pwd)/input/pdf/"
  echo ""
  echo "  You can do this now or after setup — the app will remind you."
  echo ""
else
  ok "Found $PDF_COUNT resume PDF(s) in input/pdf/"
fi

# ── Build Docker container ────────────────────────────────────────────────────
step "Building the pipeline environment (this takes 5–15 min the first time)"
echo ""
echo "  Downloading and installing required tools inside Docker."
echo "  This only happens once. Subsequent starts take < 30 seconds."
echo ""
docker compose down --remove-orphans 2>/dev/null || true
docker compose up -d --build

# Wait for container to be healthy
echo ""
echo -n "  Waiting for container to start"
for i in $(seq 1 20); do
  if docker exec markitdown echo "ok" &>/dev/null 2>&1; then
    break
  fi
  echo -n "."
  sleep 2
done
echo ""

if ! docker exec markitdown echo "ok" &>/dev/null 2>&1; then
  err "Container did not start. Check Docker Desktop for error messages."
  exit 1
fi
ok "Pipeline container is running"

# ── Run initial pipeline if PDFs exist ───────────────────────────────────────
if [ "$PDF_COUNT" -gt 0 ]; then
  step "Processing your resume files (first-time conversion)"
  echo ""
  ./scripts/router.sh 2>/dev/null || warn "Router had warnings — check output/ folder"
  ok "Resume files processed"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}${BOLD}║     ✅  Setup Complete!                               ║${NC}"
echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════════╝${NC}"
echo ""
echo "  Everything is ready. Here's how to use it:"
echo ""
echo "  START THE APP:"
echo "    ./scripts/serve.sh"
echo "    Then open: http://localhost:5001"
echo ""
echo "  EVERY TIME YOU USE IT:"
echo "    1. Make sure Docker Desktop is open and running"
echo "    2. cd $(pwd)"
echo "    3. ./scripts/serve.sh"
echo ""
if [ "$PDF_COUNT" -eq 0 ]; then
  echo "  BEFORE FIRST USE:"
  echo "    Copy your resume PDFs to: $(pwd)/input/pdf/"
  echo "    Then click 'Run pipeline' in the web app."
  echo ""
fi
echo "  For help, see: SETUP.md"
echo ""

# ── Offer to start immediately ────────────────────────────────────────────────
read -p "  Start the web app now? [Y/n]: " START_NOW
if [[ "$START_NOW" =~ ^[Nn] ]]; then
  echo ""
  echo "  When you're ready, run: ./scripts/serve.sh"
  echo ""
else
  echo ""
  ./scripts/serve.sh
fi
