#!/bin/bash
# PaintOmics AI startup script
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Load local secrets (API keys) if present. Gitignored; see deploy/env.example.
if [ -f "$SCRIPT_DIR/.env" ]; then
  set -a
  . "$SCRIPT_DIR/.env"
  set +a
fi

cd "$SCRIPT_DIR/PaintomicsServer"

# Activate conda environment
eval "$(conda shell.bash hook)"
conda activate paintomics4

# Start MongoDB if it is not running
if ! pgrep -x "mongod" > /dev/null; then
  echo "MongoDB is not running. Starting MongoDB..."
  MONGO_DATA="${PAINTOMICS_MONGO_DBPATH:-$SCRIPT_DIR/../mongodb_data}"
  MONGO_LOG="${PAINTOMICS_MONGO_LOG:-$SCRIPT_DIR/../mongodb.log}"
  mkdir -p "$MONGO_DATA"
  mongod --fork --dbpath "$MONGO_DATA" --logpath "$MONGO_LOG"
  sleep 2
fi

echo "Starting PaintOmics AI..."
echo "  MongoDB: localhost:27017"
echo "  Server:  http://localhost:8000"
echo "  Admin:   admin / admin"
echo ""

python src/launch_server.py
