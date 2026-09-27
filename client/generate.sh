#!/bin/sh
# Regenerate client/bazaar_pb2.py from the starter kit's bazaar.proto.
set -eu
cd "$(dirname "$0")/.."
python -m grpc_tools.protoc \
  -I artifacts/bazaar-protobuf-starter-linux \
  --python_out=client \
  artifacts/bazaar-protobuf-starter-linux/bazaar.proto
echo "wrote client/bazaar_pb2.py"
