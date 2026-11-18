param(
    [Parameter(Mandatory = $true)][string]$BackupFile
)

$ErrorActionPreference = 'Stop'

foreach ($name in @('PGHOST', 'PGDATABASE', 'PGUSER')) {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))) {
        throw "Set $name to the empty staging database before restoring."
    }
}
if (-not (Get-Command pg_restore -ErrorAction SilentlyContinue)) { throw 'pg_restore is not installed or is not on PATH.' }
if (-not (Get-Command psql -ErrorAction SilentlyContinue)) { throw 'psql is not installed or is not on PATH; cannot verify the target is empty.' }
if (-not (Get-Command gpg -ErrorAction SilentlyContinue)) { throw 'GPG is not installed or is not on PATH.' }

$resolvedBackup = (Resolve-Path -LiteralPath $BackupFile).Path
if (-not $resolvedBackup.EndsWith('.gpg', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Only GPG-encrypted .gpg backups can be restored by this script.'
}
$targetName = $env:PGDATABASE
$confirmation = Read-Host "Restore into PostgreSQL database '$targetName' as a single transaction? Use an empty staging database"
if ($confirmation -cne $targetName) { throw 'Restore cancelled because the database name did not match.' }
$existingTables = (& psql --no-psqlrc --tuples-only --no-align --command "SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname NOT IN ('pg_catalog', 'information_schema');").Trim()
if ($LASTEXITCODE -ne 0 -or $existingTables -ne '0') {
    throw 'Restore target is not an empty database (or PostgreSQL could not be queried). Use a new staging database.'
}

$plainPath = Join-Path ([IO.Path]::GetTempPath()) "incuva-restore-$([guid]::NewGuid().ToString('N')).dump"
try {
    & gpg --batch --output $plainPath --decrypt $resolvedBackup
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $plainPath)) { throw 'GPG decryption failed.' }
    & pg_restore --list $plainPath *> $null
    if ($LASTEXITCODE -ne 0) { throw 'The decrypted file is not a valid PostgreSQL custom-format backup.' }
    & pg_restore --exit-on-error --single-transaction --no-owner --dbname=$targetName $plainPath
    if ($LASTEXITCODE -ne 0) { throw "pg_restore failed with exit code $LASTEXITCODE; the transaction should have rolled back." }
    Write-Output "Restore completed into staging database '$targetName'. Validate the application before cutover."
}
finally {
    if (Test-Path -LiteralPath $plainPath) { Remove-Item -LiteralPath $plainPath -Force }
}
