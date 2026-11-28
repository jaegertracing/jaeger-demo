# Copyright (c) 2026 The Jaeger Authors.
# SPDX-License-Identifier: Apache-2.0

import ast
import http.server
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
import urllib.parse

import yaml

from companion_values import source_values

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "examples/trace-generator/generate_traces.py"
DEPLOYMENTS = {
    ROOT / "examples/oci/load-generator/load-generator.yaml":
        ("http://jaeger-hotrod.default.svc.cluster.local:80/hotrod/dispatch", "10"),
    ROOT / "examples/otel-demo/load-generator.yaml":
        ("http://jaeger-hotrod.jaeger.svc.cluster.local/dispatch", "5"),
}


class TraceGeneratorTest(unittest.TestCase):
    def test_the_generator_uses_only_the_standard_library(self):
        # Pin the exact top-level imports instead of sys.stdlib_module_names,
        # which only exists on Python 3.10+; the suite supports Python 3.9.
        names = set()
        for node in ast.walk(ast.parse(SCRIPT.read_text())):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add((node.module or "").split(".")[0])
        self.assertEqual({"os", "random", "time", "urllib"}, names)

    def test_each_stack_keeps_its_target_url_and_interval(self):
        for path, (url, seconds) in DEPLOYMENTS.items():
            with self.subTest(deployment=path.name):
                deploy = yaml.safe_load(path.read_text())
                container, = deploy["spec"]["template"]["spec"]["containers"]
                env = {entry["name"]: entry["value"] for entry in container["env"]}
                self.assertEqual(env["TARGET_URL"], url)
                self.assertEqual(env["SLEEP_SECONDS"], seconds)
                self.assertNotIn("pip install", " ".join(container["command"]))

    def test_both_stacks_load_the_same_script_without_requirements(self):
        companions = source_values()["companions"]
        scripts = {}
        for namespace in ("default", "jaeger"):
            configmap, = (obj for obj in companions[namespace]["objects"]
                          if obj["kind"] == "ConfigMap" and obj["metadata"]["name"] == "trace-script")
            self.assertEqual(["generate_traces.py"], list(configmap["data"]))
            scripts[namespace] = configmap["data"]["generate_traces.py"]
        self.assertEqual(scripts["default"], scripts["jaeger"])
        self.assertEqual(scripts["default"], SCRIPT.read_text())

    def test_dispatches_reach_the_target_with_customer_and_nonse(self):
        hits = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_port}/hotrod/dispatch"
        env = dict(os.environ, TARGET_URL=url, SLEEP_SECONDS="0.2")
        proc = subprocess.Popen([sys.executable, str(SCRIPT)], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            # Poll instead of a fixed sleep so slow runners cannot flake this.
            deadline = time.monotonic() + 15
            while len(hits) < 3 and time.monotonic() < deadline:
                time.sleep(0.1)
        finally:
            proc.kill()
            out, err = proc.communicate(timeout=10)
            server.shutdown()
            server.server_close()
        self.assertEqual("", err)
        self.assertIn(f"→ {url} (interval=0.2s)", out)
        self.assertGreaterEqual(len(hits), 3)
        for hit in hits:
            path, _, query = hit.partition("?")
            self.assertEqual("/hotrod/dispatch", path)
            params = urllib.parse.parse_qs(query)
            self.assertIn(params["customer"][0], ("123", "392", "731", "567"))
            float(params["nonse"][0])


if __name__ == "__main__":
    unittest.main()
