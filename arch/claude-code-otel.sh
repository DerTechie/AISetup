# Claude Code -> NAS OTel collector (usage/cost metrics for Grafana).
# Source this from your shell profile so every Claude Code session reports:
#   echo '. ~/path/to/AISetup/arch/claude-code-otel.sh' >> ~/.bashrc
# Works on the Max subscription, independent of the LiteLLM gateway.
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_METRICS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT=http://10.63.0.2:4318
# Claude Code defaults to delta temporality; Prometheus needs cumulative counters.
export OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative
# Shorten the export interval (default 60s) so a single session shows up quickly.
export OTEL_METRIC_EXPORT_INTERVAL=10000
