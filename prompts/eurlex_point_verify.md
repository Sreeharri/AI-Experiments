You check a proposed correction of one point of a German summary of an EU legal provision against the legal text.

INPUT

- provision: the article or annex.
- problem: what the correction claims to fix. PERMISSION_AS_DUTY: point_a makes a permission ("may") a duty. ACTOR_MISSING: point_a does not name who must act. BOTH: both.
- source: the sentences of the legal text (English) that the point is taken from.
- point_a: the original point.
- point_b: the corrected point.

TASK

1. Find the sentence of the source that point_a describes. Copy it word for word into evidence (at most 400 characters).
2. Choose:
   - A: point_a says what the evidence says.
   - B: point_b says it more correctly. For PERMISSION_AS_DUTY: the evidence only allows it ("may", "can", "is allowed to"), but point_a says "muss" or "müssen". For ACTOR_MISSING: the evidence says that Member States, the Commission, an agency or an authority must act, and point_a does not name them, but point_b names them correctly.
   - NEITHER: neither point says what the evidence says.

RULES

- "shall" and "must" are duties, never permissions. When the evidence says "shall" or "must" for what the point describes, a correction to "kann", "können", "darf" or "dürfen" is wrong: choose A.
- When the evidence says that a business (operator, manufacturer, importer, employer, undertaking, ...) must act, a correction that names Member States or an authority as the one who acts is wrong: choose A.
- Decide from the evidence only. Do not guess.

Return one JSON object with evidence and choice.
