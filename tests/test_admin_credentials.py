"""Local credential bootstrap and token-mode HTTP checks (no data-plane services)."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.requests import Request

from ragproof_otel import capture_scenario
from ragproof_store.admin import main
from ragproof_store.api import create_app
from ragproof_store.credentials import create_credential, load_credentials, validate_credentials
from ragproof_store.security import AccessControl


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.key, self.record = create_credential("cuad-public", "cuad-contract-review", "reader")
        self.secret = secrets.token_urlsafe(32)

    def test_cli_emits_raw_key_once_and_only_hashes_on_stdout(self):
        result = subprocess.run(
            [sys.executable, "-m", "ragproof_store.admin", "create-key",
             "--tenant-id", "cuad-public", "--project-id", "cuad-contract-review", "--role", "writer"],
            capture_output=True, text=True, check=True,
        )
        records = json.loads(result.stdout)
        raw_key = result.stderr.splitlines()[-1]
        self.assertIn("shown once", result.stderr)
        self.assertEqual(result.stderr.count(raw_key), 1)
        self.assertTrue(raw_key not in result.stdout)
        self.assertGreaterEqual(len(raw_key), 47)
        access = AccessControl(mode="token", keys=records, secret=self.secret)
        principal = access.authenticate_key(raw_key)
        self.assertIsNotNone(principal)
        self.assertEqual(principal.role, "writer")
        self.assertEqual(principal.tenant_id, "cuad-public")
        self.assertEqual(principal.project_id, "cuad-contract-review")
        self.assertTrue(records[0]["key_hash"] == hashlib.sha256(raw_key.encode()).hexdigest())

    def test_bootstrap_does_not_import_optional_dependencies(self):
        environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
        result = subprocess.run(
            [sys.executable, "-S", "-m", "ragproof_store.admin", "--help"],
            env=environment, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("create-key", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_invalid_command_inputs_never_generate_or_print_a_key(self):
        arguments = ["create-key", "--tenant-id", "cuad-public", "--project-id", "cuad-contract-review", "--role", "reader"]
        for index, value in ((2, ""), (2, " padded"), (4, "x\n"), (4, "x" * 257), (6, "owner")):
            with self.subTest(index=index, invalid_length=len(value)):
                invalid = arguments.copy()
                invalid[index] = value
                stdout, stderr = StringIO(), StringIO()
                with patch("ragproof_store.credentials.secrets.token_urlsafe") as random_key:
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        with self.assertRaises(SystemExit) as error:
                            main(invalid)
                    self.assertEqual(error.exception.code, 2)
                    random_key.assert_not_called()
                self.assertEqual(stdout.getvalue(), "")
                self.assertNotIn("rpf_", stderr.getvalue())

    def test_rejects_malformed_configurations_at_startup(self):
        invalid = [None, {}, "[]", [None], ["credential"], [dict(self.record, extra=True)]]
        for field, values in {
            "key_hash": [None, 64, "g" * 64, "A" * 64, "a" * 63, "a" * 65],
            "role": [None, [], "owner", ""],
            "tenant_id": [None, 1, "", " ", " padded", "x\n", "x" * 257],
            "project_id": [None, [], "", "padded ", "x\x00", "x" * 257],
        }.items():
            invalid.extend([[dict(self.record, **{field: value})] for value in values])
        invalid.append([self.record, dict(self.record, project_id="another-project")])
        for index, keys in enumerate(invalid):
            with self.subTest(case=index):
                with patch.dict(os.environ, {"RAGPROOF_API_KEYS_JSON": json.dumps(keys)}):
                    with self.assertRaises(ValueError):
                        AccessControl(mode="token", secret=self.secret)

    def test_json_errors_do_not_echo_configuration(self):
        for serialized in ("{", "null", json.dumps({"api_key": self.key})):
            with self.assertRaises(ValueError) as error:
                load_credentials(serialized)
            self.assertTrue(self.key not in str(error.exception))

    def test_configuration_is_copied_and_empty_local_mode_is_supported(self):
        keys = [deepcopy(self.record)]
        access = AccessControl(mode="token", keys=keys, secret=self.secret)
        keys[0]["role"] = "admin"
        self.assertEqual(access.authenticate_key(self.key).role, "reader")
        self.assertEqual(validate_credentials([]), [])
        self.assertEqual(AccessControl(mode="local", keys=[]).keys, [])
        with self.assertRaises(ValueError):
            AccessControl(mode="token", keys=[], secret=self.secret)
        with self.assertRaises(ValueError):
            AccessControl(mode="token", keys=[self.record], secret="")

    def test_rotation_and_revocation_invalidate_old_bearer_keys_and_sessions(self):
        new_key, new_record = create_credential("cuad-public", "cuad-contract-review", "reader")
        old = AccessControl(mode="token", keys=[self.record], secret=self.secret)
        cookie = old.session(old.authenticate_key(self.key))
        request = Request({"type": "http", "headers": [(b"cookie", f"ragproof_session={cookie}".encode())]})
        rotating = AccessControl(mode="token", keys=[self.record, new_record], secret=self.secret)
        self.assertIsNotNone(rotating.authenticate_key(self.key))
        self.assertIsNotNone(rotating.authenticate_key(new_key))
        self.assertIsNotNone(rotating.resolve(request))
        revoked = AccessControl(mode="token", keys=[new_record], secret=self.secret)
        self.assertIsNone(revoked.authenticate_key(self.key))
        self.assertIsNone(revoked.resolve(request))
        self.assertIsNotNone(revoked.authenticate_key(new_key))
        changed_secret = AccessControl(mode="token", keys=[self.record], secret=secrets.token_urlsafe(32))
        self.assertIsNone(changed_secret.resolve(request))
        with patch("ragproof_store.security.time.time", return_value=10**12):
            self.assertIsNone(old.resolve(request))


class AuthRepository:
    """Records API interactions; this does not stand in for storage integration tests."""
    def __init__(self):
        self.ingested = []

    def health(self):
        return {"postgres": True, "redis": True, "object_store": True}

    def audit(self, principal, method, path, status):
        pass

    def ingest(self, trace):
        self.ingested.append(trace)
        return {"trace_id": trace["trace_id"], "created": True}


class GeneratedKeyHttpTests(unittest.TestCase):
    def setUp(self):
        self.trace = capture_scenario("healthy", capture_mode="full").canonical_trace

    def client_for(self, role):
        key, record = create_credential(self.trace["tenant_id"], self.trace["project_id"], role)
        self.repository = AuthRepository()
        access = AccessControl(mode="token", keys=[record], secret=secrets.token_urlsafe(32), cookie_secure=False)
        return key, TestClient(create_app(self.repository, access_control=access))

    def test_generated_keys_enforce_roles_over_authenticated_http(self):
        for role in ("reader", "writer", "admin"):
            with self.subTest(role=role):
                key, client = self.client_for(role)
                with client:
                    self.assertEqual(client.get("/v1/workspace").status_code, 401)
                    headers = {"Authorization": f"Bearer {key}"}
                    workspace = client.get("/v1/workspace", headers=headers)
                    self.assertEqual(workspace.status_code, 200)
                    self.assertEqual(workspace.json()["project_id"], self.trace["project_id"])
                    self.assertEqual(workspace.json()["permissions"]["write"], role != "reader")
                    response = client.post("/v1/traces", json=self.trace, headers=headers)
                    self.assertEqual(response.status_code, 403 if role == "reader" else 201)
                    self.assertEqual(len(self.repository.ingested), 0 if role == "reader" else 1)

    def test_foreign_scope_and_invalid_key_are_rejected_before_storage(self):
        key, client = self.client_for("admin")
        with client:
            for field in ("tenant_id", "project_id"):
                foreign = dict(self.trace, **{field: "another-scope"})
                response = client.post("/v1/traces", json=foreign, headers={"Authorization": f"Bearer {key}"})
                self.assertEqual(response.status_code, 403)
            invalid = secrets.token_urlsafe(32)
            self.assertEqual(client.post("/v1/traces", json=self.trace, headers={"Authorization": f"Bearer {invalid}"}).status_code, 401)
        self.assertEqual(self.repository.ingested, [])

    def test_generated_key_supports_browser_login_and_logout_without_logging_it(self):
        key, client = self.client_for("writer")
        with self.assertNoLogs("ragproof", level="DEBUG"), client:
            response = client.post("/auth/session", json={"api_key": key})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(key not in response.text)
            cookie = response.headers["set-cookie"]
            self.assertIn("HttpOnly", cookie)
            self.assertIn("SameSite=strict", cookie)
            self.assertIn("Max-Age=28800", cookie)
            self.assertTrue(key not in cookie)
            self.assertEqual(client.get("/v1/workspace").status_code, 200)
            self.assertEqual(client.delete("/auth/session").status_code, 200)
            self.assertEqual(client.get("/v1/workspace").status_code, 401)


if __name__ == "__main__":
    unittest.main()
