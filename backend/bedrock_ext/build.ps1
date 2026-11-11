param(
    [Parameter(Mandatory = $true)][string]$GeyserJar,
    [switch]$Test
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$build = Join-Path $root 'build'
$classes = Join-Path $build 'classes'

if (-not (Test-Path -LiteralPath $GeyserJar)) {
    throw "Geyser standalone JAR not found: $GeyserJar"
}
$GeyserJar = (Resolve-Path -LiteralPath $GeyserJar).Path

if (Test-Path -LiteralPath $build) {
    Remove-Item -LiteralPath $build -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $classes | Out-Null

$sources = Get-ChildItem -LiteralPath (Join-Path $root 'src') -Recurse -Filter '*.java' |
    ForEach-Object { $_.FullName }

& javac --release 21 -encoding UTF-8 -Xlint:all -classpath $GeyserJar -d $classes $sources
if ($LASTEXITCODE -ne 0) {
    throw "javac failed with exit code $LASTEXITCODE"
}

Copy-Item -LiteralPath (Join-Path $root 'extension.yml') -Destination $classes

$outJar = Join-Path $root 'MineNodeRouter.jar'
& jar --create --file $outJar -C $classes .
if ($LASTEXITCODE -ne 0) {
    throw "jar failed with exit code $LASTEXITCODE"
}
Write-Host "Built $outJar"

if ($Test) {
    $testClasses = Join-Path $build 'test-classes'
    New-Item -ItemType Directory -Force -Path $testClasses | Out-Null
    $testSources = Get-ChildItem -LiteralPath (Join-Path $root 'test') -Recurse -Filter '*.java' |
        ForEach-Object { $_.FullName }
    & javac --release 21 -encoding UTF-8 -classpath "$GeyserJar$([IO.Path]::PathSeparator)$classes" -d $testClasses $testSources
    if ($LASTEXITCODE -ne 0) {
        throw "test javac failed with exit code $LASTEXITCODE"
    }
    & java -classpath "$classes$([IO.Path]::PathSeparator)$testClasses" pl.minenode.router.RouteTableTest
    if ($LASTEXITCODE -ne 0) {
        throw "RouteTableTest failed with exit code $LASTEXITCODE"
    }
}
