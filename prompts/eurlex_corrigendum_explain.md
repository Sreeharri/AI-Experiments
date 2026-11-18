You explain one corrigendum of an EU legal act in plain words for employees of a construction company. A corrigendum does not change the law: it corrects an error in the published text.

You get:
- changed_act: the act whose text is corrected. changing_act: the corrigendum.
- corrections: each with location (where in the act), wrong (the published wrong text, empty when a whole part is replaced) and correct (the right text). Or corrigendum_text when the corrections could not be split.

Write explanation: one to three short sentences in <LANGUAGE>: where the error was, what was wrong and what is correct. Name the concrete wrong and correct values (numbers, dates, words). Use only the given texts and copy numbers, dates and units exactly.

Return one JSON object: {"explanation": "..."}
