#!/usr/bin/env bash
# yt-jellyfin installer
# Safe to re-run on existing installs — idempotent where possible.
set -euo pipefail

# ── colors ─────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*"; }
header()  { echo -e "\n${BOLD}$*${RESET}"; }

# ── sudo check ─────────────────────────────────────────────────────────────────
header "yt-jellyfin Installer"
echo ""

if ! sudo -n true 2>/dev/null; then
    info "This installer needs sudo access for some steps."
    sudo -v || { error "Could not obtain sudo access. Please run as a user with sudo privileges."; exit 1; }
fi

# ── OS detection ───────────────────────────────────────────────────────────────
header "Checking operating system…"
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS_NAME="${NAME:-unknown}"
    OS_VERSION="${VERSION_ID:-unknown}"
else
    OS_NAME="unknown"
    OS_VERSION="unknown"
fi

if [[ "$OS_NAME" == *"Ubuntu"* ]] || [[ "$OS_NAME" == *"Debian"* ]]; then
    success "Detected: $OS_NAME $OS_VERSION"
else
    warn "Unsupported OS: $OS_NAME. This installer is tested on Ubuntu 22.04+ and Debian 11+."
    warn "Continuing anyway — some steps may fail."
fi

# ── install directory ──────────────────────────────────────────────────────────
header "Install location"
DEFAULT_INSTALL_DIR="/opt/yt-jellyfin"
echo -e "Default install directory: ${BOLD}$DEFAULT_INSTALL_DIR${RESET}"
read -rp "Install directory [press Enter for default]: " INSTALL_DIR
INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"
info "Installing to: $INSTALL_DIR"

# ── helper: fix or exit ────────────────────────────────────────────────────────
ask_fix() {
    local description="$1"
    local fix_cmd="$2"
    local manual_cmd="$3"

    warn "Missing: $description"
    read -rp "  Fix automatically? [y/N] " answer
    case "$answer" in
        [yY]*)
            info "Running: $fix_cmd"
            eval "$fix_cmd"
            return 0
            ;;
        *)
            error "Manual fix required:"
            echo -e "    ${BOLD}$manual_cmd${RESET}"
            echo ""
            echo "Run the installer again after fixing."
            exit 1
            ;;
    esac
}

# ── dependency checks ──────────────────────────────────────────────────────────
header "Checking dependencies…"

# python3 >= 3.11
if command -v python3 &>/dev/null; then
    PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
    if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 11 ]; then
        success "python3 $PY_VERSION"
    else
        ask_fix "python3 >= 3.11 (found $PY_VERSION)" \
            "sudo apt-get install -y python3.12" \
            "sudo apt-get install -y python3.12"
    fi
else
    ask_fix "python3 not found" \
        "sudo apt-get install -y python3" \
        "sudo apt-get install -y python3"
fi

# python3-venv
if python3 -c "import venv" &>/dev/null; then
    success "python3-venv"
else
    ask_fix "python3-venv not found" \
        "sudo apt-get install -y python3-venv" \
        "sudo apt-get install -y python3-venv"
fi

# ffmpeg
if command -v ffmpeg &>/dev/null; then
    success "ffmpeg $(ffmpeg -version 2>&1 | head -1 | awk '{print $3}')"
else
    ask_fix "ffmpeg not found (required for merging video/audio streams)" \
        "sudo apt-get install -y ffmpeg" \
        "sudo apt-get install -y ffmpeg"
fi

# git
if command -v git &>/dev/null; then
    success "git $(git --version | awk '{print $3}')"
else
    ask_fix "git not found (required for updates)" \
        "sudo apt-get install -y git" \
        "sudo apt-get install -y git"
fi

# curl
if command -v curl &>/dev/null; then
    success "curl"
else
    ask_fix "curl not found (required for yt-dlp install)" \
        "sudo apt-get install -y curl" \
        "sudo apt-get install -y curl"
fi

# sqlite3 (useful for DB maintenance)
if command -v sqlite3 &>/dev/null; then
    success "sqlite3"
else
    ask_fix "sqlite3 not found (useful for database maintenance)" \
        "sudo apt-get install -y sqlite3" \
        "sudo apt-get install -y sqlite3"
fi

# Node.js >= 22 (required by yt-dlp for YouTube JS challenge solving)
header "Checking Node.js (required for YouTube downloads)…"
NODE_OK=false
if command -v node &>/dev/null; then
    NODE_MAJOR=$(node --version | sed 's/v//' | cut -d. -f1)
    if [ "$NODE_MAJOR" -ge 22 ]; then
        success "node $(node --version)"
        NODE_OK=true
    else
        warn "Node.js $(node --version) found but version 22+ is required by yt-dlp."
    fi
fi

if [ "$NODE_OK" = false ]; then
    ask_fix "Node.js 22+ not found (required for YouTube JS challenge solving)" \
        "curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt-get install -y nodejs" \
        "curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt-get install -y nodejs"
    success "node $(node --version)"
fi

# yt-dlp — always install/update via curl, never apt
header "Installing yt-dlp (always from GitHub releases)…"
warn "The apt version of yt-dlp is frequently outdated and will fail with YouTube API errors."
warn "Installing latest version directly from GitHub…"

# Remove apt version if present
if dpkg -l yt-dlp &>/dev/null 2>&1; then
    info "Removing outdated apt version of yt-dlp…"
    sudo apt-get remove -y yt-dlp 2>/dev/null || true
fi

sudo curl -fsSL https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
    -o /usr/local/bin/yt-dlp
sudo chmod a+rx /usr/local/bin/yt-dlp

YTDLP_VERSION=$(yt-dlp --version 2>/dev/null || echo "unknown")
success "yt-dlp $YTDLP_VERSION (installed at /usr/local/bin/yt-dlp)"
info "NOTE: If downloads fail with HTTP 403 errors after install, YouTube may have"
info "broken the current stable release. Update to nightly with: sudo yt-dlp --update-to nightly"

# Configure yt-dlp to use Node.js for YouTube JS challenge solving
header "Configuring yt-dlp…"
sudo mkdir -p /etc/yt-dlp
if ! grep -q "js-runtimes" /etc/yt-dlp.conf 2>/dev/null; then
    echo "--js-runtimes node" | sudo tee /etc/yt-dlp.conf > /dev/null
    success "yt-dlp configured to use Node.js runtime (/etc/yt-dlp.conf)"
else
    success "yt-dlp already configured with JS runtime"
fi

# ── detect Jellyfin user ───────────────────────────────────────────────────────
header "Detecting Jellyfin user…"
JELLYFIN_USER=$(ps aux 2>/dev/null | grep -i "[j]ellyfin" | awk '{print $1}' | head -1)

if [ -n "$JELLYFIN_USER" ]; then
    success "Jellyfin is running as: $JELLYFIN_USER"
    read -rp "Use '$JELLYFIN_USER' as the service user? [Y/n] " answer
    case "$answer" in
        [nN]*) JELLYFIN_USER="" ;;
    esac
fi

if [ -z "$JELLYFIN_USER" ]; then
    read -rp "Enter the user to run yt-jellyfin as [jellyfin]: " JELLYFIN_USER
    JELLYFIN_USER="${JELLYFIN_USER:-jellyfin}"
fi

# Verify user exists
if ! id "$JELLYFIN_USER" &>/dev/null; then
    warn "User '$JELLYFIN_USER' does not exist."
    read -rp "Create user '$JELLYFIN_USER'? [y/N] " answer
    case "$answer" in
        [yY]*) sudo useradd -r -s /bin/false "$JELLYFIN_USER" && success "Created user $JELLYFIN_USER" ;;
        *) error "Cannot continue without a valid service user."; exit 1 ;;
    esac
fi

info "Service will run as: $JELLYFIN_USER"

# Add to systemd-journal group for log viewer
if getent group systemd-journal &>/dev/null; then
    sudo usermod -aG systemd-journal "$JELLYFIN_USER"
    success "Added $JELLYFIN_USER to systemd-journal group (enables log viewer)"
fi

# ── copy app files ─────────────────────────────────────────────────────────────
header "Installing app files to $INSTALL_DIR…"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$SCRIPT_DIR" != "$INSTALL_DIR" ]; then
    sudo mkdir -p "$INSTALL_DIR"
    sudo cp -r "$SCRIPT_DIR"/. "$INSTALL_DIR"/
    success "Files copied to $INSTALL_DIR"
else
    info "Already in install directory — skipping copy."
fi

sudo chown -R "$JELLYFIN_USER":"$JELLYFIN_USER" "$INSTALL_DIR"
sudo chmod -R 755 "$INSTALL_DIR"
success "Permissions set on $INSTALL_DIR"

# ── Python venv ────────────────────────────────────────────────────────────────
header "Setting up Python virtual environment…"
if [ ! -d "$INSTALL_DIR/venv" ]; then
    sudo -u "$JELLYFIN_USER" python3 -m venv "$INSTALL_DIR/venv"
    success "Virtual environment created"
else
    info "Virtual environment already exists — skipping."
fi

info "Installing Python dependencies…"
sudo -u "$JELLYFIN_USER" "$INSTALL_DIR/venv/bin/pip" install -q --upgrade pip
sudo -u "$JELLYFIN_USER" "$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"
success "Python dependencies installed"

# ── systemd service ────────────────────────────────────────────────────────────
header "Installing systemd service…"
SERVICE_SRC="$INSTALL_DIR/yt-jellyfin.service"
SERVICE_DEST="/etc/systemd/system/yt-jellyfin.service"

# Patch service file with correct user and working directory
sed -e "s|User=jellyfin|User=$JELLYFIN_USER|g" \
    -e "s|WorkingDirectory=/opt/yt-jellyfin|WorkingDirectory=$INSTALL_DIR|g" \
    -e "s|ExecStart=/opt/yt-jellyfin/|ExecStart=$INSTALL_DIR/|g" \
    "$SERVICE_SRC" | sudo tee "$SERVICE_DEST" > /dev/null

sudo systemctl daemon-reload
sudo systemctl enable yt-jellyfin
success "Service installed and enabled"

# ── firewall check ─────────────────────────────────────────────────────────────
header "Checking firewall and connectivity…"

# Detect configured port
PORT=5000
if grep -q "^Environment=PORT=" "$SERVICE_DEST" 2>/dev/null; then
    PORT=$(grep "^Environment=PORT=" "$SERVICE_DEST" | cut -d= -f3)
fi

# Check outbound port 443 (YouTube access)
if curl -fsSL --max-time 5 https://www.youtube.com > /dev/null 2>&1; then
    success "Outbound port 443 (YouTube) — reachable"
else
    warn "Could not reach YouTube on port 443. yt-dlp will not work."
    warn "Check your firewall's outbound rules for port 443."
fi

# Check ufw if present
if command -v ufw &>/dev/null && sudo ufw status 2>/dev/null | grep -q "Status: active"; then
    if sudo ufw status | grep -q "$PORT"; then
        success "ufw: port $PORT is already allowed"
    else
        warn "ufw is active but port $PORT is not allowed."
        read -rp "  Allow port $PORT in ufw? [y/N] " answer
        case "$answer" in
            [yY]*)
                sudo ufw allow "$PORT"/tcp
                success "ufw: port $PORT allowed"
                ;;
            *)
                warn "Port $PORT not opened. You may not be able to reach the UI."
                warn "To fix manually: sudo ufw allow $PORT/tcp"
                ;;
        esac
    fi
else
    info "ufw not active — skipping firewall check."
    info "If you use a different firewall, ensure port $PORT is open for inbound LAN traffic."
fi

# ── start service ──────────────────────────────────────────────────────────────
header "Starting yt-jellyfin…"
sudo systemctl start yt-jellyfin

sleep 2

if systemctl is-active --quiet yt-jellyfin; then
    success "Service is running"
else
    error "Service failed to start. Check logs with:"
    echo "    sudo journalctl -u yt-jellyfin -n 50 --no-pager"
    exit 1
fi

# ── done ──────────────────────────────────────────────────────────────────────
SERVER_IP=$(hostname -I | awk '{print $1}')
echo ""
echo -e "${GREEN}${BOLD}Installation complete!${RESET}"
echo ""
echo -e "Open your browser and go to:"
echo -e "  ${BOLD}http://${SERVER_IP}:${PORT}/setup${RESET}"
echo ""
echo -e "The setup wizard will guide you through connecting to Jellyfin"
echo -e "and configuring your library location."
echo ""
echo -e "To view logs:    sudo journalctl -u yt-jellyfin -f"
echo -e "To stop:         sudo systemctl stop yt-jellyfin"
echo -e "To restart:      sudo systemctl restart yt-jellyfin"
echo ""
