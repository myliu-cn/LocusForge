#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
if [[ -f .env ]]; then
  set -a
  source ./.env
  set +a
fi
CONDA_COMMAND="${CONDA_EXE:-$(command -v conda || true)}"
if [[ -z "$CONDA_COMMAND" ]]; then
  printf '%s\n' 'Conda was not found. Initialize Conda or set CONDA_EXE to its executable.' >&2
  exit 1
fi
source "$("$CONDA_COMMAND" info --base)/etc/profile.d/conda.sh"
conda activate "${LOCUS_CONDA_ENV:-locusforge}"
python -c 'import pathlib,sys; assert (pathlib.Path(sys.prefix)/"conda-meta").is_dir(), "A Conda environment is required"; import locusforge'
if [[ ! -f src/locusforge/static/index.html ]]; then
  printf '%s\n' 'Build the studio with npm --prefix web run build before starting.' >&2
  exit 1
fi
exec locusforge serve "$@"
