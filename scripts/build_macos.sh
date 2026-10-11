#!/bin/sh
# Builds dist/TouchlessX.app and dist/TouchlessX-<version>.dmg on macOS.
# Run from the project folder, inside the virtual environment:  sh scripts/build_macos.sh
set -e
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-.venv/bin/python}"
VERSION=$("$PYTHON" -c "import re;print(re.search(r'VERSION = \"(.+?)\"', open('TouchlessX.spec').read()).group(1))")

"$PYTHON" -m PyInstaller --noconfirm --clean TouchlessX.spec

# Without a paid Apple certificate the app is signed "ad hoc": it runs, but the first
# time macOS asks the user to confirm (right-click the app, then Open).
codesign --force --deep --sign - dist/TouchlessX.app

DMG="dist/TouchlessX-$VERSION.dmg"
STAGE=$(mktemp -d)
cp -R dist/TouchlessX.app "$STAGE/"
ln -s /Applications "$STAGE/Applications"   # drag the app onto this to install
rm -f "$DMG"
hdiutil create -volname "TouchlessX" -srcfolder "$STAGE" -ov -format UDZO "$DMG" > /dev/null
rm -rf "$STAGE"
echo "Built dist/TouchlessX.app and $DMG"
