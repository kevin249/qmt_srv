param(
    [string]$ConfigPath = ".\config.user.json",
    [switch]$UseTemplate
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$StrategySource = Join-Path $RepoRoot "qmt_data_export_bridge_strategy.py"

if (-not (Test-Path -LiteralPath $StrategySource)) {
    throw "Strategy file not found: $StrategySource"
}

if ($UseTemplate -or -not (Test-Path -LiteralPath $ConfigPath)) {
    $ConfigPath = Join-Path $RepoRoot "config.template.json"
}

$ConfigFullPath = (Resolve-Path -LiteralPath $ConfigPath).Path
$Config = Get-Content -LiteralPath $ConfigFullPath -Raw -Encoding UTF8 | ConvertFrom-Json
$Instances = @($Config.qmt_instances)

if ($Instances.Count -eq 0) {
    throw "No qmt_instances found in $ConfigFullPath"
}

foreach ($Instance in $Instances) {
    $PythonDirValue = $Instance.python_dir
    if (-not $PythonDirValue) {
        $PythonDirValue = $Instance.qmt_python_dir
    }
    if (-not $PythonDirValue) {
        $PythonDirValue = $Instance.qmt_dir
    }
    if (-not $PythonDirValue) {
        throw "qmt_instances item is missing python_dir"
    }

    $PythonDir = [System.IO.Path]::GetFullPath($PythonDirValue)
    if ((Split-Path -Leaf $PythonDir).ToLowerInvariant() -ne "python") {
        $Candidate = Join-Path $PythonDir "python"
        if (Test-Path -LiteralPath $Candidate) {
            $PythonDir = [System.IO.Path]::GetFullPath($Candidate)
        }
    }
    if (-not (Test-Path -LiteralPath $PythonDir)) {
        throw "QMT python directory does not exist: $PythonDir"
    }

    $InstanceId = [string]$Instance.instance_id
    if (-not $InstanceId) {
        $InstanceId = Split-Path -Leaf (Split-Path -Parent $PythonDir)
    }

    $StrategyTarget = Join-Path $PythonDir "qmt_data_export_bridge_strategy.py"
    Copy-Item -LiteralPath $StrategySource -Destination $StrategyTarget -Force

    $RuntimeConfig = [ordered]@{
        instance_id = $InstanceId
        output_dirname = "qmt_data_export"
        command_filename = "inbox.jsonl"
        symbols = @()
        accounts = @($Instance.accounts)
        legacy_zmq = [ordered]@{
            enabled = $false
        }
    }

    $RuntimeConfigPath = Join-Path $PythonDir "qmt_data_export_bridge_config.json"
    $RuntimeConfig | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $RuntimeConfigPath -Encoding UTF8

    Write-Host "synced $InstanceId -> $StrategyTarget"
    Write-Host "config $InstanceId -> $RuntimeConfigPath"
}
