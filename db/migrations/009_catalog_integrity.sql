-- Additive: duplicates abort the transaction; no data deduplication or deletion.
ALTER TABLE entity_sources
    ADD CONSTRAINT entity_sources_source_entity_unique
    UNIQUE (source_id, entity_type, entity_id);

ALTER TABLE entity_embeddings
    ADD CONSTRAINT entity_embeddings_entity_unique
    UNIQUE (entity_type, entity_id);
