# Stdlib-only Python sidecar that keeps the LiteLLM gateway's per-route prices
# in sync with OpenRouter. See docs/superpowers/specs/2026-05-26-litellm-price-refresh-design.md.
FROM python:3.12-alpine

WORKDIR /app
COPY refresh.py /app/refresh.py

# Logs straight to stdout (docker logs prices-refresher).
ENV PYTHONUNBUFFERED=1

CMD ["python", "/app/refresh.py"]
