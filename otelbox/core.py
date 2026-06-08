"""Core engine for OTELBOX.

Real logic, standard library only. Two responsibilities:

1. validate_config(): parse a minimal subset of an OpenTelemetry Collector
   YAML config (receivers / processors / exporters / service.pipelines) and
   lint it for real, well-known misconfigurations -- orphaned components,
   pipelines with no exporter, missing batch processor, exporters that point
   at the public OTel demo / 0.0.0.0 binds, etc.

2. build_bundle(): emit a complete, runnable local collector config plus a
   dashboards provisioning bundle (datasource + dashboard JSON) so a user can
   stand up an observable pipeline in one command.

The YAML parser is a small, dependency-free indentation parser covering the
mappings/lists/scalars an OTel collector config actually uses. It is not a
general YAML implementation but is sufficient and deterministic for this tool.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

SEVERITIES = ("error", "warning", "info")

# Endpoints that almost always indicate a copy-paste of public demo material
# or an insecure bind that should not ship.
_SUSPECT_ENDPOINTS = (
    "otelcol.example.com",
    "demo.opentelemetry.io",
    "0.0.0.0",
)


@dataclass
class Finding:
    code: str
    severity: str
    message: str
    location: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationResult:
    ok: bool
    findings: List[Finding] = field(default_factory=list)
    summary: Dict[str, int] = field(default_factory=dict)

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "error")

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "findings": [f.as_dict() for f in self.findings],
            "summary": self.summary,
        }


# --------------------------------------------------------------------------- #
# Minimal YAML subset parser (indentation based)                              #
# --------------------------------------------------------------------------- #
def _coerce_scalar(text: str) -> Any:
    t = text.strip()
    if t == "" or t in ("~", "null"):
        return None
    if (t.startswith('"') and t.endswith('"')) or (
        t.startswith("'") and t.endswith("'")
    ):
        return t[1:-1]
    low = t.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        pass
    # inline flow list: [a, b, c]
    if t.startswith("[") and t.endswith("]"):
        inner = t[1:-1].strip()
        if not inner:
            return []
        return [_coerce_scalar(p) for p in inner.split(",")]
    return t


def _strip_comment(line: str) -> str:
    out = []
    in_s = in_d = False
    for ch in line:
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch == "#" and not in_s and not in_d:
            break
        out.append(ch)
    return "".join(out)


def _parse_block(lines: List[Tuple[int, str]], idx: int, indent: int) -> Tuple[Any, int]:
    """Parse a block at the given indentation. Returns (value, next_index)."""
    # Decide if this block is a list or a mapping by looking at first line.
    if idx >= len(lines):
        return None, idx
    first_indent, first_text = lines[idx]
    if first_indent < indent:
        return None, idx

    if first_text.lstrip().startswith("- ") or first_text.strip() == "-":
        return _parse_list(lines, idx, first_indent)
    return _parse_map(lines, idx, first_indent)


def _parse_list(lines, idx, indent):
    items: List[Any] = []
    while idx < len(lines):
        cur_indent, text = lines[idx]
        if cur_indent < indent or not (
            text.lstrip().startswith("- ") or text.strip() == "-"
        ):
            break
        body = text.lstrip()[1:].strip()  # drop leading '-'
        if body == "":
            idx += 1
            val, idx = _parse_block(lines, idx, indent + 1)
            items.append(val)
        elif ":" in body and not body.startswith("{"):
            # inline mapping start on the dash line
            key, _, rest = body.partition(":")
            entry: Dict[str, Any] = {}
            if rest.strip():
                entry[key.strip()] = _coerce_scalar(rest)
            idx += 1
            child_indent = indent + 2
            sub, idx = _parse_map(lines, idx, child_indent, seed=entry)
            items.append(sub)
        else:
            items.append(_coerce_scalar(body))
            idx += 1
    return items, idx


def _parse_map(lines, idx, indent, seed: Optional[Dict[str, Any]] = None):
    mapping: Dict[str, Any] = dict(seed or {})
    while idx < len(lines):
        cur_indent, text = lines[idx]
        if cur_indent < indent:
            break
        if cur_indent > indent:
            # Shouldn't normally happen; skip defensively.
            idx += 1
            continue
        stripped = text.strip()
        if stripped.startswith("- "):
            break
        key, _, rest = stripped.partition(":")
        key = key.strip()
        rest = rest.strip()
        if rest:
            mapping[key] = _coerce_scalar(rest)
            idx += 1
        else:
            idx += 1
            child, idx = _parse_block(lines, idx, indent + 1)
            mapping[key] = child if child is not None else {}
    return mapping, idx


def load_config_text(text: str) -> Dict[str, Any]:
    """Parse the OTel-config YAML subset into a dict. Raises ValueError on
    structural problems (tabs, etc.)."""
    raw: List[Tuple[int, str]] = []
    for n, line in enumerate(text.splitlines(), 1):
        if "\t" in line.replace(line.lstrip("\t"), ""):
            raise ValueError(f"line {n}: tabs are not allowed in YAML indentation")
        cleaned = _strip_comment(line)
        if cleaned.strip() == "":
            continue
        indent = len(cleaned) - len(cleaned.lstrip(" "))
        raw.append((indent, cleaned.rstrip()))
    if not raw:
        return {}
    value, _ = _parse_block(raw, 0, raw[0][0])
    if not isinstance(value, dict):
        raise ValueError("top-level config must be a mapping")
    return value


# --------------------------------------------------------------------------- #
# Validation / lint logic                                                     #
# --------------------------------------------------------------------------- #
def _names(section: Any) -> List[str]:
    if isinstance(section, dict):
        return list(section.keys())
    return []


def _as_list(v: Any) -> List[Any]:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def validate_config(cfg: Dict[str, Any]) -> ValidationResult:
    """Lint a parsed OTel collector config for real misconfigurations."""
    findings: List[Finding] = []

    receivers = _names(cfg.get("receivers"))
    processors = _names(cfg.get("processors"))
    exporters = _names(cfg.get("exporters"))
    service = cfg.get("service") or {}
    pipelines = (service.get("pipelines") or {}) if isinstance(service, dict) else {}

    if not isinstance(cfg.get("receivers"), dict) or not receivers:
        findings.append(
            Finding("E001", "error", "no receivers defined", "receivers")
        )
    if not isinstance(cfg.get("exporters"), dict) or not exporters:
        findings.append(
            Finding("E002", "error", "no exporters defined", "exporters")
        )
    if not pipelines:
        findings.append(
            Finding("E003", "error", "service.pipelines is empty", "service.pipelines")
        )

    used_recv: set = set()
    used_proc: set = set()
    used_exp: set = set()

    for pname, pdef in pipelines.items() if isinstance(pipelines, dict) else []:
        loc = f"service.pipelines.{pname}"
        pdef = pdef or {}
        p_recv = _as_list(pdef.get("receivers"))
        p_proc = _as_list(pdef.get("processors"))
        p_exp = _as_list(pdef.get("exporters"))
        used_recv.update(p_recv)
        used_proc.update(p_proc)
        used_exp.update(p_exp)

        if not p_recv:
            findings.append(
                Finding("E010", "error", f"pipeline '{pname}' has no receivers", loc)
            )
        if not p_exp:
            findings.append(
                Finding("E011", "error", f"pipeline '{pname}' has no exporters", loc)
            )
        for r in p_recv:
            if r not in receivers:
                findings.append(
                    Finding("E012", "error",
                            f"pipeline '{pname}' references undefined receiver '{r}'", loc)
                )
        for pr in p_proc:
            if pr not in processors:
                findings.append(
                    Finding("E013", "error",
                            f"pipeline '{pname}' references undefined processor '{pr}'", loc)
                )
        for e in p_exp:
            if e not in exporters:
                findings.append(
                    Finding("E014", "error",
                            f"pipeline '{pname}' references undefined exporter '{e}'", loc)
                )
        if "batch" not in p_proc:
            findings.append(
                Finding("W020", "warning",
                        f"pipeline '{pname}' has no 'batch' processor (recommended for throughput)",
                        loc)
            )

    # Orphaned components: defined but never wired into a pipeline.
    for r in receivers:
        if r not in used_recv:
            findings.append(
                Finding("W030", "warning", f"receiver '{r}' is defined but unused",
                        f"receivers.{r}"))
    for pr in processors:
        if pr not in used_proc:
            findings.append(
                Finding("W031", "warning", f"processor '{pr}' is defined but unused",
                        f"processors.{pr}"))
    for e in exporters:
        if e not in used_exp:
            findings.append(
                Finding("W032", "warning", f"exporter '{e}' is defined but unused",
                        f"exporters.{e}"))

    # Endpoint hygiene across receivers + exporters.
    for section_name in ("receivers", "exporters"):
        section = cfg.get(section_name) or {}
        if not isinstance(section, dict):
            continue
        for comp_name, comp in section.items():
            endpoint = _find_endpoint(comp)
            if not endpoint:
                continue
            loc = f"{section_name}.{comp_name}"
            for suspect in _SUSPECT_ENDPOINTS:
                if suspect in str(endpoint):
                    sev = "warning" if suspect == "0.0.0.0" else "info"
                    findings.append(
                        Finding("W040", sev,
                                f"endpoint '{endpoint}' contains '{suspect}' "
                                f"(check before shipping)", loc))
            if section_name == "exporters" and str(endpoint).startswith("http://"):
                findings.append(
                    Finding("W041", "warning",
                            f"exporter '{comp_name}' uses plaintext http:// endpoint", loc))

    summary = {s: sum(1 for f in findings if f.severity == s) for s in SEVERITIES}
    ok = summary["error"] == 0
    return ValidationResult(ok=ok, findings=findings, summary=summary)


def _find_endpoint(comp: Any) -> Optional[str]:
    if not isinstance(comp, dict):
        return None
    if "endpoint" in comp and isinstance(comp["endpoint"], (str, int)):
        return str(comp["endpoint"])
    # OTLP receivers nest under protocols.grpc/http.
    protocols = comp.get("protocols")
    if isinstance(protocols, dict):
        for proto in protocols.values():
            if isinstance(proto, dict) and "endpoint" in proto:
                return str(proto["endpoint"])
    return None


# --------------------------------------------------------------------------- #
# Bundle generation                                                           #
# --------------------------------------------------------------------------- #
DEFAULT_COLLECTOR_CONFIG = """\
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 127.0.0.1:4317
      http:
        endpoint: 127.0.0.1:4318
processors:
  batch:
    timeout: 5s
  memory_limiter:
    check_interval: 1s
    limit_percentage: 80
exporters:
  prometheus:
    endpoint: 127.0.0.1:8889
  debug:
    verbosity: normal
service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [debug]
    metrics:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [prometheus]
    logs:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [debug]
"""


def _dashboard_json() -> Dict[str, Any]:
    return {
        "title": "OTELBOX Overview",
        "schemaVersion": 39,
        "panels": [
            {
                "id": 1,
                "title": "Spans received (rate)",
                "type": "timeseries",
                "targets": [{"expr": "rate(otelcol_receiver_accepted_spans[5m])"}],
            },
            {
                "id": 2,
                "title": "Exporter send failures",
                "type": "timeseries",
                "targets": [{"expr": "rate(otelcol_exporter_send_failed_spans[5m])"}],
            },
            {
                "id": 3,
                "title": "Queue size",
                "type": "timeseries",
                "targets": [{"expr": "otelcol_exporter_queue_size"}],
            },
        ],
    }


def build_bundle(name: str = "otelbox") -> Dict[str, str]:
    """Return a dict of {relative_path: file_contents} forming a runnable
    local collector + dashboards bundle. Pure data generation, no I/O."""
    datasource = {
        "apiVersion": 1,
        "datasources": [
            {
                "name": "OTELBOX-Prometheus",
                "type": "prometheus",
                "access": "proxy",
                "url": "http://127.0.0.1:9090",
                "isDefault": True,
            }
        ],
    }
    prom_scrape = (
        "global:\n"
        "  scrape_interval: 15s\n"
        "scrape_configs:\n"
        "  - job_name: otel-collector\n"
        "    static_configs:\n"
        "      - targets: ['127.0.0.1:8889']\n"
    )
    return {
        f"{name}/collector.yaml": DEFAULT_COLLECTOR_CONFIG,
        f"{name}/prometheus.yml": prom_scrape,
        f"{name}/grafana/datasource.json": json.dumps(datasource, indent=2) + "\n",
        f"{name}/grafana/dashboard.json": json.dumps(_dashboard_json(), indent=2) + "\n",
        f"{name}/README.txt": (
            "OTELBOX local bundle\n"
            "--------------------\n"
            "Generated by otelbox. Validate with: otelbox lint collector.yaml\n"
            "Components bind to 127.0.0.1 only (local, defensive default).\n"
        ),
    }
