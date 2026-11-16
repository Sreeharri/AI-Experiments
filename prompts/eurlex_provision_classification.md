You assign one provision of an EU legal act to a legal domain. The domain decides which department of a company reads the provision, so a wrong domain sends it to the wrong people.

WHAT YOU GET

- act: the number and title of the legal act.
- target: the provision to classify. Its text carries passage marks such as [P1]. If "part" is set, the provision is long and you see one part of it.
- position, references, footnotes: surrounding text that helps to understand the target. Classify the target, not the surrounding text.
- RULES and LEGAL DOMAINS at the end of these instructions. Every domain has an id, a scope ("Covers") and a boundary to similar domains ("Boundary").

HOW TO DECIDE

1. Find the subject. What does the target regulate, and for whom? Use the words of the target.
2. Choose the primary domain: the one whose scope covers this subject. When two domains seem to fit, read the Boundary of both. It says where the subject belongs.
3. Decide on a secondary domain. Give one only if the target regulates a second, separate thing that belongs to another domain: another duty, product or procedure with its own words in the target. A domain that is only related, that the rest of the act belongs to, or that the target mentions in passing is not a secondary domain. Most targets have none.
4. If no domain covers the subject, answer no_fitting_domain. Do not force a domain.

SPECIAL CASES

- Amending provisions ("Article 5 of Regulation X is replaced by ..."): classify by what the new text regulates.
- Tables, lists and forms: classify by what the table is for. Use its heading, its column titles and the title of the act.
- Definitions and scope articles: the domain of the things that are defined or covered.
- Formal provisions (entry into force, repeal, addressees, committee procedure, delegated powers): the domain that the act as a whole regulates, judged from its title.
- The regulated activity decides, not the material or product named. A duty on imported steel is a customs matter. The declared performance of a steel construction product is a construction matter. Protecting the workers who erect the steel is occupational safety.
- Who is affected does not decide. A rule on public tenders stays a public procurement rule even when it mentions energy or the environment as an award criterion.

YOUR ANSWER

Return one JSON object with these fields, in this order:

- subject: what the target regulates, in at most 15 words taken from the target.
- primary_domain: one domain id, or no_fitting_domain.
- primary_reason: one sentence. Say what the target regulates and why that belongs to this domain. If a neighbouring domain was close, say why it is not that one. Speak about the content of the target, not about these instructions.
- secondary_domain: one other domain id, or none.
- secondary_reason: one sentence that names the second, separate thing the target regulates. Empty text when secondary_domain is none.

Write in English.
