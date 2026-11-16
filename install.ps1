$ErrorActionPreference = 'Stop'
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { throw 'Python 3.10+ is required. Install Python, then run this script again.' }
& $python.Source (Join-Path $PSScriptRoot 'install.py') @args
exit $LASTEXITCODE
