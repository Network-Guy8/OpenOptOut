"""Offline integration tests; no real addresses or provider requests."""
import json
import os
import unittest
from datetime import timedelta
from unittest.mock import Mock, patch

os.environ.setdefault("SECRET_KEY", "breach-monitor-offline-tests-only-0123456789")
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ..models.database import Base, User, UserRole, FamilyMember, Identity, BreachMonitor, get_db
from ..core import breach_monitor as bm
from ..routers import breach_monitor as routes
from ..core.auth import get_current_user


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.factory()
        self.user = User(id=1, email="parent@example.invalid", full_name="Example Parent", role=UserRole.parent)
        other = User(id=2, email="other@example.invalid", full_name="Other Example", role=UserRole.parent)
        self.db.add_all([self.user, other]); self.db.flush()
        self.db.add_all([FamilyMember(id=1, user_id=1, full_name="Example Parent", age=40),
                         FamilyMember(id=2, user_id=2, full_name="Other Example", age=12)])
        self.db.flush()
        self.db.add_all([Identity(id=1, member_id=1, kind="email", value="one@example.invalid"),
                         Identity(id=2, member_id=2, kind="email", value="two@example.invalid")])
        self.db.commit()
        self.app = FastAPI(); self.app.include_router(routes.router)
        def database():
            yield self.db
        self.app.dependency_overrides[get_db] = database
        self.app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(self.app)

    def tearDown(self):
        self.db.close(); self.engine.dispose()

    def enable(self):
        bm.configure(self.db, 1, [1])
        return bm.claim(self.db, 1)

    def scan(self, token, query):
        bm.run_member(1, token, self.factory, "0" * 32, query)
        self.db.expire_all()
        return self.db.get(BreachMonitor, 1)

    def test_access_and_consent(self):
        self.assertEqual(self.client.get('/api/breach-monitor/2').status_code, 403)
        self.assertEqual(self.client.put('/api/breach-monitor/1', json={"identity_ids": [1]}).status_code, 400)
        self.assertEqual(self.client.put('/api/breach-monitor/1', json={"identity_ids": [2], "authorized": True}).status_code, 400)
        self.assertEqual(self.client.put('/api/breach-monitor/key', json={"api_key": "0"*32}).status_code, 403)

    def test_encrypted_success_and_duplicates(self):
        token = self.enable()
        self.assertIsNone(bm.claim(self.db, 1))
        row = self.scan(token, lambda email, key: [{"name": "ExampleBreach", "title": "Example breach", "date": "2020-01-01"}])
        self.assertIsNotNone(row.last_success_at)
        self.assertEqual(len(bm.unpack(row)["findings"]), 1)
        raw = self.db.execute(text('SELECT payload FROM breach_monitors')).scalar()
        self.assertNotIn("ExampleBreach", raw)
        self.assertNotIn('identity_ids', raw)
        from ..models.database import RemovalRequest
        self.assertEqual(self.db.query(RemovalRequest).count(), 0)

    def test_failure_preserves_snapshot_and_timestamp(self):
        token = self.enable()
        row = self.scan(token, lambda *args: [{"name": "ExampleBreach"}])
        previous, stamp = row.payload, row.last_success_at
        row.next_attempt_at = bm.now() - timedelta(seconds=1); self.db.commit()
        token = bm.claim(self.db, 1)
        def fail(*args):
            raise bm.ProviderError("key_rejected")
        row = self.scan(token, fail)
        self.assertEqual(row.payload, previous)
        self.assertEqual(row.last_success_at, stamp)
        self.assertEqual(row.error_code, "key_rejected")

    def test_revoked_consent_cancels_worker(self):
        token = self.enable()
        def revoke(*args):
            bm.configure(self.db, 1, [])
            return [{"name": "ExampleBreach"}]
        row = self.scan(token, revoke)
        self.assertFalse(row.enabled)
        self.assertEqual(bm.unpack(row)["findings"], [])

    def test_retention_clears_old_results(self):
        token = self.enable()
        row = self.scan(token, lambda *args: [{"name": "ExampleBreach"}])
        bm.purge(self.db, {"privacy": {"artifacts_retention_days": 1}}, bm.now() + timedelta(days=2))
        self.db.commit()
        self.assertEqual(bm.unpack(row)["findings"], [])
        self.assertIsNone(row.last_success_at)

    def test_identity_change_invalidates_old_snapshot(self):
        token = self.enable()
        row = self.scan(token, lambda *args: [{"name": "ExampleBreach"}])
        self.assertTrue(bm.snapshot_current(self.db, row))
        self.db.get(Identity, 1).value = "changed@example.invalid"
        self.db.commit()
        self.assertFalse(bm.snapshot_current(self.db, row))
        response = self.client.get('/api/breach-monitor/1').json()
        self.assertIsNone(response['last_success'])
        self.assertEqual(response['findings'], [])

    def test_minors_retention_is_shorter(self):
        bm.configure(self.db, 2, [2])
        token = bm.claim(self.db, 2)
        bm.run_member(2, token, self.factory, '0'*32, lambda *args: [{"name": "ExampleBreach"}])
        self.db.expire_all()
        bm.purge(self.db, {"privacy": {"artifacts_retention_days": 90, "coppa_children_retention_days": 30}},
                 bm.now() + timedelta(days=31))
        self.db.commit()
        self.assertEqual(bm.unpack(self.db.get(BreachMonitor, 2))['findings'], [])

    def test_no_emails_is_not_a_clean_result(self):
        token = self.enable()
        self.db.delete(self.db.get(Identity, 1)); self.db.commit()
        query = Mock()
        row = self.scan(token, query)
        query.assert_not_called()
        self.assertEqual(row.error_code, 'no_emails')
        self.assertIsNone(row.last_success_at)

    def test_parent_cannot_run_another_family(self):
        with patch.object(bm, 'api_key', return_value='0'*32):
            self.assertEqual(self.client.post('/api/breach-monitor/2/run').status_code, 403)

    def test_person_export_and_delete_include_monitor(self):
        token = self.enable()
        self.scan(token, lambda *args: [{"name": "ExampleBreach"}])
        from ..core.privacy_record import export_person
        exported = export_person(self.db, self.db.get(FamilyMember, 1))
        self.assertEqual(exported['breach_monitoring']['findings'][0]['name'], 'ExampleBreach')
        from ..core.people import delete_person
        with patch(bm.__package__ + '.settings_store.load_settings', return_value={"privacy": {"crypto_shredding_enabled": False}}):
            delete_person(self.db, self.user)
        self.db.commit()
        self.assertEqual(self.db.query(BreachMonitor).count(), 0)

    def test_provider_status_and_private_errors(self):
        for status, error in [(401, "key_rejected"), (403, "key_rejected"), (500, "provider_unavailable"), (302, "provider_unavailable")]:
            with self.subTest(status=status), patch.object(bm, '_next_call', 0):
                with self.assertRaises(bm.ProviderError) as caught:
                    bm.lookup('one@example.invalid', '0'*32, Mock(return_value=Mock(status_code=status)))
                self.assertEqual(str(caught.exception), error)
        with patch.object(bm, '_next_call', 0):
            get = Mock(return_value=Mock(status_code=404))
            self.assertEqual(bm.lookup('one@example.invalid', '0'*32, get), [])
            self.assertFalse(get.call_args.kwargs['allow_redirects'])
        with patch.object(bm, '_next_call', 0):
            with self.assertRaises(bm.ProviderError) as caught:
                bm.lookup('one@example.invalid', '0'*32, Mock(side_effect=bm.requests.ConnectionError('one@example.invalid secret')))
            self.assertNotIn('example.invalid', str(caught.exception))
        with patch.object(bm, '_next_call', 0):
            response = Mock(status_code=429, headers={'Retry-After': '120'})
            with self.assertRaises(bm.ProviderError) as caught:
                bm.lookup('one@example.invalid', '0'*32, Mock(return_value=response))
            self.assertEqual(caught.exception.retry_after, 120)
        with patch.object(bm, '_next_call', 0):
            with self.assertRaises(bm.ProviderError):
                bm.lookup('one@example.invalid', '0'*32, Mock(return_value=Mock(status_code=200, json=lambda: {"bad": "data"})))


if __name__ == '__main__':
    unittest.main()
