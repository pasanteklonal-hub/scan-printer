$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python -m pip install -r requirements.txt
python -m pip install pyinstaller
python -m PyInstaller --clean --noconfirm scanPrinter.spec
Write-Host "Aplicacion Windows creada en dist/scanPrinter/scanPrinter.exe"
