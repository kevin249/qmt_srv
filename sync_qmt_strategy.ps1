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
$BridgeRepAddress = "tcp://127.0.0.1:20140"
if ($Config.rpc -and $Config.rpc.rep_address) {
    $BridgeRepAddress = ([string]$Config.rpc.rep_address).Replace("tcp://*:", "tcp://127.0.0.1:").Replace("tcp://0.0.0.0:", "tcp://127.0.0.1:")
}

if ($Instances.Count -eq 0) {
    throw "No qmt_instances found in $ConfigFullPath"
}

foreach ($Instance in $Instances) {
    $PythonDirValue = $Instance.python_dir
    if (-not $PythonDirValue) {
        $PythonDirValue = $Instance.qmt_python_dir
    }
    if (-not $PythonDirValue) {
        $PythonDirValue = $Instance.qmt_path
    }
    if (-not $PythonDirValue) {
        $PythonDirValue = $Instance.qmt_dir
    }
    if (-not $PythonDirValue) {
        throw "qmt_instances item is missing qmt_path"
    }

    $PythonDir = [System.IO.Path]::GetFullPath($PythonDirValue)
    if ((Split-Path -Leaf $PythonDir).ToLowerInvariant() -ne "python") {
        $LeafName = (Split-Path -Leaf $PythonDir).ToLowerInvariant()
        if ($LeafName -in @("bin.x64", "userdata", "userdata_mini")) {
            $PythonDir = Split-Path -Parent $PythonDir
        }
        $Candidate = Join-Path $PythonDir "python"
        $PythonDir = [System.IO.Path]::GetFullPath($Candidate)
    }
    if (-not (Test-Path -LiteralPath $PythonDir)) {
        throw "QMT python directory does not exist: $PythonDir"
    }

    $InstanceId = [string]$Instance.instance_id
    if (-not $InstanceId) {
        $InstanceId = Split-Path -Leaf (Split-Path -Parent $PythonDir)
    }

    $StrategyTarget = Join-Path $PythonDir "qmt_data_export_bridge_strategy.py"
    $StrategyAliasTarget = Join-Path $PythonDir "QMT_BRIDGE.py"
    Copy-Item -LiteralPath $StrategySource -Destination $StrategyTarget -Force
    Copy-Item -LiteralPath $StrategySource -Destination $StrategyAliasTarget -Force

    $RuntimeAccounts = @()
    foreach ($Account in @($Instance.accounts)) {
        if ($Account) {
            $RuntimeAccounts += $Account
        }
    }
    if ($Instance.account_id) {
        $RuntimeAccounts += [ordered]@{
            account_id = [string]$Instance.account_id
            account_type = if ($Instance.account_type) { [string]$Instance.account_type } else { "STOCK" }
            name = "default"
        }
    }

    $RuntimeConfig = [ordered]@{
        instance_id = $InstanceId
        output_root = (Join-Path $PythonDir "qmt_data_export")
        output_dirname = "qmt_data_export"
        command_filename = "inbox.jsonl"
        symbols = @()
        accounts = @($RuntimeAccounts)
        legacy_zmq = [ordered]@{
            enabled = $false
        }
        bridge = [ordered]@{
            enabled = $true
            rep_address = $BridgeRepAddress
            pipe_address = "\\.\pipe\qmt_srv_bridge"
            authkey = "qmt_srv_bridge"
            timeout_ms = 300
            push_seconds = 1.0
        }
    }

    $RuntimeConfigPath = Join-Path $PythonDir "qmt_data_export_bridge_config.json"
    $RuntimeConfig | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $RuntimeConfigPath -Encoding UTF8

    Write-Host "synced $InstanceId -> $StrategyTarget"
    Write-Host "synced $InstanceId -> $StrategyAliasTarget"
    Write-Host "config $InstanceId -> $RuntimeConfigPath"
}
