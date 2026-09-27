#!/bin/sh
# Start the practice server for this machine's CPU. Output files go to ./run/.
# Extra arguments are passed through, e.g. --addr 127.0.0.1:3002
set -eu
root="$(cd "$(dirname "$0")/.." && pwd)"
kit="$root/artifacts/bazaar-protobuf-starter-linux"

case "$(uname -m)" in
  aarch64|arm64) bin="$kit/spaceport-validate-linux-arm64" ;;
  x86_64|amd64)  bin="$kit/spaceport-validate-linux-x86_64" ;;
  *) echo "unsupported CPU: $(uname -m)" >&2; exit 1 ;;
esac

if [ "$(uname -s)" != "Linux" ]; then
  echo "the practice server is Linux-only; run this inside the devcontainer" >&2
  exit 1
fi

mkdir -p "$root/run"
cd "$root/run"
exec "$bin" --codec protobuf "$@"
