#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

CONDA_COMMAND="${CONDA_EXE:-$(command -v conda || true)}"
if [[ -z "$CONDA_COMMAND" ]]; then
  printf '%s\n' 'Install Miniforge / Conda and initialize your shell first; setup never creates a venv.' >&2
  exit 1
fi
ENV_NAME="${LOCUS_CONDA_ENV:-locusforge}"
if [[ "$ENV_NAME" == "base" || ! "$ENV_NAME" =~ ^[a-zA-Z0-9_-]+$ ]]; then
  printf '%s\n' 'LOCUS_CONDA_ENV must be a dedicated named environment, not base.' >&2
  exit 1
fi
source "$("$CONDA_COMMAND" info --base)/etc/profile.d/conda.sh"
conda env update --name "$ENV_NAME" --file environment.yml
conda activate "$ENV_NAME"
python -m pip check
python scripts/install_blender.py
node -e 'if (Number(process.versions.node.split(".")[0]) !== 24) { throw new Error("Use the Conda-provided Node.js 24"); }'
npm --prefix web ci --no-audit --no-fund
npm --prefix web run build
locusforge init
printf '\n%s\n' "Setup complete. Conda environment: $ENV_NAME"
printf '%s\n' 'Run: bash scripts/start.sh'
