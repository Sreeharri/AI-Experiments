EUR-Lex v3.2 overnight connection recovery

Install
Extract this ZIP into AI-Experiments. Keep the existing v3.2 runner, prompts,
anchors and enriched data in place. This adds an overnight launcher that uses
the original runner and preserves its checkpoint fingerprint.

Run from the AI-Experiments PowerShell terminal

& .\.venv\Scripts\python.exe .\run_eurlex_provision_overnight.py --manifest .\runs\eurlex_full_v10\provision_manifest.json --enriched-dir .\output_db_corpus --no-document-hint --workers 4 --output-dir .\runs\eurlex_provisions_baseline_v3_2

Use the exact output folder from the interrupted baseline. Successfully saved
classifications and unassessable entries are reused. Pending confirmations and
unfinished targets resume through the original runner.

Recovery
After a connection failure the launcher waits 15 minutes and retries the batch.
If the next attempt also fails, it waits 30 minutes, then 45 minutes, then
60 minutes between all subsequent attempts until connectivity returns.
The retry schedule resets when a batch finishes successfully.
The process stays running during these waits. Authentication, input validation
and configuration errors stop with their error output for intervention.

Progress
Updates every five seconds. The bar counts saved classifications plus reported
unassessable targets across the corpus. It shows both counts separately, the
batch, current activity and retry countdown. Failed or pending targets are not
counted as finished. Raw child output is appended to each batch's overnight.log.
The original batch and corpus summaries continue to be written by v3.2.

Stop/resume
Ctrl+C stops the wait immediately. During requests it waits for the child runner
to finish active requests and save checkpoints. Wait for the terminal prompt.
Use the same command to resume. Keep the computer awake, powered and connected.
A sleeping or shut-down computer cannot retry. Run one launcher per output folder.

Validation
Seven local tests passed covering the delay schedule, transient/permanent errors,
checkpoint counts, repeated failures followed by success, actual subprocess
output/exit capture, countdown and cancellation. No live company endpoint or
Windows overnight run was available for testing.
