#!/bin/bash
# ============================================================
#  VitalDB Clinical Explorer - installer for macOS
#  Creates a private Python environment (.venv) inside this
#  folder and installs everything the application needs.
#  Does not change your existing Python. No admin rights needed.
# ============================================================
cd "$(dirname "$0")" || exit 1
HERE="$(pwd)"
LOG="$HERE/install_log.txt"
MIN_MINOR=10
MAX_MINOR=13

say() { echo "$@" | tee -a "$LOG"; }
finish() { echo; read -r -p "Press Return to close this window..." _; exit "$1"; }

echo "Install started $(date)" > "$LOG"
echo
echo "============================================================"
echo "  VitalDB Clinical Explorer - installation (macOS)"
echo "============================================================"
echo
echo "This takes a few minutes and needs an internet connection."
echo

# Remove the macOS quarantine flag from this folder (harmless if it fails)
xattr -dr com.apple.quarantine "$HERE" >/dev/null 2>&1

# ---- 1. find a suitable python3 (3.10 - 3.13; 3.12 preferred) ----
PYBIN=""
for cand in python3.12 python3.13 python3.11 python3.10 python3 \
            /usr/local/bin/python3 /opt/homebrew/bin/python3 \
            /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
            /Library/Frameworks/Python.framework/Versions/3.13/bin/python3; do
    if command -v "$cand" >/dev/null 2>&1; then
        c="$(command -v "$cand")"
        minor="$("$c" -c 'import sys; print(sys.version_info[0]*100+sys.version_info[1])' 2>/dev/null)"
        if [ -n "$minor" ] && [ "$minor" -ge $((300+MIN_MINOR)) ] && [ "$minor" -le $((300+MAX_MINOR)) ]; then
            PYBIN="$c"
            break
        fi
    fi
done

if [ -z "$PYBIN" ]; then
    say "No suitable Python was found (need Python 3.10 to 3.13)."
    echo
    echo "Please install Python 3.12 from https://www.python.org/downloads/macos/"
    echo "(download the macOS installer and run it), then double-click this file again."
    echo "Your existing software is not changed by this installer."
    finish 1
fi
say "Using Python: $PYBIN ($("$PYBIN" --version 2>&1))"

# ---- 2. create the private environment ----
if [ -x "$HERE/.venv/bin/python" ]; then
    say "Existing .venv found - it will be reused."
else
    say "Creating private environment in .venv ..."
    if ! "$PYBIN" -m venv "$HERE/.venv" >>"$LOG" 2>&1; then
        say "Could not create the .venv environment. See install_log.txt for details."
        finish 1
    fi
fi
VPY="$HERE/.venv/bin/python"

# ---- 3. install packages with python -m pip ----
say "Updating pip ..."
"$VPY" -m pip install --upgrade pip >>"$LOG" 2>&1
say "Installing required packages (this is the slow step) ..."
if ! "$VPY" -m pip install -r "$HERE/requirements.txt" 2>&1 | tee -a "$LOG" | tail -n 3; then
    :
fi
if ! "$VPY" "$HERE/check_install.py" >>"$LOG" 2>&1; then
    echo
    say "Installation did not complete. Details:"
    "$VPY" "$HERE/check_install.py"
    echo "The file install_log.txt in this folder contains the full log."
    finish 1
fi

chmod +x "$HERE/run_mac.command" 2>/dev/null
echo
echo "============================================================"
echo "  SUCCESS - the application is installed."
echo "  Now double-click  run_mac.command  to start it."
echo "============================================================"
finish 0
