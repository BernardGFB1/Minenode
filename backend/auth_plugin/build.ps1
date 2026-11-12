param([Parameter(Mandatory=$true)][string]$PaperJar, [string]$DependencyDir = "")
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$out = Join-Path $root 'MineNodeAuth.jar'
$classes = Join-Path $root 'build/classes'
if (Test-Path $classes) { Remove-Item $classes -Recurse -Force }
New-Item -ItemType Directory -Force $classes | Out-Null
$src = Get-ChildItem (Join-Path $root 'src') -Recurse -Filter '*.java' | ForEach-Object FullName
$cp = $PaperJar
if ($DependencyDir -and (Test-Path $DependencyDir)) {
    $cp += [IO.Path]::PathSeparator + ((Get-ChildItem (Join-Path $DependencyDir '*.jar') | ForEach-Object FullName) -join [IO.Path]::PathSeparator)
}
& javac --release 21 -encoding UTF-8 -classpath $cp -d $classes $src
if ($LASTEXITCODE -ne 0) { throw 'javac failed' }
Copy-Item (Join-Path $root 'plugin.yml') (Join-Path $classes 'plugin.yml')
& jar --create --file $out -C $classes .
if ($LASTEXITCODE -ne 0) { throw 'jar failed' }
Write-Host "Built $out"
