# v6.1 patch: short reason and matching business anchors

Apply this patch ON TOP OF the complete v6 package. Extract into your AI-Experiments
folder, replacing matching files. Keep your .env, data and current config.

Runtime changes:
- lr_ai/document_filter.py: output format now contains reason, anchor_ids, level.
- prompts/document_relevance.txt: short prompt requests a brief business connection.
- prompts/second_pass.txt: preserves the same three-field format on reconsideration.

Also included: updated tests and local offline verification tool.
No Python relevance/quote/anchor/role checks were added. Python saves Qwen's entire
answer unchanged. Only NEEDS_REVIEW triggers a second call. Technical errors remain
failures, not invented relevance decisions. Reasons and IDs are model output, not
independently verified findings. No model quotations are requested.

Output example (illustrative, not a measured result):
```json
{
  "reason": "The act may impose requirements on the supplied construction-product manufacturing role.",
  "anchor_ids": ["construction_products_ce"],
  "level": "DIRECT"
}
```

The reason is 1–2 short sentences (up to 600 characters). Up to 3 strongest anchor IDs
are requested; IGNORE uses an empty list. NEEDS_REVIEW explains the missing/damaged text.
These IDs support document screening, not exhaustive departmental routing.

Run from your existing AI-Experiments folder:
```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_simple_flow.py" -v
.\.venv\Scripts\python.exe .\tools\document_relevance.py --input .\data\inspection\eurlex_structured_10.json --as-of 2026-11-04 --input-budget 34000 --context-limit 40960 --workers 4 --out .\runs\sample10_v6_1
```

Use a NEW output directory, because the code and prompt changed. No new dependencies.
The full reason and matching anchor IDs appear under decision in each saved act JSON.
The runner also retains each raw Qwen response in rounds.

Verification: 18 automated tests passed. The supplied 10-act input also exercised the
real HTTP client against a local simulated endpoint (scripted test responses, not live
Qwen or accuracy measurements). Company endpoint was not contacted here.
