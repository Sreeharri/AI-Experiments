-- Inventory every linked stored EUR-Lex instrument/version. No relevance,
-- language, date, status, chapter/article/annex/recital-count gate.
WITH shapes AS (
 SELECT version_id, count(*) AS element_count,
   count(*) FILTER (WHERE element_type='ARTICLE') AS article_count,
   count(*) FILTER (WHERE element_type='CHAPTER') AS chapter_count,
   count(*) FILTER (WHERE element_type='ANNEX') AS annex_count,
   count(*) FILTER (WHERE element_type='RECITAL') AS recital_count,
   COALESCE(sum(length(body_text)),0) AS text_characters
 FROM legal_elements GROUP BY version_id
), recitals AS (
 SELECT version_id, sum(recital_count) AS count FROM recital_sets GROUP BY version_id
)
SELECT i.id::text AS instrument_id, i.celex,
 COALESCE(NULLIF(d.title_en,''),NULLIF(d.title_de,''),i.title) AS title,
 d.id::text AS source_document_id, d.document_kind, d.document_date, d.in_force,
 to_jsonb(d)->'addressees' AS document_addressees,
 to_jsonb(i)->'addressees' AS instrument_addressees,
 to_jsonb(d)->'territorial_scope' AS territorial_scope,
 v.id::text AS version_id, v.language, v.version_kind, v.version_date,
 v.effective_from, v.is_preferred, v.is_current, v.fetched_at, v.quality_status,
 COALESCE(s.element_count,0) AS element_count,
 COALESCE(s.article_count,0) AS article_count,
 COALESCE(s.chapter_count,0) AS chapter_count,
 COALESCE(s.annex_count,0) AS annex_count,
 greatest(COALESCE(s.recital_count,0),COALESCE(r.count,0)) AS recital_count,
 COALESCE(s.text_characters,0) AS text_characters
FROM legal_instruments i
JOIN source_documents d ON d.id=i.source_document_id
LEFT JOIN instrument_versions v ON v.instrument_id=i.id
LEFT JOIN shapes s ON s.version_id=v.id
LEFT JOIN recitals r ON r.version_id=v.id
WHERE d.source='EUR_LEX' AND d.deleted_at IS NULL
ORDER BY i.celex,i.id,v.id;
