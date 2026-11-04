# Database structure (2026-10-28 14:14)

Read-only description for writing the EUR-Lex export. Long text is shortened.

- Database: `legal_radar_backend`
- Server: PostgreSQL 18.6 on x86_64-windows

## Overview

| Table | Type | Rows | Columns |
|---|---|---:|---:|
| `public.act_roles_v` | view | 31398 | 2 |
| `public.act_structure_v` | view | error: canceling statement due to statement timeout | 21 |
| `public.agent_execution_items` | base table | 0 | 8 |
| `public.agent_executions` | base table | 31 | 20 |
| `public.alembic_version` | base table | 1 | 1 |
| `public.applicability_windows` | base table | 1579 | 8 |
| `public.change_events` | base table | 1259767 | 7 |
| `public.compliance_tasks` | base table | 0 | 11 |
| `public.coverage_register` | base table | 4 | 6 |
| `public.cross_source_relationships` | base table | 19992 | 17 |
| `public.current_instrument_versions_v` | view | 31344 | 9 |
| `public.direct_base_child_relationships_v` | view | 1041525 | 10 |
| `public.document_identifiers` | base table | 123119 | 4 |
| `public.effective_date_rules` | base table | 18724 | 9 |
| `public.element_identity_maps` | base table | 0 | 6 |
| `public.element_references` | base table | 1603455 | 11 |
| `public.external_reference_watch` | base table | 6477 | 7 |
| `public.filter_decision_logs` | base table | 171298 | 10 |
| `public.german_audit_events` | base table | 121860 | 10 |
| `public.german_operation_contexts` | base table | 0 | 4 |
| `public.german_operations` | base table | 32053 | 27 |
| `public.german_parse_revisions` | base table | 9499 | 9 |
| `public.german_publication_items` | base table | 9691 | 14 |
| `public.german_reconciliations` | base table | 10909 | 9 |
| `public.german_review_actions` | base table | 0 | 7 |
| `public.german_source_associations` | base table | 7 | 9 |
| `public.german_source_manifests` | base table | 54 | 12 |
| `public.german_source_observations` | base table | 9499 | 17 |
| `public.german_version_sources` | base table | 9203 | 10 |
| `public.german_work_items` | base table | 9097 | 24 |
| `public.gii_version_changes` | base table | 239801 | 12 |
| `public.ingestion_errors` | base table | 6164 | 9 |
| `public.ingestion_jobs` | base table | 71622 | 14 |
| `public.instrument_expressions` | base table | 28253 | 5 |
| `public.instrument_manifestations` | base table | 136994 | 8 |
| `public.instrument_relationships` | base table | 1041525 | 20 |
| `public.instrument_versions` | base table | 65640 | 21 |
| `public.legacy_relationship_candidates` | base table | 0 | 8 |
| `public.legal_elements` | base table | error: canceling statement due to statement timeout | 15 |
| `public.legal_instruments` | base table | 31398 | 9 |
| `public.legal_relationship_graph_v` | view | 1061224 | 11 |
| `public.monitor_run_findings_v` | view | 113482 | 13 |
| `public.monitor_runs` | base table | 18878 | 16 |
| `public.official_publications` | base table | 2952 | 10 |
| `public.recital_sets` | base table | 28325 | 6 |
| `public.recitals_v` | view | error: canceling statement due to statement timeout | 12 |
| `public.regulatory_attachments` | base table | 38505 | 15 |
| `public.regulatory_events` | base table | 28665 | 11 |
| `public.regulatory_item_versions` | base table | 21953 | 17 |
| `public.regulatory_items` | base table | 8024 | 21 |
| `public.regulatory_source_manifests` | base table | 881 | 14 |
| `public.relationship_observations` | base table | 1259768 | 8 |
| `public.relationship_provision_changes` | base table | 101024 | 14 |
| `public.review_items` | base table | 58266 | 11 |
| `public.run_documents` | base table | 113482 | 17 |
| `public.run_reports` | base table | 31 | 14 |
| `public.source_cursors` | base table | 16 | 6 |
| `public.source_documents` | base table | 256716 | 30 |
| `public.source_notifications` | base table | 8670 | 13 |
| `public.source_snapshots` | base table | 95844 | 10 |
| `public.watchlist_entries` | base table | 0 | 8 |

## `public.act_roles_v`

view, 31398 rows

| Column | Type | Null |
|---|---|---|
| `celex` | character varying | yes |
| `act_role` | text | yes |



Example row 1:
```json
{
 "celex": "31975D0095",
 "act_role": "STANDALONE"
}
```
Example row 2:
```json
{
 "celex": "31982D0268",
 "act_role": "STANDALONE"
}
```

## `public.act_structure_v`

view, error: canceling statement due to statement timeout rows

| Column | Type | Null |
|---|---|---|
| `celex` | character varying | yes |
| `version_key` | character varying | yes |
| `language` | character varying | yes |
| `version_kind` | character varying | yes |
| `version_date` | date | yes |
| `is_current` | boolean | yes |
| `is_preferred` | boolean | yes |
| `source_format` | character varying | yes |
| `parser_version` | character varying | yes |
| `quality_status` | character varying | yes |
| `quality_score` | double precision | yes |
| `element_id` | uuid | yes |
| `parent_id` | uuid | yes |
| `element_type` | character varying | yes |
| `identifier` | character varying | yes |
| `number` | character varying | yes |
| `heading` | text | yes |
| `ordinal` | integer | yes |
| `depth` | integer | yes |
| `path` | character varying | yes |
| `body_text` | text | yes |


## `public.agent_execution_items`

base table, 0 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `execution_id` | uuid | no |
| `item_type` | character varying | no |
| `item_id` | character varying | no |
| `role` | character varying | no |
| `outcome` | character varying | yes |
| `details` | jsonb | no |
| `created_at` | timestamp with time zone | no |

- FK: `FOREIGN KEY (execution_id) REFERENCES agent_executions(id) ON DELETE CASCADE`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (execution_id, item_type, item_id)`
- index: `CREATE UNIQUE INDEX agent_execution_items_pkey ON public.agent_execution_items USING btree (id)`
- index: `CREATE UNIQUE INDEX uq_agent_execution_item ON public.agent_execution_items USING btree (execution_id, item_type, item_id)`
- index: `CREATE INDEX ix_agent_execution_item_lookup ON public.agent_execution_items USING btree (item_type, item_id)`

## `public.agent_executions`

base table, 31 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `execution_key` | character varying | no |
| `parent_id` | uuid | yes |
| `tool` | character varying | no |
| `source` | character varying | yes |
| `operation` | character varying | yes |
| `status` | character varying | no |
| `requested_by` | character varying | no |
| `inputs` | jsonb | no |
| `lock_scope` | character varying | yes |
| `run_id` | uuid | yes |
| `run_key` | character varying | yes |
| `current_stage` | character varying | yes |
| `heartbeat_at` | timestamp with time zone | yes |
| `started_at` | timestamp with time zone | no |
| `completed_at` | timestamp with time zone | yes |
| `error_code` | character varying | yes |
| `error_message` | text | yes |
| `summary` | jsonb | no |
| `app_version` | character varying | no |

- FK: `FOREIGN KEY (run_id) REFERENCES monitor_runs(id) ON DELETE SET NULL`
- FK: `FOREIGN KEY (parent_id) REFERENCES agent_executions(id) ON DELETE RESTRICT`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (execution_key)`
- index: `CREATE INDEX ix_agent_execution_status ON public.agent_executions USING btree (status)`
- index: `CREATE INDEX ix_agent_execution_tool_source ON public.agent_executions USING btree (tool, source, started_at)`
- index: `CREATE INDEX ix_agent_execution_parent ON public.agent_executions USING btree (parent_id)`
- index: `CREATE UNIQUE INDEX uq_agent_execution_key ON public.agent_executions USING btree (execution_key)`
- index: `CREATE UNIQUE INDEX agent_executions_pkey ON public.agent_executions USING btree (id)`
- index: `CREATE INDEX ix_agent_execution_run ON public.agent_executions USING btree (run_id)`

Most common `source`: `ALL` (16), `BAUA` (11), `DGUV` (2), `BMUKN` (1), `BMAS` (1)
Most common `operation`: `MONITOR_DAILY` (16), `MONITOR` (15)
Most common `status`: `SUCCESS` (16), `PARTIAL` (12), `FAILED` (3)

Example row 1:
```json
{
 "id": "385f1feb-bfde-4520-b96c-2acc0f5d3051",
 "execution_key": "runsource-baua-monitor-20261022T091742226175Z-a32eef",
 "parent_id": "207b3bf7-4c30-4f7e-a524-4e0bac3d6816",
 "tool": "run_source",
 "source": "BAUA",
 "operation": "MONITOR",
 "status": "PARTIAL",
 "requested_by": "cli",
 "inputs": "{\"end\": null, \"limit\": 0, \"start\": null, \"source\": \"BAUA\", \"recheck\": false, \"gii_only\": false, \"fail_fast\": false, \"followups\": false, \"from_year\": 2023, \"operation\": \"MONITOR\", \"retry_now\": false, \"metadata_only\": fals… (331 chars)",
 "lock_scope": "BAUA",
 "run_id": "8d0b5272-1321-485a-b661-9d17be7e7adf",
 "run_key": "baua-monitor-20261022T091742230729Z",
 "current_stage": "BAUA complete 1/1",
 "heartbeat_at": "2026-10-22 11:22:46.373951+02:00",
 "started_at": "2026-10-22 11:17:42.226175+02:00",
 "completed_at": "2026-10-22 11:22:46.373951+02:00",
 "error_code": null,
 "error_message": null,
 "summary": "{\"counts\": {\"errors\": 0, \"created\": 0, \"updated\": 47, \"processed\": 71, \"unchanged\": 23, \"discovered\": 71, \"fetch_failures\": 1}}",
 "app_version": "3.4.1"
}
```
Example row 2:
```json
{
 "id": "5679d19c-0e6c-4f8d-906f-a4ac1ed22966",
 "execution_key": "runsource-baua-monitor-20261022T083136084018Z-da876d",
 "parent_id": "1940cd1e-8428-4f62-a7c2-a8d20398e5f9",
 "tool": "run_source",
 "source": "BAUA",
 "operation": "MONITOR",
 "status": "FAILED",
 "requested_by": "cli",
 "inputs": "{\"end\": null, \"limit\": 0, \"start\": null, \"source\": \"BAUA\", \"recheck\": false, \"gii_only\": false, \"fail_fast\": false, \"followups\": false, \"from_year\": 2023, \"operation\": \"MONITOR\", \"retry_now\": false, \"metadata_only\": fals… (331 chars)",
 "lock_scope": "BAUA",
 "run_id": "f831a881-5fb8-40bd-b5a0-57797d332b10",
 "run_key": "baua-monitor-20261022T083136084018Z",
 "current_stage": "BAUA failed 1/1",
 "heartbeat_at": "2026-10-22 10:33:46.632386+02:00",
 "started_at": "2026-10-22 10:31:36.084018+02:00",
 "completed_at": "2026-10-22 10:33:46.632386+02:00",
 "error_code": "BAUAVERIFICATIONERROR",
 "error_message": "BauaVerificationError: BAuA browser verification did not finish within 90 seconds. Complete any check in the Legal Radar browser window, then retry.",
 "summary": "{\"counts\": {}}",
 "app_version": "3.4.1"
}
```

## `public.alembic_version`

base table, 1 rows

| Column | Type | Null |
|---|---|---|
| `version_num` | character varying | no |

- PK: `PRIMARY KEY (version_num)`
- index: `CREATE UNIQUE INDEX alembic_version_pkc ON public.alembic_version USING btree (version_num)`


Example row 1:
```json
{
 "version_num": "0015"
}
```

## `public.applicability_windows`

base table, 1579 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `element_id` | uuid | no |
| `condition_type` | character varying | no |
| `starts_on` | date | yes |
| `ends_on` | date | yes |
| `raw_condition` | text | no |
| `structured_condition` | jsonb | no |
| `confidence` | double precision | no |

- FK: `FOREIGN KEY (element_id) REFERENCES legal_elements(id) ON DELETE CASCADE`
- PK: `PRIMARY KEY (id)`
- index: `CREATE UNIQUE INDEX applicability_windows_pkey ON public.applicability_windows USING btree (id)`


Example row 1:
```json
{
 "id": "0df560c4-7dfe-4645-8dd6-1b9b43ce6bf2",
 "element_id": "21f60f00-79bd-4a7b-be14-8023907f1b34",
 "condition_type": "PERMIT_ISSUED",
 "starts_on": null,
 "ends_on": null,
 "raw_condition": "ein vollständiger Antrag auf Genehmigung zur Errichtung und zum Betrieb nach § 6 oder § 16 des Bundes-Immissionsschutzgesetzes gestellt ist und die spätestens bis zum 16. August 2024 in Betrieb genommen wird,",
 "structured_condition": "{\"dates\": [\"2024-08-16\"], \"state\": \"CANDIDATE_REQUIRES_REVIEW\", \"parser_version\": \"gii-norm-v3\", \"source_locator\": \"BJNR0070A0024.xml#/dokumente/norm[6]/textdaten/text/Content/P/DL/DD[3]/LA/DL/DD[1]/LA/DL/DD[3]\"}",
 "confidence": "0.6"
}
```
Example row 2:
```json
{
 "id": "0532cac1-8374-4523-bdfe-a1e39302099e",
 "element_id": "15ea4380-e282-4f96-b6dd-d83ccf624173",
 "condition_type": "PERMIT_ISSUED",
 "starts_on": null,
 "ends_on": null,
 "raw_condition": "eine nicht genehmigungsbedürftige Anlage, deren Errichtung und Betrieb vor dem 16. Januar 2024 nach sonstigen Vorschriften des öffentlichen Rechts zugelassen worden ist oder, soweit eine solche Zulassung nicht erforderli… (269 chars)",
 "structured_condition": "{\"dates\": [\"2024-01-16\"], \"state\": \"CANDIDATE_REQUIRES_REVIEW\", \"parser_version\": \"gii-norm-v3\", \"source_locator\": \"BJNR0070A0024.xml#/dokumente/norm[6]/textdaten/text/Content/P/DL/DD[3]/LA/DL/DD[3]\"}",
 "confidence": "0.6"
}
```

## `public.change_events`

base table, 1259767 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `relationship_id` | uuid | no |
| `run_id` | uuid | no |
| `event_type` | character varying | no |
| `before_state` | jsonb | no |
| `after_state` | jsonb | no |
| `detected_at` | timestamp with time zone | no |

- FK: `FOREIGN KEY (run_id) REFERENCES monitor_runs(id) ON DELETE CASCADE`
- FK: `FOREIGN KEY (relationship_id) REFERENCES instrument_relationships(id) ON DELETE CASCADE`
- PK: `PRIMARY KEY (id)`
- index: `CREATE INDEX ix_change_events_relationship ON public.change_events USING btree (relationship_id, detected_at)`
- index: `CREATE UNIQUE INDEX change_events_pkey ON public.change_events USING btree (id)`


Example row 1:
```json
{
 "id": "01ea3db4-d74a-47fd-bdec-2a5194c75402",
 "relationship_id": "c9448a89-53e0-4fc7-a519-40a6c9f00fd0",
 "run_id": "5ab81ae0-fe7e-4046-90ee-4fc013937410",
 "event_type": "RELATIONSHIP_DISCOVERED",
 "before_state": "{}",
 "after_state": "{\"status\": \"ACTIVE\", \"relation_type\": \"CONSOLIDATION_LINK\", \"provision_changes\": []}",
 "detected_at": "2026-09-08 10:51:29.096827+02:00"
}
```
Example row 2:
```json
{
 "id": "fb836ca7-4e3a-46b9-895e-032e57186da3",
 "relationship_id": "c7e3bfc9-797d-4866-866c-b43e89bfd5b0",
 "run_id": "5ab81ae0-fe7e-4046-90ee-4fc013937410",
 "event_type": "RELATIONSHIP_DISCOVERED",
 "before_state": "{}",
 "after_state": "{\"status\": \"ACTIVE\", \"relation_type\": \"REPEALS\", \"provision_changes\": [{\"operation\": \"OTHER\", \"raw_annotation\": {\"ROLE2\": \"{AP|http://publications.europa.eu/resource/authority/fd_375/AP}\", \"START_OF_VALIDITY\": \"2026-01-0… (309 chars)",
 "detected_at": "2026-09-08 10:51:29.108847+02:00"
}
```

## `public.compliance_tasks`

base table, 0 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `fingerprint` | character varying | no |
| `status` | character varying | no |
| `title` | text | no |
| `source_element_id` | uuid | yes |
| `effective_on` | date | yes |
| `taxonomy_version` | character varying | yes |
| `superseded_by_id` | uuid | yes |
| `details` | jsonb | no |
| `created_at` | timestamp with time zone | no |
| `updated_at` | timestamp with time zone | no |

- FK: `FOREIGN KEY (superseded_by_id) REFERENCES compliance_tasks(id) ON DELETE SET NULL`
- FK: `FOREIGN KEY (source_element_id) REFERENCES legal_elements(id) ON DELETE SET NULL`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (fingerprint)`
- index: `CREATE UNIQUE INDEX compliance_tasks_fingerprint_key ON public.compliance_tasks USING btree (fingerprint)`
- index: `CREATE UNIQUE INDEX compliance_tasks_pkey ON public.compliance_tasks USING btree (id)`

## `public.coverage_register`

base table, 4 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `source_family` | character varying | no |
| `scope_key` | character varying | no |
| `status` | character varying | no |
| `description` | text | no |
| `details` | jsonb | no |

- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (source_family, scope_key)`
- index: `CREATE UNIQUE INDEX uq_coverage_scope ON public.coverage_register USING btree (source_family, scope_key)`
- index: `CREATE UNIQUE INDEX coverage_register_pkey ON public.coverage_register USING btree (id)`

Most common `status`: `MONITORED` (2), `OUT_OF_SCOPE` (1), `REFERENCE_WATCH_ONLY` (1)

Example row 1:
```json
{
 "id": "4a634ed4-e8aa-46be-bf43-9189c21e029e",
 "source_family": "GII",
 "scope_key": "FEDERAL_CONSOLIDATED",
 "status": "MONITORED",
 "description": "Current GII catalogue, targeted packages and explicit reconciliation",
 "details": "{\"recorded_by\": \"german-monitor-3.3.0\"}"
}
```
Example row 2:
```json
{
 "id": "eba34249-9a86-480b-bf97-6077d38b845e",
 "source_family": "RECHT_BUND",
 "scope_key": "ELECTRONIC_BGBL_2023_FORWARD",
 "status": "MONITORED",
 "description": "BGBl Parts I and II RSS plus sitemap catch-up from 2023",
 "details": "{\"recorded_by\": \"german-monitor-3.3.0\"}"
}
```

## `public.cross_source_relationships`

base table, 19992 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `source_document_id` | uuid | no |
| `target_document_id` | uuid | no |
| `relation_type` | character varying | no |
| `status` | character varying | no |
| `match_status` | character varying | no |
| `method` | character varying | no |
| `evidence_path` | text | no |
| `evidence_excerpt` | text | no |
| `mention_count` | integer | no |
| `matcher_version` | character varying | no |
| `first_import_batch` | character varying | no |
| `last_import_batch` | character varying | no |
| `first_seen_at` | timestamp with time zone | no |
| `last_seen_at` | timestamp with time zone | no |
| `review_note` | text | yes |
| `reviewed_at` | timestamp with time zone | yes |

- FK: `FOREIGN KEY (target_document_id) REFERENCES source_documents(id) ON DELETE CASCADE`
- FK: `FOREIGN KEY (source_document_id) REFERENCES source_documents(id) ON DELETE CASCADE`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (source_document_id, target_document_id, relation_type)`
- index: `CREATE INDEX ix_cross_source_target_type ON public.cross_source_relationships USING btree (target_document_id, relation_type)`
- index: `CREATE INDEX ix_cross_source_status ON public.cross_source_relationships USING btree (status)`
- index: `CREATE INDEX ix_cross_source_source_type ON public.cross_source_relationships USING btree (source_document_id, relation_type)`
- index: `CREATE UNIQUE INDEX uq_cross_source_relationship ON public.cross_source_relationships USING btree (source_document_id, target_document_id, relation_type)`
- index: `CREATE UNIQUE INDEX cross_source_relationships_pkey ON public.cross_source_relationships USING btree (id)`

Most common `relation_type`: `CITES_EU_ACT` (12081), `MENTIONS_LAW` (6461), `PROPOSES_CHANGE_TO` (419), `TRANSPOSES` (377), `DEBATES_EU_PROPOSAL` (300), `PUBLISHED_AS` (283), `CONCRETISES` (71)
Most common `status`: `ACTIVE` (19699), `REVIEW` (293)

Example row 1:
```json
{
 "id": "4d04bb17-9b03-4ad0-9b83-9d04dcd87fba",
 "source_document_id": "d05211f1-e443-4f1a-b568-b4ea8e8f26bd",
 "target_document_id": "e9d80df8-bb6a-46b6-a8f5-e1ca9bdedb56",
 "relation_type": "MENTIONS_LAW",
 "status": "ACTIVE",
 "match_status": "CONFIRMED",
 "method": "GII_TITLE_IN_TEXT",
 "evidence_path": "regulatory_item_versions.extracted_text",
 "evidence_excerpt": "…Blick auf das behinderungsgerechte Einrichten und Betreiben von Arbeitsstätten zudem nach § 81 Abs. 4 Nr. 4 Neuntes Buch Sozialgesetzbuch Rehabilitation und Teilhabe behinderter Menschen (SGB IX). Erforderliche Anpassun… (248 chars)",
 "mention_count": "1",
 "matcher_version": "2.4",
 "first_import_batch": "cross-source-preview-v2-20261011T131922+0200-all-links:4c3f0dbb0a9d",
 "last_import_batch": "cross-source-preview-v2-20261011T131922+0200-all-links:4c3f0dbb0a9d",
 "first_seen_at": "2026-10-11 13:22:32.985404+02:00",
 "last_seen_at": "2026-10-11 13:22:32.985404+02:00",
 "review_note": null,
 "reviewed_at": null
}
```
Example row 2:
```json
{
 "id": "7d093ac5-5393-4c39-b890-90c97ef9e4b2",
 "source_document_id": "d05211f1-e443-4f1a-b568-b4ea8e8f26bd",
 "target_document_id": "7aaec17a-a16e-44a9-9d63-a12e5f0db8ac",
 "relation_type": "MENTIONS_LAW",
 "status": "ACTIVE",
 "match_status": "CONFIRMED",
 "method": "GII_TITLE_IN_TEXT",
 "evidence_path": "regulatory_item_versions.extracted_text",
 "evidence_excerpt": "…auf das behinderungsgerechte Einrichten und Betreiben von Arbe itsstätten zudem nach § 164 Absatz 4 Nummer 4 Sozialgesetzbuch Neuntes Buch Rehabilitation und Teilhabe von Menschen mit Behinderungen (SGB IX, zuletzt geän… (248 chars)",
 "mention_count": "1",
 "matcher_version": "2.4",
 "first_import_batch": "cross-source-preview-v2-20261011T131922+0200-all-links:4c3f0dbb0a9d",
 "last_import_batch": "cross-source-preview-v2-20261011T131922+0200-all-links:4c3f0dbb0a9d",
 "first_seen_at": "2026-10-11 13:22:32.985404+02:00",
 "last_seen_at": "2026-10-11 13:22:32.985404+02:00",
 "review_note": null,
 "reviewed_at": null
}
```

## `public.current_instrument_versions_v`

view, 31344 rows

| Column | Type | Null |
|---|---|---|
| `celex` | character varying | yes |
| `version_id` | uuid | yes |
| `version_key` | character varying | yes |
| `language` | character varying | yes |
| `version_kind` | character varying | yes |
| `version_date` | date | yes |
| `source_format` | character varying | yes |
| `parser_version` | character varying | yes |
| `content_sha256` | character varying | yes |


Most common `language`: `DE` (17129), `EN` (14215)

Example row 1:
```json
{
 "celex": "31990D0266",
 "version_id": "4476447c-d079-42a6-8825-55295c47742f",
 "version_key": "ORI:EN:UNDATED:5513d4be:424bfc085ab449b7f8d66d6046343db3d49ec2f610809dd32c88c37c0bcbe6f0",
 "language": "EN",
 "version_kind": "ORIGINAL",
 "version_date": null,
 "source_format": "LEGACY_HTML",
 "parser_version": "structure-v5",
 "content_sha256": "424bfc085ab449b7f8d66d6046343db3d49ec2f610809dd32c88c37c0bcbe6f0"
}
```
Example row 2:
```json
{
 "celex": "32017D0050",
 "version_id": "2d276e47-7a70-4e9b-901c-ec328ca04197",
 "version_key": "ORI:EN:UNDATED:5513d4be:5f2eb0dfc83ce4225154ea8dbbba1a46711f8859e9f365c7bb20366a8e6fbbe9",
 "language": "EN",
 "version_kind": "ORIGINAL",
 "version_date": null,
 "source_format": "ELI_XHTML",
 "parser_version": "structure-v5",
 "content_sha256": "5f2eb0dfc83ce4225154ea8dbbba1a46711f8859e9f365c7bb20366a8e6fbbe9"
}
```

## `public.direct_base_child_relationships_v`

view, 1041525 rows

| Column | Type | Null |
|---|---|---|
| `base_celex` | character varying | yes |
| `base_title` | text | yes |
| `relation_type` | character varying | yes |
| `relation_family` | character varying | yes |
| `child_celex` | character varying | yes |
| `child_title` | text | yes |
| `status` | character varying | yes |
| `mapping_status` | character varying | yes |
| `first_seen_at` | timestamp with time zone | yes |
| `last_seen_at` | timestamp with time zone | yes |


Most common `relation_type`: `NATIONAL_IMPLEMENTATION` (899665), `CITES` (51509), `LEGAL_BASIS` (26402), `CONSOLIDATION_LINK` (18540), `AMENDS` (12577), `OTHER` (8394), `CORRECTS` (6168), `RELATED_CASE_LAW` (5929), `ADOPTS` (3494), `REPEALS` (3049), `IMPLICITLY_REPEALS` (2117), `RELATED_PROPOSAL` (1590), `DEROGATES` (627), `COMPLETES` (434), `EXTENDS_VALIDITY` (362)
Most common `status`: `ACTIVE` (1041525)

Example row 1:
```json
{
 "base_celex": "72024L1346CZE_126768",
 "base_title": null,
 "relation_type": "NATIONAL_IMPLEMENTATION",
 "relation_family": "IMPLEMENTATION",
 "child_celex": "32014L0036",
 "child_title": "Directive 2014/36/EU of the European Parliament and of the Council of 26 February 2014 on the conditions of entry and stay of third-country nationals for the purpose of employment as seasonal workers",
 "status": "ACTIVE",
 "mapping_status": "MAPPED",
 "first_seen_at": "2026-09-08 22:18:47.431601+02:00",
 "last_seen_at": "2026-09-08 22:18:47.431601+02:00"
}
```
Example row 2:
```json
{
 "base_celex": "32024R3110",
 "base_title": "Regulation (EU) 2024/3110 of the European Parliament and of the Council of 27 November 2024 laying down harmonised rules for the marketing of construction products and repealing Regulation (EU) No 305/2011 (Text with EEA… (231 chars)",
 "relation_type": "CONSOLIDATION_LINK",
 "relation_family": "VERSION",
 "child_celex": "02011R0305-20260108",
 "child_title": null,
 "status": "ACTIVE",
 "mapping_status": "MAPPED",
 "first_seen_at": "2026-09-08 10:51:29.091707+02:00",
 "last_seen_at": "2026-09-09 03:00:02.881667+02:00"
}
```

## `public.document_identifiers`

base table, 123119 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `source_document_id` | uuid | no |
| `scheme` | character varying | no |
| `value` | text | no |

- FK: `FOREIGN KEY (source_document_id) REFERENCES source_documents(id) ON DELETE CASCADE`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (source_document_id, scheme, value)`
- index: `CREATE UNIQUE INDEX document_identifiers_pkey ON public.document_identifiers USING btree (id)`
- index: `CREATE UNIQUE INDEX uq_document_identifier ON public.document_identifiers USING btree (source_document_id, scheme, value)`
- index: `CREATE INDEX ix_document_identifiers_lookup ON public.document_identifiers USING btree (scheme, value)`


Example row 1:
```json
{
 "id": "7bccbec0-5c13-45de-8b53-c767745aed31",
 "source_document_id": "4e9997e8-a413-43ea-8f90-6f97981f9808",
 "scheme": "CELEX",
 "value": "32024R3110"
}
```
Example row 2:
```json
{
 "id": "a88b5c16-9fb8-4f5b-b52f-9aa58540886b",
 "source_document_id": "4e9997e8-a413-43ea-8f90-6f97981f9808",
 "scheme": "DOCUMENT",
 "value": "celex:32024R3110"
}
```

## `public.effective_date_rules`

base table, 18724 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `publication_id` | uuid | yes |
| `change_id` | uuid | yes |
| `rule_type` | character varying | no |
| `state` | character varying | no |
| `effective_on` | date | yes |
| `raw_text` | text | no |
| `details` | jsonb | no |
| `checked_at` | timestamp with time zone | no |

- FK: `FOREIGN KEY (change_id) REFERENCES relationship_provision_changes(id) ON DELETE CASCADE`
- FK: `FOREIGN KEY (publication_id) REFERENCES official_publications(id) ON DELETE CASCADE`
- PK: `PRIMARY KEY (id)`
- index: `CREATE UNIQUE INDEX effective_date_rules_pkey ON public.effective_date_rules USING btree (id)`


Example row 1:
```json
{
 "id": "917f8dd1-7c36-4c81-8133-281f0fdd9bd3",
 "publication_id": "f09fe798-fa6c-4205-b4f4-e22ee26a73ac",
 "change_id": "10a4f037-3798-4754-9503-74f1d1054c73",
 "rule_type": "UNPARSED",
 "state": "UNKNOWN",
 "effective_on": null,
 "raw_text": "Diese Verordnung tritt am 25. Dezember 2025 in Kraft. Langen, den 10. September 2025 D e r  D i r e k t o r   d e s  B u n d e s a u f s i c h t s a m t e s  f ü r  F l u g s i c h e r u n g In Vertretung H e i n z l",
 "details": "{\"scope\": {\"item\": null, \"article\": null, \"operation\": null, \"publication\": null, \"excluded_articles\": [], \"excluded_operations\": []}, \"state\": \"UNKNOWN\", \"offset\": null, \"reason\": \"Missing or ambiguous dates; split scop… (1028 chars)",
 "checked_at": "2026-09-16 10:48:29.286294+02:00"
}
```
Example row 2:
```json
{
 "id": "d38a574e-0141-4886-9de6-4d79175fa9ae",
 "publication_id": "f09fe798-fa6c-4205-b4f4-e22ee26a73ac",
 "change_id": null,
 "rule_type": "UNPARSED",
 "state": "UNKNOWN",
 "effective_on": null,
 "raw_text": "Diese Verordnung tritt am 25. Dezember 2025 in Kraft. Langen, den 10. September 2025 D e r  D i r e k t o r   d e s  B u n d e s a u f s i c h t s a m t e s  f ü r  F l u g s i c h e r u n g In Vertretung H e i n z l",
 "details": "{\"span\": {\"end\": 1779, \"pages\": [2], \"start\": 1339, \"offset_unit\": \"unicode_codepoint_in_extracted_text\", \"parser_version\": \"bgbl-pdf-v2\"}, \"scope\": {\"item\": null, \"article\": null, \"operation\": null, \"publication\": null,… (1009 chars)",
 "checked_at": "2026-09-16 10:48:29.302390+02:00"
}
```

## `public.element_identity_maps`

base table, 0 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `old_element_id` | uuid | no |
| `new_element_id` | uuid | no |
| `operation` | character varying | no |
| `confidence` | double precision | no |
| `evidence` | jsonb | no |

- FK: `FOREIGN KEY (new_element_id) REFERENCES legal_elements(id) ON DELETE CASCADE`
- FK: `FOREIGN KEY (old_element_id) REFERENCES legal_elements(id) ON DELETE CASCADE`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (old_element_id, new_element_id)`
- index: `CREATE UNIQUE INDEX uq_element_identity_map ON public.element_identity_maps USING btree (old_element_id, new_element_id)`
- index: `CREATE UNIQUE INDEX element_identity_maps_pkey ON public.element_identity_maps USING btree (id)`

## `public.element_references`

base table, 1603455 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `source_element_id` | uuid | no |
| `target_element_id` | uuid | yes |
| `target_identifier` | character varying | no |
| `relation_type` | character varying | no |
| `resolution_status` | character varying | no |
| `evidence` | jsonb | no |
| `target_document_id` | uuid | yes |
| `target_celex` | character varying | yes |
| `parser_method` | character varying | no |
| `confidence` | double precision | no |

- FK: `FOREIGN KEY (target_document_id) REFERENCES source_documents(id) ON DELETE SET NULL`
- FK: `FOREIGN KEY (target_element_id) REFERENCES legal_elements(id) ON DELETE SET NULL`
- FK: `FOREIGN KEY (source_element_id) REFERENCES legal_elements(id) ON DELETE CASCADE`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (source_element_id, target_identifier, relation_type)`
- index: `CREATE UNIQUE INDEX uq_element_reference ON public.element_references USING btree (source_element_id, target_identifier, relation_type)`
- index: `CREATE UNIQUE INDEX element_references_pkey ON public.element_references USING btree (id)`

Most common `relation_type`: `INTERNAL_CITATION` (500744), `ARTICLE_TO_ARTICLE` (433902), `REFERS_TO_EXTERNAL_ACT` (349364), `ARTICLE_TO_ANNEX` (97474), `RECITAL_TO_ARTICLE` (74800), `ANNEX_TO_ARTICLE` (70906), `ANNEX_TO_ANNEX` (35047), `RECITAL_TO_ANNEX` (28193), `EXTERNAL_STANDARD_REFERENCE` (6477), `ANNEX_TO_PROVISION` (2421), `ELEMENT_TO_ARTICLE` (2240), `EU_EXPLICIT_REFERENCE` (1420), `ELEMENT_TO_ANNEX` (467)

Example row 1:
```json
{
 "id": "2b193f78-1705-4bc2-90d3-46419a51e4a7",
 "source_element_id": "0ca4d166-2dc9-404b-910f-fb8368272865",
 "target_element_id": null,
 "target_identifier": "Annex I",
 "relation_type": "ARTICLE_TO_ANNEX",
 "resolution_status": "UNRESOLVED",
 "evidence": "{\"matched_text\": \"Annex I\"}",
 "target_document_id": null,
 "target_celex": null,
 "parser_method": "LEGAL_REFERENCE_REGEX",
 "confidence": "0.95"
}
```
Example row 2:
```json
{
 "id": "7e7b9496-4cd5-487b-b857-b30922b7216a",
 "source_element_id": "4d01340b-c534-4bc4-bd2c-6ddbdfb61f5b",
 "target_element_id": null,
 "target_identifier": "Article 60",
 "relation_type": "ARTICLE_TO_ARTICLE",
 "resolution_status": "UNRESOLVED",
 "evidence": "{\"matched_text\": \"Article 60\"}",
 "target_document_id": null,
 "target_celex": null,
 "parser_method": "LEGAL_REFERENCE_REGEX",
 "confidence": "0.95"
}
```

## `public.external_reference_watch`

base table, 6477 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `source_element_id` | uuid | no |
| `reference_key` | character varying | no |
| `family` | character varying | no |
| `raw_text` | text | no |
| `status` | character varying | no |
| `last_seen_at` | timestamp with time zone | no |

- FK: `FOREIGN KEY (source_element_id) REFERENCES legal_elements(id) ON DELETE CASCADE`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (source_element_id, reference_key)`
- index: `CREATE UNIQUE INDEX external_reference_watch_pkey ON public.external_reference_watch USING btree (id)`
- index: `CREATE UNIQUE INDEX uq_external_reference ON public.external_reference_watch USING btree (source_element_id, reference_key)`

Most common `status`: `UNMONITORED` (6477)

Example row 1:
```json
{
 "id": "82bdf9b6-49e1-4bae-b234-775a4ecb41a8",
 "source_element_id": "725d4c7a-bc06-4161-af62-3f6773830f09",
 "reference_key": "din 51402",
 "family": "DIN_EN_ISO",
 "raw_text": "DIN 51402",
 "status": "UNMONITORED",
 "last_seen_at": "2026-09-12 12:05:50.075556+02:00"
}
```
Example row 2:
```json
{
 "id": "95581211-8b82-4c94-82ff-ec2e42beab45",
 "source_element_id": "888c1d0f-e481-4423-befa-79aa2830a4ea",
 "reference_key": "din 51402",
 "family": "DIN_EN_ISO",
 "raw_text": "DIN 51402",
 "status": "UNMONITORED",
 "last_seen_at": "2026-09-12 12:05:50.075556+02:00"
}
```

## `public.filter_decision_logs`

base table, 171298 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `run_document_id` | uuid | no |
| `ordinal` | integer | no |
| `rule_set` | character varying | no |
| `rule_id` | character varying | no |
| `field` | character varying | no |
| `language` | character varying | no |
| `matched_text` | text | no |
| `evidence_kind` | character varying | no |
| `created_at` | timestamp with time zone | no |

- FK: `FOREIGN KEY (run_document_id) REFERENCES run_documents(id) ON DELETE CASCADE`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (run_document_id, ordinal)`
- index: `CREATE UNIQUE INDEX uq_filter_decision_log ON public.filter_decision_logs USING btree (run_document_id, ordinal)`
- index: `CREATE UNIQUE INDEX filter_decision_logs_pkey ON public.filter_decision_logs USING btree (id)`

Most common `language`: `DE` (93033), `EN` (78134), `N/A` (131)

Example row 1:
```json
{
 "id": "3f42d890-39df-491f-b2ad-2cec274c5e98",
 "run_document_id": "d3b1fab1-09a8-4f04-a324-b479845e253b",
 "ordinal": "1",
 "rule_set": "Legal Radar conservative negative-only primary filter",
 "rule_id": "global_positive_veto",
 "field": "title",
 "language": "EN",
 "matched_text": "construction products",
 "evidence_kind": "VETO",
 "created_at": "2026-09-08 10:51:29.478686+02:00"
}
```
Example row 2:
```json
{
 "id": "a675f5bc-aa37-44ca-9a51-b1de6b2d7e1e",
 "run_document_id": "d3b1fab1-09a8-4f04-a324-b479845e253b",
 "ordinal": "2",
 "rule_set": "blocklist-de-v10",
 "rule_id": "protect",
 "field": "title",
 "language": "DE",
 "matched_text": "Bauprodukten",
 "evidence_kind": "VETO",
 "created_at": "2026-09-08 10:51:29.478686+02:00"
}
```

## `public.german_audit_events`

base table, 121860 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `run_id` | uuid | yes |
| `source` | character varying | no |
| `action` | character varying | no |
| `subject_type` | character varying | no |
| `subject_id` | character varying | no |
| `actor` | character varying | yes |
| `reason` | text | yes |
| `evidence` | jsonb | no |
| `created_at` | timestamp with time zone | no |

- FK: `FOREIGN KEY (run_id) REFERENCES monitor_runs(id) ON DELETE RESTRICT`
- PK: `PRIMARY KEY (id)`
- index: `CREATE UNIQUE INDEX german_audit_events_pkey ON public.german_audit_events USING btree (id)`
- index: `CREATE INDEX ix_german_audit_subject ON public.german_audit_events USING btree (subject_type, subject_id)`

Most common `source`: `GERMAN_FEDERAL` (121859), `RECHT_BUND` (1)

Example row 1:
```json
{
 "id": "34325e1e-0b01-4c8b-890a-6089168b9ba3",
 "run_id": "65aefec2-a5cf-4580-88c3-ae7a9cfca847",
 "source": "GERMAN_FEDERAL",
 "action": "STAGE_FAILED",
 "subject_type": "WORK_ITEM",
 "subject_id": "c7634b22-766f-437c-b27f-b22fcf9d19e4",
 "actor": null,
 "reason": "BGBL_CONTENT",
 "evidence": "{\"stage\": \"BGBL_CONTENT\", \"message\": \"Unexpected German source URL: http://www.recht.bund.de/bgbl/2/2025/288/VO\", \"error_type\": \"ValueError\", \"external_id\": \"https://www.recht.bund.de/eli/bund/bgbl-2/2025/288\"}",
 "created_at": "2026-09-16 09:18:56.777452+02:00"
}
```
Example row 2:
```json
{
 "id": "4d8b7f43-3d79-4fa9-b647-87256e4fa5c2",
 "run_id": "65aefec2-a5cf-4580-88c3-ae7a9cfca847",
 "source": "GERMAN_FEDERAL",
 "action": "STAGE_FAILED",
 "subject_type": "WORK_ITEM",
 "subject_id": "e5f4f79a-8f6a-4149-949b-ecad94dcb6cd",
 "actor": null,
 "reason": "BGBL_CONTENT",
 "evidence": "{\"stage\": \"BGBL_CONTENT\", \"message\": \"Unexpected German source URL: http://www.recht.bund.de/bgbl/2/2025/287/VO\", \"error_type\": \"ValueError\", \"external_id\": \"https://www.recht.bund.de/eli/bund/bgbl-2/2025/287\"}",
 "created_at": "2026-09-16 09:18:57.209838+02:00"
}
```

## `public.german_operation_contexts`

base table, 0 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `change_id` | uuid | no |
| `item_id` | uuid | no |
| `evidence` | jsonb | no |

- FK: `FOREIGN KEY (change_id) REFERENCES relationship_provision_changes(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (item_id) REFERENCES german_publication_items(id) ON DELETE RESTRICT`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (change_id)`
- index: `CREATE UNIQUE INDEX german_operation_contexts_change_id_key ON public.german_operation_contexts USING btree (change_id)`
- index: `CREATE UNIQUE INDEX german_operation_contexts_pkey ON public.german_operation_contexts USING btree (id)`

## `public.german_operations`

base table, 32053 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `fingerprint` | character varying | no |
| `item_id` | uuid | no |
| `target_document_id` | uuid | yes |
| `canonical_change_id` | uuid | yes |
| `effective_rule_id` | uuid | yes |
| `operation` | character varying | no |
| `evidence_state` | character varying | no |
| `target_name` | text | yes |
| `target_locator` | text | yes |
| `target_locator_type` | character varying | yes |
| `target_locator_number` | character varying | yes |
| `raw_text` | text | no |
| `old_wording` | text | yes |
| `new_wording` | text | yes |
| `source_span` | jsonb | no |
| `state` | character varying | no |
| `effective_on` | date | yes |
| `due_at` | timestamp with time zone | yes |
| `checks` | integer | no |
| `historical_context` | boolean | no |
| `evidence` | jsonb | no |
| `created_at` | timestamp with time zone | no |
| `updated_at` | timestamp with time zone | no |
| `confirmation_checks_used` | integer | no |
| `last_confirmation_check_on` | date | yes |
| `confirmation_exhausted_at` | timestamp with time zone | yes |

- FK: `FOREIGN KEY (item_id) REFERENCES german_publication_items(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (effective_rule_id) REFERENCES effective_date_rules(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (target_document_id) REFERENCES source_documents(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (canonical_change_id) REFERENCES relationship_provision_changes(id) ON DELETE RESTRICT`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (fingerprint)`
- index: `CREATE INDEX ix_german_operation_target ON public.german_operations USING btree (target_document_id, state)`
- index: `CREATE INDEX ix_german_operation_due ON public.german_operations USING btree (state, due_at)`
- index: `CREATE UNIQUE INDEX german_operations_fingerprint_key ON public.german_operations USING btree (fingerprint)`
- index: `CREATE UNIQUE INDEX german_operations_pkey ON public.german_operations USING btree (id)`
- index: `CREATE INDEX ix_german_operation_exhausted ON public.german_operations USING btree (confirmation_exhausted_at)`

Most common `operation`: `UNPARSED` (12706), `REPLACE` (10339), `ADD` (3872), `EDIT` (3114), `REPEAL` (1209), `REPLACE_ANNEX` (529), `RENUMBERS` (284)

Example row 1:
```json
{
 "id": "8a61c269-8994-432d-9db6-a2d1168f21c5",
 "fingerprint": "5da56f4cd3e6769c9dc9357aa2fd24c9cf902f2b8b577e33ed151c964a0b1934",
 "item_id": "702be92a-7c8b-4761-879b-f0b37d76d5a8",
 "target_document_id": null,
 "canonical_change_id": null,
 "effective_rule_id": null,
 "operation": "UNPARSED",
 "evidence_state": "UNKNOWN",
 "target_name": "Bekanntmachung über den Geltungsbereich des Übereinkommens über das Recht der nichtschifffahrtlichen Nutzung internationaler Wasserläufe",
 "target_locator": "Artikel 36 Absatz 2",
 "target_locator_type": "ARTICLE",
 "target_locator_number": "36",
 "raw_text": "Bekanntmachung  über den Geltungsbereich des Übereinkommens über das Recht der  nichtschifffahrtlichen Nutzung internationaler Wasserläufe  Vom 28. August 2025  Das Übereinkommen vom 21. Mai 1997 über das Recht der nicht… (784 chars)",
 "old_wording": null,
 "new_wording": null,
 "source_span": "{\"end\": 784, \"pages\": [1], \"start\": 0, \"offset_unit\": \"unicode_codepoint_in_extracted_text\", \"snapshot_id\": \"a4663c20-56a1-43be-9436-bc49d1b34189\", \"parser_version\": \"bgbl-pdf-v2\", \"artifact_sha256\": \"7e319b01c2c6abde9aa… (267 chars)",
 "state": "UNRESOLVED_TARGET",
 "effective_on": null,
 "due_at": "2026-09-16 10:48:27.098885+02:00",
 "checks": "0",
 "historical_context": "True",
 "evidence": "{\"article\": null, \"resolution\": {\"method\": \"UNRESOLVED\", \"target\": \"Bekanntmachung über den Geltungsbereich des Übereinkommens über das Recht der nichtschifffahrtlichen Nutzung internationaler Wasserläufe\", \"exact_candid… (714 chars)",
 "created_at": "2026-09-16 10:48:27.098885+02:00",
 "updated_at": "2026-09-16 10:48:27.098885+02:00",
 "confirmation_checks_used": "0",
 "last_confirmation_check_on": null,
 "confirmation_exhausted_at": null
}
```
Example row 2:
```json
{
 "id": "130d9c8c-2595-4ff3-9639-38984ec5eef3",
 "fingerprint": "a84a47eff5881318aed678e1ba864f9f09792b27e9c1b7697fe0c5f0e0512004",
 "item_id": "2531da8a-6e6a-4a96-8471-7a7670215e60",
 "target_document_id": "6977af3a-bccf-4c21-ae80-54f0608617ce",
 "canonical_change_id": "9f561920-3c60-4dbf-b827-8e48b7995f1e",
 "effective_rule_id": "10e5ac4f-49ad-4342-aa44-dd3ce3d0a14f",
 "operation": "REPLACE",
 "evidence_state": "CANDIDATE",
 "target_name": "Einführungsgesetzes zum Bürgerlichen Gesetzbuche",
 "target_locator": "§§ 358",
 "target_locator_type": "PROVISION",
 "target_locator_number": "§",
 "raw_text": "2. der Vertrag folgende Angaben enthalten: a) den Gegenstand und den Barzahlungspreis sowie b) Informationen über die sich aus den §§ 358 und 359 oder § 360 des Bürgerlichen Gesetzbuchs  ergebenden Rechte und über die Be… (5648 chars)",
 "old_wording": "§ 3 Abs. 1 Nr. 14, § 4 Abs. 1 Nr. 3 und § 7 Nummer 3",
 "new_wording": "§ 3 Absatz 1 Satz 1 Nummer 11 und 21 und § 6 Absatz 1 Satz 1 Nummer 19",
 "source_span": "{\"end\": 75397, \"pages\": [17, 18], \"start\": 69749, \"offset_unit\": \"unicode_codepoint_in_extracted_text\", \"snapshot_id\": \"62c1bce7-6949-4140-b904-76f71004ea33\", \"parser_version\": \"bgbl-pdf-v2\", \"artifact_sha256\": \"498ddd09… (278 chars)",
 "state": "EXPECTED_PENDING",
 "effective_on": null,
 "due_at": "2026-10-13 12:07:40.291070+02:00",
 "checks": "3",
 "historical_context": "True",
 "evidence": "{\"article\": \"2\", \"resolution\": {\"method\": \"EXACT_ALIAS\", \"matched\": \"Einführungsgesetzes zum Bürgerlichen Gesetzbuche\"}, \"postcondition\": {\"new\": \"§ 3 Absatz 1 Satz 1 Nummer 11 und 21 und § 6 Absatz 1 Satz 1 Nummer 19\", … (1287 chars)",
 "created_at": "2026-09-16 11:30:51.942412+02:00",
 "updated_at": "2026-10-12 12:07:40.291070+02:00",
 "confirmation_checks_used": "3",
 "last_confirmation_check_on": null,
 "confirmation_exhausted_at": null
}
```

## `public.german_parse_revisions`

base table, 9499 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `observation_id` | uuid | no |
| `version_id` | uuid | yes |
| `parser_version` | character varying | no |
| `canonicalization_version` | character varying | no |
| `canonical_sha256` | character varying | no |
| `output_sha256` | character varying | no |
| `parsed_content` | jsonb | no |
| `created_at` | timestamp with time zone | no |

- FK: `FOREIGN KEY (version_id) REFERENCES instrument_versions(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (observation_id) REFERENCES german_source_observations(id) ON DELETE RESTRICT`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (observation_id, parser_version, canonicalization_version)`
- index: `CREATE UNIQUE INDEX uq_german_parse_revision ON public.german_parse_revisions USING btree (observation_id, parser_version, canonicalization_version)`
- index: `CREATE UNIQUE INDEX german_parse_revisions_pkey ON public.german_parse_revisions USING btree (id)`


Example row 1:
```json
{
 "id": "39f8ac92-cb55-42d6-b218-7c1188048cf1",
 "observation_id": "6f1f801b-27c0-4ce9-b30e-ffd05ead40dc",
 "version_id": "f7b13e78-9a0d-4f14-ab40-c6317c79a90d",
 "parser_version": "gii-norm-v3",
 "canonicalization_version": "gii-c14n-v2",
 "canonical_sha256": "53187b82b0cb92c622240cdbc37d3bc928dc4d32284af4fd70aaa91a282821a0",
 "output_sha256": "29b1ec3005c938890eac3e17db97c80b3f846c6473e24e0f60543817b499c3d7",
 "parsed_content": "{\"title\": \"Erste Verordnung zur Durchführung des Bundesentschädigungsgesetzes\", \"elements\": [{\"key\": \"bjnr002710954.xml/introductory_text:einleitung-1\", \"path\": \"bjnr002710954.xml/introductory_text:einleitung-1\", \"depth\"… (3596657 chars)",
 "created_at": "2026-09-16 13:16:24.836671+02:00"
}
```
Example row 2:
```json
{
 "id": "4391a9e1-4ae9-4f84-98bf-4bbf357e096f",
 "observation_id": "45b5ba92-8947-48fd-b33e-1962d21a3f52",
 "version_id": "18a70646-32fc-40bd-bff3-2dbcae7e72bb",
 "parser_version": "gii-norm-v3",
 "canonicalization_version": "gii-c14n-v2",
 "canonical_sha256": "0fff8b9eb96bc66953d6888773c6b358fc490a3d7805833fcc8767963c1782a1",
 "output_sha256": "5361b9a5067c8c0c5bee6c89d1a7785d9144f97cb0a2518d30975fd2fe6b9a55",
 "parsed_content": "{\"title\": \"Zehnte Gebäudereinigungsarbeitsbedingungenverordnung\", \"elements\": [{\"key\": \"bjnr0160a0025.xml/introductory_text:einleitung-1\", \"path\": \"bjnr0160a0025.xml/introductory_text:einleitung-1\", \"depth\": 0, \"number\":… (23621 chars)",
 "created_at": "2026-09-16 13:16:25.936640+02:00"
}
```

## `public.german_publication_items`

base table, 9691 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `publication_id` | uuid | no |
| `item_key` | character varying | no |
| `title` | text | yes |
| `roles` | jsonb | no |
| `evidence` | jsonb | no |
| `source_document_id` | uuid | yes |
| `snapshot_id` | uuid | yes |
| `ordinal` | integer | yes |
| `item_kind` | character varying | yes |
| `body_text` | text | yes |
| `target_document_id` | uuid | yes |
| `state` | character varying | no |
| `parser_version` | character varying | yes |

- FK: `FOREIGN KEY (target_document_id) REFERENCES source_documents(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (snapshot_id) REFERENCES source_snapshots(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (source_document_id) REFERENCES source_documents(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (publication_id) REFERENCES official_publications(id) ON DELETE RESTRICT`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (publication_id, item_key)`
- index: `CREATE INDEX ix_german_publication_item_target ON public.german_publication_items USING btree (target_document_id, state)`
- index: `CREATE UNIQUE INDEX uq_german_publication_item ON public.german_publication_items USING btree (publication_id, item_key)`
- index: `CREATE UNIQUE INDEX german_publication_items_pkey ON public.german_publication_items USING btree (id)`


Example row 1:
```json
{
 "id": "702be92a-7c8b-4761-879b-f0b37d76d5a8",
 "publication_id": "ffc6468a-1e34-4b4c-8f26-187c67747d96",
 "item_key": "7e319b01c2c6abde9aa251e60ec3c8d68ef802337edc9db82a88e1b91da3dd9a:1",
 "title": "Bekanntmachung über den Geltungsbereich des Übereinkommens über das Recht der nichtschifffahrtlichen Nutzung internationaler Wasserläufe",
 "roles": "[\"AMENDMENT\"]",
 "evidence": "{\"span\": {\"end\": 784, \"pages\": [1], \"start\": 0, \"offset_unit\": \"unicode_codepoint_in_extracted_text\", \"parser_version\": \"bgbl-pdf-v2\"}, \"article\": null, \"version_id\": \"333ab678-a592-4ff0-8a85-d4cee0fa03a6\", \"target_name\"… (389 chars)",
 "source_document_id": "1729cc7c-ce34-442b-8ab2-7574fcd90239",
 "snapshot_id": "a4663c20-56a1-43be-9436-bc49d1b34189",
 "ordinal": "1",
 "item_kind": "AMENDMENT",
 "body_text": "Bekanntmachung  über den Geltungsbereich des Übereinkommens über das Recht der  nichtschifffahrtlichen Nutzung internationaler Wasserläufe  Vom 28. August 2025  Das Übereinkommen vom 21. Mai 1997 über das Recht der nicht… (784 chars)",
 "target_document_id": null,
 "state": "UNRESOLVED",
 "parser_version": "bgbl-pdf-v2"
}
```
Example row 2:
```json
{
 "id": "f040ec4a-ea14-4719-b080-6e8bb81790ad",
 "publication_id": "f09fe798-fa6c-4205-b4f4-e22ee26a73ac",
 "item_key": "7b5e5f91201852a51d24cbbffb49453bb7defebd98a01d098054c8333ccd62c6:1",
 "title": "Änderung der Zweihundertfünfunddreißigsten Durchführungsverordnung",
 "roles": "[\"AMENDMENT\"]",
 "evidence": "{\"span\": {\"end\": 1339, \"pages\": [1], \"start\": 593, \"offset_unit\": \"unicode_codepoint_in_extracted_text\", \"parser_version\": \"bgbl-pdf-v2\"}, \"article\": \"1\", \"version_id\": \"2319bc6c-d186-4622-bc21-d352fd265a47\", \"target_nam… (308 chars)",
 "source_document_id": "668585d0-b4a0-4888-8a6a-bcdd6607ee6f",
 "snapshot_id": "0c035b1b-d5a6-48f7-abf3-bbbd264df704",
 "ordinal": "1",
 "item_kind": "AMENDMENT",
 "body_text": " zur Luftverkehrs-Ordnung  (Festlegung von Flugverfahren für An- und Abflüge nach  Instrumentenflugregeln zum und vom Flughafen Nürnberg) Die Zweihundertfünfunddreißigste Durchführungsverordnung zur Luftverkehrs-Ordnung … (669 chars)",
 "target_document_id": "e38d5ab5-35a3-4a71-8eea-feca7c23a76a",
 "state": "LINKED",
 "parser_version": "bgbl-pdf-v2"
}
```

## `public.german_reconciliations`

base table, 10909 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `fingerprint` | character varying | no |
| `operation_change_id` | uuid | yes |
| `gii_change_id` | uuid | yes |
| `state` | character varying | no |
| `evidence` | jsonb | no |
| `follow_up_at` | timestamp with time zone | yes |
| `created_at` | timestamp with time zone | no |
| `operation_id` | uuid | yes |

- FK: `FOREIGN KEY (operation_change_id) REFERENCES relationship_provision_changes(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (gii_change_id) REFERENCES gii_version_changes(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (operation_id) REFERENCES german_operations(id) ON DELETE RESTRICT`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (fingerprint)`
- index: `CREATE INDEX ix_german_reconciliation_operation ON public.german_reconciliations USING btree (operation_id, state)`
- index: `CREATE UNIQUE INDEX german_reconciliations_fingerprint_key ON public.german_reconciliations USING btree (fingerprint)`
- index: `CREATE UNIQUE INDEX german_reconciliations_pkey ON public.german_reconciliations USING btree (id)`

## `public.german_review_actions`

base table, 0 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `review_item_id` | uuid | no |
| `action` | character varying | no |
| `actor` | character varying | no |
| `reason` | text | no |
| `evidence` | jsonb | no |
| `created_at` | timestamp with time zone | no |

- FK: `FOREIGN KEY (review_item_id) REFERENCES review_items(id) ON DELETE RESTRICT`
- PK: `PRIMARY KEY (id)`
- index: `CREATE UNIQUE INDEX german_review_actions_pkey ON public.german_review_actions USING btree (id)`

## `public.german_source_associations`

base table, 7 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `left_document_id` | uuid | no |
| `right_document_id` | uuid | no |
| `association_type` | character varying | no |
| `state` | character varying | no |
| `actor` | character varying | yes |
| `reason` | text | yes |
| `evidence` | jsonb | no |
| `created_at` | timestamp with time zone | no |

- FK: `FOREIGN KEY (right_document_id) REFERENCES source_documents(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (left_document_id) REFERENCES source_documents(id) ON DELETE RESTRICT`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (left_document_id, right_document_id, association_type)`
- index: `CREATE UNIQUE INDEX german_source_associations_pkey ON public.german_source_associations USING btree (id)`
- index: `CREATE UNIQUE INDEX uq_german_source_association ON public.german_source_associations USING btree (left_document_id, right_document_id, association_type)`


Example row 1:
```json
{
 "id": "974fb30f-5353-4942-9372-53ac5664a322",
 "left_document_id": "45302708-d59b-4ed9-af42-53d1442195ea",
 "right_document_id": "d55e1ef1-9839-4702-b5f3-bd2585ba0568",
 "association_type": "NEW_INSTRUMENT",
 "state": "CANDIDATE",
 "actor": null,
 "reason": "Exact title/identifier candidate; confirmation required",
 "evidence": "{\"resolution\": {\"method\": \"EXACT_ALIAS\", \"matched\": \"Verordnung zur Ermöglichung der Anlegung, Führung oder Weiterführung papiergebundener Akten in Bußgeldverfahren im Geschäftsbereich des Bundesministeriums für Verkehr\"… (285 chars)",
 "created_at": "2026-09-16 13:04:37.687479+02:00"
}
```
Example row 2:
```json
{
 "id": "c093188e-adf2-45b3-9bfd-843510f26070",
 "left_document_id": "09f61923-0f76-499c-8d7c-e5dd343cd295",
 "right_document_id": "fe7a7b0b-f146-4e39-8693-998ab08009bb",
 "association_type": "NEW_INSTRUMENT",
 "state": "CANDIDATE",
 "actor": null,
 "reason": "Exact title/identifier candidate; confirmation required",
 "evidence": "{\"resolution\": {\"method\": \"EXACT_ALIAS\", \"matched\": \"Verordnung zur Ermöglichung der Anlegung, Führung und Weiterführung papiergebundener Akten in Straf- und Bußgeldverfahren im Geschäftsbereich des Bundesministeriums de… (296 chars)",
 "created_at": "2026-09-16 13:04:37.722201+02:00"
}
```

## `public.german_source_manifests`

base table, 54 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `run_id` | uuid | no |
| `source` | character varying | no |
| `channel` | character varying | no |
| `source_url` | text | no |
| `final_url` | text | no |
| `http_status` | integer | no |
| `sha256` | character varying | no |
| `payload` | bytea | no |
| `headers` | jsonb | no |
| `validators` | jsonb | no |
| `retrieved_at` | timestamp with time zone | no |

- FK: `FOREIGN KEY (run_id) REFERENCES monitor_runs(id) ON DELETE RESTRICT`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (run_id, source_url)`
- index: `CREATE UNIQUE INDEX german_source_manifests_pkey ON public.german_source_manifests USING btree (id)`
- index: `CREATE UNIQUE INDEX uq_german_manifest_run_url ON public.german_source_manifests USING btree (run_id, source_url)`
- index: `CREATE INDEX ix_german_manifest_source_time ON public.german_source_manifests USING btree (source, retrieved_at)`

Most common `source`: `RECHT_BUND` (41), `GII` (13)

Example row 1:
```json
{
 "id": "553aabf3-bbb2-4479-96ee-cc6ad5d9f394",
 "run_id": "65aefec2-a5cf-4580-88c3-ae7a9cfca847",
 "source": "RECHT_BUND",
 "channel": "SITEMAP",
 "source_url": "https://www.recht.bund.de/XMLSitemaps/Sitemap_Verkuendungen.xml",
 "final_url": "https://www.recht.bund.de/XMLSitemaps/Sitemap_Verkuendungen.xml",
 "http_status": "200",
 "sha256": "aec45c5a07352a489f80bbb0484fce7ddecf695f221fb09ed20b7e60570648ba",
 "payload": "<493654 bytes>",
 "headers": "{\"date\": \"Wed, 16 Sep 2026 07:18:35 GMT\", \"vary\": \"Accept-Encoding\", \"server\": \"Apache\", \"connection\": \"Keep-Alive\", \"keep-alive\": \"timeout=10, max=500\", \"set-cookie\": \"AL_LB-S=$xc/CCjfmw1j7pzKJZbDxDPW4nAs6k7uUUo9XvGfUh5… (1188 chars)",
 "validators": "{\"last-modified\": \"Wed, 16 Sep 2026 07:18:37 GMT\"}",
 "retrieved_at": "2026-09-16 09:18:37.278285+02:00"
}
```
Example row 2:
```json
{
 "id": "9be6c6d9-5cf8-4be4-8e8b-87fd956f94e1",
 "run_id": "65aefec2-a5cf-4580-88c3-ae7a9cfca847",
 "source": "RECHT_BUND",
 "channel": "RSS",
 "source_url": "https://www.recht.bund.de/rss/feeds/rss_bgbl-1.xml",
 "final_url": "https://www.recht.bund.de/rss/feeds/rss_bgbl-1.xml",
 "http_status": "200",
 "sha256": "7e0debc1683ebfd35991a227749a1cff07c9e1aa5f7225a7273590fdce111336",
 "payload": "<419125 bytes>",
 "headers": "{\"date\": \"Wed, 16 Sep 2026 07:18:50 GMT\", \"vary\": \"Accept-Encoding\", \"server\": \"Apache\", \"connection\": \"Keep-Alive\", \"keep-alive\": \"timeout=10, max=500\", \"content-type\": \"text/xml;charset=utf-8\", \"cache-control\": \"max-ag… (1069 chars)",
 "validators": "{\"last-modified\": \"Wed, 16 Sep 2026 07:18:52 GMT\"}",
 "retrieved_at": "2026-09-16 09:18:53.066951+02:00"
}
```

## `public.german_source_observations`

base table, 9499 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `source` | character varying | no |
| `observation_key` | character varying | no |
| `source_document_id` | uuid | yes |
| `publication_id` | uuid | yes |
| `item_id` | uuid | yes |
| `snapshot_id` | uuid | yes |
| `version_id` | uuid | yes |
| `run_id` | uuid | yes |
| `source_url` | text | no |
| `source_sha256` | character varying | yes |
| `payload` | bytea | yes |
| `http_status` | integer | yes |
| `validators` | jsonb | no |
| `outcome` | character varying | no |
| `retrieved_at` | timestamp with time zone | no |
| `evidence` | jsonb | no |

- FK: `FOREIGN KEY (publication_id) REFERENCES official_publications(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (run_id) REFERENCES monitor_runs(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (version_id) REFERENCES instrument_versions(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (snapshot_id) REFERENCES source_snapshots(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (item_id) REFERENCES german_publication_items(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (source_document_id) REFERENCES source_documents(id) ON DELETE RESTRICT`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (source, observation_key)`
- index: `CREATE UNIQUE INDEX uq_german_observation ON public.german_source_observations USING btree (source, observation_key)`
- index: `CREATE UNIQUE INDEX german_source_observations_pkey ON public.german_source_observations USING btree (id)`
- index: `CREATE INDEX ix_german_observation_document ON public.german_source_observations USING btree (source_document_id, retrieved_at)`

Most common `source`: `GII` (9499)

Example row 1:
```json
{
 "id": "6f1f801b-27c0-4ce9-b30e-ffd05ead40dc",
 "source": "GII",
 "observation_key": "b0558e549ccfd8ee9fe4a1a55c4966fc61e284971245e877968187d37f987156",
 "source_document_id": "486d2bdf-b3f2-472f-a073-c727be1e66e1",
 "publication_id": null,
 "item_id": null,
 "snapshot_id": "377250d5-d896-4196-b4d4-d0e902a308b1",
 "version_id": "f7b13e78-9a0d-4f14-ab40-c6317c79a90d",
 "run_id": "e00db166-3238-4225-9b8a-709bd64e8153",
 "source_url": "https://www.gesetze-im-internet.de/begdv_1/xml.zip",
 "source_sha256": "fad5f8bed7e4d64bc8cc2906dcaa2c7fce0f0580ac88c86b1dca4679744fc027",
 "payload": null,
 "http_status": "200",
 "validators": "{}",
 "outcome": "PARSER_REPAIRED",
 "retrieved_at": "2026-09-16 13:16:24.704867+02:00",
 "evidence": "{\"mode\": \"RECONCILE\", \"headers\": {\"date\": \"Wed, 16 Sep 2026 11:16:21 GMT\", \"etag\": \"\\\"4ad3-6167852fdfe2b\\\"\", \"server\": \"Apache\", \"connection\": \"Keep-Alive\", \"keep-alive\": \"timeout=5, max=99\", \"content-type\": \"application… (437 chars)"
}
```
Example row 2:
```json
{
 "id": "45b5ba92-8947-48fd-b33e-1962d21a3f52",
 "source": "GII",
 "observation_key": "f0be655f1bb6522fb6584f89d8ff3f7fba0ca8e7223787d760b437f09bc20858",
 "source_document_id": "f1f1c7bf-a808-4eb5-9865-0c68cde409bd",
 "publication_id": null,
 "item_id": null,
 "snapshot_id": "04f90b6a-247b-4de4-b569-610208f0f7c7",
 "version_id": "18a70646-32fc-40bd-bff3-2dbcae7e72bb",
 "run_id": "e00db166-3238-4225-9b8a-709bd64e8153",
 "source_url": "https://www.gesetze-im-internet.de/geb_udearbbv_10/xml.zip",
 "source_sha256": "aa37409349addd26a343f43402b81afdc8953bed9a01d592eb72e385991af8a9",
 "payload": null,
 "http_status": "200",
 "validators": "{}",
 "outcome": "PARSER_REPAIRED",
 "retrieved_at": "2026-09-16 13:16:25.936640+02:00",
 "evidence": "{\"mode\": \"RECONCILE\", \"headers\": {\"date\": \"Wed, 16 Sep 2026 11:16:25 GMT\", \"etag\": \"\\\"778-62d0740b3f09f\\\"\", \"server\": \"Apache\", \"connection\": \"Keep-Alive\", \"keep-alive\": \"timeout=5, max=99\", \"content-type\": \"application/… (435 chars)"
}
```

## `public.german_version_sources`

base table, 9203 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `version_id` | uuid | no |
| `snapshot_id` | uuid | no |
| `relation_kind` | character varying | no |
| `source_sha256` | character varying | no |
| `canonical_sha256` | character varying | yes |
| `parser_version` | character varying | yes |
| `canonicalization_version` | character varying | yes |
| `evidence` | jsonb | no |
| `created_at` | timestamp with time zone | no |

- FK: `FOREIGN KEY (version_id) REFERENCES instrument_versions(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (snapshot_id) REFERENCES source_snapshots(id) ON DELETE RESTRICT`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (version_id, snapshot_id)`
- index: `CREATE UNIQUE INDEX german_version_sources_pkey ON public.german_version_sources USING btree (id)`
- index: `CREATE UNIQUE INDEX uq_german_version_source ON public.german_version_sources USING btree (version_id, snapshot_id)`


Example row 1:
```json
{
 "id": "2d330527-f56b-48a5-a2cb-22712ae5da72",
 "version_id": "333ab678-a592-4ff0-8a85-d4cee0fa03a6",
 "snapshot_id": "a4663c20-56a1-43be-9436-bc49d1b34189",
 "relation_kind": "SOURCE_PAYLOAD",
 "source_sha256": "7e319b01c2c6abde9aa251e60ec3c8d68ef802337edc9db82a88e1b91da3dd9a",
 "canonical_sha256": "d0fe5fbea953950bb4938cd089283bd45eb1ee5ba63dd12748f0c31fcae6c016",
 "parser_version": "bgbl-pdf-v2",
 "canonicalization_version": "pdf-bundle-sha256-v1",
 "evidence": "{\"pdf_ordinal\": 1, \"artifact_url\": \"https://www.recht.bund.de/bgbl/2/2025/236/regelungstext.pdf?__blob=publicationFile&v=3\"}",
 "created_at": "2026-09-16 10:48:26.988100+02:00"
}
```
Example row 2:
```json
{
 "id": "0de7596c-824f-4303-bb60-0316a247bf1f",
 "version_id": "2319bc6c-d186-4622-bc21-d352fd265a47",
 "snapshot_id": "0c035b1b-d5a6-48f7-abf3-bbbd264df704",
 "relation_kind": "SOURCE_PAYLOAD",
 "source_sha256": "7b5e5f91201852a51d24cbbffb49453bb7defebd98a01d098054c8333ccd62c6",
 "canonical_sha256": "80462f459bb8e1e447099c7cceec6a8692e27d1008dac23bd7a2dd64175cad34",
 "parser_version": "bgbl-pdf-v2",
 "canonicalization_version": "pdf-bundle-sha256-v1",
 "evidence": "{\"pdf_ordinal\": 1, \"artifact_url\": \"https://www.recht.bund.de/bgbl/1/2025/214/regelungstext.pdf?__blob=publicationFile&v=3\"}",
 "created_at": "2026-09-16 10:48:28.672606+02:00"
}
```

## `public.german_work_items`

base table, 9097 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `source` | character varying | no |
| `work_key` | character varying | no |
| `stage` | character varying | no |
| `reason` | character varying | no |
| `source_document_id` | uuid | yes |
| `item_id` | uuid | yes |
| `date_rule_id` | uuid | yes |
| `status` | character varying | no |
| `due_at` | timestamp with time zone | no |
| `attempts` | integer | no |
| `lease_owner` | character varying | yes |
| `lease_expires_at` | timestamp with time zone | yes |
| `evidence` | jsonb | no |
| `external_id` | text | yes |
| `source_url` | text | yes |
| `first_run_id` | uuid | yes |
| `last_run_id` | uuid | yes |
| `last_error` | text | yes |
| `completed_at` | timestamp with time zone | yes |
| `max_attempts` | integer | no |
| `priority` | integer | no |
| `updated_at` | timestamp with time zone | no |
| `result` | jsonb | no |

- FK: `FOREIGN KEY (source_document_id) REFERENCES source_documents(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (last_run_id) REFERENCES monitor_runs(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (item_id) REFERENCES german_publication_items(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (first_run_id) REFERENCES monitor_runs(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (date_rule_id) REFERENCES effective_date_rules(id) ON DELETE RESTRICT`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (source, work_key)`
- index: `CREATE UNIQUE INDEX german_work_items_pkey ON public.german_work_items USING btree (id)`
- index: `CREATE INDEX ix_german_work_due ON public.german_work_items USING btree (status, due_at)`
- index: `CREATE UNIQUE INDEX uq_german_work ON public.german_work_items USING btree (source, work_key)`

Most common `source`: `GII` (6145), `RECHT_BUND` (2952)
Most common `status`: `COMPLETE` (9096), `FAILED` (1)

Example row 1:
```json
{
 "id": "aaf132c5-8cee-4977-9a87-54548b1625ac",
 "source": "GII",
 "work_key": "af8be9971bde3aeea2a6022d6476de59342463f0dafbaadbef330d0dccd0220e",
 "stage": "GII_PACKAGE",
 "reason": "CORPUS_RECONCILIATION",
 "source_document_id": null,
 "item_id": null,
 "date_rule_id": null,
 "status": "COMPLETE",
 "due_at": "2026-09-16 17:16:45.716666+02:00",
 "attempts": "1",
 "lease_owner": null,
 "lease_expires_at": null,
 "evidence": "{\"title\": \"Verordnung über die Berufsausbildung im Laborbereich Chemie, Biologie und Lack *)\", \"reasons\": [\"CORPUS_RECONCILIATION\"], \"metadata\": {\"gii_slug\": \"chembiolackausbv_2009\", \"official_title\": \"Verordnung über di… (286 chars)",
 "external_id": "chembiolackausbv_2009",
 "source_url": "https://www.gesetze-im-internet.de/chembiolackausbv_2009/xml.zip",
 "first_run_id": "e00db166-3238-4225-9b8a-709bd64e8153",
 "last_run_id": "12b98a0e-f2a8-4626-96dd-19201cc98cc0",
 "last_error": null,
 "completed_at": "2026-09-17 09:28:38.829057+02:00",
 "max_attempts": "8",
 "priority": "30",
 "updated_at": "2026-09-17 09:28:38.829057+02:00",
 "result": "{\"status\": \"PARSER_REPAIRED\", \"version_id\": \"671cdc41-497c-4709-b13b-b662e25a901b\", \"document_id\": \"4e07c253-5db0-4665-b180-8ac59c9d7a93\", \"reconciliation\": {\"confirmed\": 0, \"candidates\": 0, \"not_observed\": 0}, \"changes_… (367 chars)"
}
```
Example row 2:
```json
{
 "id": "c7ea6a9a-1656-401e-8620-774436e2bb6d",
 "source": "GII",
 "work_key": "e2f6cf4d02dd07eba047e624a3c0b595ca7a66bd83350c45e413fe1a762a24b9",
 "stage": "GII_PACKAGE",
 "reason": "CORPUS_RECONCILIATION",
 "source_document_id": null,
 "item_id": null,
 "date_rule_id": null,
 "status": "COMPLETE",
 "due_at": "2026-09-16 17:27:05.253713+02:00",
 "attempts": "1",
 "lease_owner": null,
 "lease_expires_at": null,
 "evidence": "{\"title\": \"Verordnung über die Emissionsberichterstattung nach dem Brennstoffemissionshandelsgesetz für die Jahre 2023 bis 2030\", \"reasons\": [\"CORPUS_RECONCILIATION\"], \"metadata\": {\"gii_slug\": \"ebev_2030\", \"official_titl… (344 chars)",
 "external_id": "ebev_2030",
 "source_url": "https://www.gesetze-im-internet.de/ebev_2030/xml.zip",
 "first_run_id": "e00db166-3238-4225-9b8a-709bd64e8153",
 "last_run_id": "12b98a0e-f2a8-4626-96dd-19201cc98cc0",
 "last_error": null,
 "completed_at": "2026-09-17 09:30:53.757053+02:00",
 "max_attempts": "8",
 "priority": "30",
 "updated_at": "2026-09-17 09:30:53.757053+02:00",
 "result": "{\"status\": \"PARSER_REPAIRED\", \"version_id\": \"187a2cdc-6189-4c0f-a6af-f35d1c92cb60\", \"document_id\": \"9e23bcc0-c4f1-4f0b-8102-1dd03a03e795\", \"reconciliation\": {\"confirmed\": 0, \"candidates\": 0, \"not_observed\": 0}, \"changes_… (367 chars)"
}
```

## `public.gii_version_changes`

base table, 239801 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `run_id` | uuid | no |
| `source_document_id` | uuid | no |
| `from_version_id` | uuid | yes |
| `to_version_id` | uuid | no |
| `operation` | character varying | no |
| `old_path` | character varying | yes |
| `new_path` | character varying | yes |
| `substantive` | boolean | no |
| `reason` | text | no |
| `details` | jsonb | no |
| `detected_at` | timestamp with time zone | no |

- FK: `FOREIGN KEY (from_version_id) REFERENCES instrument_versions(id) ON DELETE SET NULL`
- FK: `FOREIGN KEY (to_version_id) REFERENCES instrument_versions(id) ON DELETE CASCADE`
- FK: `FOREIGN KEY (run_id) REFERENCES monitor_runs(id) ON DELETE CASCADE`
- FK: `FOREIGN KEY (source_document_id) REFERENCES source_documents(id) ON DELETE CASCADE`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (from_version_id, to_version_id, operation, old_path, new_path)`
- index: `CREATE INDEX ix_gii_version_changes_to_version ON public.gii_version_changes USING btree (to_version_id, operation)`
- index: `CREATE UNIQUE INDEX gii_version_changes_pkey ON public.gii_version_changes USING btree (id)`
- index: `CREATE UNIQUE INDEX uq_gii_version_change ON public.gii_version_changes USING btree (from_version_id, to_version_id, operation, old_path, new_path)`

Most common `operation`: `ADD` (237627), `AMEND` (1262), `REMOVED_FROM_CONSOLIDATION` (638), `TECHNICAL_ONLY` (261), `REPEAL_CANDIDATE` (13)

Example row 1:
```json
{
 "id": "a60322e5-5574-480b-b8ea-b5c97be7d30b",
 "run_id": "093b5e76-8072-4d7d-a751-da7a4cced55a",
 "source_document_id": "b1f1bd0a-ce4e-4f0b-a549-129ce4c79bbc",
 "from_version_id": null,
 "to_version_id": "c73d1a4a-f181-44f3-9255-7b9c317bc583",
 "operation": "ADD",
 "old_path": null,
 "new_path": "mitbestgwo_1_2002/bjnr168200002-xml/89/absatz-3/nummer-3",
 "substantive": "True",
 "reason": "new legal element",
 "details": "{}",
 "detected_at": "2026-09-12 12:07:42.276553+02:00"
}
```
Example row 2:
```json
{
 "id": "ee5f91a4-c605-4556-b25c-ade4c369114e",
 "run_id": "093b5e76-8072-4d7d-a751-da7a4cced55a",
 "source_document_id": "b1f1bd0a-ce4e-4f0b-a549-129ce4c79bbc",
 "from_version_id": null,
 "to_version_id": "c73d1a4a-f181-44f3-9255-7b9c317bc583",
 "operation": "ADD",
 "old_path": null,
 "new_path": "mitbestgwo_1_2002/bjnr168200002-xml/71/absatz-1/nummer-12",
 "substantive": "True",
 "reason": "new legal element",
 "details": "{}",
 "detected_at": "2026-09-12 12:07:42.276553+02:00"
}
```

## `public.ingestion_errors`

base table, 6164 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `run_id` | uuid | no |
| `external_id` | character varying | yes |
| `stage` | character varying | no |
| `error_type` | character varying | no |
| `message` | text | no |
| `retryable` | boolean | no |
| `details` | jsonb | no |
| `occurred_at` | timestamp with time zone | no |

- FK: `FOREIGN KEY (run_id) REFERENCES monitor_runs(id) ON DELETE CASCADE`
- PK: `PRIMARY KEY (id)`
- index: `CREATE INDEX ix_ingestion_errors_run_stage ON public.ingestion_errors USING btree (run_id, stage)`
- index: `CREATE UNIQUE INDEX ingestion_errors_pkey ON public.ingestion_errors USING btree (id)`

## `public.ingestion_jobs`

base table, 71622 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `job_key` | character varying | no |
| `job_type` | character varying | no |
| `status` | character varying | no |
| `payload` | jsonb | no |
| `attempts` | integer | no |
| `max_attempts` | integer | no |
| `available_at` | timestamp with time zone | no |
| `locked_at` | timestamp with time zone | yes |
| `locked_by` | character varying | yes |
| `last_error` | text | yes |
| `created_at` | timestamp with time zone | no |
| `completed_at` | timestamp with time zone | yes |
| `subject_id` | character varying | yes |

- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (job_key)`
- index: `CREATE UNIQUE INDEX uq_ingestion_jobs_active_subject ON public.ingestion_jobs USING btree (job_type, subject_id) WHERE ((subject_id IS NOT NULL) AND ((status)::text = ANY ((ARRAY['PENDING'::character varying, 'RUNNING'::character varying])::text[])))`
- index: `CREATE INDEX ix_ingestion_jobs_subject ON public.ingestion_jobs USING btree (job_type, subject_id, status)`
- index: `CREATE INDEX ix_ingestion_jobs_claim ON public.ingestion_jobs USING btree (status, available_at)`
- index: `CREATE UNIQUE INDEX ingestion_jobs_pkey ON public.ingestion_jobs USING btree (id)`
- index: `CREATE UNIQUE INDEX ingestion_jobs_job_key_key ON public.ingestion_jobs USING btree (job_key)`

## `public.instrument_expressions`

base table, 28253 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `instrument_id` | uuid | no |
| `language` | character varying | no |
| `expression_uri` | text | yes |
| `title` | text | yes |

- FK: `FOREIGN KEY (instrument_id) REFERENCES legal_instruments(id) ON DELETE CASCADE`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (instrument_id, language, expression_uri)`
- index: `CREATE UNIQUE INDEX instrument_expressions_pkey ON public.instrument_expressions USING btree (id)`
- index: `CREATE UNIQUE INDEX uq_expression ON public.instrument_expressions USING btree (instrument_id, language, expression_uri)`

Most common `language`: `DE` (14155), `EN` (14098)

Example row 1:
```json
{
 "id": "cc0603b2-ab2e-4408-b1de-95ac03c87b5c",
 "instrument_id": "0410ffad-1a76-4e95-9d36-d9e55030f123",
 "language": "DE",
 "expression_uri": null,
 "title": "Verordnung (EU) 2024/3110 des Europäischen Parlaments und des Rates vom 27. November 2024 zur Festlegung harmonisierter Vorschriften für die Vermarktung von Bauprodukten und zur Aufhebung der Verordnung (EU) Nr. 305/2011… (253 chars)"
}
```
Example row 2:
```json
{
 "id": "dcf3d488-46cb-4738-a978-d3da5d7001e0",
 "instrument_id": "0410ffad-1a76-4e95-9d36-d9e55030f123",
 "language": "EN",
 "expression_uri": null,
 "title": "Regulation (EU) 2024/3110 of the European Parliament and of the Council of 27 November 2024 laying down harmonised rules for the marketing of construction products and repealing Regulation (EU) No 305/2011 (Text with EEA… (231 chars)"
}
```

## `public.instrument_manifestations`

base table, 136994 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `instrument_id` | uuid | no |
| `language` | character varying | yes |
| `format_code` | character varying | no |
| `item_url` | text | no |
| `expression_uri` | text | yes |
| `manifestation_uri` | text | yes |
| `media_type` | character varying | yes |

- FK: `FOREIGN KEY (instrument_id) REFERENCES legal_instruments(id) ON DELETE CASCADE`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (instrument_id, language, format_code, item_url)`
- index: `CREATE UNIQUE INDEX uq_manifestation ON public.instrument_manifestations USING btree (instrument_id, language, format_code, item_url)`
- index: `CREATE UNIQUE INDEX instrument_manifestations_pkey ON public.instrument_manifestations USING btree (id)`

Most common `language`: `DE` (73631), `EN` (63363)

Example row 1:
```json
{
 "id": "4e3f7692-b026-40af-acfe-bfe2682e0b20",
 "instrument_id": "0410ffad-1a76-4e95-9d36-d9e55030f123",
 "language": "EN",
 "format_code": "xhtml",
 "item_url": "https://publications.europa.eu/resource/cellar/a860ee13-bce2-11ef-91ed-01aa75ed71a1.0006.03/DOC_1",
 "expression_uri": "http://publications.europa.eu/resource/cellar/a860ee13-bce2-11ef-91ed-01aa75ed71a1.0006",
 "manifestation_uri": "http://publications.europa.eu/resource/cellar/a860ee13-bce2-11ef-91ed-01aa75ed71a1.0006.03",
 "media_type": null
}
```
Example row 2:
```json
{
 "id": "8c93ebc8-1f87-4a7c-be61-8223a055ef70",
 "instrument_id": "0410ffad-1a76-4e95-9d36-d9e55030f123",
 "language": "EN",
 "format_code": "fmx4",
 "item_url": "https://publications.europa.eu/resource/cellar/a860ee13-bce2-11ef-91ed-01aa75ed71a1.0006.02/DOC_1",
 "expression_uri": "http://publications.europa.eu/resource/cellar/a860ee13-bce2-11ef-91ed-01aa75ed71a1.0006",
 "manifestation_uri": "http://publications.europa.eu/resource/cellar/a860ee13-bce2-11ef-91ed-01aa75ed71a1.0006.02",
 "media_type": null
}
```

## `public.instrument_relationships`

base table, 1041525 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `source_document_id` | uuid | no |
| `target_document_id` | uuid | no |
| `relation_type` | character varying | no |
| `raw_predicate` | text | no |
| `raw_element` | character varying | no |
| `related_work_uri` | text | yes |
| `provision_changes` | jsonb | no |
| `first_seen_run_id` | uuid | no |
| `last_seen_run_id` | uuid | no |
| `first_seen_at` | timestamp with time zone | no |
| `last_seen_at` | timestamp with time zone | no |
| `canonical_key` | character varying | yes |
| `relation_family` | character varying | no |
| `is_direct` | boolean | no |
| `status` | character varying | no |
| `mapping_status` | character varying | no |
| `source_direction` | character varying | no |
| `valid_from` | date | yes |
| `valid_to` | date | yes |

- FK: `FOREIGN KEY (target_document_id) REFERENCES source_documents(id) ON DELETE CASCADE`
- FK: `FOREIGN KEY (last_seen_run_id) REFERENCES monitor_runs(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (first_seen_run_id) REFERENCES monitor_runs(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (source_document_id) REFERENCES source_documents(id) ON DELETE CASCADE`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (canonical_key)`
- UNIQUE: `UNIQUE (source_document_id, target_document_id, relation_type, raw_predicate)`
- index: `CREATE INDEX ix_relationships_source_type ON public.instrument_relationships USING btree (source_document_id, relation_type)`
- index: `CREATE INDEX ix_relationships_target_type ON public.instrument_relationships USING btree (target_document_id, relation_type)`
- index: `CREATE UNIQUE INDEX uq_instrument_relationships_canonical_key ON public.instrument_relationships USING btree (canonical_key)`
- index: `CREATE UNIQUE INDEX instrument_relationships_pkey ON public.instrument_relationships USING btree (id)`
- index: `CREATE UNIQUE INDEX uq_instrument_relationship_fact ON public.instrument_relationships USING btree (source_document_id, target_document_id, relation_type, raw_predicate)`

Most common `relation_type`: `NATIONAL_IMPLEMENTATION` (899665), `CITES` (51509), `LEGAL_BASIS` (26402), `CONSOLIDATION_LINK` (18540), `AMENDS` (12577), `OTHER` (8394), `CORRECTS` (6168), `RELATED_CASE_LAW` (5929), `ADOPTS` (3494), `REPEALS` (3049), `IMPLICITLY_REPEALS` (2117), `RELATED_PROPOSAL` (1590), `DEROGATES` (627), `COMPLETES` (434), `EXTENDS_VALIDITY` (362)
Most common `status`: `ACTIVE` (1041525)

Example row 1:
```json
{
 "id": "46342a04-3033-45f2-af4b-103823b01d72",
 "source_document_id": "2b95553a-3938-4cfc-b9b4-04de61422fcf",
 "target_document_id": "00d4ddfe-51b4-40e1-a485-fc0a0212d4fa",
 "relation_type": "NATIONAL_IMPLEMENTATION",
 "raw_predicate": "http://publications.europa.eu/ontology/cdm#resource_legal_implemented_by_measure_national_implementing",
 "raw_element": "RESOURCE_LEGAL_IMPLEMENTED_BY_MEASURE_NATIONAL_IMPLEMENTING",
 "related_work_uri": "http://publications.europa.eu/resource/cellar/e21cd6e5-8d46-4c6a-8f8c-fd13f3cdcc17",
 "provision_changes": "[]",
 "first_seen_run_id": "68ffe4ba-0178-40e2-96f6-83f3c3e9f13e",
 "last_seen_run_id": "68ffe4ba-0178-40e2-96f6-83f3c3e9f13e",
 "first_seen_at": "2026-09-08 22:18:47.431601+02:00",
 "last_seen_at": "2026-09-08 22:18:47.431601+02:00",
 "canonical_key": "334345d85e08c6bd02b6660d83737729f2a1985070eb58d2ca9091a259d425b9",
 "relation_family": "IMPLEMENTATION",
 "is_direct": "True",
 "status": "ACTIVE",
 "mapping_status": "MAPPED",
 "source_direction": "FORWARD",
 "valid_from": null,
 "valid_to": null
}
```
Example row 2:
```json
{
 "id": "c9448a89-53e0-4fc7-a519-40a6c9f00fd0",
 "source_document_id": "6359adeb-76f0-4567-a839-2a9ae297fc63",
 "target_document_id": "4e9997e8-a413-43ea-8f90-6f97981f9808",
 "relation_type": "CONSOLIDATION_LINK",
 "raw_predicate": "http://publications.europa.eu/ontology/cdm#resource_legal_consolidated_by_act_consolidated",
 "raw_element": "RESOURCE_LEGAL_CONSOLIDATED_BY_ACT_CONSOLIDATED",
 "related_work_uri": "http://publications.europa.eu/resource/cellar/e412ab05-2d78-11f1-906d-01aa75ed71a1",
 "provision_changes": "[]",
 "first_seen_run_id": "5ab81ae0-fe7e-4046-90ee-4fc013937410",
 "last_seen_run_id": "68ffe4ba-0178-40e2-96f6-83f3c3e9f13e",
 "first_seen_at": "2026-09-08 10:51:29.091707+02:00",
 "last_seen_at": "2026-09-09 03:00:02.881667+02:00",
 "canonical_key": "a14003eb55ce1b745961b0d9b337213d5eeb93d53e723c65f612f00d50f3621a",
 "relation_family": "VERSION",
 "is_direct": "True",
 "status": "ACTIVE",
 "mapping_status": "MAPPED",
 "source_direction": "INVERSE",
 "valid_from": null,
 "valid_to": null
}
```

## `public.instrument_versions`

base table, 65640 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `instrument_id` | uuid | no |
| `version_key` | character varying | no |
| `language` | character varying | no |
| `version_kind` | character varying | no |
| `version_date` | date | yes |
| `is_current` | boolean | no |
| `source_url` | text | no |
| `content_sha256` | character varying | no |
| `parse_status` | character varying | no |
| `parse_warnings` | jsonb | no |
| `fetched_at` | timestamp with time zone | no |
| `source_document_id` | uuid | yes |
| `effective_from` | date | yes |
| `source_format` | character varying | yes |
| `parser_version` | character varying | no |
| `quality_status` | character varying | no |
| `quality_score` | double precision | yes |
| `quality_report` | jsonb | no |
| `is_preferred` | boolean | no |
| `canonicalization_version` | character varying | no |

- FK: `FOREIGN KEY (source_document_id) REFERENCES source_documents(id) ON DELETE SET NULL`
- FK: `FOREIGN KEY (instrument_id) REFERENCES legal_instruments(id) ON DELETE CASCADE`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (instrument_id, version_key)`
- index: `CREATE UNIQUE INDEX uq_instrument_version ON public.instrument_versions USING btree (instrument_id, version_key)`
- index: `CREATE UNIQUE INDEX uq_instrument_versions_preferred ON public.instrument_versions USING btree (instrument_id, language) WHERE is_preferred`
- index: `CREATE UNIQUE INDEX instrument_versions_pkey ON public.instrument_versions USING btree (id)`

Most common `language`: `DE` (37322), `EN` (28318)

Example row 1:
```json
{
 "id": "5d695e92-366e-4faa-b85f-eba9eda1cb93",
 "instrument_id": "d6b5b074-543f-4daf-81cb-6f3825c7b55c",
 "version_key": "ORI:EN:UNDATED:8125d7a0:d6e7feebf5fdb0ed9e693a2e1ac319acab19551ec0d870432f0d97d991422cbc",
 "language": "EN",
 "version_kind": "ORIGINAL",
 "version_date": null,
 "is_current": "False",
 "source_url": "https://publications.europa.eu/resource/cellar/25d80cbc-d493-4031-86cf-a2754276245f.0005.01/DOC_1",
 "content_sha256": "d6e7feebf5fdb0ed9e693a2e1ac319acab19551ec0d870432f0d97d991422cbc",
 "parse_status": "PARSED",
 "parse_warnings": "[\"LEGACY_HTML heuristic boundaries used; inspect parser evidence.\"]",
 "fetched_at": "2026-09-08 16:54:35.110734+02:00",
 "source_document_id": "cf0f384b-da48-44f7-bfc5-caadae34c140",
 "effective_from": null,
 "source_format": "LEGACY_HTML",
 "parser_version": "structure-v3",
 "quality_status": "LEGACY_UNVALIDATED",
 "quality_score": null,
 "quality_report": "{}",
 "is_preferred": "False",
 "canonicalization_version": "eurlex-identity-v1"
}
```
Example row 2:
```json
{
 "id": "2a72fb91-3cd6-4f12-beb8-b7f596f5badd",
 "instrument_id": "146b11c8-3741-406c-9840-ae026f6040cf",
 "version_key": "ORI:EN:UNDATED:8125d7a0:424bfc085ab449b7f8d66d6046343db3d49ec2f610809dd32c88c37c0bcbe6f0",
 "language": "EN",
 "version_kind": "ORIGINAL",
 "version_date": null,
 "is_current": "False",
 "source_url": "https://publications.europa.eu/resource/cellar/8c59a29d-251a-41bb-bc28-0f42868cd0dc.0006.01/DOC_1",
 "content_sha256": "424bfc085ab449b7f8d66d6046343db3d49ec2f610809dd32c88c37c0bcbe6f0",
 "parse_status": "PARSED",
 "parse_warnings": "[\"LEGACY_HTML heuristic boundaries used; inspect parser evidence.\"]",
 "fetched_at": "2026-09-09 10:44:58.420793+02:00",
 "source_document_id": "643f6e52-70d9-41d1-985f-b00059339d2c",
 "effective_from": null,
 "source_format": "LEGACY_HTML",
 "parser_version": "structure-v3",
 "quality_status": "LEGACY_UNVALIDATED",
 "quality_score": null,
 "quality_report": "{}",
 "is_preferred": "False",
 "canonicalization_version": "eurlex-identity-v1"
}
```

## `public.legacy_relationship_candidates`

base table, 0 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `base_celex` | character varying | no |
| `child_celex` | character varying | no |
| `raw_relation` | character varying | no |
| `validation_status` | character varying | no |
| `authoritative_relationship_id` | uuid | yes |
| `evidence` | jsonb | no |
| `imported_at` | timestamp with time zone | no |

- FK: `FOREIGN KEY (authoritative_relationship_id) REFERENCES instrument_relationships(id) ON DELETE SET NULL`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (base_celex, child_celex, raw_relation)`
- index: `CREATE UNIQUE INDEX legacy_relationship_candidates_pkey ON public.legacy_relationship_candidates USING btree (id)`
- index: `CREATE INDEX ix_legacy_candidate_status ON public.legacy_relationship_candidates USING btree (validation_status)`
- index: `CREATE UNIQUE INDEX uq_legacy_candidate ON public.legacy_relationship_candidates USING btree (base_celex, child_celex, raw_relation)`

## `public.legal_elements`

base table, error: canceling statement due to statement timeout rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `version_id` | uuid | no |
| `parent_id` | uuid | yes |
| `element_type` | character varying | no |
| `identifier` | character varying | no |
| `number` | character varying | yes |
| `heading` | text | yes |
| `body_text` | text | no |
| `ordinal` | integer | no |
| `depth` | integer | no |
| `path` | character varying | no |
| `details` | jsonb | no |
| `text_sha256` | character varying | yes |
| `source_locator` | text | yes |
| `parse_confidence` | double precision | no |

- FK: `FOREIGN KEY (version_id) REFERENCES instrument_versions(id) ON DELETE CASCADE`
- FK: `FOREIGN KEY (parent_id) REFERENCES legal_elements(id) ON DELETE CASCADE`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (version_id, path)`
- index: `CREATE UNIQUE INDEX uq_legal_element_path ON public.legal_elements USING btree (version_id, path)`
- index: `CREATE UNIQUE INDEX legal_elements_pkey ON public.legal_elements USING btree (id)`
- index: `CREATE INDEX ix_legal_elements_lookup ON public.legal_elements USING btree (version_id, element_type, identifier)`

## `public.legal_instruments`

base table, 31398 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `source_document_id` | uuid | no |
| `celex` | character varying | no |
| `title` | text | yes |
| `resource_type` | character varying | yes |
| `act_kind` | character varying | yes |
| `lifecycle_status` | character varying | no |
| `created_at` | timestamp with time zone | no |
| `updated_at` | timestamp with time zone | no |

- FK: `FOREIGN KEY (source_document_id) REFERENCES source_documents(id) ON DELETE RESTRICT`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (celex)`
- UNIQUE: `UNIQUE (source_document_id)`
- index: `CREATE UNIQUE INDEX legal_instruments_pkey ON public.legal_instruments USING btree (id)`
- index: `CREATE UNIQUE INDEX legal_instruments_celex_key ON public.legal_instruments USING btree (celex)`
- index: `CREATE UNIQUE INDEX legal_instruments_source_document_id_key ON public.legal_instruments USING btree (source_document_id)`
- index: `CREATE INDEX ix_legal_instruments_celex ON public.legal_instruments USING btree (celex)`

Most common `resource_type`: `BUNDESRECHT` (6146), `LEGISLATIVE_PROCEDURE` (4126), `DEC` (3366), `BGBL_PUBLICATION` (2952), `REG` (2694), `REG_IMPL` (2692), `ACCIDENT_PREVENTION_RULE` (2674), `DEC_ENTSCHEID` (1982), `DEC_IMPL` (1410), `REG_DEL` (1082), `DIR` (783), `MINISTRY_LEGAL_DOCUMENT` (750), `MINISTRY_DRAFT` (274), `DIR_DEL` (119), `TECHNICAL_RULE` (71)

Example row 1:
```json
{
 "id": "514ddc00-f640-4aee-a671-b3806c1d4ba6",
 "source_document_id": "725f8a0d-730a-48b4-b346-edc62294246b",
 "celex": "32024R3110R(02)",
 "title": "Berichtigung der Verordnung (EU) 2024/3110 des Europäischen Parlaments und des Rates vom 27. November 2024 zur Festlegung harmonisierter Vorschriften für die Vermarktung von Bauprodukten und zur Aufhebung der Verordnung … (269 chars)",
 "resource_type": "CORRIGENDUM",
 "act_kind": "CORRIGENDUM",
 "lifecycle_status": "UNKNOWN",
 "created_at": "2026-09-08 10:51:42.297127+02:00",
 "updated_at": "2026-09-08 10:51:42.297127+02:00"
}
```
Example row 2:
```json
{
 "id": "21ca7420-7d91-4830-ba0f-cba1afb9506f",
 "source_document_id": "e37d12fa-0ec9-42de-990c-ab9c25db16ed",
 "celex": "32026R1310",
 "title": "Commission Delegated Regulation (EU) 2026/1310 of 11 June 2026 supplementing Regulation (EU) 2024/3110 of the European Parliament and of the Council by establishing the applicable assessment and verification systems for … (251 chars)",
 "resource_type": "REG_DEL",
 "act_kind": "DELEGATED_ACT",
 "lifecycle_status": "NOT_IN_FORCE",
 "created_at": "2026-09-08 10:51:42.860512+02:00",
 "updated_at": "2026-09-08 10:51:42.860512+02:00"
}
```

## `public.legal_relationship_graph_v`

view, 1061224 rows

| Column | Type | Null |
|---|---|---|
| `origin` | text | yes |
| `id` | uuid | yes |
| `source_document_id` | uuid | yes |
| `source_system` | character varying | yes |
| `source_external_id` | character varying | yes |
| `target_document_id` | uuid | yes |
| `target_system` | character varying | yes |
| `target_external_id` | character varying | yes |
| `relation_type` | character varying | yes |
| `relation_family` | character varying | yes |
| `status` | character varying | yes |


Most common `origin`: `INSTRUMENT_RELATIONSHIP` (1041525), `CROSS_SOURCE` (19699)
Most common `relation_type`: `NATIONAL_IMPLEMENTATION` (899665), `CITES` (51509), `LEGAL_BASIS` (26402), `CONSOLIDATION_LINK` (18540), `AMENDS` (12577), `CITES_EU_ACT` (12081), `OTHER` (8394), `MENTIONS_LAW` (6171), `CORRECTS` (6168), `RELATED_CASE_LAW` (5929), `ADOPTS` (3494), `REPEALS` (3049), `IMPLICITLY_REPEALS` (2117), `RELATED_PROPOSAL` (1590), `DEROGATES` (627)
Most common `status`: `ACTIVE` (1061224)

Example row 1:
```json
{
 "origin": "INSTRUMENT_RELATIONSHIP",
 "id": "46342a04-3033-45f2-af4b-103823b01d72",
 "source_document_id": "2b95553a-3938-4cfc-b9b4-04de61422fcf",
 "source_system": "EUR_LEX",
 "source_external_id": "32014L0036",
 "target_document_id": "00d4ddfe-51b4-40e1-a485-fc0a0212d4fa",
 "target_system": "EUR_LEX",
 "target_external_id": "72024L1346CZE_126768",
 "relation_type": "NATIONAL_IMPLEMENTATION",
 "relation_family": "IMPLEMENTATION",
 "status": "ACTIVE"
}
```
Example row 2:
```json
{
 "origin": "INSTRUMENT_RELATIONSHIP",
 "id": "c9448a89-53e0-4fc7-a519-40a6c9f00fd0",
 "source_document_id": "6359adeb-76f0-4567-a839-2a9ae297fc63",
 "source_system": "EUR_LEX",
 "source_external_id": "02011R0305-20260108",
 "target_document_id": "4e9997e8-a413-43ea-8f90-6f97981f9808",
 "target_system": "EUR_LEX",
 "target_external_id": "32024R3110",
 "relation_type": "CONSOLIDATION_LINK",
 "relation_family": "VERSION",
 "status": "ACTIVE"
}
```

## `public.monitor_run_findings_v`

view, 113482 rows

| Column | Type | Null |
|---|---|---|
| `run_key` | character varying | yes |
| `mode` | character varying | yes |
| `run_status` | character varying | yes |
| `celex` | character varying | yes |
| `title` | text | yes |
| `discovery_role` | character varying | yes |
| `base_celex` | character varying | yes |
| `primary_decision` | character varying | yes |
| `decision_stage` | character varying | yes |
| `decision_reason` | text | yes |
| `relationship_protected` | boolean | yes |
| `fetch_status` | character varying | yes |
| `processed_at` | timestamp with time zone | yes |



Example row 1:
```json
{
 "run_key": "demo-20260908T085118365299Z",
 "mode": "DEMO",
 "run_status": "PARTIAL",
 "celex": "32024R3110",
 "title": "Regulation (EU) 2024/3110 of the European Parliament and of the Council of 27 November 2024 laying down harmonised rules for the marketing of construction products and repealing Regulation (EU) No 305/2011 (Text with EEA… (231 chars)",
 "discovery_role": "DEMO_BASE",
 "base_celex": "32024R3110",
 "primary_decision": "KEEP",
 "decision_stage": "PROTECTED_FROM_NEGATIVE_BLOCK",
 "decision_reason": "A configured protect rule matched before negative title rules were applied.",
 "relationship_protected": "False",
 "fetch_status": "METADATA_ONLY",
 "processed_at": "2026-09-08 10:51:29.475136+02:00"
}
```
Example row 2:
```json
{
 "run_key": "demo-20260908T085118365299Z",
 "mode": "DEMO",
 "run_status": "PARTIAL",
 "celex": "32001R1936",
 "title": "Council Regulation (EC) No 1936/2001 of 27 September 2001 laying down control measures applicable to fishing for certain stocks of highly migratory fish",
 "discovery_role": "DEMO_BASE",
 "base_celex": "32001R1936",
 "primary_decision": "BLOCK",
 "decision_stage": "NEGATIVE_TITLE_RULES",
 "decision_reason": "A conservative hard negative title rule matched.",
 "relationship_protected": "False",
 "fetch_status": "BLOCKED_NO_CONTENT",
 "processed_at": "2026-09-08 10:51:34.908435+02:00"
}
```

## `public.monitor_runs`

base table, 18878 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `run_key` | character varying | no |
| `mode` | character varying | no |
| `status` | character varying | no |
| `config_sha256` | character varying | no |
| `seed_count` | integer | no |
| `started_at` | timestamp with time zone | no |
| `completed_at` | timestamp with time zone | yes |
| `stats` | jsonb | no |
| `error_message` | text | yes |
| `source` | character varying | no |
| `cursor_start` | text | yes |
| `cursor_end` | text | yes |
| `app_version` | character varying | no |
| `parser_version` | character varying | no |
| `canonicalization_version` | character varying | no |

- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (run_key)`
- index: `CREATE UNIQUE INDEX monitor_runs_pkey ON public.monitor_runs USING btree (id)`
- index: `CREATE UNIQUE INDEX monitor_runs_run_key_key ON public.monitor_runs USING btree (run_key)`

Most common `status`: `SUCCESS` (16672), `PARTIAL` (2179), `RUNNING` (15), `FAILED` (6), `INTERRUPTED` (6)
Most common `source`: `EUR_LEX` (18788), `GERMAN_FEDERAL` (32), `BAUA` (19), `DGUV` (8), `BMUKN` (6), `DIP` (6), `BMAS` (6), `BUNDESRAT` (4), `BNETZA` (4), `DIBT` (3), `GII` (2)

Example row 1:
```json
{
 "id": "1c1167d6-749a-4944-aa5a-712a6e8b9796",
 "run_key": "demo-20260908T070056727606Z",
 "mode": "DEMO",
 "status": "FAILED",
 "config_sha256": "3dd54c271de234b80094eae739133eb227f9d99e92189024c9b47d223cb60169",
 "seed_count": "20",
 "started_at": "2026-09-08 09:00:57.432856+02:00",
 "completed_at": "2026-09-08 09:02:00.398900+02:00",
 "stats": "{\"seed_count\": 20, \"error_count\": 20, \"aborted_early\": false, \"metadata_only\": true, \"content_successes\": 0, \"metadata_successes\": 0, \"retained_base_count\": 0, \"selected_child_count\": 0, \"max_children_per_retained_base\":… (223 chars)",
 "error_message": null,
 "source": "EUR_LEX",
 "cursor_start": null,
 "cursor_end": null,
 "app_version": "3.0.0",
 "parser_version": "structure-v3",
 "canonicalization_version": "eurlex-identity-v1"
}
```
Example row 2:
```json
{
 "id": "5ab81ae0-fe7e-4046-90ee-4fc013937410",
 "run_key": "demo-20260908T085118365299Z",
 "mode": "DEMO",
 "status": "PARTIAL",
 "config_sha256": "3dd54c271de234b80094eae739133eb227f9d99e92189024c9b47d223cb60169",
 "seed_count": "20",
 "started_at": "2026-09-08 10:51:19.103479+02:00",
 "completed_at": "2026-09-08 10:51:42.885342+02:00",
 "stats": "{\"seed_count\": 20, \"error_count\": 18, \"aborted_early\": false, \"metadata_only\": true, \"content_successes\": 0, \"metadata_successes\": 4, \"retained_base_count\": 1, \"selected_child_count\": 2, \"max_children_per_retained_base\":… (223 chars)",
 "error_message": null,
 "source": "EUR_LEX",
 "cursor_start": null,
 "cursor_end": null,
 "app_version": "3.0.2",
 "parser_version": "structure-v3",
 "canonicalization_version": "eurlex-identity-v1"
}
```

## `public.official_publications`

base table, 2952 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `source` | character varying | no |
| `canonical_eli` | text | no |
| `title` | text | yes |
| `part` | character varying | yes |
| `issue_number` | character varying | yes |
| `promulgated_on` | date | yes |
| `source_url` | text | no |
| `metadata_json` | jsonb | no |
| `first_seen_at` | timestamp with time zone | no |

- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (source, canonical_eli)`
- index: `CREATE UNIQUE INDEX official_publications_pkey ON public.official_publications USING btree (id)`
- index: `CREATE UNIQUE INDEX uq_publication_eli ON public.official_publications USING btree (source, canonical_eli)`

Most common `source`: `RECHT_BUND` (2952)

Example row 1:
```json
{
 "id": "ffc6468a-1e34-4b4c-8f26-187c67747d96",
 "source": "RECHT_BUND",
 "canonical_eli": "https://www.recht.bund.de/eli/bund/bgbl-2/2025/236",
 "title": "Bekanntmachung über den Geltungsbereich des Übereinkommens über das Recht der nichtschifffahrtlichen Nutzung internationaler Wasserläufe",
 "part": "II",
 "issue_number": "236",
 "promulgated_on": "2025-08-28",
 "source_url": "https://www.recht.bund.de/eli/bund/bgbl-2/2025/236",
 "metadata_json": "{\"discovery\": {\"feed\": \"https://www.recht.bund.de/rss/feeds/rss_bgbl-2.xml\", \"links\": [\"https://www.recht.bund.de/eli/bund/bgbl-2/2025/236\"], \"titles\": [\"Bekanntmachung über den Geltungsbereich des Übereinkommens über da… (878 chars)",
 "first_seen_at": "2026-09-16 10:48:26.169952+02:00"
}
```
Example row 2:
```json
{
 "id": "f09fe798-fa6c-4205-b4f4-e22ee26a73ac",
 "source": "RECHT_BUND",
 "canonical_eli": "https://www.recht.bund.de/eli/bund/bgbl-1/2025/214",
 "title": "Achtzehnte Verordnung zur Änderung der Zweihundertfünfunddreißigsten Durchführungsverordnung zur Luftverkehrs-Ordnung (Festlegung von Flugverfahren für An- und Abflüge nach Instrumentenflugregeln zum und vom Flughafen Nü… (227 chars)",
 "part": "I",
 "issue_number": "214",
 "promulgated_on": "2025-09-17",
 "source_url": "https://www.recht.bund.de/eli/bund/bgbl-1/2025/214",
 "metadata_json": "{\"discovery\": {\"feed\": \"https://www.recht.bund.de/rss/feeds/rss_bgbl-1.xml\", \"links\": [\"https://www.recht.bund.de/eli/bund/bgbl-1/2025/214\"], \"titles\": [\"Achtzehnte Verordnung zur Änderung der Zweihundertfünfunddreißigst… (1010 chars)",
 "first_seen_at": "2026-09-16 10:48:28.101811+02:00"
}
```

## `public.recital_sets`

base table, 28325 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `version_id` | uuid | no |
| `recital_count` | integer | no |
| `combined_text` | text | no |
| `recitals_json` | jsonb | no |
| `checksum_sha256` | character varying | no |

- FK: `FOREIGN KEY (version_id) REFERENCES instrument_versions(id) ON DELETE CASCADE`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (version_id)`
- index: `CREATE UNIQUE INDEX recital_sets_pkey ON public.recital_sets USING btree (id)`
- index: `CREATE UNIQUE INDEX recital_sets_version_id_key ON public.recital_sets USING btree (version_id)`


Example row 1:
```json
{
 "id": "806923b5-da94-48ad-80dc-9d7e825c1e7b",
 "version_id": "6799a5b0-4868-439a-8ef8-99abfffa90a8",
 "recital_count": "0",
 "combined_text": "",
 "recitals_json": "[]",
 "checksum_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
}
```
Example row 2:
```json
{
 "id": "9eb35239-45af-4592-a405-4897c06b6bc2",
 "version_id": "e3046038-8f13-4d38-91a1-7578faf24f5a",
 "recital_count": "0",
 "combined_text": "",
 "recitals_json": "[]",
 "checksum_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
}
```

## `public.recitals_v`

view, error: canceling statement due to statement timeout rows

| Column | Type | Null |
|---|---|---|
| `celex` | character varying | yes |
| `version_key` | character varying | yes |
| `language` | character varying | yes |
| `version_kind` | character varying | yes |
| `version_date` | date | yes |
| `is_preferred` | boolean | yes |
| `quality_status` | character varying | yes |
| `ordinal` | integer | yes |
| `number` | character varying | yes |
| `identifier` | character varying | yes |
| `body_text` | text | yes |
| `path` | character varying | yes |


## `public.regulatory_attachments`

base table, 38505 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `item_id` | uuid | no |
| `version_id` | uuid | no |
| `snapshot_id` | uuid | yes |
| `attachment_key` | character varying | no |
| `title` | text | no |
| `kind` | character varying | no |
| `source_url` | text | no |
| `final_url` | text | no |
| `media_type` | character varying | no |
| `sha256` | character varying | yes |
| `fetch_status` | character varying | no |
| `is_current` | boolean | no |
| `metadata_json` | jsonb | no |
| `fetched_at` | timestamp with time zone | no |

- FK: `FOREIGN KEY (item_id) REFERENCES regulatory_items(id) ON DELETE CASCADE`
- FK: `FOREIGN KEY (snapshot_id) REFERENCES source_snapshots(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (version_id) REFERENCES regulatory_item_versions(id) ON DELETE CASCADE`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (version_id, attachment_key)`
- index: `CREATE UNIQUE INDEX regulatory_attachments_pkey ON public.regulatory_attachments USING btree (id)`
- index: `CREATE UNIQUE INDEX uq_regulatory_attachment_version_key ON public.regulatory_attachments USING btree (version_id, attachment_key)`
- index: `CREATE INDEX ix_regulatory_attachment_item_current ON public.regulatory_attachments USING btree (item_id, is_current)`
- index: `CREATE INDEX ix_regulatory_attachment_sha ON public.regulatory_attachments USING btree (sha256)`

Most common `kind`: `ATTACHMENT` (38505)

Example row 1:
```json
{
 "id": "f425f382-758a-4f42-b6c9-cb168b369322",
 "item_id": "f2108520-b218-4720-8f31-c982d04ea9bd",
 "version_id": "1628958f-f7fe-4f88-aea6-800593e1551d",
 "snapshot_id": "830143b0-619a-4e35-8745-00719b77305d",
 "attachment_key": "ASR:V3:PDF",
 "title": "ASR V3",
 "kind": "ATTACHMENT",
 "source_url": "https://www.baua.de/DE/Angebote/Regelwerk/ASR/pdf/ASR-V3.pdf?__blob=publicationFile",
 "final_url": "https://www.baua.de/DE/Angebote/Regelwerk/ASR/pdf/ASR-V3.pdf?__blob=publicationFile",
 "media_type": "application/pdf",
 "sha256": "328b8d94969fa5a87963ea0275ef1b6ade7b9662712c98272046af071df854c1",
 "fetch_status": "FETCHED",
 "is_current": "False",
 "metadata_json": "{\"required\": true}",
 "fetched_at": "2026-09-21 14:39:15.979912+02:00"
}
```
Example row 2:
```json
{
 "id": "66c0370a-b560-4681-9943-66b307c64775",
 "item_id": "526f1b5f-4a2a-4e58-b9aa-dde8c6a84029",
 "version_id": "711f221d-3c11-487c-bdc0-c47289b4e469",
 "snapshot_id": "de36949e-a7ce-4a43-87d5-a5536c45fbab",
 "attachment_key": "FILE:0f3471a84c018cac776a2227",
 "title": "VKI [PDF, 82KB]",
 "kind": "ATTACHMENT",
 "source_url": "https://www.bmas.de/SharedDocs/Downloads/DE/Gesetze/Stellungnahmen/sorgfaltspflichtengesetz-vki.pdf?__blob=publicationFile&v=2",
 "final_url": "https://www.bmas.de/SharedDocs/Downloads/DE/Gesetze/Stellungnahmen/sorgfaltspflichtengesetz-vki.pdf?__blob=publicationFile&v=2",
 "media_type": "application/pdf",
 "sha256": "25efd5426b3b9fdf14a69d770fdad80953308cd988d8d8cc8fbc9209ef59a2e7",
 "fetch_status": "FETCHED",
 "is_current": "False",
 "metadata_json": "{\"required\": true}",
 "fetched_at": "2026-09-22 15:33:51.618413+02:00"
}
```

## `public.regulatory_events`

base table, 28665 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `fingerprint` | character varying | no |
| `source` | character varying | no |
| `item_id` | uuid | no |
| `version_id` | uuid | yes |
| `run_id` | uuid | no |
| `event_type` | character varying | no |
| `occurred_at` | timestamp with time zone | no |
| `before_json` | jsonb | no |
| `after_json` | jsonb | no |
| `evidence` | jsonb | no |

- FK: `FOREIGN KEY (item_id) REFERENCES regulatory_items(id) ON DELETE CASCADE`
- FK: `FOREIGN KEY (run_id) REFERENCES monitor_runs(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (version_id) REFERENCES regulatory_item_versions(id) ON DELETE RESTRICT`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (fingerprint)`
- index: `CREATE UNIQUE INDEX regulatory_events_fingerprint_key ON public.regulatory_events USING btree (fingerprint)`
- index: `CREATE UNIQUE INDEX regulatory_events_pkey ON public.regulatory_events USING btree (id)`
- index: `CREATE INDEX ix_regulatory_event_source_time ON public.regulatory_events USING btree (source, occurred_at)`
- index: `CREATE INDEX ix_regulatory_event_item_time ON public.regulatory_events USING btree (item_id, occurred_at)`

Most common `source`: `DGUV` (18001), `DIP` (5016), `BMUKN` (2745), `BMAS` (1850), `BAUA` (830), `BUNDESRAT` (98), `BNETZA` (73), `DIBT` (52)

Example row 1:
```json
{
 "id": "6ab1af71-50ee-47a3-8bff-600ef8558b12",
 "fingerprint": "0015f888e493e55134e26226d2331edd590bd0124f31672bf9f74645fa7a3b09",
 "source": "BUNDESRAT",
 "item_id": "3908c4f8-7bc7-48e9-8fee-9b7de92c1810",
 "version_id": "f674a0c3-4112-4396-b4d5-bad98a2f994b",
 "run_id": "24b8d088-8df7-4ab2-8ef0-b4f520328d52",
 "event_type": "CREATED",
 "occurred_at": "2026-09-21 10:59:55.035952+02:00",
 "before_json": "{}",
 "after_json": "{\"title\": \"25.09.2026 | Antragsloses Kindergeld, Maßnahmen gegen Autoposing, Haushalt 2027 und Reform der Nachrichtendienste\", \"detail\": {\"url\": \"https://www.bundesrat.de/DE/plenum/bundesrat-kompakt/26/1068/1068-pk.html\"… (1098 chars)",
 "evidence": "{\"document_created\": true}"
}
```
Example row 2:
```json
{
 "id": "d4a5f90a-4f0b-42bf-ba3d-c72ded040a57",
 "fingerprint": "95c0d8737b7ebd64dbf882c18c922cf8cc593e37862c33c281d03f6387fbd15e",
 "source": "BUNDESRAT",
 "item_id": "d3ee1a3a-f387-4668-8a7c-8c6a9fb0182f",
 "version_id": "92403dd0-b499-4ced-950f-0035521d4ffb",
 "run_id": "24b8d088-8df7-4ab2-8ef0-b4f520328d52",
 "event_type": "CREATED",
 "occurred_at": "2026-09-21 10:59:55.501710+02:00",
 "before_json": "{}",
 "after_json": "{\"title\": \"25.09.2026 | Haushalt 2027\", \"detail\": {\"url\": \"https://www.bundesrat.de/DE/plenum/bundesrat-kompakt/26/1068/02a.html#top-2a\", \"sha256\": \"43135885e15f3d44dceabf4e36885f47b7a20ebbf12a45ac13789a29619cdbc2\", \"med… (1023 chars)",
 "evidence": "{\"document_created\": true}"
}
```

## `public.regulatory_item_versions`

base table, 21953 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `item_id` | uuid | no |
| `run_id` | uuid | no |
| `manifest_id` | uuid | yes |
| `detail_snapshot_id` | uuid | yes |
| `instrument_version_id` | uuid | yes |
| `version_number` | integer | no |
| `content_sha256` | character varying | no |
| `text_sha256` | character varying | yes |
| `parser_version` | character varying | no |
| `canonicalization_version` | character varying | no |
| `canonical_data` | jsonb | no |
| `extracted_text` | text | yes |
| `parse_warnings` | jsonb | no |
| `source_modified_at` | timestamp with time zone | yes |
| `is_current` | boolean | no |
| `created_at` | timestamp with time zone | no |

- FK: `FOREIGN KEY (manifest_id) REFERENCES regulatory_source_manifests(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (instrument_version_id) REFERENCES instrument_versions(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (item_id) REFERENCES regulatory_items(id) ON DELETE CASCADE`
- FK: `FOREIGN KEY (run_id) REFERENCES monitor_runs(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (detail_snapshot_id) REFERENCES source_snapshots(id) ON DELETE RESTRICT`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (item_id, version_number)`
- index: `CREATE UNIQUE INDEX regulatory_item_versions_pkey ON public.regulatory_item_versions USING btree (id)`
- index: `CREATE UNIQUE INDEX uq_regulatory_item_version ON public.regulatory_item_versions USING btree (item_id, version_number)`
- index: `CREATE UNIQUE INDEX uq_regulatory_version_current ON public.regulatory_item_versions USING btree (item_id) WHERE is_current`
- index: `CREATE INDEX ix_regulatory_version_run ON public.regulatory_item_versions USING btree (run_id, created_at)`


Example row 1:
```json
{
 "id": "8cb65e45-7f0d-4c5b-a4b0-950982c1786e",
 "item_id": "fb0de394-ba3e-4a02-a5a9-a7c4e6d14434",
 "run_id": "b61b9de1-f091-4656-8a43-43a4d8b82b7a",
 "manifest_id": "d9d316c4-f747-40c4-8fbb-f1fdcd44abf8",
 "detail_snapshot_id": "417e24fe-d877-45c8-b442-48dff785d569",
 "instrument_version_id": "df47c4e3-a311-4441-acd1-409564ce4dc4",
 "version_number": "2",
 "content_sha256": "9ba1686bcbd58423296bc701fb28bcc2d105e6e590af72620eeb5f0f81921e0c",
 "text_sha256": "45b0c7bde1793cda81c8cbba50135406cbb29eecdffafc218844e4288f8ab68d",
 "parser_version": "regulatory-text-v1",
 "canonicalization_version": "regulatory-c14n-v2",
 "canonical_data": "{\"title\": \"Leitpapier zur Evaluation\", \"detail\": {\"url\": \"https://publikationen.dguv.de/Regelwerk/DGUV-Grundsaetze/2873/Leitpapier-zur-Evaluation-Grundverstaendnis-in-der-gesetzlichen-Unfallversicherung\", \"sha256\": \"3654… (3960 chars)",
 "extracted_text": "Leitpapier zur Evaluation  DGUV Grundsatz 311-001 \"Leitpapier zur Evaluation: Grundverständnis in der gesetzlichen Unfallversicherung\" | DGUV Publikationen Um DGUV Publikationen in vollem Umfang nutzen zu können, empfehl… (43973 chars)",
 "parse_warnings": "[]",
 "source_modified_at": null,
 "is_current": "False",
 "created_at": "2026-09-22 10:17:18.496567+02:00"
}
```
Example row 2:
```json
{
 "id": "23bf993b-d924-4c22-8db0-301c0ad09b3c",
 "item_id": "e11f63e1-4e2d-4739-a7fc-2b4cdf37d480",
 "run_id": "b61b9de1-f091-4656-8a43-43a4d8b82b7a",
 "manifest_id": "d9d316c4-f747-40c4-8fbb-f1fdcd44abf8",
 "detail_snapshot_id": "ffff1164-267f-4b4c-9a7a-bab48387c9c5",
 "instrument_version_id": "dbb8ae49-7453-4c7d-881a-474b04a99417",
 "version_number": "2",
 "content_sha256": "e73d6619f94dbc3124507e22557b5782da44040093d300d92bb48b06ac09fc7e",
 "text_sha256": "3f37196fdda105a2216c98a75716c22bda05dc8d6a2aa58fb660838dc52237f8",
 "parser_version": "regulatory-text-v1",
 "canonicalization_version": "regulatory-c14n-v2",
 "canonical_data": "{\"title\": \"Industriestaubsauger und Entstauber\", \"detail\": {\"url\": \"https://publikationen.dguv.de/Regelwerk/DGUV-Informationen/3162/Industriestaubsauger-und-Entstauber\", \"sha256\": \"505115f2528bb686d0f551e0859573ff67e0c8a… (1752 chars)",
 "extracted_text": "Industriestaubsauger und Entstauber  Industriestaubsauger und Entstauber | DGUV Informationen | Regelwerk | DGUV Publikationen Um DGUV Publikationen in vollem Umfang nutzen zu können, empfehlen wir Ihnen Javascript in Ih… (2224 chars)",
 "parse_warnings": "[]",
 "source_modified_at": null,
 "is_current": "False",
 "created_at": "2026-09-22 09:55:13.772796+02:00"
}
```

## `public.regulatory_items`

base table, 8024 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `source_document_id` | uuid | no |
| `source` | character varying | no |
| `channel` | character varying | no |
| `item_kind` | character varying | no |
| `authority` | text | no |
| `binding_status` | character varying | no |
| `lifecycle_status` | character varying | no |
| `stable_number` | character varying | yes |
| `duty_bearer_role` | character varying | yes |
| `summary` | text | yes |
| `canonical_url` | text | no |
| `published_on` | date | yes |
| `source_modified_at` | timestamp with time zone | yes |
| `is_current` | boolean | no |
| `missing_count` | integer | no |
| `first_seen_run_id` | uuid | no |
| `last_seen_run_id` | uuid | no |
| `metadata_json` | jsonb | no |
| `created_at` | timestamp with time zone | no |
| `updated_at` | timestamp with time zone | no |

- FK: `FOREIGN KEY (last_seen_run_id) REFERENCES monitor_runs(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (first_seen_run_id) REFERENCES monitor_runs(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (source_document_id) REFERENCES source_documents(id) ON DELETE RESTRICT`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (source_document_id)`
- index: `CREATE INDEX ix_regulatory_item_source_current ON public.regulatory_items USING btree (source, is_current)`
- index: `CREATE UNIQUE INDEX regulatory_items_pkey ON public.regulatory_items USING btree (id)`
- index: `CREATE UNIQUE INDEX regulatory_items_source_document_id_key ON public.regulatory_items USING btree (source_document_id)`
- index: `CREATE INDEX ix_regulatory_item_kind_status ON public.regulatory_items USING btree (item_kind, lifecycle_status)`
- index: `CREATE INDEX ix_regulatory_item_published ON public.regulatory_items USING btree (published_on)`
- index: `CREATE INDEX ix_regulatory_item_metadata_gin ON public.regulatory_items USING gin (metadata_json)`

Most common `source`: `DIP` (4126), `DGUV` (2674), `BMUKN` (628), `BMAS` (396), `BAUA` (71), `DIBT` (52), `BUNDESRAT` (39), `BNETZA` (38)

Example row 1:
```json
{
 "id": "6242ab10-c798-44e0-9c5a-efd1ba27ed87",
 "source_document_id": "b496dfa5-7cd1-4ec7-8982-dd74fe050949",
 "source": "DIBT",
 "channel": "NOTICES",
 "item_kind": "MVV_TB",
 "authority": "Deutsches Institut für Bautechnik",
 "binding_status": "TECHNICAL_RULE",
 "lifecycle_status": "PUBLISHED",
 "stable_number": "MVVTB_2025-1_ZWEITE_DRUCKFEHLERBERICHTIGUNG_MITTEILUNG",
 "duty_bearer_role": null,
 "summary": null,
 "canonical_url": "https://www.dibt.de/fileadmin/dibt-website/Dokumente/Referat/P5/Technische_Bestimmungen/MVVTB_2025-1_Zweite_Druckfehlerberichtigung_Mitteilung.pdf",
 "published_on": null,
 "source_modified_at": null,
 "is_current": "True",
 "missing_count": "0",
 "first_seen_run_id": "b29fd4d0-eaa0-4d55-b97e-664ac05fe909",
 "last_seen_run_id": "0c38ba9f-1634-4864-a60a-6da64a9e5752",
 "metadata_json": "{\"filename\": \"MVVTB_2025-1_Zweite_Druckfehlerberichtigung_Mitteilung.pdf\", \"coverage_channel\": \"NOTICES\"}",
 "created_at": "2026-09-21 11:06:59.393183+02:00",
 "updated_at": "2026-10-12 14:37:43.649944+02:00"
}
```
Example row 2:
```json
{
 "id": "98574ed1-6754-4d5f-b78f-87686caf6ebe",
 "source_document_id": "ded20334-b2cf-4810-a0ab-b3d596ab919b",
 "source": "DIBT",
 "channel": "NOTICES",
 "item_kind": "MVV_TB",
 "authority": "Deutsches Institut für Bautechnik",
 "binding_status": "TECHNICAL_RULE",
 "lifecycle_status": "PUBLISHED",
 "stable_number": "MVVTB_2024-1",
 "duty_bearer_role": null,
 "summary": null,
 "canonical_url": "https://www.dibt.de/fileadmin/dibt-website/Dokumente/Referat/P5/Technische_Bestimmungen/MVVTB_2024-1.pdf",
 "published_on": null,
 "source_modified_at": null,
 "is_current": "True",
 "missing_count": "0",
 "first_seen_run_id": "b29fd4d0-eaa0-4d55-b97e-664ac05fe909",
 "last_seen_run_id": "0c38ba9f-1634-4864-a60a-6da64a9e5752",
 "metadata_json": "{\"filename\": \"MVVTB_2024-1.pdf\", \"coverage_channel\": \"NOTICES\"}",
 "created_at": "2026-09-21 11:07:20.804767+02:00",
 "updated_at": "2026-10-12 14:38:05.259072+02:00"
}
```

## `public.regulatory_source_manifests`

base table, 881 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `run_id` | uuid | no |
| `source` | character varying | no |
| `channel` | character varying | no |
| `page_key` | character varying | no |
| `requested_url` | text | no |
| `final_url` | text | no |
| `http_status` | integer | no |
| `sha256` | character varying | no |
| `payload` | bytea | no |
| `headers` | jsonb | no |
| `validators` | jsonb | no |
| `item_count` | integer | no |
| `retrieved_at` | timestamp with time zone | no |

- FK: `FOREIGN KEY (run_id) REFERENCES monitor_runs(id) ON DELETE RESTRICT`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (run_id, source, channel, page_key)`
- index: `CREATE UNIQUE INDEX regulatory_source_manifests_pkey ON public.regulatory_source_manifests USING btree (id)`
- index: `CREATE INDEX ix_regulatory_manifest_source_time ON public.regulatory_source_manifests USING btree (source, retrieved_at)`
- index: `CREATE UNIQUE INDEX uq_regulatory_manifest_page ON public.regulatory_source_manifests USING btree (run_id, source, channel, page_key)`

Most common `source`: `DIP` (426), `BMAS` (201), `BMUKN` (195), `BAUA` (40), `DGUV` (8), `BUNDESRAT` (4), `BNETZA` (4), `DIBT` (3)

Example row 1:
```json
{
 "id": "9bf4a6ca-c9e4-46e5-b75f-0925751a1b72",
 "run_id": "24b8d088-8df7-4ab2-8ef0-b4f520328d52",
 "source": "BUNDESRAT",
 "channel": "PLENUM_RSS",
 "page_key": "feed",
 "requested_url": "https://www.bundesrat.de/SiteGlobals/Functions/RSSFeed/RSSGenerator_top_plenumkompakt.xml",
 "final_url": "https://www.bundesrat.de/SiteGlobals/Functions/RSSFeed/RSSGenerator_top_plenumkompakt.xml",
 "http_status": "200",
 "sha256": "6cf645968f77f76555ef28dc3d3ae0bae79356c105443fbf22b19bdcdc9bab89",
 "payload": "<7701 bytes>",
 "headers": "{\"date\": \"Mon, 21 Sep 2026 08:59:53 GMT\", \"vary\": \"Accept-Encoding\", \"server\": \"Apache\", \"connection\": \"Keep-Alive\", \"keep-alive\": \"timeout=10, max=500\", \"set-cookie\": \"AL_BALANCE-S=$xc/grZlPhhNA9ji!q1HzzmvRAyILQ2YvGWJDb… (3697 chars)",
 "validators": "{\"last-modified\": \"Mon, 21 Sep 2026 08:59:53 GMT\"}",
 "item_count": "24",
 "retrieved_at": "2026-09-21 10:59:52.823973+02:00"
}
```
Example row 2:
```json
{
 "id": "85803a9b-2358-44ee-b769-bf379a2d00b1",
 "run_id": "4a7332cc-eb5b-48c8-94dc-e14145b01a00",
 "source": "BAUA",
 "channel": "ASR",
 "page_key": "ASR",
 "requested_url": "https://www.baua.de/DE/Angebote/Regelwerk/ASR/ASR.html",
 "final_url": "https://www.baua.de/DE/Angebote/Regelwerk/ASR/ASR.html",
 "http_status": "200",
 "sha256": "7fee906a6ef9dd009508c7b4339496b460848676e0efd10bc843f6b62eb997b1",
 "payload": "<258079 bytes>",
 "headers": "{\"age\": \"278\", \"date\": \"Mon, 21 Sep 2026 09:00:47 GMT\", \"vary\": \"Accept-Encoding, Accept-Encoding\", \"server\": \"BunnyCDN-DE1-1330\", \"x-cache\": \"cached\", \"cdn-cache\": \"EXPIRED\", \"cdn-status\": \"200\", \"connection\": \"keep-ali… (1598 chars)",
 "validators": "{\"last-modified\": \"Mon, 21 Sep 2026 08:56:07 GMT\"}",
 "item_count": "23",
 "retrieved_at": "2026-09-21 11:00:47.354902+02:00"
}
```

## `public.relationship_observations`

base table, 1259768 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `relationship_id` | uuid | no |
| `run_id` | uuid | no |
| `carrier_document_id` | uuid | no |
| `is_present` | boolean | no |
| `evidence_sha256` | character varying | yes |
| `details` | jsonb | no |
| `observed_at` | timestamp with time zone | no |

- FK: `FOREIGN KEY (carrier_document_id) REFERENCES source_documents(id) ON DELETE CASCADE`
- FK: `FOREIGN KEY (relationship_id) REFERENCES instrument_relationships(id) ON DELETE CASCADE`
- FK: `FOREIGN KEY (run_id) REFERENCES monitor_runs(id) ON DELETE CASCADE`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (relationship_id, run_id, carrier_document_id)`
- index: `CREATE UNIQUE INDEX uq_relationship_observation ON public.relationship_observations USING btree (relationship_id, run_id, carrier_document_id)`
- index: `CREATE UNIQUE INDEX relationship_observations_pkey ON public.relationship_observations USING btree (id)`


Example row 1:
```json
{
 "id": "57f6f1ff-b80b-4019-9829-98d6b8b5de3b",
 "relationship_id": "c9448a89-53e0-4fc7-a519-40a6c9f00fd0",
 "run_id": "5ab81ae0-fe7e-4046-90ee-4fc013937410",
 "carrier_document_id": "4e9997e8-a413-43ea-8f90-6f97981f9808",
 "is_present": "True",
 "evidence_sha256": "889edd830bee830848516a30d13fdad0ad65a6dbb1ee68e6e1bfdf449b32d5ee",
 "details": "{\"raw_element\": \"RESOURCE_LEGAL_CONSOLIDATED_BY_ACT_CONSOLIDATED\", \"raw_predicate\": \"http://publications.europa.eu/ontology/cdm#resource_legal_consolidated_by_act_consolidated\"}",
 "observed_at": "2026-09-08 10:51:29.099223+02:00"
}
```
Example row 2:
```json
{
 "id": "4418dd3b-1b7e-4c65-bb8b-c040c16c4b9c",
 "relationship_id": "c7e3bfc9-797d-4866-866c-b43e89bfd5b0",
 "run_id": "5ab81ae0-fe7e-4046-90ee-4fc013937410",
 "carrier_document_id": "4e9997e8-a413-43ea-8f90-6f97981f9808",
 "is_present": "True",
 "evidence_sha256": "3c0f0e282c4e04698fd99a001b9daee65d5faf81829ef1c8d631cb45cf774df4",
 "details": "{\"raw_element\": \"RESOURCE_LEGAL_REPEALS_RESOURCE_LEGAL\", \"raw_predicate\": \"http://publications.europa.eu/ontology/cdm#resource_legal_repeals_resource_legal\"}",
 "observed_at": "2026-09-08 10:51:29.109847+02:00"
}
```

## `public.relationship_provision_changes`

base table, 101024 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `relationship_id` | uuid | no |
| `ordinal` | integer | no |
| `operation` | character varying | no |
| `target_provision` | text | yes |
| `target_element_id` | uuid | yes |
| `resolution_status` | character varying | no |
| `raw_annotation` | jsonb | no |
| `raw_operation_code` | character varying | yes |
| `target_locator_type` | character varying | yes |
| `target_locator_number` | character varying | yes |
| `effective_from` | date | yes |
| `effective_to` | date | yes |
| `change_key` | character varying | yes |

- FK: `FOREIGN KEY (target_element_id) REFERENCES legal_elements(id) ON DELETE SET NULL`
- FK: `FOREIGN KEY (relationship_id) REFERENCES instrument_relationships(id) ON DELETE CASCADE`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (relationship_id, ordinal)`
- index: `CREATE UNIQUE INDEX uq_relationship_provision_change ON public.relationship_provision_changes USING btree (relationship_id, ordinal)`
- index: `CREATE INDEX ix_provision_changes_target_element ON public.relationship_provision_changes USING btree (target_element_id, operation)`
- index: `CREATE UNIQUE INDEX uq_provision_changes_change_key ON public.relationship_provision_changes USING btree (relationship_id, change_key) WHERE (change_key IS NOT NULL)`
- index: `CREATE UNIQUE INDEX relationship_provision_changes_pkey ON public.relationship_provision_changes USING btree (id)`

Most common `operation`: `OTHER` (41974), `REPLACE` (36481), `ADD` (16159), `EDIT` (2638), `DELETE` (2050), `REPEAL` (1026), `REPLACE_ANNEX` (489), `RENUMBERS` (207)

Example row 1:
```json
{
 "id": "c77c1e19-f54a-4683-8054-cd1ab730e143",
 "relationship_id": "940d5455-4d51-4814-9f63-8f4d20d107eb",
 "ordinal": "1",
 "operation": "EDIT",
 "target_provision": "§ 17a Absatz 1 Satz 2",
 "target_element_id": null,
 "resolution_status": "UNRESOLVED_ELEMENT",
 "raw_annotation": "{\"evidence\": {\"article\": \"7\", \"resolution\": {\"method\": \"EXACT_ALIAS\", \"matched\": \"Krankenhausfinanzierungsgesetzes\"}, \"postcondition\": null, \"effective_rule\": {\"scope\": {\"item\": null, \"article\": \"7\", \"operation\": \"articl… (1647 chars)",
 "raw_operation_code": "EDIT",
 "target_locator_type": "PROVISION",
 "target_locator_number": "17a",
 "effective_from": null,
 "effective_to": null,
 "change_key": "f220d194402160bb26c8a371b20de2cc6e9c6ded83e4dd5ef9cce88137b2d3f6"
}
```
Example row 2:
```json
{
 "id": "64dc0416-9dce-4184-9a8d-1ad792c79060",
 "relationship_id": "eb1817d9-077d-4ac9-847e-757918d16007",
 "ordinal": "1",
 "operation": "OTHER",
 "target_provision": "Article 1",
 "target_element_id": "6de08dc8-614b-42ea-a792-171f35033b9c",
 "resolution_status": "RESOLVED_EXACT",
 "raw_annotation": "{\"ROLE2\": \"{C|http://publications.europa.eu/resource/authority/fd_375/C}\", \"BUILD_INFO\": \"cdm:CDM_2.1.7 tdm:1523 xslt:3945 saxon:9.0.0.1J JVM:1.6.0_29 metaconvJar:1.2.0 builddate:24/06/2014 17:44:40\", \"START_OF_VALIDITY\"… (367 chars)",
 "raw_operation_code": "C",
 "target_locator_type": "ARTICLE",
 "target_locator_number": "1",
 "effective_from": "1981-01-01",
 "effective_to": null,
 "change_key": "fed1a730b9ec2d1470567e0f266a77439bbf8a21cc8667042adf14ae68439598"
}
```

## `public.review_items`

base table, 58266 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `fingerprint` | character varying | no |
| `queue` | character varying | no |
| `subject_type` | character varying | no |
| `subject_id` | character varying | no |
| `status` | character varying | no |
| `priority` | integer | no |
| `due_at` | timestamp with time zone | yes |
| `escalated_at` | timestamp with time zone | yes |
| `details` | jsonb | no |
| `created_at` | timestamp with time zone | no |

- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (fingerprint)`
- index: `CREATE UNIQUE INDEX review_items_pkey ON public.review_items USING btree (id)`
- index: `CREATE UNIQUE INDEX review_items_fingerprint_key ON public.review_items USING btree (fingerprint)`
- index: `CREATE INDEX ix_review_items_queue ON public.review_items USING btree (queue, status, due_at)`

## `public.run_documents`

base table, 113482 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `run_id` | uuid | no |
| `source_document_id` | uuid | no |
| `discovery_role` | character varying | no |
| `base_celex` | character varying | yes |
| `expected_scope` | character varying | yes |
| `primary_decision` | character varying | no |
| `decision_stage` | character varying | no |
| `decision_reason` | text | no |
| `filter_evidence` | jsonb | no |
| `filter_signals` | jsonb | no |
| `full_text_requested` | boolean | no |
| `fetch_status` | character varying | no |
| `processed_at` | timestamp with time zone | no |
| `source_notification_id` | uuid | yes |
| `relationship_protected` | boolean | no |
| `filter_config_sha256` | character varying | yes |

- FK: `FOREIGN KEY (run_id) REFERENCES monitor_runs(id) ON DELETE CASCADE`
- FK: `FOREIGN KEY (source_document_id) REFERENCES source_documents(id) ON DELETE CASCADE`
- FK: `FOREIGN KEY (source_notification_id) REFERENCES source_notifications(id) ON DELETE SET NULL`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (run_id, source_document_id)`
- index: `CREATE UNIQUE INDEX uq_run_document ON public.run_documents USING btree (run_id, source_document_id)`
- index: `CREATE UNIQUE INDEX run_documents_pkey ON public.run_documents USING btree (id)`
- index: `CREATE INDEX ix_run_documents_decision ON public.run_documents USING btree (run_id, primary_decision)`


Example row 1:
```json
{
 "id": "d3b1fab1-09a8-4f04-a324-b479845e253b",
 "run_id": "5ab81ae0-fe7e-4046-90ee-4fc013937410",
 "source_document_id": "4e9997e8-a413-43ea-8f90-6f97981f9808",
 "discovery_role": "DEMO_BASE",
 "base_celex": "32024R3110",
 "expected_scope": "RELEVANT",
 "primary_decision": "KEEP",
 "decision_stage": "PROTECTED_FROM_NEGATIVE_BLOCK",
 "decision_reason": "A configured protect rule matched before negative title rules were applied.",
 "filter_evidence": "[{\"kind\": \"VETO\", \"field\": \"title\", \"rule_id\": \"global_positive_veto\", \"language\": \"EN\", \"rule_set\": \"Legal Radar conservative negative-only primary filter\", \"matched_text\": \"construction products\"}, {\"kind\": \"VETO\", \"fi… (339 chars)",
 "filter_signals": "[{\"kind\": \"SUPPORTING_SIGNAL\", \"field\": \"title\", \"rule_id\": \"landwirtschaft_fischerei:never_use_alone\", \"language\": \"DE\", \"rule_set\": \"Legal Radar conservative negative-only primary filter\", \"matched_text\": \"vermarktung\"… (442 chars)",
 "full_text_requested": "False",
 "fetch_status": "METADATA_ONLY",
 "processed_at": "2026-09-08 10:51:29.475136+02:00",
 "source_notification_id": null,
 "relationship_protected": "False",
 "filter_config_sha256": "3dd54c271de234b80094eae739133eb227f9d99e92189024c9b47d223cb60169"
}
```
Example row 2:
```json
{
 "id": "117d9205-d144-4f75-9b69-40e6ca7480dd",
 "run_id": "5ab81ae0-fe7e-4046-90ee-4fc013937410",
 "source_document_id": "4984cf1d-9084-4783-8f3b-807eca6d82df",
 "discovery_role": "DEMO_BASE",
 "base_celex": "32001R1936",
 "expected_scope": "IRRELEVANT",
 "primary_decision": "BLOCK",
 "decision_stage": "NEGATIVE_TITLE_RULES",
 "decision_reason": "A conservative hard negative title rule matched.",
 "filter_evidence": "[{\"kind\": \"HARD_BLOCK\", \"field\": \"title\", \"rule_id\": \"landwirtschaft_fischerei:keyword_title_only\", \"language\": \"EN\", \"rule_set\": \"Legal Radar conservative negative-only primary filter\", \"matched_text\": \"fish\"}]",
 "filter_signals": "[]",
 "full_text_requested": "False",
 "fetch_status": "BLOCKED_NO_CONTENT",
 "processed_at": "2026-09-08 10:51:34.908435+02:00",
 "source_notification_id": null,
 "relationship_protected": "False",
 "filter_config_sha256": "3dd54c271de234b80094eae739133eb227f9d99e92189024c9b47d223cb60169"
}
```

## `public.run_reports`

base table, 31 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `execution_id` | uuid | no |
| `parent_execution_id` | uuid | yes |
| `run_id` | uuid | yes |
| `report_type` | character varying | no |
| `source` | character varying | no |
| `operation` | character varying | no |
| `status` | character varying | no |
| `schema_version` | character varying | no |
| `report` | jsonb | no |
| `report_sha256` | character varying | no |
| `export_path` | text | yes |
| `needs_attention` | boolean | no |
| `created_at` | timestamp with time zone | no |

- FK: `FOREIGN KEY (execution_id) REFERENCES agent_executions(id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (run_id) REFERENCES monitor_runs(id) ON DELETE SET NULL`
- FK: `FOREIGN KEY (parent_execution_id) REFERENCES agent_executions(id) ON DELETE RESTRICT`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (execution_id, report_type, source)`
- index: `CREATE INDEX ix_run_report_status ON public.run_reports USING btree (status)`
- index: `CREATE INDEX ix_run_report_parent ON public.run_reports USING btree (parent_execution_id)`
- index: `CREATE UNIQUE INDEX uq_run_report_execution ON public.run_reports USING btree (execution_id, report_type, source)`
- index: `CREATE INDEX ix_run_report_source_time ON public.run_reports USING btree (source, created_at)`
- index: `CREATE UNIQUE INDEX run_reports_pkey ON public.run_reports USING btree (id)`

Most common `source`: `ALL` (16), `BAUA` (11), `DGUV` (2), `BMUKN` (1), `BMAS` (1)
Most common `operation`: `MONITOR_DAILY` (16), `MONITOR` (15)
Most common `status`: `SUCCESS` (16), `PARTIAL` (12), `FAILED` (3)

Example row 1:
```json
{
 "id": "c1bfb332-45d1-43ef-956c-f8260ebd969e",
 "execution_id": "5679d19c-0e6c-4f8d-906f-a4ac1ed22966",
 "parent_execution_id": "1940cd1e-8428-4f62-a7c2-a8d20398e5f9",
 "run_id": "f831a881-5fb8-40bd-b5a0-57797d332b10",
 "report_type": "SOURCE",
 "source": "BAUA",
 "operation": "MONITOR",
 "status": "FAILED",
 "schema_version": "legal-radar-run-report/1.0",
 "report": "{\"errors\": {\"count\": 1, \"items\": [{\"stage\": \"DISCOVERY\", \"message\": \"BAuA browser verification did not finish within 90 seconds. Complete any check in the Legal Radar browser window, then retry.\", \"evidence\": {}, \"retrya… (3579 chars)",
 "report_sha256": "5b78a172c9c7e8cd7b0b446f07c91147400d0e6673588d9ff0cf9c97d847aa5a",
 "export_path": "D:\\Desktop\\Work\\Project\\legal_radar_backend\\reports\\baua\\baua-monitor-20261022T083136Z-5679d19c.json",
 "needs_attention": "True",
 "created_at": "2026-10-22 10:33:47.730032+02:00"
}
```
Example row 2:
```json
{
 "id": "b7d6d965-1b1c-4c1d-8b62-9793a1f2acf2",
 "execution_id": "1940cd1e-8428-4f62-a7c2-a8d20398e5f9",
 "parent_execution_id": null,
 "run_id": null,
 "report_type": "UNIFIED",
 "source": "ALL",
 "operation": "MONITOR_DAILY",
 "status": "FAILED",
 "schema_version": "legal-radar-run-report/1.0",
 "report": "{\"errors\": {\"count\": 1, \"by_source\": {\"BAUA\": 1}}, \"review\": {\"by_source\": {\"BAUA\": true}, \"needs_attention\": true}, \"results\": {\"sources\": [{\"counts\": {}, \"source\": \"BAUA\", \"status\": \"FAILED\", \"reports\": [{\"source\": \"BA… (3192 chars)",
 "report_sha256": "a61e8892d792224c8f36e59a972f168861c38110e852e5b4327ea7d4d19d067a",
 "export_path": "D:\\Desktop\\Work\\Project\\legal_radar_backend\\reports\\unified\\unified-monitor_daily-20261022T083135Z-1940cd1e.json",
 "needs_attention": "True",
 "created_at": "2026-10-22 10:33:47.753052+02:00"
}
```

## `public.source_cursors`

base table, 16 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `source` | character varying | no |
| `cursor_value` | text | yes |
| `watermark_at` | timestamp with time zone | yes |
| `state` | jsonb | no |
| `updated_at` | timestamp with time zone | no |

- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (source)`
- index: `CREATE UNIQUE INDEX source_cursors_pkey ON public.source_cursors USING btree (id)`
- index: `CREATE UNIQUE INDEX source_cursors_source_key ON public.source_cursors USING btree (source)`

Most common `source`: `REG:DIP:VORGANG` (1), `REG:BNETZA:BK6_CURRENT` (1), `REG:DGUV:REGELWERK` (1), `BGBL_RSS_1` (1), `EUR_LEX_BASELINE` (1), `REG:BAUA:RSS` (1), `EUR_LEX` (1), `REG:BMAS:LEGISLATIVE_PROJECTS` (1), `REG:BUNDESRAT:PLENUM_RSS` (1), `GII_TOC` (1), `REG:DIBT:NOTICES` (1), `REG:BMUKN:LEGAL_DOCUMENTS` (1), `REG:BAUA:ASR_TRBS` (1), `GII_SWEEP` (1), `BGBL_SITEMAP` (1)

Example row 1:
```json
{
 "id": "fa92649d-58c7-4ae2-a187-3799c66b6bba",
 "source": "BGBL_RSS_2",
 "cursor_value": "f242f090e64fff2b6e41232f9fed5b562fe1177ced6d4a8c5d174289aa0fce07",
 "watermark_at": "2026-10-12 10:48:11.900566+02:00",
 "state": "{\"entries\": 500, \"coverage\": \"FINITE_FEED\"}",
 "updated_at": "2026-10-12 10:48:11.900566+02:00"
}
```
Example row 2:
```json
{
 "id": "445ad7f3-82b4-4e66-b01a-79cde8c791e4",
 "source": "GII_TOC",
 "cursor_value": "5b7c36d131ceeac6a56851e4157aa7acae75bac3d4ccf6e33ab02641390cc955",
 "watermark_at": "2026-10-12 10:56:27.908677+02:00",
 "state": "{\"entries\": 6139, \"seen_at\": \"2026-10-12T08:56:27.906672+00:00\"}",
 "updated_at": "2026-10-12 10:56:27.908677+02:00"
}
```

## `public.source_documents`

base table, 256716 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `source` | character varying | no |
| `external_id` | character varying | no |
| `work_uri` | text | yes |
| `document_kind` | character varying | yes |
| `resource_type` | character varying | yes |
| `title_en` | text | yes |
| `title_de` | text | yes |
| `canonical_title` | text | yes |
| `publication_date` | date | yes |
| `document_date` | date | yes |
| `in_force` | boolean | yes |
| `english_available` | boolean | yes |
| `metadata_url` | text | yes |
| `information_url` | text | yes |
| `metadata_json` | jsonb | no |
| `metadata_sha256` | character varying | yes |
| `first_seen_at` | timestamp with time zone | no |
| `last_seen_at` | timestamp with time zone | no |
| `cellar_id` | character varying | yes |
| `eli_uri` | text | yes |
| `oj_id` | text | yes |
| `lifecycle_status` | character varying | yes |
| `date_entry_into_force` | date | yes |
| `date_application` | date | yes |
| `date_end_validity` | date | yes |
| `source_modified_at` | timestamp with time zone | yes |
| `deleted_at` | timestamp with time zone | yes |
| `last_reconciled_at` | timestamp with time zone | yes |
| `scope_status` | character varying | yes |

- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (source, external_id)`
- index: `CREATE INDEX ix_source_documents_scope ON public.source_documents USING btree (source, in_force, scope_status)`
- index: `CREATE INDEX ix_source_documents_cellar_id ON public.source_documents USING btree (cellar_id)`
- index: `CREATE INDEX ix_source_documents_metadata_gin ON public.source_documents USING gin (metadata_json)`
- index: `CREATE INDEX ix_source_documents_external_id ON public.source_documents USING btree (external_id)`
- index: `CREATE UNIQUE INDEX uq_source_document_identity ON public.source_documents USING btree (source, external_id)`
- index: `CREATE UNIQUE INDEX source_documents_pkey ON public.source_documents USING btree (id)`

Most common `source`: `EUR_LEX` (239593), `GII` (6146), `DIP` (4126), `RECHT_BUND` (2953), `DGUV` (2674), `BMUKN` (628), `BMAS` (396), `BAUA` (71), `DIBT` (52), `BUNDESRAT` (39), `BNETZA` (38)
Most common `resource_type`: `None` (228159), `BUNDESRECHT` (6108), `LEGISLATIVE_PROCEDURE` (4126), `DEC` (3366), `REG` (2694), `REG_IMPL` (2692), `ACCIDENT_PREVENTION_RULE` (2674), `DEC_ENTSCHEID` (1982), `DEC_IMPL` (1410), `REG_DEL` (1082), `DIR` (783), `MINISTRY_LEGAL_DOCUMENT` (750), `MINISTRY_DRAFT` (274), `CONS_TEXT` (177), `DIR_DEL` (119)
Most common `in_force`: `None` (236455), `true` (20249), `false` (12)

Example row 1:
```json
{
 "id": "3bdcbc7c-088d-453c-8524-a831124886dd",
 "source": "EUR_LEX",
 "external_id": "31979D0195",
 "work_uri": null,
 "document_kind": "ORIGINAL_ACT",
 "resource_type": null,
 "title_en": "79/195/EEC: Council Decision of 21 December 1978 concerning the conclusion of the Agreement between the European Economic Community, the Member States and the International Development Association",
 "title_de": "79/195/EWG: Beschluß des Rates vom 21. Dezember 1978 über den Abschluß des Abkommens zwischen der Europäischen Wirtschaftsgemeinschaft, deren Mitgliedstaaten und der Internationalen Entwicklungsorganisation",
 "canonical_title": "79/195/EEC: Council Decision of 21 December 1978 concerning the conclusion of the Agreement between the European Economic Community, the Member States and the International Development Association",
 "publication_date": null,
 "document_date": null,
 "in_force": null,
 "english_available": null,
 "metadata_url": null,
 "information_url": null,
 "metadata_json": "{}",
 "metadata_sha256": null,
 "first_seen_at": "2026-09-08 11:41:03.351059+02:00",
 "last_seen_at": "2026-09-08 14:19:59.599514+02:00",
 "cellar_id": null,
 "eli_uri": null,
 "oj_id": null,
 "lifecycle_status": null,
 "date_entry_into_force": null,
 "date_application": null,
 "date_end_validity": null,
 "source_modified_at": null,
 "deleted_at": null,
 "last_reconciled_at": null,
 "scope_status": null
}
```
Example row 2:
```json
{
 "id": "6e747aec-a851-4e66-89a6-b96ea78c8888",
 "source": "EUR_LEX",
 "external_id": "31976D0688",
 "work_uri": null,
 "document_kind": "ORIGINAL_ACT",
 "resource_type": null,
 "title_en": "76/688/EEC: Commission Decision of 30 June 1976 authorizing the French Republic to restrict the marketing of seed of certain varieties of agricultural plant species (Only the French text is authentic)",
 "title_de": "76/688/EWG: Entscheidung der Kommission vom 30. Juni 1976 zur Ermächtigung der Französischen Republik, den Verkehr mit Saagut einiger Sorten landwirtschaftlicher Pflanzenarten zu beschränken (Nur der französische Text is… (234 chars)",
 "canonical_title": "76/688/EEC: Commission Decision of 30 June 1976 authorizing the French Republic to restrict the marketing of seed of certain varieties of agricultural plant species (Only the French text is authentic)",
 "publication_date": null,
 "document_date": null,
 "in_force": null,
 "english_available": null,
 "metadata_url": null,
 "information_url": null,
 "metadata_json": "{}",
 "metadata_sha256": null,
 "first_seen_at": "2026-09-08 11:37:48.197953+02:00",
 "last_seen_at": "2026-09-08 14:15:36.774204+02:00",
 "cellar_id": null,
 "eli_uri": null,
 "oj_id": null,
 "lifecycle_status": null,
 "date_entry_into_force": null,
 "date_application": null,
 "date_end_validity": null,
 "source_modified_at": null,
 "deleted_at": null,
 "last_reconciled_at": null,
 "scope_status": null
}
```

## `public.source_notifications`

base table, 8670 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `source` | character varying | no |
| `notification_key` | character varying | no |
| `source_uri` | text | no |
| `work_uri` | text | yes |
| `celex_candidates` | jsonb | no |
| `event_type` | character varying | yes |
| `wemi_class` | character varying | yes |
| `published_at` | timestamp with time zone | yes |
| `source_updated_at` | timestamp with time zone | yes |
| `first_seen_run_id` | uuid | yes |
| `raw_payload` | jsonb | no |
| `created_at` | timestamp with time zone | no |

- FK: `FOREIGN KEY (first_seen_run_id) REFERENCES monitor_runs(id) ON DELETE SET NULL`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (source, notification_key)`
- index: `CREATE UNIQUE INDEX uq_source_notification ON public.source_notifications USING btree (source, notification_key)`
- index: `CREATE UNIQUE INDEX source_notifications_pkey ON public.source_notifications USING btree (id)`
- index: `CREATE INDEX ix_source_notifications_published ON public.source_notifications USING btree (source, published_at)`

Most common `source`: `EUR_LEX` (8670)

Example row 1:
```json
{
 "id": "e04335ad-1888-40f7-bd9d-1d073677a3cf",
 "source": "EUR_LEX",
 "notification_key": "2ab3cad4183ec499f4c5d5cc973e310964c2762c0a23ba19d41b8bf1e4ca03f5",
 "source_uri": "cellar:7b85e698-ab1d-11f1-b9e5-01aa75ed71a1_2026-09-08T02:37:41.830+02:00",
 "work_uri": "https://publications.europa.eu/resource/cellar/7b85e698-ab1d-11f1-b9e5-01aa75ed71a1",
 "celex_candidates": "[\"32026R1848R(02)\"]",
 "event_type": "create",
 "wemi_class": "WORK",
 "published_at": "2026-09-08 02:37:41.830000+02:00",
 "source_updated_at": "2026-09-08 02:37:41.830000+02:00",
 "first_seen_run_id": "b8a1a7a3-5122-452c-8c69-40329a36136d",
 "raw_payload": "{\"links\": [], \"fields\": {\"date\": [\"2026-09-08T02:37:41.830+02:00\"], \"guid\": [\"cellar:7b85e698-ab1d-11f1-b9e5-01aa75ed71a1_2026-09-08T02:37:41.830+02:00\"], \"item\": [\"$item.title cellar:7b85e698-ab1d-11f1-b9e5-01aa75ed71a1… (1498 chars)",
 "created_at": "2026-09-11 10:46:24.824666+02:00"
}
```
Example row 2:
```json
{
 "id": "aa662e08-ce55-4a18-9bb2-488ec3d5b66a",
 "source": "EUR_LEX",
 "notification_key": "7430c82793dbdce508e861142cff4a9e3bf3c446778cbd0387c598ec840654b8",
 "source_uri": "cellar:74c45120-ab1d-11f1-b9e5-01aa75ed71a1_2026-09-08T02:39:12.638+02:00",
 "work_uri": "https://publications.europa.eu/resource/cellar/74c45120-ab1d-11f1-b9e5-01aa75ed71a1",
 "celex_candidates": "[\"32026D2033\"]",
 "event_type": "create",
 "wemi_class": "WORK",
 "published_at": "2026-09-08 02:39:12.638000+02:00",
 "source_updated_at": "2026-09-08 02:39:12.638000+02:00",
 "first_seen_run_id": "b8a1a7a3-5122-452c-8c69-40329a36136d",
 "raw_payload": "{\"links\": [], \"fields\": {\"date\": [\"2026-09-08T02:39:12.638+02:00\"], \"guid\": [\"cellar:74c45120-ab1d-11f1-b9e5-01aa75ed71a1_2026-09-08T02:39:12.638+02:00\"], \"item\": [\"$item.title cellar:74c45120-ab1d-11f1-b9e5-01aa75ed71a1… (1531 chars)",
 "created_at": "2026-09-11 10:46:24.825894+02:00"
}
```

## `public.source_snapshots`

base table, 95844 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `source_document_id` | uuid | no |
| `run_id` | uuid | no |
| `snapshot_kind` | character varying | no |
| `language` | character varying | yes |
| `media_type` | character varying | no |
| `source_url` | text | yes |
| `sha256` | character varying | no |
| `payload` | bytea | no |
| `fetched_at` | timestamp with time zone | no |

- FK: `FOREIGN KEY (source_document_id) REFERENCES source_documents(id) ON DELETE CASCADE`
- FK: `FOREIGN KEY (run_id) REFERENCES monitor_runs(id) ON DELETE CASCADE`
- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (source_document_id, snapshot_kind, sha256)`
- index: `CREATE UNIQUE INDEX uq_source_snapshot ON public.source_snapshots USING btree (source_document_id, snapshot_kind, sha256)`
- index: `CREATE UNIQUE INDEX source_snapshots_pkey ON public.source_snapshots USING btree (id)`

Most common `language`: `DE` (67167), `EN` (28577), `None` (100)

Example row 1:
```json
{
 "id": "861cf872-23c4-4cb1-878a-ed823ba8810b",
 "source_document_id": "4e9997e8-a413-43ea-8f90-6f97981f9808",
 "run_id": "5ab81ae0-fe7e-4046-90ee-4fc013937410",
 "snapshot_kind": "NOTICE_EN",
 "language": "EN",
 "media_type": "application/xml",
 "source_url": "https://publications.europa.eu/resource/celex/32024R3110?language=eng",
 "sha256": "938fa254b9d9a71a038e31175f589ad9e2e2d00d1d62f5670700b0d82dc23ac1",
 "payload": "<1358893 bytes>",
 "fetched_at": "2026-09-08 10:51:29.044016+02:00"
}
```
Example row 2:
```json
{
 "id": "9aac30db-e944-48ea-bf25-2066891bd917",
 "source_document_id": "4e9997e8-a413-43ea-8f90-6f97981f9808",
 "run_id": "5ab81ae0-fe7e-4046-90ee-4fc013937410",
 "snapshot_kind": "NOTICE_DE",
 "language": "DE",
 "media_type": "application/xml",
 "source_url": "https://publications.europa.eu/resource/celex/32024R3110?language=deu",
 "sha256": "eff67ce1b94aecf1081142644dad8eb8e59319d24b52542a9d13aab30c5a4ba2",
 "payload": "<1356783 bytes>",
 "fetched_at": "2026-09-08 10:51:29.066246+02:00"
}
```

## `public.watchlist_entries`

base table, 0 rows

| Column | Type | Null |
|---|---|---|
| `id` | uuid | no |
| `source` | character varying | no |
| `external_id` | character varying | no |
| `active` | boolean | no |
| `scope_label` | character varying | yes |
| `imported_from` | character varying | yes |
| `details` | jsonb | no |
| `created_at` | timestamp with time zone | no |

- PK: `PRIMARY KEY (id)`
- UNIQUE: `UNIQUE (source, external_id)`
- index: `CREATE UNIQUE INDEX watchlist_entries_pkey ON public.watchlist_entries USING btree (id)`
- index: `CREATE UNIQUE INDEX uq_watchlist_entry ON public.watchlist_entries USING btree (source, external_id)`
