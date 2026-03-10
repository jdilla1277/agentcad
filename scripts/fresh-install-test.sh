#!/usr/bin/env bash
#
# fresh-install-test.sh — Create an isolated cadtool workspace for agent testing.
#
# Creates a temporary directory with a fresh Python 3.12 venv and cadtool
# installed as a user would (not editable, no repo access). The agent works
# in a clean directory with only the cadtool CLI available.
#
# Usage:
#   ./scripts/fresh-install-test.sh              # install from local repo
#   ./scripts/fresh-install-test.sh --from-git   # install from GitHub main
#   ./scripts/fresh-install-test.sh --from-git --ref my-branch  # install from branch
#
# After running, activate the workspace:
#   source <printed_path>/activate.sh
#
set -euo pipefail

PYTHON="/opt/homebrew/bin/python3.12"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FROM_GIT=false
GIT_REF="main"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --from-git)  FROM_GIT=true; shift ;;
        --ref)       GIT_REF="$2"; shift 2 ;;
        *)           echo "Unknown option: $1"; exit 1 ;;
    esac
done

# Verify Python 3.12 is available
if ! "$PYTHON" --version 2>/dev/null | grep -q "3.12"; then
    echo "Error: Python 3.12 not found at $PYTHON"
    exit 1
fi

# Create workspace in /tmp
WORKSPACE="$(mktemp -d -t cadtool-test-XXXXXX)"
WORKSPACE_DIR="$WORKSPACE/workspace"
VENV_DIR="$WORKSPACE/.venv"

mkdir -p "$WORKSPACE_DIR"
echo "Creating isolated workspace at: $WORKSPACE"

# Create fresh venv
"$PYTHON" -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"

# Install cadtool
if [ "$FROM_GIT" = true ]; then
    echo "Installing cadtool from GitHub ($GIT_REF)..."
    pip install -q "git+https://github.com/jdilla1277/mountain-climber.git@${GIT_REF}#subdirectory=app"
else
    echo "Installing cadtool from local repo (non-editable)..."
    pip install -q "$REPO_ROOT/app"
fi

# Verify install
if ! command -v cadtool &>/dev/null; then
    echo "Error: cadtool not found after install"
    exit 1
fi

# Create activation script for the agent
cat > "$WORKSPACE/activate.sh" << 'ACTIVATE'
#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/.venv/bin/activate"
cd "$SCRIPT_DIR/workspace"
echo "cadtool workspace activated."
echo "  Working directory: $(pwd)"
echo "  Python: $(which python)"
echo "  cadtool: $(which cadtool)"
ACTIVATE
chmod +x "$WORKSPACE/activate.sh"

# Run smoke test
echo ""
echo "Running smoke test..."
cd "$WORKSPACE_DIR"
cadtool init --name smoke_test > /dev/null
echo 'box = cq.Workplane("XY").box(10, 20, 5)
show_object(box)' > script.py
RESULT=$(cadtool run script.py --output test_box)
STATUS=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")

if [ "$STATUS" = "success" ]; then
    echo "Smoke test passed."
else
    echo "Smoke test FAILED:"
    echo "$RESULT"
    exit 1
fi

# Clean up smoke test artifacts, leave workspace ready
rm -rf cadtool.json v1_test_box script.py

echo ""
echo "========================================="
echo "Workspace ready: $WORKSPACE_DIR"
echo ""
echo "To use:"
echo "  source $WORKSPACE/activate.sh"
echo ""
echo "Or point an agent at this working directory"
echo "with VIRTUAL_ENV=$VENV_DIR"
echo "========================================="
