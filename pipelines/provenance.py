"""Conservative, offline evidence comparison; never persists proposals."""
from datetime import datetime, timezone
from pipelines.validate_entities import normalize_url


def instant(value):
    if value is None:
        return None
    if isinstance(value,str):
        value=datetime.fromisoformat(value.replace("Z","+00:00"))
    return value.astimezone(timezone.utc).isoformat()


def safe_url(value):
    try:
        return normalize_url(value)
    except (ValueError, TypeError):
        return None


def classify(item, source, links, evidence):
    """Different prose is not a contradiction. Alternate URLs remain reviewable."""
    if len(links) > 1:
        return 'REVIEW', 'multiple_source_links'
    url = item.source_url
    reference = item.metadata.source_reference
    note = item.metadata.note
    link = links[0] if links else None
    children = [e for e in evidence if link and e['entity_source_id'] == link.get('id')]
    for row in children:
        same = (safe_url(row.get('source_url')) == url
                and row.get('source_reference') == reference and row.get('notes') == note
                and instant(row.get('observed_at')) == item.metadata.observed_at
                and row.get('metadata', {}) == item.metadata.model_dump(exclude={'note','source_reference','observed_at'}, exclude_none=True))
        if same:
            status = row.get('verification_status')
            if status == 'rejected':
                return 'CONFLICT', 'evidence_previously_rejected'
            if status == 'disputed':
                return 'REVIEW', 'evidence_disputed'
            return 'UNCHANGED', 'same_stored_evidence'
    if link and safe_url(link.get('source_url')) == url and link.get('notes') == note and reference is None and item.metadata.observed_at is None and not item.metadata.description and item.metadata.stars is None:
        return 'UNCHANGED', 'same_legacy_evidence'
    if reference and any(e.get('source_reference') == reference for e in children):
        return 'REVIEW', 'same_document_different_observation'
    if link and not safe_url(link.get('source_url')):
        return 'REVIEW', 'legacy_evidence_url_unresolved'
    known_urls = {safe_url(source.get('url'))}
    if link:
        known_urls.add(safe_url(link.get('source_url')))
    if url not in known_urls:
        return 'REVIEW', 'alternate_evidence_url_requires_review'
    return 'ADD', 'additional_compatible_evidence'
