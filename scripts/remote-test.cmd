@echo off
rem Remote compute test menu (scripts/remote_test.py), using the code in this repo as it is now.
cd /d "%~dp0.."
uv run python -X utf8 scripts/remote_test.py
pause
