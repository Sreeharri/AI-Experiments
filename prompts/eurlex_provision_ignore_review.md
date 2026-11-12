CONFIRMATION PASS
This section applies only when the TASK line asks for a confirmation pass.
The first pass returned IGNORE. The TASK line gives its ignore reason, its
effect sentence and its reason. Treat them as a first opinion to check.

Read the whole target again and look for what the first pass may have missed:
a duty or right for a role Max Bögl holds, a scope rule, definition,
exception or condition that decides whether one of its activities is covered,
or an indirect path from another actor's duty to one of its business areas.
Then check that the ignore reason really applies. For OUTSIDE_BUSINESS, read
the "except" of the negative anchor and check that no business anchor covers
the subject of the target. For FORMAL, check that the target sets no rule of
its own. For NO_PATH, go through the indirect_paths one by one.

Keep IGNORE when the reason holds. Otherwise return DIRECT or INDIRECT. Use
the same fields and rules as in the first pass.
