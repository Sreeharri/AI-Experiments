You check one point of a German summary of an EU legal provision against the legal text it was taken from.

INPUT

- provision: the article or annex.
- kind: the kind of the point (WHO, DUTY, DEADLINE, CONDITION, OTHER).
- point: the German point to check.
- source: the sentences of the legal text (English) that the point is taken from.

PROBLEMS TO FIND

- PERMISSION_AS_DUTY: the source allows something ("may", "can", "is allowed to", "may choose", "may require"), but the point makes it a duty ("muss", "müssen", "ist verpflichtet"). Correct it with "kann", "können", "darf", "dürfen" or with who may require it ("Die Mitgliedstaaten können verlangen, dass …").
- ACTOR_MISSING: the source says who must act (Member States, the Commission, an agency, a competent or national authority, ...), but the point does not name them, for example a passive sentence ("Die Regeln müssen bis … erlassen werden"). Correct it by naming who must act ("Die Mitgliedstaaten müssen bis … Regeln für Sanktionen erlassen").
- BOTH: both problems.
- NONE: the point says what the source says.

RULES

- Change only what the problem needs. Keep all other content of the point: numbers, dates, units, names, standards and references exactly as they are. Add nothing that is not in the source.
- When problem is NONE, return the point unchanged.
- Write the point in German, in one or two sentences, in plain words.

Return one JSON object with problem and point.
