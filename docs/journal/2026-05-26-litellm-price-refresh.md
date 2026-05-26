# 2026-05-26 — LiteLLM live OpenRouter prices

## What broke

Grafana cost panel, LiteLLM `/spend`, and OpenRouter's own dashboard each
disagreed about the same hour's spend. Investigation: **LiteLLM does not call
OpenRouter's `/api/v1/models` for cost accounting.** It looks each model up in
a JSON price map bundled in the container image. For a model not in that map
the cost silently defaults to `$0`; for a re-priced model it keeps the stale
number. Three of our four cloud routes hit this — `main` (DeepSeek V4 Flash),
`deep` (Gemini 3.1 Pro), `deep-fallback` (GPT-5) all had `(None, None)` for
`input_cost_per_token` / `output_cost_per_token` in the DB-stored routes,
meaning their per-call cost in Langfuse / `/spend` / Grafana was effectively
zero. The €100 cap was technically intact, but the spend dashboard couldn't
tell us when we were approaching it.

## What was rejected

- **Hand-edit prices in `nas/models.seed.json`.** Simple but drifts immediately;
  OpenRouter prices move ([[displaced-price-no-narrative-protection]] already
  bit us this period — opens climbed a tier).
- **Force LiteLLM to fetch the upstream community price map on startup
  (`model_cost_map_url`).** The community map is curated and lags new model IDs
  by days; doesn't solve the freshness problem.
- **n8n workflow.** n8n is already live on the NAS; HTTP nodes would work, but
  logic-as-clicks is harder to review in git than a 150-LOC Python file.
- **Host systemd timer on TrueNAS.** Considered fragile across TrueNAS Scale
  upgrades — Dockge is the existing pattern for everything else.
- **Healthchecks deadman + ntfy.** Skipped at user request — errors surface in
  `docker logs` and the restart loop; drift re-appears naturally in the Grafana
  / OpenRouter reconciliation.

## What landed

Tiny stdlib-only Python sidecar (`nas/prices-refresher/`) added to the litellm
Dockge stack. Every `REFRESH_INTERVAL_SEC` (default 3600) it diffs
`/model/info` against live OpenRouter prices and PATCHes any route whose
input/output cost changed. `nas/seed_models.py` was extended in parallel so a
fresh DB seeding (DR restore, `--force` re-create) doesn't start at `$0` and
wait for the first tick. Tests: 13 pure-function unit cases for the sidecar +
4 for the seed enrichment, all stdlib `unittest`.

Spec: [`../superpowers/specs/2026-05-26-litellm-price-refresh-design.md`](../superpowers/specs/2026-05-26-litellm-price-refresh-design.md).
Plan: [`../superpowers/plans/2026-05-26-litellm-price-refresh.md`](../superpowers/plans/2026-05-26-litellm-price-refresh.md).

## Empirical verification

First successful tick on the NAS, 2026-05-26 ~20:59 local:

```
2026-05-26 18:59:51,527 INFO starting (gateway=http://litellm:4000 interval=3600s)
2026-05-26 18:59:51,748 INFO main          prices (None, None) -> (1e-07,    2e-07)
2026-05-26 18:59:51,798 INFO deep          prices (None, None) -> (2e-06,    1.2e-05)
2026-05-26 18:59:51,842 INFO deep-fallback prices (None, None) -> (1.25e-06, 1e-05)
2026-05-26 18:59:51,887 INFO tick ok (3 openrouter routes checked, 3 updated)
```

Three observations worth keeping:

1. **All three `(None, None) → ...`.** Every cloud route had been silently
   `$0` in the gateway's accounting. None of our existing cost dashboards
   could have been right.
2. **`main` is now $0.10 / $0.20 per Mtok, not $0.112 / $0.224 from the
   2026-05-22 CSV snapshot.** OpenRouter dropped DeepSeek V4 Flash 11% in
   four days. Without the auto-refresh, Grafana would have continued under-
   counting `main` spend by ~11% — small per call, real per month.
3. **`deep` and `deep-fallback` matched the CSV exactly** ($2 / $12 and
   $1.25 / $10). For these the bug was "$0" not "wrong number". A static
   re-seed would have been correct for a week and silently wrong after.

## What we learned in the deploy

Three things weren't in the spec and are worth recording for the next person
reading this back during the talk:

- **Dockge owns the stacks.** I (Claude) assumed `/mnt/nvme/apps/litellm/` and
  was wrong; the litellm stack actually lives in Dockge's directory at
  `/mnt/nvme/apps/dockge/stacks/litellm/`, compose file is `compose.yaml`. Lost
  20 minutes copying files to the wrong place. Memory updated:
  [[nas-stacks-managed-by-dockge]].
- **`depends_on` is a sequencing hint, not a readiness gate.** First tick after
  a fresh `up` hit `ConnectionRefused` because LiteLLM was started but hadn't
  bound port 4000 yet. The simple-loop design means a missed tick costs an
  hour. Mitigation: `docker compose restart prices-refresher` once LiteLLM is
  stable — same "refresh now" knob documented in the runbook.
- **LiteLLM's `/model/update` returns a misleading 400.** The body needs
  `model_info.id` (nested), NOT a top-level `model_id`. Wrong shape returns
  `"Authentication Error, model_info not provided"` — the message looks like
  auth, but it's body validation. Cost an extra debug round to surface the
  response body in the sidecar's error log. Memory:
  [[litellm-model-update-body-shape]].

## Out-of-band — backfill?

`LiteLLM_SpendLogs` rows written before the deploy carry the wrong (zero or
stale) cost. The cost dashboards will continue to show a discontinuity at
2026-05-26 ~21:00 until those rows are either truncated or accepted as
historical. Decision deferred — flagged in the spec's [Future work].
