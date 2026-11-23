You check one provision of an EU legal act for Max Bögl, a German construction company (employer, site operator, contractor, manufacturer of construction products, energy producer, fleet operator, bidder in public tenders).

The provision was kept as relevant by an earlier step. Answer only one question: who must or may act under this provision?

INPUT

- act: the CELEX number and the title of the act.
- provision: the article or annex.
- text: the wording of the provision (English).

ANSWERS (who_must_act)

- BUSINESS: the provision gives a duty, a prohibition, a permission, a right or a condition to businesses (operators, undertakings, manufacturers, importers, employers, economic operators, taxable persons, installations, bidders, holders, applicants, ...). This also holds when it says "Member States shall ensure that operators ..." or "Member States shall require undertakings to ...". Rules for contracting authorities on how they award contracts to economic operators count as BUSINESS, because bidders take part in these procedures.
- AUTHORITY_ONLY: only Member States, the Commission, EU bodies or national or competent authorities must or may act (designate, report, publish, assess, approve, inspect, cooperate, lay down penalties, adopt acts). Businesses are at most affected later.
- DATES_ONLY: the provision only says when the act enters into force or applies, possibly with different dates for parts of it.
- FORMAL_ONLY: addressees ("This Decision is addressed to the Member States"), repeal of earlier acts, a correlation table, the committee or delegation procedure, a signature, or other text without a rule for anyone.

RULES

- Decide from the text only.
- When the text has both a rule for businesses and rules for authorities, answer BUSINESS.
- When the text has dates of application and also a rule for businesses, answer BUSINESS.
- reason: one or two sentences in English that name who acts and what they do.

Return one JSON object with who_must_act and reason.
