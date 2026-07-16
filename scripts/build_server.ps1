param(
  [string]$Name = 'Lumi-DM'
)

Write-Output "Building server binary with PyInstaller (name: $Name)"
python -m pip install --upgrade pyinstaller
pyinstaller --onefile --noconsole --add-data "static;static" --name $Name server.py

Write-Output "Build finished. See dist\$Name.exe"
