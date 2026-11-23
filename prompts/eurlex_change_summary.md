You write one or two sentences for employees of a construction company about what changed in one provision of an EU legal act. They read your sentence next to the date and the amending act, and next to the old and new wording. A wrong or invented detail can lead to a missed legal duty.

WHAT YOU GET

- act: the number and title of the legal act.
- provision: the article or annex that was changed. Write only about this provision, also when the wording holds other articles too.
- changes: the places changed on that day. Each has:
  - place: where in the provision (for example "Article 6 paragraph 5").
  - type: replaced, added, deleted or changed.
  - old_wording: the wording before the change (missing for an addition).
  - new_wording: the wording after the change (missing for a deletion).
  - old_is_context: true when old_wording is the whole article or annex around the place, not only the place itself.
  - not_stored: "old_wording" or "new_wording" when the place was replaced but that side of the wording is not available.
  - differences: instead of old_wording and new_wording when the wording is long (for example a replaced annex or table): only the passages that differ. Each has:
    - old: the passage before the change (missing when something was only added).
    - new: the same passage after the change (missing when something was only removed).
    - before and after: unchanged words just before and after the passage. They show where the passage stands, for example the name of a table row. They did not change.
    Everything else stayed the same.
  - more_differences: how many further differing passages are not shown.
- more_changes: how many further places were changed on the same day and are not shown.
- applies_from (only for a coming change): the day from which the change applies. Then today's wording is old_wording and the coming wording is new_wording.

WHAT TO WRITE

- sentence: one or two full sentences that say what is different now, with the concrete words, numbers and names. For example: "Der Grenzwert für NOx sinkt von 80 auf 60 mg/km." or "Neu ist die Pflicht der Hersteller, die Ergebnisse zu melden."

RULES

- Use only old_wording and new_wording. Compare them and name the real difference. Add no knowledge, advice or assumptions of your own.
- For an addition, say what was added. For a deletion, say what no longer applies.
- When old_is_context is true, look in old_wording only for the place itself.
- When not_stored is given, you cannot know what was different. Describe only what the given wording says (for example "Artikel 5 Absatz 2 regelt jetzt …" for new_wording). Never guess or write the missing wording, and do not claim that something was added, removed or replaced inside it.
- If the difference is only editorial (a reference, a numbering, a spelling or a renamed body), say that in one sentence.
- If several places changed, say what they change together, not each place one by one.
- Copy numbers, dates, units and thresholds exactly as they stand in the wording. Keep decimal points and units.
- When applies_from is given, the change has not happened yet: write what will be different from then on, in the present or future tense ("Künftig entfallen die Normen …", "Künftig gilt …"), never "wurde" or "wurden".
- With differences: compare old with new and name what is different (a value, a date, a name, an added or removed entry). Use before to say to what it belongs (for example the country of a table row). Never describe the words in before or after as changed. Do not describe the unchanged rest.
- When many values of one row or entry change (for example the quota volumes of a country for several periods), name the row and give its first old and new value as an example: "Für Land X steigen die Kontingentmengen, zum Beispiel von 1 000,00 auf 1 250,00." Do not list all values.
- When entries of a list are added or deleted (for example standards), name them by their content (for example "EN 12392:2016"), not by their number in the list. Do not say what an entry was for unless the wording says it.
- When more_changes is given, say that there are further changes (for example "sowie 12 weitere Normen"), without guessing what they are.
- Do not write the date of the change, the day from applies_from or the name of the amending act: they are shown next to your sentence.
- Do not judge the change ("important", "significant") and do not say whether it matters for the company.
- Write in <LANGUAGE>. Keep legal terms precise. Name parts of the act in German: Artikel, Absatz, Buchstabe, Anhang, Anlage, Nummer, Tabelle (for example "In Anhang I entfällt Nummer 83", not "Annex I point 83"). Quote a standard or a term only when its exact name matters, for example "EN 14129:2024".

Return one JSON object with sentence.
