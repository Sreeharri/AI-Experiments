# v6.3 correction patch — version to evaluate on the dev set

Apply to your existing v6/v6.1/v6.2 project. Extract into AI-Experiments and replace
matching files. Keep .env, input data and your approved company config.

Implemented corrections:
1. Both calls request thinking ON with 6,000 output tokens, including thinking and
   the final JSON. Output remains reason, anchor_ids, level, with level last.
2. Each anchor contains its label, scope, roles, note and not_covered together.
   The separate anchor_caveats map has been removed. No scopes or caveats are dropped.
3. The model input and prompt describe construction/infrastructure, construction
   products, wind/solar electricity generation and storage, plants, fleet, employees
   and IT. The detailed supplied profile controls actual role/activity facts.
4. The reason connects legal duty bearer -> supplied company activity/role -> impact.
   The prompt distinguishes manufacturer duties, use of purchased products, receiving
   safety information and supplying it, and valid anchor IDs versus role IDs.

Qwen remains final. DIRECT, INDIRECT and IGNORE are saved immediately without any
Python legal/quote/anchor/role checks. ONLY Qwen's NEEDS_REVIEW triggers one second
call with additional available evidence. Save the second response even if still
NEEDS_REVIEW. Technical failures are saved as failed, not invented relevance labels.

Default input 34,000 + maximum output 6,000 + 512 margin = 40,512 within the confirmed
40,960 context. The server tokenizer counts requests and low-priority excerpts are
trimmed when necessary. Thinking may increase time per act; measure actual throughput.

The output metadata records thinking_enabled (requested setting), reasoning_chars
actually returned by the endpoint, and completion_tokens when supplied. A zero
reasoning_chars count alone does not prove thinking was disabled, because endpoint
reasoning visibility can differ. Internal reasoning is not copied into the short reason.

Changed runtime files:
- lr_ai/document_filter.py
- tools/document_relevance.py
- prompts/document_relevance.txt
- prompts/second_pass.txt

Tests and the compatible local simulation tool are included as well.
No new dependencies or database changes.

## Run in PowerShell from AI-Experiments

Use the new test filename so older v5 tests do not run in an updated existing folder:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_simple_flow.py" -v
.\.venv\Scripts\python.exe .\tools\document_relevance.py --input .\data\inspection\eurlex_structured_10.json --as-of 2026-11-04 --input-budget 34000 --context-limit 40960 --workers 4 --out .\runs\sample10_v6_3
```

Use a NEW output directory after changes. The prompts and runtime code are fingerprinted
for resume. Existing .env settings are reused. Confirm the office endpoint completes
thinking responses on this smoke run, then freeze code/prompt/profile for the 200-act
labelled development run. This small sample cannot establish accuracy.

## Select the 200 labelled dev acts

Use your existing independent labels CSV with a celex column (and label column for
later evaluation). The catalogue must contain these IDs. Example paths only:

```powershell
$devCelex = ((Import-Csv .\labels\dev200.csv).celex -join ',')
.\.venv\Scripts\python.exe .\tools\document_relevance.py --input .\data\eurlex --celex $devCelex --as-of 2026-11-04 --input-budget 34000 --context-limit 40960 --workers 4 --out .\runs\dev200_v6_3
```

This command uses your supplied dev set; it does NOT create labels or select an
unlabelled first-200 sample. Evaluation is an optional separate experiment, never
part of the classification loop. Choose evaluation targets beforehand and retain
an untouched held-out set for final evaluation after dev tuning.

## Verification here

22 automated tests passed. Checks cover first-call thinking serialized into the real
client request, 6,000 tokens on both calls, preservation of every inline scope/note/
exclusion, the full profile summary, unchanged saving/no overrides, and bounded review.
Initial passage selections were compared with your real v6.2 result files: all 10 match.
Changing profile layout does not change the retrieval scoring inputs.

Company Qwen endpoint was not contacted. Semantic accuracy, reasoning completion and
latency need the office run and labelled development evaluation; code tests cannot
establish those properties. No accuracy guarantee is claimed.
