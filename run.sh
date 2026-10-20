#!/usr/bin/env bash
# One-click start for macOS / Linux: creates the virtual env, trains the model if needed, launches the web app.
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.txt
fi

if [ ! -f artifacts/cf.npz ]; then
    echo "No trained model found - installing training dependencies and training, about 5 minutes..."
    .venv/bin/python -m pip install -r requirements-dev.txt
    .venv/bin/python -m recsys.train
fi

echo
echo " Folio is running at http://127.0.0.1:8000   (Ctrl+C to stop)"
echo
.venv/bin/python app.py --open
