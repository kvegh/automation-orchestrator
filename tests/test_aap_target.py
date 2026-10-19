"""Exercise cleanup against a local HTTPS Gateway double, never a real AAP."""

import base64
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import yaml


ROOT = Path(__file__).resolve().parents[1]


class AAPTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="orchestrator-target-test-")
        cls.directory = Path(cls.workspace.name)
        cls.cert = cls.directory / "gateway.pem"
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", str(cls.directory / "gateway.key"), "-out", str(cls.cert),
                "-days", "1", "-subj", "/CN=localhost",
                "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
            ],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        cls.runner = shutil.which("ansible-playbook")
        if not cls.runner:
            raise unittest.SkipTest("ansible-playbook must already be installed")

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def setUp(self):
        self.requests = []
        self.matches = [{"id": 42, "name": "Syntara"}]
        self.fail_delete = False
        test = self

        class Gateway(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def handle_request(self):
                path = urlsplit(self.path)
                test.requests.append((self.command, self.path, self.headers.get("Authorization")))
                status, body = 200, {}
                expected_auth = "Bearer temporary-test-token"
                if self.command == "POST" and path.path == "/api/gateway/v1/tokens/":
                    expected_auth = "Basic " + base64.b64encode(b"target-admin:target-password").decode()
                    status, body = 201, {"id": 99, "token": "temporary-test-token"}
                elif self.command == "GET" and path.path == "/api/gateway/v1/applications/":
                    test.assertEqual(parse_qs(path.query), {"name": ["Syntara"]})
                    body = {"count": len(test.matches), "results": test.matches}
                elif self.command == "DELETE" and path.path == "/api/gateway/v1/applications/42/":
                    status = 500 if test.fail_delete else 204
                elif self.command == "DELETE" and path.path == "/api/gateway/v1/tokens/99/":
                    status = 204
                else:
                    status = 404
                if self.headers.get("Authorization") != expected_auth:
                    status = 401
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if status != 204:
                    self.wfile.write(json.dumps(body).encode())

            do_POST = handle_request
            do_GET = handle_request
            do_DELETE = handle_request

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(self.cert), str(self.directory / "gateway.key"))
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.variables = {
            "aap_gateway_url": f"https://localhost:{self.server.server_port}/",
            "aap_admin_username": "target-admin",
            "aap_admin_password": "target-password",
            "aap_cleanup_application_names": ["Syntara"],
            "aap_ca_path": str(self.cert),
        }

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def run_cleanup(self, playbook="cleanup-aap-orchestrator.yml"):
        variables_file = self.directory / "launch.json"
        variables_file.write_text(json.dumps(self.variables))
        variables_file.chmod(0o600)
        environment = dict(os.environ)
        environment.update(
            ANSIBLE_LOCAL_TEMP=str(self.directory / "local"),
            ANSIBLE_REMOTE_TEMP=str(self.directory / "remote"),
            ANSIBLE_NOCOLOR="1",
        )
        result = subprocess.run(
            [self.runner, "-i", "localhost,", str(playbook), "-e", f"@{variables_file}"],
            cwd=ROOT, env=environment, capture_output=True, text=True, timeout=60,
        )
        self.assertNotIn("target-password", result.stdout + result.stderr)
        self.assertNotIn("temporary-test-token", result.stdout + result.stderr)
        return result

    def assert_token_revoked(self):
        self.assertEqual(self.requests[-1][:2], ("DELETE", "/api/gateway/v1/tokens/99/"))

    def test_explicit_target_uses_gateway_token_and_exact_application(self):
        result = self.run_cleanup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.requests), 4)
        self.assertEqual(self.requests[2][:2], ("DELETE", "/api/gateway/v1/applications/42/"))
        self.assert_token_revoked()

    def test_absent_application_is_safe_and_revokes_token(self):
        self.matches = []
        result = self.run_cleanup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.requests), 3)
        self.assert_token_revoked()

    def test_survey_json_list_selects_exact_application(self):
        self.variables["aap_cleanup_application_names"] = '["Syntara"]'
        result = self.run_cleanup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.requests[2][:2], ("DELETE", "/api/gateway/v1/applications/42/"))
        self.assert_token_revoked()

    def test_ambiguous_names_fail_before_any_application_deletion(self):
        self.matches.append({"id": 43, "name": "Syntara"})
        result = self.run_cleanup()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.requests), 3)
        self.assert_token_revoked()

    def test_unexpected_name_fails_before_any_application_deletion(self):
        self.matches = [{"id": 42, "name": "Unrelated Orchestrator"}]
        result = self.run_cleanup()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.requests), 3)
        self.assert_token_revoked()

    def test_deletion_failure_still_revokes_temporary_token(self):
        self.fail_delete = True
        result = self.run_cleanup()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.requests), 4)
        self.assert_token_revoked()

    def test_missing_or_unsafe_inputs_fail_before_network_access(self):
        good = self.variables.copy()
        cases = [
            {key: value for key, value in good.items() if key != "aap_gateway_url"},
            {key: value for key, value in good.items() if key != "aap_admin_password"},
            dict(good, aap_gateway_url="http://localhost"),
            dict(good, aap_gateway_url="https://user:password@localhost"),
            dict(good, aap_gateway_url="https://localhost/api/gateway"),
            dict(good, aap_gateway_url="https://localhost:invalid"),
            dict(good, aap_cleanup_application_names=[]),
            dict(good, aap_cleanup_application_names="Syntara"),
        ]
        for variables in cases:
            with self.subTest(keys=sorted(variables), url=variables.get("aap_gateway_url")):
                self.variables = variables
                self.assertNotEqual(self.run_cleanup().returncode, 0)
                self.assertEqual(self.requests, [])

    def run_deploy_preflight(self, assertions):
        source = yaml.safe_load((ROOT / "deploy-automation-orchestrator.yml").read_text())[0]
        preflight = source["pre_tasks"]
        preflight[0]["ansible.builtin.include_tasks"] = str(ROOT / "tasks/validate-aap-target.yml")
        playbook = self.directory / "deploy-preflight.yml"
        playbook.write_text(yaml.safe_dump([{
            "hosts": "localhost", "connection": "local", "gather_facts": False,
            "pre_tasks": preflight,
            "tasks": [{"ansible.builtin.assert": {"that": assertions}}],
        }]))
        return self.run_cleanup(playbook)

    def test_standalone_deploy_accepts_empty_survey_target_inputs(self):
        self.variables.update(aap_gateway_url="", aap_admin_username="", aap_admin_password="")
        result = self.run_deploy_preflight(["orchestrator_aap_url is not defined"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.requests, [])

    def test_deploy_preflight_selects_launch_target_without_repository_vault(self):
        result = self.run_deploy_preflight(["orchestrator_aap_url == aap_gateway_url.rstrip('/')"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.requests, [])

    def test_partial_deploy_target_fails_before_ocp_changes(self):
        self.variables.pop("aap_admin_username")
        result = self.run_deploy_preflight(["true"])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.requests, [])

    def test_existing_integrations_cannot_silently_reuse_another_target(self):
        source = yaml.safe_load((ROOT / "deploy-automation-orchestrator.yml").read_text())[0]

        def tasks(items):
            for task in items:
                yield task
                for section in ("block", "rescue", "always"):
                    yield from tasks(task.get(section, []))

        checks = [task for task in tasks(source["tasks"])
                  if task.get("name", "").startswith("Reject an existing AAP")]
        self.assertEqual(len(checks), 2)
        target = self.variables["aap_gateway_url"].rstrip("/")
        self.variables.update(
            orchestrator_aap_url=target,
            existing_idps={"json": {"resources": [{"name": "AAP", "configuration": {
                "issuer_url": target + "/api/gateway/o/"}}]}},
            existing_integrations={"json": {"resources": [{"name": "AAP Demo", "configuration": {
                "base_url": target}}]}},
        )
        playbook = self.directory / "target-drift.yml"
        playbook.write_text(yaml.safe_dump([{
            "hosts": "localhost", "connection": "local", "gather_facts": False, "tasks": checks,
        }]))
        self.assertEqual(self.run_cleanup(playbook).returncode, 0)
        self.variables["existing_idps"]["json"]["resources"][0]["configuration"]["issuer_url"] = "https://other.example.com/o/"
        self.assertNotEqual(self.run_cleanup(playbook).returncode, 0)
        self.variables["existing_idps"]["json"]["resources"][0]["configuration"]["issuer_url"] = target + "/o/"
        self.variables["existing_integrations"]["json"]["resources"][0]["configuration"]["base_url"] = "https://other.example.com"
        self.assertNotEqual(self.run_cleanup(playbook).returncode, 0)
        self.assertEqual(self.requests, [])


if __name__ == "__main__":
    unittest.main()
