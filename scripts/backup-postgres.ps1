param(
    [string]$BackupDirectory = (Join-Path $PSScriptRoot '..\backups'),
    [string]$Recipient = $env:INCUVA_BACKUP_RECIPIENT
)

$ErrorActionPreference = 'Stop'

foreach ($name in @('PGHOST', 'PGDATABASE', 'PGUSER')) {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))) {
        throw "Set $name before creating a backup. Do not pass database passwords on the command line."
    }
}
if ([string]::IsNullOrWhiteSpace($Recipient)) {
    throw 'Set INCUVA_BACKUP_RECIPIENT to the fingerprint or email of a trusted GPG public key.'
}
if (-not (Get-Command pg_dump -ErrorAction SilentlyContinue)) { throw 'pg_dump is not installed or is not on PATH.' }
if (-not (Get-Command gpg -ErrorAction SilentlyContinue)) { throw 'GPG is not installed or is not on PATH.' }

$resolvedBackupDirectory = [IO.Path]::GetFullPath($BackupDirectory)
New-Item -ItemType Directory -Force -Path $resolvedBackupDirectory | Out-Null
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$plainPath = Join-Path $resolvedBackupDirectory "incuva-$timestamp.dump"
$encryptedPath = "$plainPath.gpg"

try {
    & pg_dump --format=custom --no-owner --file="$plainPath"
    if ($LASTEXITCODE -ne 0) { throw "pg_dump failed with exit code $LASTEXITCODE." }

    & gpg --batch --yes --trust-model pgp --recipient $Recipient --output $encryptedPath --encrypt $plainPath
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $encryptedPath)) { throw 'GPG encryption failed; the plaintext dump will be removed.' }

    $hash = (Get-FileHash -LiteralPath $encryptedPath -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath "$encryptedPath.sha256" -Value "$hash  $(Split-Path -Leaf $encryptedPath)" -Encoding ascii
    Remove-Item -LiteralPath $plainPath -Force
    Write-Output "Encrypted PostgreSQL backup created: $encryptedPath"
    Write-Output "SHA-256 manifest: $encryptedPath.sha256"
}
finally {
    if (Test-Path -LiteralPath $plainPath) { Remove-Item -LiteralPath $plainPath -Force }
}
