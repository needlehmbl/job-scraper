# stop.ps1 -- tell the local API to shut down, then kill leftovers.
try { Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/shutdown" } catch {}
Get-Process -Name "JobScraper" -ErrorAction SilentlyContinue | Stop-Process
