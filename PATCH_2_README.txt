Patch 2 for provision relevance v3.1

Apply after Claude's v3.1 patch. Extract the ZIP into AI-Experiments and replace the included files while the runner is stopped.

Changes
- Confirmation requests no longer disclose the first verdict.
- Formal answers conflicting with a company role or indirect path become UNSURE.
- Negative topics conflicting with a positive other_anchor or indirect path become UNSURE. A generic role alone does not override a negative topic.
- An unresolved company applicability question produces UNSURE before the other decision rules.
- Prompt rules match these decision rules.

Validation
40 provision tests passed locally, including conflict and missing-fact regression cases. No live Qwen endpoint test was performed. More UNSURE results may increase review workload.

Pilot
From the AI-Experiments PowerShell terminal run the command below. Use this new output directory so previous cached decisions are not reused.

& .\.venv\Scripts\python.exe .\screen_eurlex_provisions_simple.py pilot --manifest .\runs\eurlex_full_v10\provision_manifest.json --enriched-dir .\output_db_corpus --targets-file .\config\eurlex_provision_review100.json --output-dir .\runs\review100_v3_1_patch2

Review incorrect IGNORE decisions, unsupported DIRECT decisions and UNSURE counts before the full run. Existing workbook labels were AI generated and need human adjudication on disputed rows.
