# Compila NextPlay Sync y la empaqueta en dist/NextPlaySync.pext, el archivo que Playnite instala al abrirlo y que
# NextPlay ofrece en Editar perfil › Cuentas vinculadas › Playnite. Necesita el .NET SDK (dotnet).
# Uso: powershell -ExecutionPolicy Bypass -File playnite-extension/build.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
dotnet build "$root/NextPlaySync/NextPlaySync.csproj" -c Release --nologo
if ($LASTEXITCODE -ne 0) { throw "No se ha podido compilar NextPlay Sync." }

# Un .pext es un .zip con la extensión dentro: su extension.yaml y su .dll.
$built = "$root/NextPlaySync/bin/Release/net462"
$stage = "$root/dist/NextPlaySync"
$zip = "$root/dist/NextPlaySync.zip"
$pext = "$root/dist/NextPlaySync.pext"
Remove-Item -Recurse -Force $stage, $zip, $pext -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $stage | Out-Null
Copy-Item "$built/NextPlaySync.dll", "$built/extension.yaml" $stage
Compress-Archive -Path "$stage/*" -DestinationPath $zip
Move-Item $zip $pext
Write-Output "Lista: $pext"
