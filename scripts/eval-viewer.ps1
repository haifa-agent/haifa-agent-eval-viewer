[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $ViewerArguments
)

$repositoryRoot = Split-Path -Parent $PSScriptRoot
& uv run --project $repositoryRoot haifa-eval-viewer @ViewerArguments
exit $LASTEXITCODE
