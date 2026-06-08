"""Smoke + behavior tests for OTELBOX. Standard library only, no network."""
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from otelbox import (
    TOOL_NAME,
    TOOL_VERSION,
    validate_config,
    build_bundle,
    load_config_text,
)
from otelbox.cli import main

GOOD = """\
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 127.0.0.1:4317
processors:
  batch:
    timeout: 5s
exporters:
  prometheus:
    endpoint: 127.0.0.1:8889
service:
  pipelines:
    metrics:
      receivers: [otlp]
      processors: [batch]
      exporters: [prometheus]
"""

BROKEN = """\
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
processors:
  batch:
    timeout: 5s
exporters:
  prometheus:
    endpoint: 127.0.0.1:8889
service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: []
      exporters: [missing_exporter]
"""

DEMO = os.path.join(os.path.dirname(__file__), "..", "demos", "01-basic", "broken-collector.yaml")


class TestMeta(unittest.TestCase):
    def test_metadata(self):
        self.assertEqual(TOOL_NAME, "otelbox")
        self.assertTrue(TOOL_VERSION)


class TestParser(unittest.TestCase):
    def test_parses_nested_and_flow_lists(self):
        cfg = load_config_text(GOOD)
        self.assertIn("otlp", cfg["receivers"])
        self.assertEqual(
            cfg["service"]["pipelines"]["metrics"]["receivers"], ["otlp"]
        )
        ep = cfg["receivers"]["otlp"]["protocols"]["grpc"]["endpoint"]
        self.assertEqual(ep, "127.0.0.1:4317")

    def test_tabs_rejected(self):
        with self.assertRaises(ValueError):
            load_config_text("receivers:\n\totlp: {}\n")


class TestValidation(unittest.TestCase):
    def test_good_config_is_ok(self):
        result = validate_config(load_config_text(GOOD))
        self.assertTrue(result.ok)
        self.assertEqual(result.error_count, 0)

    def test_broken_config_has_errors(self):
        result = validate_config(load_config_text(BROKEN))
        self.assertFalse(result.ok)
        codes = {f.code for f in result.findings}
        # undefined exporter reference is an error
        self.assertIn("E014", codes)
        # 0.0.0.0 bind flagged
        self.assertIn("W040", codes)
        # missing batch warned
        self.assertIn("W020", codes)

    def test_empty_config(self):
        result = validate_config({})
        self.assertFalse(result.ok)
        codes = {f.code for f in result.findings}
        self.assertTrue({"E001", "E002", "E003"} <= codes)


class TestBundle(unittest.TestCase):
    def test_bundle_is_valid_config(self):
        files = build_bundle(name="mybox")
        self.assertIn("mybox/collector.yaml", files)
        # The generated collector config must itself lint clean.
        cfg = load_config_text(files["mybox/collector.yaml"])
        result = validate_config(cfg)
        self.assertTrue(result.ok, msg=str(result.as_dict()))
        # Dashboard JSON must be valid JSON.
        json.loads(files["mybox/grafana/dashboard.json"])


class TestCLI(unittest.TestCase):
    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(argv)
        return rc, buf.getvalue()

    def test_lint_demo_exits_nonzero(self):
        rc, out = self._run(["--format", "json", "lint", os.path.abspath(DEMO)])
        self.assertEqual(rc, 1)
        payload = json.loads(out)
        self.assertFalse(payload["ok"])
        self.assertGreater(payload["summary"]["error"], 0)

    def test_bundle_json(self):
        rc, out = self._run(["--format", "json", "bundle", "--name", "b"])
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["name"], "b")
        self.assertIn("b/collector.yaml", payload["files"])

    def test_missing_file(self):
        rc, _ = self._run(["lint", "does_not_exist_12345.yaml"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
