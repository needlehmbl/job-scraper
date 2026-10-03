# run.ps1 -- portable launcher: start exe, open default browser is done by the exe.
Start-Process -FilePath (Join-Path $PSScriptRoot "JobScraper.exe")
