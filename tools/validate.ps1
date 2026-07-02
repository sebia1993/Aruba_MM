$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

python -m pip install -e ".[dev]" -c .\constraints.txt
python -m pip check
python -m pytest
python -m compileall src
