#!/bin/sh
# Link tinker into ~/.local/bin so it runs from any folder.
set -e
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 14))' 2>/dev/null; then
  echo "⚠️  Tinker needs Python 3.14 or newer; python3 here is: $(python3 --version 2>&1)"
  echo "   On a Mac: brew install python"
  exit 1
fi
here="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/.local/bin"
ln -sf "$here/tinker.py" "$HOME/.local/bin/tinker"
echo "🔧 tinker is linked: $HOME/.local/bin/tinker -> $here/tinker.py"
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo "⚠️  Add ~/.local/bin to your PATH:  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac
