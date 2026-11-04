-- Read-only, coarse structural shortlist. Full text is checked by the existing
-- v6.3 evidence builder before any act enters either development set.
WITH shapes AS MATERIALIZED (
    SELECT version_id,
        count(*) FILTER (WHERE element_type = 'ARTICLE') AS articles,
        count(*) FILTER (WHERE element_type = 'CHAPTER') AS chapters,
        count(*) FILTER (WHERE element_type = 'ANNEX') AS annexes,
        count(*) FILTER (WHERE element_type = 'RECITAL') AS recitals
    FROM legal_elements
    WHERE element_type IN ('ARTICLE', 'CHAPTER', 'ANNEX', 'RECITAL')
    GROUP BY version_id
)
SELECT i.celex, v.id::text AS version_id, v.language,
       v.version_kind, v.version_date, v.effective_from,
       v.is_preferred, v.is_current, v.fetched_at,
       d.document_date, v.quality_status
FROM legal_instruments AS i
JOIN source_documents AS d ON d.id = i.source_document_id
JOIN instrument_versions AS v ON v.instrument_id = i.id
JOIN shapes AS s ON s.version_id = v.id
LEFT JOIN recital_sets AS r ON r.version_id = v.id
WHERE d.source = 'EUR_LEX' AND d.deleted_at IS NULL
  AND i.celex ~ '^3[0-9]{4}[RLD][0-9]{4}(\([0-9]{2}\))?$'
  AND NOT (i.celex = ANY(%(excluded)s::text[]))
  AND v.language IN ('EN', 'DE')
  AND (d.document_date IS NULL OR d.document_date <= %(as_of)s)
  AND (v.version_date IS NULL OR v.version_date <= %(as_of)s)
  -- An already published ORIGINAL may have a future commencement date.
  AND (v.version_kind <> 'CONSOLIDATED' OR v.effective_from IS NULL
       OR v.effective_from <= %(as_of)s)
  AND COALESCE(v.quality_status, '') <> 'REJECTED'
  AND (COALESCE(NULLIF(btrim(d.title_en), ''), NULLIF(btrim(d.title_de), ''),
                NULLIF(btrim(i.title), '')) IS NOT NULL)
  AND s.articles >= 9 AND s.chapters >= 1 AND s.annexes >= 1
  AND greatest(s.recitals, COALESCE(r.recital_count, 0)) >= 3
ORDER BY i.celex, (v.language = 'EN') DESC,
         v.is_preferred DESC, v.is_current DESC,
         v.version_date DESC NULLS LAST, v.fetched_at DESC, v.id;
