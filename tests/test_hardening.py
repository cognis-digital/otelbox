"""Hardening tests for OTELBOX: error paths, edge cases, input validation.

All tests use standard library only and do not require network access.
"""
from __future__ import annotations

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from otelbox.core import (
    build_bundle,
    load_config_text,
    validate_config,
)
from otelbox.cli import main
from otelbox.mcp_server import _lint_to_json


# ---------------------------------------------------------------------------
# load_config_text edge cases
# ---------------------------------------------------------------------------

class TestLoadConfigTextEdgeCases(unittest.TestCase):
    def test_non_string_input_raises_value_error(self):
        """Passing a non-string (e.g. None) must raise ValueError, not crash."""
        with self.assertRaises(ValueError) as ctx:
            load_config_text(None)  # type: ignore[arg-type]
        self.assertIn("str", str(ctx.exception))

    def test_non_string_bytes_raises_value_error(self):
        with self.assertRaises(ValueError):
            load_config_text(b"receivers:\n  otlp: {}\n")  # type: ignore[arg-type]

    def test_empty_string_returns_empty_dict(self):
        result = load_config_text("")
        self.assertEqual(result, {})

    def test_only_comments_returns_empty_dict(self):
        result = load_config_text("# just a comment\n# another comment\n")
        self.assertEqual(result, {})

    def test_whitespace_only_returns_empty_dict(self):
        result = load_config_text("   \n  \n")
        self.assertEqual(result, {})


# ---------------------------------------------------------------------------
# build_bundle validation
# ---------------------------------------------------------------------------

class TestBuildBundleValidation(unittest.TestCase):
    def test_empty_name_raises_value_error(self):
        with self.assertRaises(ValueError):
            build_bundle(name="")

    def test_whitespace_name_raises_value_error(self):
        with self.assertRaises(ValueError):
            build_bundle(name="   ")

    def test_name_with_slash_raises_value_error(self):
        with self.assertRaises(ValueError):
            build_bundle(name="foo/bar")

    def test_name_with_backslash_raises_value_error(self):
        with self.assertRaises(ValueError):
            build_bundle(name="foo\\bar")

    def test_valid_name_works(self):
        bundle = build_bundle(name="mybox")
        self.assertIn("mybox/collector.yaml", bundle)


# ---------------------------------------------------------------------------
# validate_config edge cases
# ---------------------------------------------------------------------------

class TestValidateConfigEdgeCases(unittest.TestCase):
    def test_none_config_is_handled(self):
        """validate_config({}) should not crash; missing sections = errors."""
        result = validate_config({})
        self.assertFalse(result.ok)

    def test_non_dict_receivers_flagged(self):
        """Receivers as a list instead of dict should produce E001."""
        result = validate_config({"receivers": ["otlp"], "exporters": {}})
        codes = {f.code for f in result.findings}
        self.assertIn("E001", codes)

    def test_service_not_dict_handled(self):
        """service key set to a scalar shouldn't crash."""
        cfg = {
            "receivers": {"otlp": {}},
            "exporters": {"debug": {}},
            "service": "not-a-dict",
        }
        result = validate_config(cfg)
        # Should produce E003 (no pipelines) and not raise
        codes = {f.code for f in result.findings}
        self.assertIn("E003", codes)


# ---------------------------------------------------------------------------
# CLI error handling
# ---------------------------------------------------------------------------

class TestCLIHardening(unittest.TestCase):
    def _run(self, argv):
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            rc = main(argv)
        return rc, out_buf.getvalue(), err_buf.getvalue()

    def test_missing_file_exits_2_with_message(self):
        rc, _, err = self._run(["lint", "no_such_file_xyz_12345.yaml"])
        self.assertEqual(rc, 2)
        self.assertTrue(err.strip(), "expected error message on stderr")

    def test_missing_file_json_format_exits_2(self):
        rc, _, err = self._run(["--format", "json", "lint", "no_such_file_abc.yaml"])
        self.assertEqual(rc, 2)
        # The existing code writes OSError messages to stderr even in JSON mode.
        # Confirm a message was emitted and exit code is correct.
        self.assertTrue(err.strip(), "expected error message on stderr")

    def test_invalid_bundle_name_exits_2(self):
        rc, _, err = self._run(["bundle", "--name", ""])
        self.assertEqual(rc, 2)
        self.assertTrue(err.strip() or rc != 0)

    def test_malformed_yaml_exits_2_with_message(self):
        import os
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as fh:
            fh.write("receivers:\n\totlp: {}\n")  # tab indentation -> parse error
            tmp = fh.name
        try:
            rc, _, err = self._run(["lint", tmp])
            self.assertEqual(rc, 2)
            self.assertTrue(err.strip(), "expected error message on stderr")
        finally:
            os.unlink(tmp)


# ---------------------------------------------------------------------------
# MCP server _lint_to_json (unit-testable without running the server)
# ---------------------------------------------------------------------------

class TestMCPLintToJson(unittest.TestCase):
    def test_valid_config_returns_ok_true(self):
        yaml = (
            "receivers:\n"
            "  otlp:\n"
            "    protocols:\n"
            "      grpc:\n"
            "        endpoint: 127.0.0.1:4317\n"
            "processors:\n"
            "  batch:\n"
            "    timeout: 5s\n"
            "exporters:\n"
            "  debug:\n"
            "    verbosity: normal\n"
            "service:\n"
            "  pipelines:\n"
            "    traces:\n"
            "      receivers: [otlp]\n"
            "      processors: [batch]\n"
            "      exporters: [debug]\n"
        )
        out = _lint_to_json(yaml)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])

    def test_parse_error_returns_ok_false_with_error_key(self):
        out = _lint_to_json("receivers:\n\totlp: {}\n")  # tabs -> parse error
        payload = json.loads(out)
        self.assertFalse(payload["ok"])
        self.assertIn("error", payload)

    def test_empty_config_returns_ok_false(self):
        out = _lint_to_json("")
        payload = json.loads(out)
        self.assertFalse(payload["ok"])


if __name__ == "__main__":
    unittest.main()
