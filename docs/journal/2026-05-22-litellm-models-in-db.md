# 2026-05-22 — Moving model routes into the DB so swaps don't need a redeploy

## Context / where this started

Right after re-picking the cloud routes, the question surfaced: *every* model change
meant editing `litellm-config.yaml` and running a `docker compose` restart on the NAS.
For a model swap that's overkill. The UI looked like it should allow editing the model,
but the routes showed as **read-only / "config"** and couldn't be changed there.

## What was actually happening (not a bug)

Models defined in `config.yaml` are **read-only in the LiteLLM UI by design** — the file
is the source of truth, so the UI locks them. Confirmed live: `/model/info` reported
`db_model=False` for all five routes. Nothing was misconfigured; this is how config-defined
models behave.

LiteLLM's answer is `general_settings: store_model_in_db: true` — model definitions live in
Postgres instead, and are then add/edit/deletable through the UI or `/model/new` +
`/model/update` **with no restart**. The DB was already wired up (per-route `max_budget`
only works because of it), so enabling the flag is low-risk.

## What we decided and why

- **Go all-DB** (chosen over a hybrid keeping `main`/`deep` in config). Every route moves to
  the database; `litellm-config.yaml` keeps only `litellm_settings` (fallbacks, retries,
  Langfuse callback) and `general_settings` (master key + the `store_model_in_db` flag).
- **The cost:** DB-stored models are **not in git**, so the per-change history of "when did a
  route change and why" is lost for routes — a real giveaway for a project that treats the
  config as part of the reconstructable record. Accepted deliberately in exchange for
  restart-free model swaps in the UI.
- **The mitigation:** a git-tracked **seed** — `nas/models.seed.json` (the five routes, with
  per-route rationale carried as `model_info.description` so it shows in the UI) plus
  `nas/seed_models.py` to apply it (`--force` to overwrite, `--dry-run` to preview). This is a
  **bootstrap / disaster-recovery artifact, not a source of truth**: live UI edits can drift
  from it. Routing *safety* logic that must stay reviewable (fallbacks, the budget split as
  seeded values) is documented in git even though the live budgets now live in the DB.
- **One last restart is unavoidable.** Config-defined models can only be removed by editing
  the file + restarting; after that, model management is restart-free forever. We folded the
  route re-pick deploy into this same restart — the new models (`deepseek-v4-flash` /
  `gemini-3.1-pro` / `gpt-5` fallback) go straight into the DB via the seed.

## What we rejected / considered

- **Hybrid (infra in config, only `main`/`deep` in DB):** keeps git history for the stable
  routes. Rejected in favour of uniform all-DB management; the local/private routes change
  rarely anyway, and split source-of-truth is more confusing than one model.
- **Stay config-only, accept the restart:** simplest, fully git-tracked, but it's exactly the
  friction that prompted this. Rejected.

## Deploy (2026-05-22) — and the credential gotcha it surfaced

Deployed the model-less config + restarted, then seeded all five routes (`seed_models.py`).
Migration mechanics worked: `/model/info` showed `db_model=True` for all five, **per-route
`max_budget` carried** (50/35/15 — the €100 cap survived the API path), and the `main →
private` budget-cap fallback **resolved against DB routes** (proven by what happened next).

Then verification caught a real failure: a live `main` call came back **served by
`qwen3.6:27b`** — i.e. the cloud route 401'd and silently fell back to local `private`.
`/health` showed all three cloud routes failing with OpenRouter **401 "Missing Authentication
header"** — the gateway was sending an *empty* key. `deep` was fully down (both it and its
`deep-fallback` are cloud).

**Root cause:** DB-stored models do **not** resolve `os.environ/OPENROUTER_API_KEY` at call
time the way config models do. Config `api_key: os.environ/...` is resolved at YAML load;
a DB model stores the credential and expects a **literal** value (encrypted at rest). The
seeded ref resolved to nothing → empty key. Confirmed empirically: `docker exec` showed
`OPENROUTER_API_KEY=set` (so the container *had* the key) but `LITELLM_SALT_KEY=` (unset).
This is the hidden cost of all-DB that the docs bury: **DB credentials need real values + a
stable `LITELLM_SALT_KEY`**, which config-file models never required.

**Fix:** add a fixed `LITELLM_SALT_KEY` to the stack `.env` + compose (set once, never change —
it derives the encryption key for stored creds), restart, then re-seed the cloud routes with
the **real** key. `seed_models.py` now expands `os.environ/VAR` refs to the live secret at
seed time, so `models.seed.json` stays git-clean while the DB gets an encrypted literal. Run
the seed where the secret is present (NAS, `.env` sourced).

## Resolved + verified live (2026-05-22)

Set `LITELLM_SALT_KEY` in the stack `.env`, recreated the container with `docker compose up -d`
(a plain `restart` does NOT re-read env/compose changes — needed `up -d`), and re-seeded with
`--force` and `OPENROUTER_API_KEY` sourced from the `.env`. (zsh gotcha: `source ./.env` — bare
`. .env` searches `$PATH`, not cwd.) Verified: `/health` healthy for deepseek-v4-flash, gemini,
and both local routes; live `main` and `deep` calls served by the **cloud** models, not the qwen
fallback. The one `/health` "unhealthy" is `gpt-5` (`deep-fallback`) — the known `max_tokens<16`
probe artifact, not a real outage (it returns fine on a real request with `max_tokens: 256`).

- **Lesson worth keeping:** after any all-DB cloud-route change, **verify the served model**,
  not just HTTP 200 — the `main → private` fallback turns an auth failure into a silent success.
