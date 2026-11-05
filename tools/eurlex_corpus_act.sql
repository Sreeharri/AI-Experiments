SELECT jsonb_build_object(
 'celex',i.celex,
 'document',jsonb_build_object(
   'id',d.id,'external_id',d.external_id,'source',d.source,
   'title_en',d.title_en,'title_de',d.title_de,'document_date',d.document_date,
   'document_kind',d.document_kind,'in_force',d.in_force,
   'eli_uri',d.eli_uri,'work_uri',d.work_uri,
   'full_text',to_jsonb(d)->'full_text','body_text',to_jsonb(d)->'body_text',
   'text_content',to_jsonb(d)->'text_content','content_text',to_jsonb(d)->'content_text',
   'plain_text',to_jsonb(d)->'plain_text'),
 'instrument',to_jsonb(i),
 'version',COALESCE(to_jsonb(v)-'quality_report',jsonb_build_object('id',NULL)),
 'recital_sets',COALESCE((SELECT jsonb_agg(to_jsonb(r)) FROM recital_sets r WHERE r.version_id=v.id),'[]'::jsonb),
 'elements',COALESCE((SELECT jsonb_agg(to_jsonb(e) ORDER BY e.ordinal,e.path)
                    FROM legal_elements e WHERE e.version_id=v.id),'[]'::jsonb)
)
FROM legal_instruments i
JOIN source_documents d ON d.id=i.source_document_id
LEFT JOIN instrument_versions v ON v.id=%(version_id)s::uuid AND v.instrument_id=i.id
WHERE i.id=%(instrument_id)s::uuid;
