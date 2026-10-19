#!/bin/bash
# ============================================================
#  VitalDB Clinical Explorer - launcher for macOS
#  Starts the application with the private .venv environment.
# ============================================================
cd "$(dirname "$0")" || exit 1
HERE="$(pwd)"
VPY="$HERE/.venv/bin/python"

problem() {
    echo
    echo "$1"
    echo
    read -r -p "Press Return to close this window..." _
    exit 1
}

[ -x "$VPY" ] || problem "The application has not been installed yet.
Please double-click  install_mac.command  first, wait until it says SUCCESS,
then double-click this file again."

if ! "$VPY" "$HERE/check_install.py" --quiet >/dev/null 2>&1; then
    echo "Some required components are missing or damaged."
    "$VPY" "$HERE/check_install.py"
    problem "Please run  install_mac.command  again (it is safe to re-run), then try again."
fi

if [ -n "$VDB_DRYRUN" ]; then
    echo "DRYRUN would launch: $VPY $HERE/run_app.py"
    exit 0
fi

# Run in the background so the Terminal window can be closed.
nohup "$VPY" "$HERE/run_app.py" >/dev/null 2>&1 &
disown 2>/dev/null
# Close this Terminal window politely (only when started by double-click)
if [ -z "$VDB_NOPAUSE" ] && [ "$TERM_PROGRAM" = "Apple_Terminal" ]; then
    osascript -e 'tell application "Terminal" to close (every window whose name contains "run_mac")' >/dev/null 2>&1 &
fi
exit 0
