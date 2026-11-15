# Collector config

`collector.yaml` is mounted over `/otel-lgtm/otelcol-config.yaml` in `grafana/otel-lgtm:0.38.1` (the image's `run-otelcol.sh` starts `./otelcol-contrib/otelcol-contrib --feature-gates service.profilesSupport --config=file:./otelcol-config.yaml`).

Validate: `make otel-validate` or `make otel-validate CONFIG=ops/otel/tests/broken.yaml`. This runs, in a throwaway container of the same image:

```
/otel-lgtm/otelcol-contrib/otelcol-contrib validate --feature-gates service.profilesSupport --config=file:/cfg/config.yaml
```
