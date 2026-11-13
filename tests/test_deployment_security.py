"""Run the real deployment's sensitive tasks against a local TLS double."""

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

import yaml


ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "synthetic-orchestrator-password-canary"
TOKEN = "synthetic-orchestrator-token-canary"


class DeploymentSecurityTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(prefix="orchestrator-security-")
        self.directory = Path(self.workspace.name)
        self.runner = shutil.which("ansible-playbook")
        if not self.runner:
            self.skipTest("ansible-playbook must already be installed")
        self.cert = self.directory / "server.pem"
        key = self.directory / "server.key"
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", str(key), "-out", str(self.cert), "-days", "1",
             "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.requests = []
        test = self

        class Orchestrator(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                test.requests.append(self.path)
                test.assertEqual(payload["password"], PASSWORD)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"access_token": TOKEN}).encode())

            def do_GET(self):
                test.requests.append(self.path)
                test.assertEqual(self.headers["Authorization"], "Bearer " + TOKEN)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"id": "test-user"}).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Orchestrator)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.cert, key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.play = yaml.safe_load((ROOT / "deploy-automation-orchestrator.yml").read_text())[0]
        self.deployment = next(t for t in self.play["tasks"] if t["name"] == "Deploy Orchestrator on OCP")
        self.defaults = yaml.safe_load((ROOT / "vars/main.yml").read_text())

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.workspace.cleanup()

    def run_tasks(self, tasks, **overrides):
        variables = dict(self.defaults, orchestrator_url=f"https://localhost:{self.server.server_port}",
                         orchestrator_admin_password=PASSWORD, secure_logging="true")
        variables.update(overrides)
        path = self.directory / "play.yml"
        path.write_text(yaml.safe_dump([{"hosts": "localhost", "connection": "local",
                                        "gather_facts": False, "vars": variables, "tasks": tasks}]))
        environment = dict(os.environ, ANSIBLE_NOCOLOR="1",
                           ANSIBLE_LOCAL_TEMP=str(self.directory / "local"),
                           ANSIBLE_REMOTE_TEMP=str(self.directory / "remote"),
                           TMPDIR=str(self.directory / "ca"))
        (self.directory / "ca").mkdir(exist_ok=True)
        result = subprocess.run([self.runner, "-i", "localhost,", str(path), "-v"],
                                capture_output=True, text=True, env=environment, timeout=45)
        output = result.stdout + result.stderr
        self.assertNotIn(PASSWORD, output)
        self.assertNotIn(TOKEN, output)
        return result

    def integration_tasks(self):
        integration = next(t for t in self.deployment["block"] if t["name"] == "Configure AAP integration")
        names = {"Authenticate to Orchestrator API", "Set auth token", "Get local admin user info"}
        return [{"name": self.deployment["name"], "no_log": self.deployment.get("no_log", False),
                 "block": [t for t in integration["block"] if t["name"] in names]}]

    def test_authenticated_results_and_token_facts_are_protected(self):
        result = self.run_tasks(self.integration_tasks(), orchestrator_ca_path=str(self.cert))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.requests), 2)

    def test_untrusted_server_is_rejected_before_credentials_are_sent(self):
        self.assertIs(self.defaults["ocp_validate_certs"], True)
        self.assertIs(self.defaults["orchestrator_validate_certs"], True)
        result = self.run_tasks(self.integration_tasks())
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.requests, [])

    def test_summary_never_renders_the_administrator_password(self):
        summary = next(t for t in self.deployment["block"] if t["name"] == "Display Orchestrator deployment info")
        result = self.run_tasks([summary], secure_logging=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_supplied_ca_file_is_removed_after_a_deployment_failure(self):
        prepare = next(t for t in self.play["tasks"]
                       if t["name"] == "Prepare an explicitly supplied OpenShift API CA bundle")
        tasks = [prepare, {"block": [{"ansible.builtin.fail": {"msg": "synthetic deployment failure"}}],
                           "always": self.deployment["always"]}]
        result = self.run_tasks(tasks, ocp_ca_bundle=self.cert.read_text())
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list((self.directory / "ca").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
