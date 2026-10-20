@echo off
REM One-click start: creates the virtual env, trains the model if needed, launches the web app.
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
    echo Creating virtual environment...
    python -m venv .venv || goto :error
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt || goto :error
)

if not exist artifacts\cf.npz (
    echo No trained model found - installing training dependencies and training, about 5 minutes...
    .venv\Scripts\python.exe -m pip install -r requirements-dev.txt || goto :error
    .venv\Scripts\python.exe -m recsys.train || goto :error
)

echo.
echo  Folio is running at http://127.0.0.1:8000   (Ctrl+C to stop)
echo.
.venv\Scripts\python.exe app.py --open
goto :eof

:error
echo Something went wrong. See the messages above.
pause
