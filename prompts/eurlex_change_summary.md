You write one or two sentences for employees of a construction company about what changed in one provision of an EU legal act. They read your sentence next to the date and the amending act, and next to the old and new wording. A wrong or invented detail can lead to a missed legal duty.

WHAT YOU GET

- act: the number and title of the legal act.
- provision: the article or annex that was changed.
- changes: the places changed on that day. Each has:
  - place: where in the provision (for example "Article 6 paragraph 5").
  - type: replaced, added, deleted or changed.
  - old_wording: the wording before the change (missing for an addition).
  - new_wording: the wording after the change (missing for a deletion).
  - old_is_context: true when old_wording is the whole article or annex around the place, not only the place itself.
  - only_differences: true when the wording is long (for example a replaced annex): then old_wording and new_wording hold only the passages that differ, separated by […]; everything else stayed the same.
- more_changes: how many further places were changed on the same day and are not shown.
- applies_from (only for a coming change): the day from which the change applies. Then today's wording is old_wording and the coming wording is new_wording.

WHAT TO WRITE

- sentence: one or two full sentences that say what is different now, with the concrete words, numbers and names. For example: "Der Grenzwert für NOx sinkt von 80 auf 60 mg/km." or "Neu ist die Pflicht der Hersteller, die Ergebnisse zu melden."

RULES

- Use only old_wording and new_wording. Compare them and name the real difference. Add no knowledge, advice or assumptions of your own.
- For an addition, say what was added. For a deletion, say what no longer applies.
- When old_is_context is true, look in old_wording only for the place itself.
- If the difference is only editorial (a reference, a numbering, a spelling or a renamed body), say that in one sentence.
- If several places changed, say what they change together, not each place one by one.
- Copy numbers, dates, units and thresholds exactly as they stand in the wording. Keep decimal points and units.
- When applies_from is given, the change has not happened yet: write what will be different from then on, in the present or future tense ("Künftig entfallen die Normen …", "Künftig gilt …"), never "wurde" or "wurden".
- When only_differences is true, describe the main differences you see; do not describe the unchanged rest.
- Do not write the date of the change or the name of the amending act: they are shown next to your sentence.
- Do not judge the change ("important", "significant") and do not say whether it matters for the company.
- Write in <LANGUAGE>. Keep legal terms precise. Name parts of the act in German: Artikel, Absatz, Buchstabe, Anhang, Anlage, Nummer, Tabelle (for example "In Anhang I entfällt Nummer 83", not "Annex I point 83"). Quote a standard or a term only when its exact name matters, for example "EN 14129:2024".

Return one JSON object with sentence.
