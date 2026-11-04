-- One complete stored version, including ALL legal-element and recital text.
-- Large source-document harvesting/relationship metadata is unnecessary for
-- the evidence builder. Retain source identity, titles, dates and parser data.
SELECT jsonb_build_object(
    'celex', i.celex,
    'document', jsonb_build_object(
        'id', d.id, 'external_id', d.external_id, 'source', d.source,
        'title_en', d.title_en, 'title_de', d.title_de,
        'document_date', d.document_date, 'document_kind', d.document_kind,
        'in_force', d.in_force, 'eli_uri', d.eli_uri, 'work_uri', d.work_uri),
    'instrument', to_jsonb(i),
    'version', to_jsonb(v) - 'quality_report',
    'recital_set', (SELECT to_jsonb(r) FROM recital_sets AS r WHERE r.version_id = v.id),
    'elements', COALESCE((
        SELECT jsonb_agg(to_jsonb(e) ORDER BY e.ordinal, e.path)
        FROM legal_elements AS e WHERE e.version_id = v.id
    ), '[]'::jsonb)
)
FROM instrument_versions AS v
JOIN legal_instruments AS i ON i.id = v.instrument_id
JOIN source_documents AS d ON d.id = i.source_document_id
WHERE v.id = %(version_id)s::uuid;
