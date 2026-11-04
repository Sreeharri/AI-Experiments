# V7: overall document relevance

Extract this patch into your existing AI-Experiments project root. It replaces
`lr_ai/document_filter.py`, `tools/document_relevance.py` and two prompt files.
It does not replace your business anchor YAML, Qwen client or stored inputs.
Keep your v6.3 result folder for comparison.

The decision has exactly three fields: `reason`, `anchor_ids`, `level`.
Levels are DIRECT, INDIRECT and IGNORE only. Qwen makes the decision in one
model round. NEEDS_REVIEW and its second relevance call have been removed for
this requested three-level version. Existing HTTP retries for technical problems
remain in your client. Technical failures are saved as failures, never IGNORE.
Existing wrapper metadata (CELEX, timing, excerpts and diagnostics) remains
outside the decision for inspection and evaluation.

The prompt asks for overall business relevance, a simple 1–2 sentence reason,
and up to three business anchor IDs. Direct relevance need not establish a
particular article's confirmed applicability. Directives addressed to Member
States can still matter to the company's activities. Supplier, customer,
purchased-product, project, cost and funding paths can support INDIRECT.
The profile scopes still matter; no company activities are invented.

Passage selection uses source order and sampling across the text. There is no
business-keyword ranking, exact anchor phrase matching, or heading-word scoring.
Opening text and passages spread through the body, recitals and annexes are
interleaved. Plain paragraphs outside recognized article containers are retained.
It does not require chapter/article labels or fixed counts to send an act to Qwen.
Up to the existing --max-chunks cap is sent; large acts are sampled, not fully
read. The existing tokenizer trims excerpts to the request budget. Missing or
misparsed source content cannot be reconstructed by changing this prompt.

The JSON schema supplies the allowed anchor ID vocabulary to the server. Python
does not check a relevance level against an anchor or rewrite the model's output.
The diagnostic scripts in check_run.zip are not added to the runtime classifier.

Run in PowerShell from the project root:

```powershell
Expand-Archive "$env:USERPROFILE\Downloads\document_screening_v7_patch.zip" -DestinationPath . -Force
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_document_screening_v7.py" -v
.\rerun_dev_A_v7.ps1
```

The rerun script uses exactly the 200 CELEX IDs and original input hash recorded
in `runs/dev_A_200_v6_3/manifest.json`. It writes to a new `runs/dev_A_200_v7`
folder. Your original cached eligible_acts.json must still exist. If PowerShell
blocks .ps1 scripts, run its commands in the terminal directly; do not change
company execution-policy settings.

The six included offline tests cover the output contract, missing headings,
profile-independent sampling, source coverage, orphan paragraphs, and model
request/schema construction. Previous tests specifically expecting NEEDS_REVIEW
are obsolete for this version. These are software tests, not accuracy tests.
No live Qwen or new 200-act classification was run in the preparation environment.
Compare v7 with v6.3 on independent labels after the office rerun; better accuracy
is not guaranteed by a simpler prompt.
