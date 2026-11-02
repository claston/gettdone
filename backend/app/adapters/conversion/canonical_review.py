"""Request-scoped adapter for the existing review contract (not wired into rollout).

The composition root must resolve scope from the authenticated, entitled PRO
request and original job. Do not cache this adapter across users or requests.
"""
from app.application.errors import AnalysisAccessDeniedError, AnalysisNotFoundError


class CanonicalReviewAdapter:
    def __init__(self, repository, scope):
        self.repository, self.scope = repository, scope

    def _scope(self, analysis_id):
        if analysis_id != self.scope.analysis_id:
            raise AnalysisNotFoundError
        return self.scope

    def assert_convert_owner(self, analysis_id, identity_type, identity_id):
        if identity_type != "user" or identity_id != self.scope.actor_id:
            raise AnalysisAccessDeniedError
        self.repository.read(self._scope(analysis_id))

    def get_convert_edit_operation(self, analysis_id, operation_id):
        return self.repository.confirm(self._scope(analysis_id), operation_id)

    def apply_convert_edits(self, analysis_id, edits, expected_updated_at=None, operation_id=None, **overrides):
        return self.repository.apply(self._scope(analysis_id), edits, expected_updated_at=expected_updated_at,
                                     operation_id=operation_id, channel="pro", **overrides)
