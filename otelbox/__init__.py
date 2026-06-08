"""OTELBOX - One-command OpenTelemetry collector + dashboards bundle.

Defensive / authorized-testing tooling: validates and triages an
OpenTelemetry Collector configuration, generates a ready-to-run collector
config + a dashboards bundle (Prometheus/Grafana-style provisioning), and
lints telemetry pipelines for common misconfigurations. Analysis only --
it never sends telemetry or contacts any endpoint.

Spirit of the LGTM stack / HyperDX: get a working, observable OTel pipeline
in one command, with a sane local default.
"""
from .core import (
    Finding,
    ValidationResult,
    validate_config,
    build_bundle,
    load_config_text,
)

TOOL_NAME = "otelbox"
TOOL_VERSION = "1.0.0"

__all__ = [
    "TOOL_NAME",
    "TOOL_VERSION",
    "Finding",
    "ValidationResult",
    "validate_config",
    "build_bundle",
    "load_config_text",
]
