"""Only event names and account IDs; never document names or financial data."""
import logging

from app.application.conversion.identity import IdentityContext


def office_event(access, owner, event):
    try:
        access.record_product_event(identity=IdentityContext('user', owner, 0), event_type=event, page_path='/documents')
    except Exception:
        logging.getLogger(__name__).warning('office_telemetry_unavailable event=%s', event)
