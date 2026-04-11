[CmdletBinding()]
param(
    [int[]]$Ports = @(8000, 3000)
)

$ErrorActionPreference = "Stop"

function Get-PidsByPort {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port
    )

    $lines = netstat -ano -p tcp | Select-String -Pattern "[:\.]$Port\s+.*LISTENING\s+(\d+)\s*$"
    $pids = @()

    foreach ($line in $lines) {
        $parts = ($line.Line -split "\s+") | Where-Object { $_ -ne "" }
        if ($parts.Length -ge 5) {
            $pidValue = $parts[-1]
            if ($pidValue -match "^\d+$") {
                $pids += [int]$pidValue
            }
        }
    }

    return $pids | Sort-Object -Unique
}

foreach ($port in $Ports) {
    $pids = Get-PidsByPort -Port $port
    if (-not $pids -or $pids.Count -eq 0) {
        Write-Host "No process listening on port $port."
        continue
    }

    foreach ($processId in $pids) {
        try {
            Stop-Process -Id $processId -Force
            Write-Host "Stopped PID $processId on port $port."
        }
        catch {
            Write-Warning "Could not stop PID $processId on port ${port}: $($_.Exception.Message)"
        }
    }
}
