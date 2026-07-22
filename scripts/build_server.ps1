param(
  [string]$Name = 'Lumi-DM'
)

Write-Output "Normalizing Lumi branding"
python scripts\normalize_branding.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Output "Building server binary with PyInstaller (name: $Name)"
python -m pip install --upgrade pyinstaller
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

pyinstaller --onefile --noconsole --add-data "static;static" --name $Name server.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Output "Build finished. See dist\$Name.exe"
