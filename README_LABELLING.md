# Export the same 200 acts for independent human labelling

Extract into the AI-Experiments root. This adds a standalone exporter, without
changing your model or existing results. It accepts a saved run folder or ZIP.
The generated workbook is for Microsoft Excel and has six sheets:

| Sheet | Contents |
|---|---|
| Labels | 200 acts in manifest order, full titles, blank human label/reason/anchor/notes columns, evidence navigation and official links |
| Evidence | Every saved passage Qwen saw, CELEX, source ID, heading, full text parts, version and boundary warnings |
| Decision_guide | DIRECT/INDIRECT/IGNORE criteria, instructions and your supplied actual prompt with a fingerprint comparison |
| Business_profile | Business anchors, roles, excluded roles, indirect paths and negative anchors from the run-matching YAML |
| Qwen_results | Predictions and reasons kept separate from your human labels |
| Run_info | Source fingerprints, run settings and complete raw profile text, including comments/assumptions |

Human entry columns on Labels are yellow, with a label dropdown. Blank labels
mean not yet decided. Use Review notes when uncertain; do not invent a label
to fill every row. Fill labels before consulting Qwen_results to reduce bias.
Matching business anchors should explain the company's connection, not merely
share a word. The guide is document screening, not detailed provision analysis.

Evidence text is split into <=1200-character cells so it remains readable and
cannot exceed Excel's cell-text limit. Rejoin parts in numerical order to
recover each exact saved passage. The exporter uses the saved run's `evidence`
array, not a new retrieval pass. It does not recreate missing headings or fetch
additional legal text. These passages are not the entire legal act or the full
model request. The workbook also supplies the business profile and prompt for
decision context. Original act titles come from the exact original input.

The profile and act-input hashes must match the run or export stops. A changed
prompt is marked as unmatched instead of falsely described as the prompt Qwen
used. Use --prompt/--second-prompt to supply archived prompt files if needed.
The original run fingerprint concatenates both files even though v7 uses only
the decision prompt. Technical metadata is preserved in the run; raw model
thinking is not required for human labelling and is not exported.

PowerShell from the AI-Experiments folder:

```powershell
Expand-Archive "$env:USERPROFILE\Downloads\excel_labelling_export.zip" -DestinationPath . -Force
.\.venv\Scripts\python.exe -m pip install -r .\requirements-labelling.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_export_labelling_excel.py" -v
$info = Get-Content .\data\dev400_new_v2_insufficient.json -Raw | ConvertFrom-Json
$inputFile = Join-Path $info.eligible_cache 'eligible_acts.json'
.\.venv\Scripts\python.exe .\tools\export_labelling_excel.py --run .\runs\dev_A_200_v6_4 --input $inputFile --out .\data\dev_A_200_labelling.xlsx
```

If your latest result directory is called `dev_A_200_v7`, change only the --run
path. To export directly from your saved ZIP, pass that ZIP path to --run.
Your exact business_anchors_en.yaml must still be in config. The exporter does
not use the database or Qwen endpoint. An existing Excel output is never
overwritten, to protect human labels; choose a new --out filename if needed.

After labelling, save a separate CSV copy of the Labels sheet for the existing
evaluator. Its required `celex` and `label` headers are already present. Keep
the original .xlsx so the evidence and other sheets are preserved.

Verification: five offline data-handling tests cover passage reconstruction,
source IDs, run membership/order, input hashes and visible encoding of forbidden
XML characters. The supplied 200-act ZIP was also checked for evidence extraction.
The Excel-generation code is supplied for execution on your machine; a complete
workbook was not generated or visually reviewed here. After export, open it in
Excel and confirm the 200 labels and saved evidence are present before labelling.
