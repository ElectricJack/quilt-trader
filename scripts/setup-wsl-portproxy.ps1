# setup-wsl-portproxy.ps1
#
# Forward Windows host ports 8000 (coordinator API) and 3000 (dashboard dev
# server) to the running WSL2 instance, and open the Windows firewall for
# those ports. This makes services inside WSL2 reachable from other machines
# on your Tailnet (e.g. a Raspberry Pi worker).
#
# Run as Administrator. Re-run whenever WSL2's internal IP changes (after a
# reboot or `wsl --shutdown`). See docs/notes/wsl-tailscale-setup.md.

$ErrorActionPreference = "Stop"

if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "This script must be run as Administrator."
    exit 1
}

$wslIp = (wsl hostname -I).Trim().Split()[0]
if (-not $wslIp) {
    Write-Error "Could not detect WSL2 IP. Is WSL running?"
    exit 1
}
Write-Host "WSL2 IP: $wslIp"

$ports = @(3000, 8000)

Write-Host "Resetting existing portproxy rules..."
netsh interface portproxy reset | Out-Null

foreach ($port in $ports) {
    Write-Host "Forwarding 0.0.0.0:$port -> ${wslIp}:$port"
    netsh interface portproxy add v4tov4 `
        listenaddress=0.0.0.0 listenport=$port `
        connectaddress=$wslIp connectport=$port | Out-Null
}

$firewallRules = @{
    "quilt-coordinator 8000" = 8000
    "quilt-dashboard 3000"   = 3000
}

foreach ($rule in $firewallRules.GetEnumerator()) {
    $existing = Get-NetFirewallRule -DisplayName $rule.Key -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "Firewall rule '$($rule.Key)' already exists; skipping."
    } else {
        Write-Host "Adding firewall rule '$($rule.Key)' for port $($rule.Value)"
        New-NetFirewallRule -DisplayName $rule.Key `
            -Direction Inbound -LocalPort $rule.Value -Protocol TCP -Action Allow | Out-Null
    }
}

Write-Host ""
Write-Host "Current portproxy table:"
netsh interface portproxy show all
