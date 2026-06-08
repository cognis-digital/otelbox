# Demo 01 - Basic: linting a broken OTel collector config

This scenario shows OTELBOX triaging a realistic, slightly-broken
OpenTelemetry Collector configuration before it ships.

## Input

`broken-collector.yaml` is a config a team copy-pasted from a demo. It has
several real problems:

- The `logs` pipeline references an exporter `otlp/backend` that is **not
  defined** under `exporters:` (typo / leftover).
- A `memory_limiter` processor is **defined but never wired** into any
  pipeline.
- The `traces` pipeline has **no `batch` processor**, hurting throughput.
- The OTLP receiver binds to `0.0.0.0`, and the exporter points at the public
  demo endpoint `demo.opentelemetry.io` over plaintext `http://`.

## Run it

```
python -m otelbox --format table lint demos/01-basic/broken-collector.yaml
```

or JSON for automation / CI gating:

```
python -m otelbox --format json lint demos/01-basic/broken-collector.yaml
```

## Expected outcome

OTELBOX reports the undefined-exporter reference as an **error** (exit code
`1`), plus warnings for the unused processor, missing batch, the `0.0.0.0`
bind, and the plaintext public endpoint. Fix the error and re-run to get a
clean `ok=true` / exit `0`.

To generate a known-good local starting point instead:

```
python -m otelbox bundle --name mybox
```
