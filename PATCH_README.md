# v6.2 improvements — explanation and anchor selection

Apply to your existing v6 or v6.1 project. Extract into AI-Experiments and replace
matching files. Keep your existing .env, data and approved company configuration.

Changed runtime files:
- prompts/document_relevance.txt
- prompts/second_pass.txt
- tools/document_relevance.py
- lr_ai/document_filter.py is included with the three-field v6.1 format for compatibility.

The output is still reason, anchor_ids, level. Qwen's decision remains FINAL.
Only NEEDS_REVIEW triggers one second call; the second answer is saved as returned.
No Python relevance, quote, role or anchor-ID checks were added.

Improvements:
1. The reason should connect duty bearer -> supplied company role/activity -> impact.
2. Qwen must read specific anchor notes and exclusions before choosing the level.
   Broad keyword/role overlap does not override a specific anchor's INDIRECT note.
3. Using purchased machinery is distinguished from manufacturing machinery.
   A specific direct-duty route must be supported by the legal text and company profile.
4. Receiving/applying chemical safety information is distinguished from supplying it.
5. valid_anchor_ids is copied from the actual anchors mapping into the MODEL INPUT.
   This is a reference list, NOT a Python output checker. Role IDs and invented
   combinations should not appear in anchor_ids; Qwen is instructed to select exact keys.
6. All company scopes, not_roles, assumptions, negative anchors and caveats remain supplied.

Tests exercise data delivery and saving behaviour. They do not establish that Qwen
will always obey the prompt or that its legal reasoning is correct. Compare the new
run with your earlier run, then use independently labelled examples to measure quality.
No live company endpoint test was performed here.

Run the new workflow tests (avoids old v5 tests in existing folders):
```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_simple_flow.py" -v
```

Run all 10 acts with NEW output directory:
```powershell
.\.venv\Scripts\python.exe .\tools\document_relevance.py --input .\data\inspection\eurlex_structured_10.json --as-of 2026-11-04 --input-budget 34000 --context-limit 40960 --workers 4 --out .\runs\sample10_v6_2
```

Do not reuse old run directories after changing prompts/code. No new dependencies.
Upload the resulting sample10_v6_2 folder to inspect the actual model explanations.
