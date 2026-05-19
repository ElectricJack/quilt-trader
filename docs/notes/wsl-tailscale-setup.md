# WSL2 + Tailscale: exposing the coordinator to remote workers

## The problem

When the coordinator runs inside WSL2 and a worker Pi joins your Tailnet, the Pi
will see the **Windows host's** Tailscale IP — not WSL2's. WSL2 lives on its own
private network behind a NAT, so by default nothing on the Tailnet (including
the Pi) can reach a service bound inside WSL2.

Symptoms:

- `curl http://<windows-tailscale-ip>:8000/api/health` from the Pi times out.
- `ss -ltn` inside WSL2 shows the coordinator listening on `0.0.0.0:8000`, but
  it's only reachable from the Windows host (via `localhost`), not from any
  other Tailnet machine.

## Fix: Windows portproxy + firewall rule

The Windows host forwards traffic on `:8000` (and optionally `:3000` for the
dashboard) to the WSL2 instance, and the firewall is told to allow it.

### One-time setup

Run the script in PowerShell **as Administrator**:

```powershell
.\scripts\setup-wsl-portproxy.ps1
```

What it does:

1. Detects WSL2's current IP via `wsl hostname -I`.
2. Resets any existing portproxy rules and adds forwards for ports 8000
   (coordinator API) and 3000 (dashboard dev server).
3. Adds inbound firewall rules for both ports.
4. Prints the current portproxy table so you can verify.

### Re-running after a reboot

**WSL2's internal IP changes on every reboot** (and after `wsl --shutdown`), so
the portproxy `connectaddress` becomes stale. Re-run `setup-wsl-portproxy.ps1`
whenever the Pi can no longer reach the coordinator. The firewall rules
persist — only the portproxy needs refreshing.

### Verifying

From the Windows host:

```powershell
netsh interface portproxy show all
```

Expected output:

```
Listen on ipv4:             Connect to ipv4:
Address         Port        Address         Port
--------------- ----------  --------------- ----------
0.0.0.0         3000        <wsl-ip>        3000
0.0.0.0         8000        <wsl-ip>        8000
```

From another Tailnet machine (e.g. the Pi):

```bash
curl http://<windows-tailscale-ip>:8000/api/health
```

## Alternative: WSL2 mirrored networking

If you're on a recent Windows (11 22H2+ with `wsl --update`), you can avoid
portproxy entirely by switching WSL2 to **mirrored networking**, which makes
WSL2 share the Windows host's network stack (including Tailscale).

Add to `%USERPROFILE%\.wslconfig`:

```ini
[wsl2]
networkingMode=mirrored
```

Then `wsl --shutdown` and reopen WSL. After that, anything bound to `0.0.0.0`
inside WSL is reachable on the Windows host's Tailscale IP directly — no
portproxy or firewall rules required.

Mirrored mode is the cleaner option when it's available; portproxy is the
fallback for older Windows builds.

## Cleanup

To remove the portproxy and firewall rules:

```powershell
netsh interface portproxy delete v4tov4 listenport=8000 listenaddress=0.0.0.0
netsh interface portproxy delete v4tov4 listenport=3000 listenaddress=0.0.0.0
Remove-NetFirewallRule -DisplayName "quilt-coordinator 8000"
Remove-NetFirewallRule -DisplayName "quilt-dashboard 3000"
```

## Future automation ideas

- Run the portproxy script automatically on Windows login via Task Scheduler
  (action: PowerShell, trigger: At log on, run with highest privileges).
- Or: switch to mirrored networking and remove this whole dance.
- Or: install Tailscale directly inside WSL2 so it joins the Tailnet as its
  own node (then the coordinator's Tailscale IP is WSL2's, and the Windows
  host is out of the loop entirely).
