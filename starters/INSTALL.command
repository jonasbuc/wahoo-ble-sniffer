#!/bin/bash
# ════════════════════════════════════════════════════════════════
#  Bike VR – Full Installer (macOS)
#  Double-click this file to set up everything.
# ════════════════════════════════════════════════════════════════

set -e
cd "$(dirname "$0")/.."
REPO="$(pwd)"

echo ""
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║     🚴  Bike VR – Installer  🚴             ║"
echo "  ╚══════════════════════════════════════════════╝"
echo ""

# ── 1. Python ─────────────────────────────────────────────────
echo "  [1/4] Tjekker Python …"
if ! command -v python3 &>/dev/null; then
    echo "  ✗  Python 3 ikke fundet!"
    echo "     Installér fra https://www.python.org/downloads/"
    read -rp "  Tryk Enter for at lukke …"
    exit 1
fi
PY_VER=$(python3 --version | cut -d' ' -f2)
echo "  ✓  Python $PY_VER"
echo ""

# ── 2. Virtual environment ────────────────────────────────────
echo "  [2/4] Opretter virtual environment …"
if [ -d ".venv" ]; then
    echo "  ✓  .venv eksisterer allerede"
else
    python3 -m venv .venv
    echo "  ✓  .venv oprettet"
fi
echo ""

# ── 3. Installér afhængigheder ────────────────────────────────
echo "  [3/4] Installerer afhængigheder …"
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
pip install --quiet -e .
echo "  ✓  Alle pakker installeret"
echo ""

# ── 4. Verificér ──────────────────────────────────────────────
echo "  [4/4] Verificerer installation …"
python -c "
import bleak, websockets, fastapi, uvicorn, pydantic, streamlit
print('  ✓  Alle moduler OK')
"
echo ""

# ── Gør start-scripts executable ──────────────────────────────
chmod +x "$REPO/starters/"*.command 2>/dev/null || true
chmod +x "$REPO/starters/"*.sh 2>/dev/null || true

echo "  ╔══════════════════════════════════════════════╗"
echo "  ║     ✅  INSTALLATION FÆRDIG!                 ║"
echo "  ╚══════════════════════════════════════════════╝"
echo ""
echo "  Næste trin:"
echo "    Double-click  starters/START_ALL.command"
echo ""
read -rp "  Tryk Enter for at lukke …"
