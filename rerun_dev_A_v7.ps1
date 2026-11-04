param(
    [string]$Diagnostic = '.\data\dev400_new_v2_insufficient.json',
    [string]$Baseline = '.\runs\dev_A_200_v6_3\manifest.json',
    [string]$Output = '.\runs\dev_A_200_v7'
)
$ErrorActionPreference = 'Stop'
$info = Get-Content $Diagnostic -Raw | ConvertFrom-Json
$inputFile = Join-Path $info.eligible_cache 'eligible_acts.json'
$previous = Get-Content $Baseline -Raw | ConvertFrom-Json
$ids = @($previous.selected_celex)
if ($ids.Count -ne 200) { throw 'The baseline must identify the original 200 acts.' }
$inputHash = (Get-FileHash $inputFile -Algorithm SHA256).Hash.ToLowerInvariant()
if ($inputHash -ne $previous.input_sha256) { throw 'Input differs from the baseline. Use the original saved eligible_acts.json.' }
$celex = $ids -join ','
& .\.venv\Scripts\python.exe .\tools\document_relevance.py --input $inputFile --celex $celex --as-of $previous.as_of --input-budget 34000 --context-limit 40960 --workers 4 --out $Output
if ($LASTEXITCODE -ne 0) { throw 'The v7 run failed; inspect its terminal output.' }
