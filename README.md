# Simple four-column export

The Excel file has one sheet and exactly four columns:
ID, Title, Evidence pack, Label.

Each act has one blank label cell with DIRECT / INDIRECT / IGNORE dropdown.
Packs longer than Excel's cell-text limit continue on the next row. ID, Title
and Label are merged across those rows; no evidence is truncated. These merged
act blocks should not be sorted as independent rows. Expand the formula bar to
read long cell text. The pack contains the saved excerpt text, headings and
source IDs, not the entire act or the business profile.

To export again from the AI-Experiments root:

```powershell
.\.venv\Scripts\python.exe -m pip install openpyxl
$info = Get-Content .\data\dev400_new_v2_insufficient.json -Raw | ConvertFrom-Json
$inputFile = Join-Path $info.eligible_cache "eligible_acts.json"
.\.venv\Scripts\python.exe .\tools\export_simple_labelling.py --run .\runs\dev_A_200_v6_4 --input $inputFile --out .\data\simple_labels.xlsx
```

No profile parsing, AI calls or database connection is needed. The original
act input hash must match the saved run. Choose a new output name rather than
overwriting a file containing your human labels.
