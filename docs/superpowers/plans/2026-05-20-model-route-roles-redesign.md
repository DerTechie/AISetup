# Model-Route Roles & Brain Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the gateway routes to role-based names (`main`/`private`/`deep`), invert the default brain from the slow local model to a fast cloud model (GPT-5-mini), and propagate the change across config, the triage advisor, and docs.

**Architecture:** The LiteLLM gateway (`mac/litellm-config.yaml`) gains a cloud `main` route (GPT-5-mini) as the new default brain, renames `local`→`private`, drops `local-think`, and reworks budgets ($50/$35/$15) and fallbacks (`main`→`private` at budget cap; `deep`→`deep-fallback` on error; no hardwired context fallbacks — the triage advisor decides overflow). The triage advisor (`hermes/skills/triage-advisor/`) becomes a three-way recommender judged on `main`. Docs (`runbook.md`, `README.md`, base spec, a new journal entry) are reconciled.

**Tech Stack:** LiteLLM (YAML config, Docker), Python 3 stdlib (triage advisor, pytest), Markdown docs.

**Spec:** [`docs/superpowers/specs/2026-05-20-model-route-roles-redesign.md`](../specs/2026-05-20-model-route-roles-redesign.md)

**Note on verification environment:** The LiteLLM gateway and Ollama run on the headless Mac (`10.63.0.32`), and Hermes' `~/.hermes/config.yaml` lives on the Arch box — neither is editable from this repo. This plan covers the **repo artifacts**; live-deploy steps (restart LiteLLM, edit Hermes config) are called out explicitly as manual runbook actions, not repo edits.

---

### Task 1: Rewrite the triage-advisor tests for the three-way rubric (TDD red)

The advisor changes from a binary `local`/`deep` recommender to a three-way `main`/`private`/`deep` recommender judged on `main`. Update the tests first — they are the behavior spec.

**Files:**
- Modify: `hermes/skills/triage-advisor/test_triage_advisor.py`

- [ ] **Step 1: Replace the affected tests**

In `hermes/skills/triage-advisor/test_triage_advisor.py`, replace these test functions with the versions below (the imports at the top stay unchanged):

```python
def test_build_prompt_includes_rubric_and_task():
    msgs = build_prompt("summarize my inbox")
    assert msgs[0]["role"] == "system"
    content = msgs[0]["content"]
    assert "MAIN" in content and "DEEP" in content and "PRIVATE" in content
    assert msgs[1]["role"] == "user"
    assert "summarize my inbox" in msgs[1]["content"]


def test_parse_missing_signals_defaults_to_empty_list():
    rec = parse_recommendation('{"recommendation": "main", "reason": "routine"}')
    assert rec.recommendation == "main"
    assert rec.signals == []


def test_parse_valid_json():
    raw = '{"recommendation": "deep", "reason": "market analysis", "signals": ["strategic"]}'
    rec = parse_recommendation(raw)
    assert rec.recommendation == "deep"
    assert rec.reason == "market analysis"
    assert rec.signals == ["strategic"]


def test_parse_private_recommendation_is_valid():
    rec = parse_recommendation('{"recommendation": "private", "reason": "sensitive data"}')
    assert rec.recommendation == "private"


def test_parse_malformed_falls_back_to_unknown():
    rec = parse_recommendation("I think you should use deep, definitely")
    assert rec.recommendation == "unknown"
    assert "deep" in rec.reason


def test_parse_legacy_local_value_is_now_unknown():
    # "local" was the old route name; it must no longer validate.
    rec = parse_recommendation('{"recommendation": "local", "reason": "x"}')
    assert rec.recommendation == "unknown"


def test_parse_unexpected_recommendation_value_is_unknown():
    rec = parse_recommendation('{"recommendation": "cloud", "reason": "x"}')
    assert rec.recommendation == "unknown"


def test_format_log_line_is_valid_jsonl():
    rec = Recommendation("main", "routine", ["summary"])
    when = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    data = json.loads(format_log_line(rec, "my task", when))
    assert data["recommendation"] == "main"
    assert data["task"] == "my task"
    assert data["reason"] == "routine"
    assert data["signals"] == ["summary"]
    assert data["ts"].startswith("2026-05-20")


def test_format_output_deep_suggests_model_deep():
    out = format_output(Recommendation("deep", "needs heavy reasoning", ["strategic"]))
    assert "deep" in out
    assert "/model deep" in out
    assert "won't switch automatically" in out


def test_format_output_private_suggests_model_private():
    out = format_output(Recommendation("private", "sensitive data", []))
    assert "/model private" in out


def test_format_output_main_says_stay_no_switch():
    out = format_output(Recommendation("main", "routine drafting", []))
    assert "main" in out
    assert "/model" not in out  # main is the default; no switch command


def test_format_output_unknown_shows_raw():
    out = format_output(Recommendation("unknown", "garbled text", []))
    assert "Could not parse" in out
    assert "garbled text" in out
```

- [ ] **Step 2: Update the `call_gateway` test to expect `main`**

Replace `test_call_gateway_posts_and_extracts_content` with this version (the judge now posts `model: main` and returns a `main` recommendation):

```python
def test_call_gateway_posts_and_extracts_content(monkeypatch):
    captured = {}

    class FakeResp:
        def __init__(self, payload): self._p = payload
        def read(self): return json.dumps(self._p).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data)
        captured["auth"] = req.headers.get("Authorization")
        return FakeResp(
            {"choices": [{"message": {"content": '{"recommendation": "main", "reason": "ok"}'}}]})

    monkeypatch.setattr(triage_advisor.urllib.request, "urlopen", fake_urlopen)
    content = triage_advisor.call_gateway(
        [{"role": "user", "content": "hi"}], "http://mac:4000/v1", "sk-test")
    assert captured["url"] == "http://mac:4000/v1/chat/completions"
    assert captured["body"]["model"] == "main"
    assert captured["auth"] == "Bearer sk-test"
    assert "main" in content
```

- [ ] **Step 3: Update the config-fallback test's stubbed recommendation and sample config**

Replace `test_main_falls_back_to_config_when_env_missing` and `test_read_hermes_config_creds_parses_model_block` with these (only the recommendation value and the sample `default:` change; the assertions on creds are unchanged):

```python
def test_read_hermes_config_creds_parses_model_block(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "model:\n"
        "  default: main\n"
        "  provider: custom\n"
        "  base_url: http://10.63.0.32:4000/v1\n"
        "  api_key: sk-secret\n"
        "providers: {}\n"
    )
    base_url, api_key = triage_advisor.read_hermes_config_creds(str(cfg))
    assert base_url == "http://10.63.0.32:4000/v1"
    assert api_key == "sk-secret"


def test_main_falls_back_to_config_when_env_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("model:\n  base_url: http://mac:4000/v1\n  api_key: sk-cfg\n")
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(cfg))
    monkeypatch.setenv("TRIAGE_LOG_PATH", str(tmp_path / "log.jsonl"))
    captured = {}
    def fake_call(messages, base_url, api_key):
        captured["base_url"] = base_url
        captured["api_key"] = api_key
        return '{"recommendation": "main", "reason": "ok"}'
    monkeypatch.setattr(triage_advisor, "call_gateway", fake_call)
    assert triage_advisor.main(["prog", "summarize this"]) == 0
    assert captured["base_url"] == "http://mac:4000/v1"
    assert captured["api_key"] == "sk-cfg"
```

> Leave all other tests (`test_main_missing_task_returns_2`, `test_main_missing_env_returns_2`, `test_main_handles_unreachable_gateway`, `test_main_handles_gateway_timeout`, `test_read_hermes_config_creds_missing_file_returns_none`, `test_read_hermes_config_creds_ignores_keys_outside_model_block`) unchanged.

- [ ] **Step 4: Run the tests to verify they FAIL**

Run: `cd hermes/skills/triage-advisor && .venv/bin/pytest -q`
Expected: FAIL — e.g. `test_build_prompt_includes_rubric_and_task` fails because the current rubric has no "MAIN"/"PRIVATE"; `test_parse_missing_signals_defaults_to_empty_list` fails because `parse_recommendation` rejects `"main"`; `test_call_gateway_*` fails because the body model is `"local"`.

---

### Task 2: Update `triage_advisor.py` for the three-way rubric (TDD green)

**Files:**
- Modify: `hermes/skills/triage-advisor/triage_advisor.py`

- [ ] **Step 1: Update the module docstring and `JUDGE_MODEL`**

Replace lines 2 and 13:

```python
"""Triage advisor: ask the `main` model whether a task should run on `main`, `private`, or `deep`."""
```

```python
JUDGE_MODEL = "main"
```

- [ ] **Step 2: Replace the `RUBRIC`**

Replace the `RUBRIC = """..."""` block with:

```python
RUBRIC = """You are a routing advisor for an AI agent. The agent fetches data and runs \
tools (email, web, files) by itself, regardless of which model it uses. So do NOT route a \
task to a different model just because it involves emails, files, web pages, or \
current/external data. Routing depends on how much reasoning the task needs and whether it \
must stay on-device.

Recommend MAIN (the fast default cloud model) for routine work: triage, summarizing, \
drafting, extracting, classifying, and simple Q&A — even when the task operates on external \
or current data. MAIN is the default; prefer it unless a clear reason below applies.

Recommend DEEP (stronger cloud reasoning) only when the task needs heavy multi-step \
reasoning or synthesis — strategic or market analysis, comparing many sources, or drawing \
non-obvious conclusions — i.e. when MAIN's answer quality would likely be insufficient.

Recommend PRIVATE (the on-device local model — free but slow) only when the task handles \
sensitive data that should not leave the machine, or is high-volume bulk work where zero \
cost matters and the slowness is acceptable.

Respond with ONLY a JSON object, no prose, in exactly this shape:
{"recommendation": "main" | "deep" | "private", "reason": "<one short sentence>", "signals": ["<signal>", ...]}"""
```

- [ ] **Step 3: Update the `Recommendation` docstring and `parse_recommendation` validation**

Change the `Recommendation` dataclass field comment:

```python
    recommendation: str  # "main" | "private" | "deep" | "unknown"
```

In `parse_recommendation`, change the validation tuple:

```python
        if rec not in ("main", "private", "deep"):
            raise ValueError(f"unexpected recommendation: {rec!r}")
```

- [ ] **Step 4: Rewrite `format_output` for three outcomes**

Replace the whole `format_output` function with:

```python
def format_output(rec: Recommendation) -> str:
    if rec.recommendation == "unknown":
        return ("Could not parse a clean recommendation from the judge.\n"
                f"Raw judge output: {rec.reason}")
    signals = f" (signals: {', '.join(rec.signals)})" if rec.signals else ""
    if rec.recommendation == "main":
        action = "Stay on `main` (the default) — no switch needed"
    else:
        action = f"Switch with `/model {rec.recommendation}`?"
    return (f"Recommend **{rec.recommendation}** — {rec.reason}{signals}.\n"
            f"{action} (your call — I won't switch automatically.)")
```

- [ ] **Step 5: Run the tests to verify they PASS**

Run: `cd hermes/skills/triage-advisor && .venv/bin/pytest -q`
Expected: PASS — all tests green.

- [ ] **Step 6: Commit**

```bash
git add hermes/skills/triage-advisor/triage_advisor.py hermes/skills/triage-advisor/test_triage_advisor.py
git commit -m "feat(triage): three-way main/private/deep advisor judged on main"
```

---

### Task 3: Update the triage-advisor `SKILL.md`

**Files:**
- Modify: `hermes/skills/triage-advisor/SKILL.md`

- [ ] **Step 1: Replace the frontmatter `description`**

```markdown
description: Use BEFORE starting any market/strategic/business-research task, multi-source synthesis, or other heavy-reasoning request — and whenever unsure whether to use main, private, or deep. Run it FIRST, before any web search or analysis, to recommend main vs private vs deep. Recommend-only: the user confirms the switch; it never changes models itself. (Skip only if already deliberately on the recommended model.)
```

- [ ] **Step 2: Replace the intro paragraph and step 3**

Replace the paragraph under `# Triage Advisor` with:

```markdown
Recommend whether the current task should stay on the fast default **main** (cloud) model, drop to the on-device **private** model, or escalate to the **deep** cloud model. **Run this at the START of a qualifying task — before doing web searches or analysis — not after.** The **main** model judges; you present its recommendation; the user decides. **Never run `/model` yourself without the user's explicit go-ahead.**
```

Replace step 3 in "How to run" with:

```markdown
3. Show the script's recommendation to the user verbatim and wait for their decision. On their explicit confirmation: `/model deep` for deep, `/model private` for private. If the recommendation is `main`, that is the default — no switch is needed.
```

- [ ] **Step 3: Commit**

```bash
git add hermes/skills/triage-advisor/SKILL.md
git commit -m "docs(triage): SKILL.md describes three-way main/private/deep routing"
```

---

### Task 4: Rework `mac/litellm-config.yaml`

Add the cloud `main` route as the default brain, rename `local`→`private`, drop `local-think`, rework budgets ($50/$35/$15) and fallbacks (`main`→`private` at cap, `deep`→`deep-fallback` on error), and remove the hardwired context fallbacks (the advisor decides overflow).

**Files:**
- Modify: `mac/litellm-config.yaml`

- [ ] **Step 1: Replace the entire file contents**

```yaml
model_list:
  - model_name: main             # default brain: fast cloud (GPT-5-mini), low latency, every turn
    litellm_params:
      model: openrouter/openai/gpt-5-mini
      api_key: os.environ/OPENROUTER_API_KEY
      max_tokens: 8000          # per-request completion ceiling (GPT-5-mini caps at 128k)
      max_budget: 50            # USD - default brain runs every turn; 50 + 35 + 15 = 100 total (~EUR92)
      budget_duration: 30d
  - model_name: private          # on-device local: privacy / EUR0 / bulk; slow, no budget (always available)
    litellm_params:
      model: ollama_chat/qwen3.6:27b
      api_base: http://host.docker.internal:11434
      keep_alive: -1             # keep model resident: no reload, prompt cache stays warm
      reasoning_effort: none     # thinking off (the only local route now; local-think dropped)
  - model_name: deep             # heavy research: GPT-5 (served first-party by OpenAI + Azure)
    litellm_params:
      model: openrouter/openai/gpt-5
      api_key: os.environ/OPENROUTER_API_KEY
      max_tokens: 8000
      max_budget: 35            # USD - carved from the old 75; cloud cap stays <=100 total
      budget_duration: 30d
  - model_name: deep-fallback    # used only if `deep` (GPT-5) errors/outage; different vendor
    litellm_params:
      model: openrouter/google/gemini-3.1-pro-preview
      api_key: os.environ/OPENROUTER_API_KEY
      max_tokens: 8000          # Gemini 3.1 Pro caps at 65k; 1M context
      max_budget: 15            # USD - outage-only insurance; keeps total cloud cap at <=100
      budget_duration: 30d

litellm_settings:
  drop_params: true
  num_retries: 2
  # main hits its budget cap -> degrade to private (slow local brain), never die.
  # deep vendor outage/error -> deep-fallback (different vendor).
  # No context_window_fallbacks: the triage advisor decides overflow targets (per redesign spec).
  fallbacks: [{"main": ["private"]}, {"deep": ["deep-fallback"]}]

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
```

- [ ] **Step 2: Verify the YAML parses and the model names are correct**

Run:
```bash
python3 -c "import yaml; c=yaml.safe_load(open('mac/litellm-config.yaml')); names=[m['model_name'] for m in c['model_list']]; print(names); assert names==['main','private','deep','deep-fallback'], names; print('fallbacks:', c['litellm_settings']['fallbacks']); assert 'context_window_fallbacks' not in c['litellm_settings']; print('OK')"
```
Expected: `['main', 'private', 'deep', 'deep-fallback']`, the fallbacks list, then `OK`. (If `yaml` is missing, run `pip install pyyaml` in a scratch venv or use the repo's `.venv` at `hermes/skills/triage-advisor/.venv/bin/python`.)

- [ ] **Step 3: Commit**

```bash
git add mac/litellm-config.yaml
git commit -m "feat(gateway): main/private/deep routes, GPT-5-mini brain, 50/35/15 split"
```

> **Live deploy (manual, on the Mac — not a repo step):** `cd ~/AISetup/mac && docker compose --env-file .env restart litellm`, then `curl http://10.63.0.32:4000/v1/models -H "Authorization: Bearer $KEY"` and confirm it returns `main, private, deep, deep-fallback`. **Verify the budget-cap degrade path** (`main` → `private` on `budget_exceeded`) is honored by LiteLLM `fallbacks` — this is an open item in the spec (§9). No new secret is needed: GPT-5-mini reuses `OPENROUTER_API_KEY`.

---

### Task 5: Update `docs/runbook.md`

**Files:**
- Modify: `docs/runbook.md`

- [ ] **Step 1: Replace the "Model routes" table and the bullet under it**

Replace the table at lines 13-19 (routes table + the two bullets) with:

```markdown
| Route | Backend | Use |
|---|---|---|
| `main` | OpenRouter `openai/gpt-5-mini` (cloud) | **Default brain.** Orchestration + routine: triage, drafting, summarizing, route decisions. Fast, every turn. |
| `private` | qwen3.6:27b on Ollama, thinking **off** | On-device work where privacy or €0 is worth the latency (slow). Also the email-triage pin. |
| `deep` | OpenRouter `openai/gpt-5` | Heavy research / strategic reasoning / large context. |
| `deep-fallback` | OpenRouter `google/gemini-3.1-pro-preview` | Reliability backstop; used only if `deep` errors. |

- **At its budget cap, `main` degrades to `private`** (slow local brain) via LiteLLM `fallbacks` — the agent stays alive and the hard cap holds.
- **No automatic context fallback:** the triage advisor decides where an oversized prompt goes (it is not hardwired).
- `keep_alive: -1` keeps the local model resident (~17 GB always in memory; warm `private` responses).
```

- [ ] **Step 2: Fix the health-check and Hermes sections**

Replace the `/v1/models` expected output (was `local, local-think, deep`):

```markdown
curl http://10.63.0.32:4000/v1/models -H "Authorization: Bearer $KEY"  # -> main, private, deep, deep-fallback
```

Replace the Hermes bullets (was `default: local`, `/model deep`, `/model local-think`, `/model local`):

```markdown
- `~/.hermes/config.yaml`: `model.provider: custom`, `base_url: http://10.63.0.32:4000/v1`, `default: main`, `api_key: <litellm master key literal>`.
- Switch model in a session: `/model deep` (heavy research), `/model private` (on-device/private). `main` is the default — no switch needed to return to it.
- Pin email triage to `private` so sensitive inbox content stays on-device even though the brain is cloud.
- Config backups: `~/.hermes/config.yaml.bak-*`.
```

- [ ] **Step 3: Update the "Cost control" section**

Replace the three bullets under `## Cost control` with:

```markdown
- Cloud models: `main` = GPT-5-mini (`openrouter/openai/gpt-5-mini`, the default brain), `deep` = GPT-5 (`openrouter/openai/gpt-5`), `deep-fallback` = Gemini 3.1 Pro Preview (used only if `deep` errors).
- Hard cap on **cloud only**, split so the total stays ≤100 USD (~€92): `main` `max_budget: 50` + `deep` `max_budget: 35` + `deep-fallback` `max_budget: 15` / `budget_duration: 30d` in `litellm-config.yaml`. (`private` is local and intentionally uncapped, so it always works.)
- At cap: the capped cloud model is blocked with a `budget_exceeded` (429) error. **`main` then degrades to `private`** (slow but free) so the agent keeps working; `private` itself has no budget.
```

- [ ] **Step 4: Update the "Triage advisor" section**

Replace the judge/deepen bullets (was `local` no-think judge, `JUDGE_MODEL = "local-think"`):

```markdown
- Judge runs on the `main` route (fast cloud) — running it on the slow `private` model would cost ~2 min per recommendation. Costs a fraction of a cent. Recommend-only — it never switches models.
- Recommends one of `main` (stay, the default), `private` (on-device), or `deep` (escalate).
- Recommendations are logged to `~/.hermes/triage-advisor.jsonl` (review before considering auto-routing).
```

(Delete the old "To deepen judgment: set `JUDGE_MODEL = \"local-think\"`" bullet — `local-think` no longer exists.)

- [ ] **Step 5: Commit**

```bash
git add docs/runbook.md
git commit -m "docs(runbook): main/private/deep routes, 50/35/15 cap, main-judge advisor"
```

---

### Task 6: Update `README.md`

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the status line and the lead paragraph's routing claim**

Replace the `> **Status:** ...` line:

```markdown
> **Status:** Phase 1 (routing gateway) complete; routes redesigned to role-based names (`main`/`private`/`deep`) with a fast-cloud default brain — see the [redesign spec](docs/superpowers/specs/2026-05-20-model-route-roles-redesign.md). Phase 2 (triage advisor) — script + judge verified live; Hermes-side integration pending. See the [decision journal](docs/journal/).
```

Replace the second sentence of "## The idea" (the `A LiteLLM gateway ... routes ... local Ollama model (routine work) or to OpenRouter` sentence):

```markdown
A **LiteLLM gateway** on the Mac makes a fast cloud model (**GPT-5-mini**) the agent's default brain for speed, drops to a **local Ollama model** only when privacy or €0 is worth its latency, and escalates to a **frontier model** (GPT-5) for heavy research. This frees the workstation GPU, keeps a hard monthly cap on cloud spend, and keeps sensitive workflows (email triage) on-device.
```

- [ ] **Step 2: Update the architecture diagram edge labels**

In the mermaid block, replace the two edge labels:

```
        L -->|model: private| O
```
```
    L -->|model: deep / main| OR
```

- [ ] **Step 3: Rewrite "How routing works"**

Replace the whole numbered list under `## How routing works` with:

```markdown
The cloud brain is fast and cheap, and total cloud spend is hard-capped, so routing favors speed by default and reaches local/frontier only with reason:

1. **Default → main.** Orchestration and routine work run on the fast cloud brain (GPT-5-mini). The slow local model is *not* the default — it takes ~2 min even for trivial turns.
2. **Private on demand.** Sensitive or bulk work routes to the local `private` model (free, on-device); email triage is pinned there.
3. **Triage advisor.** When unsure, a Hermes skill asks the `main` model "main, private, or deep?" and recommends; you confirm. (Recommend-only; can become automatic later.)
4. **Explicit deep.** `/model deep`, or a cloud-pinned research subagent, for confirmed heavy research.
5. **Backstop.** A hard **€100/month** cloud cap (split `main` $50 / `deep` $35 / `deep-fallback` $15). At the cap, `main` degrades to `private` so the agent keeps working; `private` is free and uncapped.
```

- [ ] **Step 4: Update the "Components" and "Implementation phases" rows that name routes**

In the Components table, replace the Ollama and OpenRouter rows:

```markdown
| Ollama | Mac M2 Max (`:11434`) | One ~24 GB agentic model — the `private` route (privacy / €0 / bulk). |
| OpenRouter | Cloud | `main` (GPT-5-mini default brain) + `deep`/`deep-fallback` (frontier research). |
```

In "Implementation phases", replace phase 1's line:

```markdown
1. **Done (routes redesigned).** Routing in place: Mac prep + Ollama; LiteLLM gateway with role-based routes — `main` (GPT-5-mini brain), `private` (local), `deep`/`deep-fallback` (frontier) — €100/mo split cap, token cap, dashboard; wire Hermes (default `main`, `/model deep`/`/model private`).
```

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs(readme): role-based routes and fast-cloud default brain"
```

---

### Task 7: Reconcile the base spec and write the decision journal entry

**Files:**
- Modify: `docs/superpowers/specs/2026-05-20-hybrid-ai-routing-design.md`
- Create: `docs/journal/2026-05-20-model-route-roles-redesign.md`

- [ ] **Step 1: Add a superseding banner to the base spec**

Immediately after the `- **Owner:** DerTechie` line near the top of `docs/superpowers/specs/2026-05-20-hybrid-ai-routing-design.md`, insert:

```markdown
- **⚠️ Superseded in part:** §5 (Routing), §6 (Cost control), §8 (Model choice), and §13 (Success criteria) are revised by [`2026-05-20-model-route-roles-redesign.md`](2026-05-20-model-route-roles-redesign.md): routes are renamed `main`/`private`/`deep`, the default brain is now a fast cloud model (GPT-5-mini) rather than local, and the budget splits three ways ($50/$35/$15). Where the two conflict, the redesign wins.
```

- [ ] **Step 2: Write the journal entry**

Create `docs/journal/2026-05-20-model-route-roles-redesign.md`:

```markdown
# 2026-05-20 — Model-route roles & the brain inversion

## Decision

Renamed the gateway routes from implementation-based (`local`, `local-think`, `deep`, `deep-fallback`) to role-based names (`main`, `private`, `deep`, `deep-fallback`), and **inverted the default brain**: `main` is now a fast cloud model (GPT-5-mini), and the local 24 GB model demotes to a `private` specialist.

## Why

Two problems converged:

1. **`local` named a place, not a role.** It was slow and likely to be swapped for a faster backend, which would make the name lie. Routes should name what they are *for*.
2. **The slow local model can't be the brain (empirical).** It takes ~2 minutes for even the simplest task. The brain fires on *every* turn and re-prefills Hermes' ~16K system prompt each time, so a 2-minute floor on the cheapest operation made the whole agent unusable. The base spec's "Default = local" was wrong in practice.

Fixing (2) forced the default to a fast cloud model; that made the role-based naming from (1) natural to express. `main` is fully backend-agnostic — if Phase 4 ever makes a local model fast enough, the name still holds.

## What was rejected

- **Small fast local brain** (tiny model orchestrates, delegates to the 24 GB model): infeasible — 32 GB fits only one resident ~24 GB model, and 4B-class models tested inadequate for agentic use.
- **Keep local as brain, fix latency in Phase 4 first:** defers relief that blocks work now; Phase 4 gains unproven. Kept only as a future "reclaim the default" path.
- **`main` = Claude Haiku 4.5:** best agentic reputation but 4× the input cost; kept as the empirical fallback if GPT-5-mini's tool-calling proves weak.
- **Budget `€50/€35/€15`:** arithmetically over the $100 cap (~€109); corrected to USD `$50/$35/$15`.

## Trade accepted

"Private + free **by default**" became "**fast by default, private on demand**." Email triage is explicitly pinned to `private` so the one genuinely sensitive workflow still stays on-device.

## Cascade

Three-way triage advisor (`main`/`private`/`deep`) judged on `main` (not the slow local model); `main` degrades to `private` at its budget cap; `local-think` dropped (the fast `main` brain now does synthesis).
```

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-05-20-hybrid-ai-routing-design.md docs/journal/2026-05-20-model-route-roles-redesign.md
git commit -m "docs: reconcile base spec + journal the brain inversion"
```

---

### Task 8: Full verification sweep

**Files:** none (verification only)

- [ ] **Step 1: Run the full advisor test suite**

Run: `cd hermes/skills/triage-advisor && .venv/bin/pytest -q`
Expected: all tests PASS.

- [ ] **Step 2: Grep the live runtime artifacts for stale route names**

Grep only the files that should contain *active* route references (not the plan/spec/journal/tests, which legitimately mention the old names to explain or reject them):

```bash
grep -rnE '\blocal-think\b|/model local\b|model: local\b' \
  README.md docs/runbook.md mac/litellm-config.yaml \
  hermes/skills/triage-advisor/SKILL.md hermes/skills/triage-advisor/triage_advisor.py \
  || echo "no stale local/local-think route refs in live artifacts"
```
Expected: `no stale local/local-think route refs in live artifacts`.

Then confirm the judge model is `main`:
```bash
grep -n 'JUDGE_MODEL' hermes/skills/triage-advisor/triage_advisor.py
```
Expected: `JUDGE_MODEL = "main"`.

- [ ] **Step 3: Re-validate the gateway config**

Run the Task 4 Step 2 YAML check again.
Expected: `['main', 'private', 'deep', 'deep-fallback']` … `OK`.

- [ ] **Step 4: Confirm clean tree**

Run: `git status --short`
Expected: empty (everything committed).

---

## Out of scope (separate effort)

- **Live deploy** to the Mac (restart LiteLLM, swap Hermes `~/.hermes/config.yaml` `default` to `main`, pin email triage to `private`) — manual ops per the runbook, not repo edits.
- **Verifying GPT-5-mini tool-calling quality** on the real inbox (and the Haiku 4.5 fallback decision) — empirical, post-deploy.
- **Tuning the $50/$35/$15 split** against real dashboard usage.
- **Phase 4** local inference optimization (could later make `main` a local backend without renaming).
