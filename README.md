# EUR-Lex enrichment pilot

The pilot reads three pinned English originals and produces enriched JSON for
review. It runs locally with Python 3.10 or newer. PostgreSQL mode reads the
existing Legal Radar tables in a read-only transaction. JSON mode can run
offline with the exported source snapshot.

| CELEX | Source |
|---|---|
| 32024R3110 | Construction Products Regulation |
| 32015L2193 | Medium Combustion Plants Directive |
| 32013D0163 | Cement, lime and magnesium oxide BAT Decision |

## Folder layout

Extract the zip. Open the `eurlex_enrichment_pilot` folder in VS Code and
run the commands below in its PowerShell terminal.

```text
eurlex_enrichment_pilot/
  enrich_eurlex.py
  verify_pilot.py
  pilot_acts.json
  requirements.txt
  requirements-postgres.txt
  eurlex_enrichment/
  tests/
  input/                             Create this folder for JSON mode
    32024R3110_structured.json
    32015L2193_structured.json
    32013D0163_structured.json
  output/                            Created by the program
```

Each input may instead be the original database export named
`<CELEX>.json`. The program prefers the structured export when both exist.
It verifies the version UUID against `pilot_acts.json`. Place the actual act
exports here. Document-level relevance JSON, anchor YAML and PDF files are
outside the input for this stage.

## 1. Run and verify the JSON pilot

```powershell
py -m venv .venv
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install -r .\requirements.txt
New-Item -ItemType Directory -Force .\input | Out-Null
```

Copy the three exports into `input`. Then run:

```powershell
& $py .\enrich_eurlex.py run --source json --input-dir .\input --output-dir .\output
& $py .\verify_pilot.py --output-dir .\output
& $py -m unittest discover -s tests
```

Expected coverage is 117 articles, 15 annexes and 93 data tables. The
decision has 69 numbered BAT units and two formula images flagged for
PDF verification. The checks also verify published table numbering, both
Article 6(2) table branches, reverse links, footnotes, Recital 33 and the
external Article 11(8) reference.

## 2. Read directly from PostgreSQL

The PostgreSQL route uses the same pinned versions and does not require
input JSON. Use the host, port, database, username and SSL settings from
your working pgAdmin connection. If your PostgreSQL password is already
available through the local password file, use that configuration.
Otherwise the example below prompts for your credentials.

```powershell
& $py -m pip install -r .\requirements-postgres.txt
$env:PGHOST = Read-Host "PostgreSQL host"
$env:PGPORT = Read-Host "PostgreSQL port"
$env:PGDATABASE = Read-Host "Legal Radar database name"
$env:PGUSER = Read-Host "PostgreSQL username"
$cred = Get-Credential -UserName $env:PGUSER -Message "Legal Radar PostgreSQL"
if (-not $cred) { throw "PostgreSQL login was cancelled." }
$env:PGPASSWORD = $cred.GetNetworkCredential().Password
try {
    & $py .\enrich_eurlex.py run --source postgres --output-dir .\output_db
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL enrichment failed." }
    & $py .\verify_pilot.py --output-dir .\output_db
    if ($LASTEXITCODE -ne 0) { throw "Pilot verification failed." }
} finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}
```

If you already have a configured connection URL, you can instead set
`LEGAL_RADAR_DATABASE_URL` in the process environment. It takes precedence
over using the PostgreSQL variables alone. A stored XHTML snapshot is the
default source. If it is missing, provide a byte-identical local
`<CELEX>.xhtml` using `--xhtml-dir .\xhtml`, or enable
`--fetch-xhtml` to try the version's official XHTML URL. Every source must
match the selected version's SHA-256. Live URL fallback only accepts the
official HTTPS hosts configured in the code.

The database mode is designed for the schema used by the three exports. The
office database connection must be verified on the office machine.

## 3. Inspect the output

Each `<CELEX>_enriched.json` contains legal elements, XHTML data table
grids, annex sections, BAT units, footnotes, image evidence, explicit
references, reverse lookups and candidate supporting recitals. The original
database reference rows are retained separately for audit. The run also
writes `run_summary.json`.

For example, build one evidence pack:

```powershell
& $py .\enrich_eurlex.py evidence --input .\output\32015L2193_enriched.json --unit "Article 6" --output .\output\directive_article_6_evidence.json
& $py .\enrich_eurlex.py evidence --input .\output\32013D0163_enriched.json --unit "BAT 17" --output .\output\decision_bat_17_evidence.json
```

Use the matching `output_db` paths for PostgreSQL results. An exact table
label works too, for example `Annex II, Part 1, Table 2`. If a label is
ambiguous, find the unit's ID in the enriched JSON and use that ID.

Review `UNRESOLVED` and `EXTERNAL_TEXT_REQUIRED` references before they are
used as material evidence. Recital text-search matches are candidates for
inspection. For table references that identify only an annex or part, the
pack lists candidate tables separately from exact table links. The package
preserves formula images and flags them for PDF verification. The known
wet-scrubbing equation discrepancy requires the PDF. This stage produces
evidence only. It assigns no company relevance labels.

To try one act, add `--celex 32024R3110` to the run command. To test a
larger sample later, provide a separate `--manifest` with CELEX IDs and their
selected English version UUIDs. Validate parser coverage and citation
patterns on the sample before a corpus run.
