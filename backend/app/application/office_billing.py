"""Billing policy for an explicitly configured office plan, never for Free."""

STRUCTURED_FORMATS = frozenset({'ofx', 'csv', 'xlsx'})


def office_billing(identity, product='free'):
    return (product == 'pro' and getattr(identity, 'identity_type', None) == 'user'
            and getattr(identity, 'batch_conversion_enabled', False)
            and getattr(identity, 'quota_mode', None) == 'document_conversion')


def imported_without_conversion(identity, file_type, product='free'):
    return office_billing(identity, product) and file_type in STRUCTURED_FORMATS
