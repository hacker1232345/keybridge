param([string[]]$Args)
Add-Content -Path 'D:\keybridge-test.log' -Value ('SCRIPT-PS1-OK ' + (Get-Date -Format o) + ' args=' + ($Args -join ','))
