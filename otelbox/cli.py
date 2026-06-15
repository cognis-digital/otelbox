"""Command-line interface for OTELBOX.

Subcommands:
  lint <config.yaml>   Validate/triage an OTel collector config. Exits non-zero
                       if any error-severity findings are present.
  bundle [--name N]    Emit a runnable collector + dashboards bundle (as JSON
                       map of path -> contents). Analysis/generation only;
                       writes nothing and contacts no network.

Global: --version, --format {table,json}
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import TOOL_NAME, TOOL_VERSION
from .core import validate_config, build_bundle, load_config_text


def _print_table_findings(result) -> None:
    if not result.findings:
        print("no findings")
    else:
        width = max(len(f.code) for f in result.findings)
        for f in result.findings:
            loc = f" [{f.location}]" if f.location else ""
            print(f"{f.severity.upper():7} {f.code:<{width}}  {f.message}{loc}")
    s = result.summary
    print(
        f"-- {s.get('error', 0)} error(s), "
        f"{s.get('warning', 0)} warning(s), "
        f"{s.get('info', 0)} info; ok={result.ok}"
    )


def _cmd_lint(args) -> int:
    try:
        with open(args.config, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        print(json.dumps({"error": str(exc)}) if args.format == "json"
              else f"error: {exc}", file=sys.stderr)
        return 2
    try:
        cfg = load_config_text(text)
    except ValueError as exc:
        msg = f"parse error: {exc}"
        if args.format == "json":
            print(json.dumps({"ok": False, "error": msg}))
        else:
            print(msg, file=sys.stderr)
        return 2

    result = validate_config(cfg)
    if args.format == "json":
        print(json.dumps(result.as_dict(), indent=2))
    else:
        _print_table_findings(result)
    return 0 if result.ok else 1


def _cmd_bundle(args) -> int:
    try:
        bundle = build_bundle(name=args.name)
    except ValueError as exc:
        msg = f"invalid bundle name: {exc}"
        if args.format == "json":
            print(json.dumps({"ok": False, "error": msg}))
        else:
            print(msg, file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps({"name": args.name, "files": bundle}, indent=2))
    else:
        for path in sorted(bundle):
            lines = bundle[path].count("\n")
            print(f"{path}  ({lines} lines)")
        print(f"-- {len(bundle)} file(s) generated")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description="One-command OpenTelemetry collector + dashboards bundle "
                    "(validate / triage / generate).",
    )
    parser.add_argument(
        "--version", action="version",
        version=f"{TOOL_NAME} {TOOL_VERSION}",
    )
    parser.add_argument(
        "--format", choices=("table", "json"), default="table",
        help="output format (default: table)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_lint = sub.add_parser("lint", help="validate an OTel collector config")
    p_lint.add_argument("config", help="path to collector YAML config")
    p_lint.set_defaults(func=_cmd_lint)

    p_bundle = sub.add_parser("bundle", help="emit a runnable collector + dashboards bundle")
    p_bundle.add_argument("--name", default="otelbox", help="bundle directory name")
    p_bundle.set_defaults(func=_cmd_bundle)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        raise  # let argparse handle --help / bad flags normally
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"unexpected error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
