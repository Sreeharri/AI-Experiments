-- 10 named structural inspection candidates, not relevance labels. One stored version per CELEX, with English
-- preferred and German as fallback. Eligibility requires a nonblank article
-- or a nonblank descendant within an article.
WITH sample AS MATERIALIZED (
    SELECT
        i.celex,
        to_jsonb(i) AS instrument,
        to_jsonb(d) AS document,
        d.id AS document_id,
        v.id AS version_id,
        to_jsonb(v) AS version
    FROM legal_instruments AS i
    JOIN source_documents AS d ON d.id = i.source_document_id
    JOIN LATERAL (
        SELECT iv.*
        FROM instrument_versions AS iv
        WHERE iv.instrument_id = i.id
          AND iv.language IN ('EN', 'DE')
          AND EXISTS (
              SELECT 1
              FROM legal_elements AS a
              WHERE a.version_id = iv.id
                AND a.element_type = 'ARTICLE'
                AND (
                    btrim(a.body_text) <> ''
                    OR EXISTS (
                        SELECT 1
                        FROM legal_elements AS child
                        WHERE child.version_id = iv.id
                          AND starts_with(child.path, a.path || '/')
                          AND btrim(child.body_text) <> ''
                    )
                )
          )
        ORDER BY (iv.language = 'EN') DESC, iv.is_preferred DESC,
                 iv.is_current DESC, iv.version_date DESC NULLS LAST,
                 iv.fetched_at DESC, iv.id
        LIMIT 1
    ) AS v ON true
    WHERE d.source = 'EUR_LEX'
      AND d.deleted_at IS NULL
      AND i.celex IN ('32024R3110', '32023R1230', '32024R1689', '32006R1907', '32008R1272', '32010L0075', '32008L0098', '32019R0943', '32023R1542', '32023R2053')
    ORDER BY md5(i.celex), i.celex
    LIMIT 10
)
SELECT s.celex, jsonb_build_object(
    'celex', s.celex,
    'document', s.document,
    'instrument', s.instrument,
    'version', s.version,
    'recital_set', (
        SELECT to_jsonb(r)
        FROM recital_sets AS r
        WHERE r.version_id = s.version_id
    ),
    'elements', COALESCE((
        SELECT jsonb_agg(to_jsonb(e) ORDER BY e.ordinal, e.path)
        FROM legal_elements AS e
        WHERE e.version_id = s.version_id
    ), '[]'::jsonb),
    'outgoing_relationships', COALESCE((
        SELECT jsonb_agg(
            to_jsonb(rel) || jsonb_build_object(
                'target_external_id', target.external_id, 'target_source', target.source
            ) ORDER BY rel.relation_type, target.external_id, rel.id
        )
        FROM instrument_relationships AS rel
        JOIN source_documents AS target ON target.id = rel.target_document_id
        WHERE rel.source_document_id = s.document_id
    ), '[]'::jsonb),
    'incoming_relationships', COALESCE((
        SELECT jsonb_agg(
            to_jsonb(rel) || jsonb_build_object(
                'source_external_id', origin.external_id, 'source', origin.source
            ) ORDER BY rel.relation_type, origin.external_id, rel.id
        )
        FROM instrument_relationships AS rel
        JOIN source_documents AS origin ON origin.id = rel.source_document_id
        WHERE rel.target_document_id = s.document_id
    ), '[]'::jsonb)
) AS act
FROM sample AS s
ORDER BY s.celex;
