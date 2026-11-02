"""Controlled adoption of new PRO analyses, before exposing them to editors.

Runtime wiring/cohort activation belongs to OFX-70. This coordinator is never
called by Free or by a feature-flag fallback on already canonical documents.
"""
from app.application.canonical_movements import CanonicalRollout


class CanonicalIngestionService:
    def __init__(self, repository, legacy_storage, *, rollout: CanonicalRollout):
        self.repository, self.legacy_storage, self.rollout = repository, legacy_storage, rollout

    def ingest(self, scope, *, currency, product, new_analysis):
        if not self.rollout.allows(organization_id=scope.organization_id, product=product, new_analysis=new_analysis):
            raise ValueError("Canonical ingestion is disabled for this document")
        self.repository.authorize_source(scope)
        # Fence first. A crash here leaves a recoverable pending document; it
        # cannot silently return to legacy editing. SQL remains uncommitted until
        # the durable marker write succeeds. Resume with the same input/baseline.
        content = self.legacy_storage.fence_canonical_analysis(scope.analysis_id)
        return self.repository.ingest(scope, content, currency=currency, product=product,
                                      new_analysis=new_analysis, rollout=self.rollout)
