@echo off
REM Pratyaya — Windows quickstart
setlocal

if not exist .venv (
  echo [pratyaya] creating venv...
  python -m venv .venv
)
call .venv\Scripts\activate.bat

pip install -q --disable-pip-version-check -r requirements.txt

echo.
echo [pratyaya] starting on http://localhost:8000
echo  - landing  : http://localhost:8000/
echo  - agent    : http://localhost:8000/agent
echo  - citizen  : http://localhost:8000/citizen
echo.

python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

endlocal
