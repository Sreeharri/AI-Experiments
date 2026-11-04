# Profile format correction

Replace tools/export_labelling_excel.py using this patch. The profile loop now
handles both keyed mappings and lists, including not_roles. Original entries
are retained. No changes to the model, profile file or saved results are needed.

Eight offline data tests pass, including list-based exclusions and list objects.
A complete Excel export still runs on your machine; it was not rendered here.

From the AI-Experiments root:

```powershell
Expand-Archive "$env:USERPROFILE\Downloads\excel_labelling_export_fix.zip" -DestinationPath . -Force
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_export_labelling_excel.py" -v
$info = Get-Content .\data\dev400_new_v2_insufficient.json -Raw | ConvertFrom-Json
$inputFile = Join-Path $info.eligible_cache "eligible_acts.json"
.\.venv\Scripts\python.exe .\tools\export_labelling_excel.py --run .\runs\dev_A_200_v6_4 --input $inputFile --out .\data\dev_A_200_labelling.xlsx
```
