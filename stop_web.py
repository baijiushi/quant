"""
Stop local oversell web console processes.

Only processes that look like this project's FastAPI/Vite commands are stopped.
"""
from __future__ import annotations

import subprocess


def main() -> None:
    ps_script = r"""
$targets = @()
$ports = @(8000, 5173)
foreach ($port in $ports) {
  $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
  foreach ($conn in $conns) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($conn.OwningProcess)" -ErrorAction SilentlyContinue
    if ($null -eq $proc) { continue }
    $cmd = [string]$proc.CommandLine
    $isOversellBackend = $cmd -like "*uvicorn backend.app:app*"
    $isOversellFrontend = ($cmd -like "*vite*" -and $cmd -like "*oversell*")
    if ($isOversellBackend -or $isOversellFrontend) {
      $targets += [pscustomobject]@{ Id = $proc.ProcessId; Port = $port; CommandLine = $cmd }
    }
  }
}
$targets = $targets | Sort-Object Id -Unique
if ($targets.Count -eq 0) {
  Write-Host "没有发现正在运行的 oversell 控制台进程。"
  exit 0
}
foreach ($target in $targets) {
  Write-Host "停止 PID $($target.Id)，端口 $($target.Port)"
  Stop-Process -Id $target.Id -Force -ErrorAction SilentlyContinue
}
"""
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            ps_script,
        ],
        check=False,
    )


if __name__ == "__main__":
    main()
