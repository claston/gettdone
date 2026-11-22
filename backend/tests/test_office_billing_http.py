import hashlib
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from openpyxl import Workbook

from app.application.conversion.document_conversion_pipeline import DocumentConversionPipeline
from app.application.conversion.uploaded_document import ingest_uploaded_document
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.office_plan import office_commercial
from app.application.report_service import ReportService
from app.application.storage_service import TempAnalysisStorage
from app.dependencies import get_access_control_service, get_conversion_batch_service
from app.main import app
from app.workers.conversion_lambda import ConversionLambdaProcessor
from tests.http_runtime import LoopbackClient
from tests.test_conversion_workspace_http_real_api import _run_pro_upload_http_server

pytestmark = pytest.mark.real_http


def test_structured_imports_complete_with_exhausted_document_allowance(tmp_path, monkeypatch):
    monkeypatch.setenv('PRO_OFFICE_ENABLED', 'true')
    originals = {'statement.csv': b'date,amount,description\n2026-04-01,10.00,Received\n',
        'statement.ofx': b'''OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>BRL<BANKACCTFROM><BANKID>001<ACCTID>1234<ACCTTYPE>CHECKING</BANKACCTFROM>
<BANKTRANLIST><DTSTART>20260401<DTEND>20260430<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20260401<TRNAMT>10.00<FITID>one<MEMO>Received</STMTTRN>
</BANKTRANLIST><LEDGERBAL><BALAMT>10.00<DTASOF>20260430</LEDGERBAL></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>'''}
    workbook = Workbook()
    workbook.active.append(['date', 'amount', 'description'])
    workbook.active.append(['2026-04-01', 10, 'Received'])
    stream = BytesIO()
    workbook.save(stream)
    originals['statement.xlsx'] = stream.getvalue()
    class Originals:
        def load(self, reference):
            return ingest_uploaded_document(reference.filename, originals[reference.filename])
    with _run_pro_upload_http_server(tmp_path) as (url, publisher, token):
        access = app.dependency_overrides[get_access_control_service]()
        before = access.resolve_identity(anonymous_fingerprint=None, user_token=token)
        admin = dict(access=access, actor_kind='system', actor_user_id=None)
        office_commercial(**admin, operation_id='configure', action='configure', values=dict(version=1,
            price_cents=1000, quota_limit=1, max_pages_per_file=100, max_pages_per_file_ocr=6, batch_max_files=12))
        office_commercial(**admin, operation_id='activate', action='activate', values=dict(user_id=before.identity_id,
            expires_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        identity = access.resolve_identity(anonymous_fingerprint=None, user_token=token)
        access.consume_quota(identity, consumed_units=1, idempotency_key='previous-pdf')
        assert access.get_remaining_quota(identity) == 0
        batches = app.dependency_overrides[get_conversion_batch_service]()
        storage = TempAnalysisStorage(tmp_path / 'reports')
        processor = ConversionLambdaProcessor(repository=batches.repository, document_store=Originals(),
            pipeline=DocumentConversionPipeline(report_service=ReportService(storage), access_control_service=access,
                processing_pipeline=build_default_conversion_pipeline(), analysis_repository=storage))
        with LoopbackClient(base_url=url, headers={'Authorization': f'Bearer {token}',
                'Origin': 'http://localhost:3000', 'X-OFX-Pro-Client': 'web'}) as client:
            mime = {'csv': 'text/csv', 'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    'ofx': 'application/x-ofx'}
            created = client.post('/api/pro/conversion-batches', headers={'Idempotency-Key': 'free-imports'}, json={'files': [
                {'filename': name, 'content_type': mime[name.rsplit('.', 1)[1]], 'size_bytes': len(raw),
                 'sha256_hex': hashlib.sha256(raw).hexdigest()} for name, raw in originals.items()]})
            assert created.status_code == 201, created.text
            batch = created.json()
            assert client.post(f"/api/pro/conversion-batches/{batch['batch_id']}/submit", json={}).status_code == 202
            for job in publisher.calls:
                processor.process(job_id=job, expected_batch_id=batch['batch_id'], trace_id='billing',
                                  receive_count=1, lease_owner='billing-worker')
            response = client.get(f"/api/pro/conversion-batches/{batch['batch_id']}")
            assert all(job['status'] == 'completed' for job in response.json()['jobs']), response.text
            assert access.get_remaining_quota(identity) == 0
            with access.db.connect() as conn:
                assert access.db.fetchone(conn, 'SELECT COUNT(*) AS n FROM conversion_quota_consumptions')['n'] == 1
            assert client.post('/admin/plans/office/configure', json={'operation_id': 'unauthorized', 'version': 2}).status_code == 401
        from tests.admin_setup import register_admin
        register_admin(access, name='Admin', email='admin-office@example.test', password='strong-password')
        with LoopbackClient(base_url=url) as admin_client:
            assert admin_client.post('/admin/auth/login',
                json={'email': 'admin-office@example.test', 'password': 'strong-password'}).status_code == 200
            configured = admin_client.post('/admin/plans/office/configure', json={'operation_id': 'admin-config',
                'version': 2, 'price_cents': 2000, 'quota_limit': 2})
            assert configured.status_code == 200, configured.text
            release = {'operation_id': 'admin-release', 'user_id': before.identity_id,
                       'expires_at': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}
            assert admin_client.post('/admin/plans/office/activate', json=release).status_code == 200
            assert access.resolve_identity(anonymous_fingerprint=None, user_token=token).quota_limit == 2
            assert admin_client.post('/admin/plans/office/cancel', json={'operation_id': 'admin-cancel',
                'user_id': before.identity_id}).status_code == 200
            assert not access.resolve_identity(anonymous_fingerprint=None, user_token=token).batch_conversion_enabled
