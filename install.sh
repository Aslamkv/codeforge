#!/bin/sh
# Link tinker into ~/.local/bin so it runs from any folder.
set -e
here="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/.local/bin"
ln -sf "$here/tinker.py" "$HOME/.local/bin/tinker"
echo "🔧 tinker is linked: $HOME/.local/bin/tinker -> $here/tinker.py"
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo "⚠️  Add ~/.local/bin to your PATH:  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac
