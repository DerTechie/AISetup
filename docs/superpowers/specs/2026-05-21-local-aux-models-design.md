# Local Auxiliary Models on Arch — Design Spec

- **Date:** 2026-05-21
- **Status:** Approved — implementing
- **Owner:** DerTechie
- **Relates to:** Revives the deferred "second small local model for aux" alternative from [`../../journal/2026-05-21-prefix-cache-aux-eviction.md`](../../journal/2026-05-21-prefix-cache-aux-eviction.md), but on **dedicated Arch hardware** instead of the Mac. Builds on the route roles in [`2026-05-20-model-route-roles-redesign.md`](2026-05-20-model-route-roles-redesign.md) (`main`/`private`/`deep`). Does **not** change those roles.

---

## 1. Why this work

Hermes runs several **auxiliary** LLM tasks beside the main agent: `title_generation`, `compression` (context summarization), `triage_specifier` (Kanban spec-writing), `profile_describer`, and `curator` (a background skill-maintenance agent). All five currently route to the **cloud** `main` route (GPT-5-mini via the Mac LiteLLM gateway).

They were put on the cloud as a *fix*, not a preference. The original failure: running `title_generation` on the **same single-slot** local model on the Mac evicted the agent's 18k-token KV cache mid-conversation, turning every turn into a ~2-minute cold re-ingest (see the eviction journal). Routing aux to the fast cloud route made the symptom disappear.

The goal now: **min-max the Hermes auxiliary lane** — run it locally for privacy (in mind, not enforced) and zero marginal cost, **without** reintroducing the cache-eviction problem and **without** taxing the workstation. The success metric is a **snappy workflow**. The enabling change is hardware: a dedicated **AMD Radeon RX 7900 XTX (24 GB, ROCm)** in the Arch workstation, separate from the Mac's main model — so aux can run on-device and never touch the Mac's KV cache.

## 2. Hardware & current state (measured, 2026-05-21)

| Component | State |
|---|---|
| Arch GPU | RX 7900 XTX, 24 GB VRAM, ROCm at `/opt/rocm` |
| Arch Ollama | 0.23.3, enabled service; `qwen3:4b-instruct-2507-q4_K_M` (2.5 GB) + `qwen3.6:27b` (17 GB) already pulled |
| Arch system RAM | 30 GB |
| Mac | M2 Max, headless **no-sleep**, runs LiteLLM gateway `:4000` + Ollama (`qwen3.6:27b` = the `private` route) |
| Gateway IP | `http://10.63.0.32:4000/v1`, €100/month hard cap |

GPU verified working: a `qwen3:4b-instruct-2507` load reports **`100% GPU`** in `ollama ps`, 5.7 GB resident at 32k context.

## 3. What each aux task actually does (verified against installed source)

Source: `~/.hermes/hermes-agent/`. This map drove every sizing decision — it corrected an early wrong assumption that `triage_specifier` was a request router.

| Task | What it does | Hot path? | Output | Capability need |
|---|---|---|---|---|
| `title_generation` | 3–7 word conversation title (`agent/title_generator.py`) | **Yes** (per convo) | freeform string | trivial |
| `compression` | prose summary of old turns (`agent/conversation_compression.py`) | **Yes** (mid-convo) | freeform prose | low |
| `triage_specifier` | flesh a Kanban one-liner into a full spec (`hermes_cli/kanban_specify.py`) | No (on-demand) | JSON `{title, body}`, **lenient** (falls back to body on parse fail) | moderate (writing) |
| `profile_describer` | short profile description (`hermes_cli/profile_describer.py`) | No | JSON 1-key, lenient | low |
| `curator` | forked tool-using AIAgent that walks/maintains skills, writes a report (`agent/curator.py`) | No | agentic loop + report | **high** |

**Key facts that shaped the design:**
- `triage_specifier` is **spec-writing, not routing**, it is **off the hot path** (only `hermes kanban specify`), and its JSON is parsed leniently — so a weaker model degrades gracefully, it does not break.
- `curator` is the only capability-hungry task. It is **inactivity-triggered, no daemon**: runs only when idle ≥ `min_idle_hours` (config: **2 h**) and the last run was > `interval_hours` ago (config: **168 h = weekly**); 600 s timeout. So: rare, background, never on the hot path.

## 4. Design decisions & rationale

### 4.1 One general model, not a zoo of specialists

**Decision:** a single small general instruct model serves all locally-run aux tasks.

**Rationale + hard facts:**
- For **titling/summarization**, task-specialized fine-tunes are effectively obsolete: small *general* instruct models (Phi3-Mini, Llama-3.2-3B) produce summaries "comparable to 70B LLMs," and instruction-tuning had *negligible-or-negative* effect with simple prompts ([arXiv 2502.00641](https://arxiv.org/abs/2502.00641)). Dedicated HF fine-tunes (e.g. `Llama-Chat-Summary-3.2-3B`) have ~47 downloads/month and restrictive licenses.
- **Hot-swapping multiple models on one GPU is a named anti-pattern** ("model churn"): cold model loads cost ~15–45 s each, paid repeatedly when interleaved aux tasks map to different models. Standard guidance is the opposite — keep one model resident.

**Rejected:** many task-specialized small models (per-task fine-tunes / HF specialized models). Killed by the two points above.

### 4.2 Model size: a 4B, not 8B / 14B / 30B-A3B

**Decision:** `qwen3:4b-instruct-2507` (Q4_K_M) for the locally-run aux tasks.

**Rationale + hard facts (Qwen3 family, vendor-reported via the Qwen3 Technical Report [arXiv 2505.09388](https://arxiv.org/pdf/2505.09388) and the 2507 HF cards; AMD/ROCm tok/s likely below the NVIDIA-measured figures — re-measure):**

| Model | IFEval | BFCL-v3 (tool-use) | MMLU-Pro | ~tok/s @ Q4, 24 GB | VRAM (Q4) |
|---|---|---|---|---|---|
| **Qwen3-4B-Instruct-2507** | **83.4** | 61.9 | 69.6 | very fast | ~3 GB (5.7 GB @ 32k ctx) |
| Qwen3-8B | 83.0 (85.0 think) | 60.2 / 68.1 think | — | ~131 | ~5–6 GB |
| Qwen3-14B | 84.8 | 63.2 / **70.4** think | — | ~64 | ~9 GB |
| Qwen3-30B-A3B (MoE) | 83.7 (84.7 in 2507) | 58.6 / 69.1 think (65.1 in 2507) | 78.4 (2507) | ~130–196 | **~18 GB** |

- The **8B → 30B-A3B tool-use gain is marginal** (BFCL 68.1 → 69.1 thinking; 8B is *higher* non-thinking). The MoE's real edge is reasoning/coding judgment (LiveCodeBench 57.5 → 62.6) — overkill for housekeeping.
- **14B is the worst pick:** slower than the 8B and not clearly better than the MoE.
- **30B-A3B is reserved** as a candidate for the Mac *main* model, and at ~18 GB it would crowd the workstation GPU (see §4.5).
- The 2507 4B nearly matches the *original* 30B-A3B on knowledge (MMLU-Pro 69.6 vs 69.1) — plenty for utility work.

### 4.3 Empirical proof the 4B is sufficient (incl. triage_specifier)

We did not trust the benchmarks alone. We ran the **exact production `triage_specifier` prompt** (from `kanban_specify.py`, `temperature=0.3`) through `qwen3:4b-instruct-2507` on the 7900 XTX, on three realistic one-liners:

| Input one-liner | Valid JSON | Sections | Latency | Throughput |
|---|---|---|---|---|
| "add dark mode" | ✅ | Goal/Approach/Acceptance/Out-of-scope | 3.1 s | ~74 tok/s |
| "backup db nightly" | ✅ | all | 1.9 s | ~125 tok/s |
| "investigate slow search" | ✅ | all | 2.2 s | ~122 tok/s |

**3/3 valid JSON, all required sections, genuinely actionable specs** (correct goals, concrete approach bullets, verifiable checklists, sane out-of-scope). The feared "3B JSON cliff" (3B emits valid JSON only ~48–56% of the time, per AscentCore) **did not materialize** for the 2507 4B. Minor cosmetic nit: it doubled bold markers (`****Goal****`); content is correct.

**Conclusion:** the 4B clears the hardest aux task. The hot-path tasks (title, compression) are strictly easier.

### 4.4 Route everything through LiteLLM (observability over a round trip)

**Decision:** local aux calls go Hermes (Arch) → **LiteLLM gateway (Mac)** → Arch Ollama, not direct to localhost.

**Rationale:** observability is a project pillar (dashboard now → Langfuse later → the talk). Routing through the gateway puts *every* call — local 4B, Mac-27B curator, cloud — in one pane. The cost is two LAN hops (~1–2 ms each), **negligible** against multi-hundred-ms generation. The Mac is headless no-sleep, so the added dependency is acceptable.

**Rejected:** direct-to-localhost Ollama. Lower latency and Mac-independent, but leaves aux calls invisible — fights the observability goal. The latency win is irrelevant on a LAN.

**Supporting changes:** register `aux-local` in LiteLLM → `ollama/qwen3:4b-instruct-2507` at the Arch IP with **`cost_per_token: 0`** (logged, never counts against the €100 cap); bind Arch Ollama to the LAN (`OLLAMA_HOST=0.0.0.0`) scoped by firewall to the Mac.

### 4.5 Curator on the Mac `private` route, not local Arch, not cloud-by-default

**Decision:** `curator` → gateway model `private` (Mac `qwen3.6:27b`).

**Rationale:** the curator is the one capability-hungry task, but it runs **weekly, only when idle**. The dense 27B is more capable than any small Arch model and **free + on-device**. Because it fires only after ≥2 h idle, the KV-cache eviction that doomed title-gen is harmless here — the only cost is **one ~84 s cold re-ingest on the *next* turn** after a run (measured cold-ingest figure). It must **not** run on Arch: that would need a large resident model competing with the future autocomplete (§6).

**Caveat to verify:** the Mac generates at ~11 tok/s; an agentic loop may need the **600 s timeout raised**.

## 5. Final architecture

```
Hermes (Arch) ──aux──> LiteLLM gateway (Mac :4000) ──┬── aux-local ──> Ollama (Arch 7900XTX) qwen3:4b   [title, profile_describer, triage_specifier]
                                                     ├── private   ──> Ollama (Mac M2 Max)  qwen3.6:27b [curator, weekly/idle]
                                                     └── main      ──> GPT-5-mini / OpenRouter           [compression — see §10]
```

| Aux task | Gateway model | Backend | Why |
|---|---|---|---|
| `title_generation` | `aux-local` | Arch 4B | hot path, trivial, snappy + private |
| `compression` | `main` | GPT-5-mini | summary ctx must ≥ main + 64k floor — see §10 |
| `profile_describer` | `aux-local` | Arch 4B | light |
| `triage_specifier` | `aux-local` | Arch 4B | off path; **proven** in §4.3 |
| `curator` | `private` | Mac 27B | rare, capable, free, on-device |

## 6. VRAM budget on the workstation (the constraint that ruled out big-local)

The 7900 XTX is the **daily-driver** GPU and must later also host a **local coding-autocomplete** model. Budget at Q4_K_M:

| Item | VRAM |
|---|---|
| Desktop / browser | ~1–2 GB |
| Aux 4B (trim `num_ctx` below 32k) | ~3–5.7 GB |
| Future autocomplete: **Qwen2.5-Coder-7B** Q4 | ~4.7 GB + KV |
| **Used** | **~10–12 GB** |
| **Free** | **~12–14 GB** |

Closes comfortably. **Autocomplete note (future, out of scope):** `Qwen2.5-Coder-7B` (base, FIM, Apache-2.0, ~110 tok/s measured on a 7900 XTX) is the pick — **not Qwen3-Coder**, which is large-only (smallest checkpoint 30B-A3B). ROCm caveat: gfx1100 token-gen may trail the Vulkan llama.cpp backend; try Vulkan if sluggish.

## 7. Implementation steps

1. **Mac / LiteLLM:** add `aux-local` model → `ollama/qwen3:4b-instruct-2507` at the Arch LAN IP, `cost_per_token: 0`; reload the gateway.
2. **Arch / Ollama:** set `OLLAMA_HOST=0.0.0.0`, restart the service, firewall-scope `:11434` to the Mac.
3. **Arch / Hermes** (`~/.hermes/config.yaml`): point `title_generation`, `profile_describer`, `triage_specifier` at `model: aux-local`; point `compression` at `model: main` (see §10); point `curator` at `model: private`; raise `curator` timeout.
4. **Docs:** update `README.md` and `docs/runbook.md` in the same change.

## 8. Verification gates

- [x] GPU/ROCm in use on Arch (`ollama ps` → 100% GPU).
- [x] 4B produces valid, quality `triage_specifier` output (§4.3).
- [x] Gateway round trip works: `aux-local` call via the gateway returns from the Arch 4B (`served model: aux-local`, ~1 s round trip, usage tracked). Required binding Arch Ollama to the LAN (`OLLAMA_HOST=0.0.0.0:11434`) and removing a stray `iptables`-created `ip filter` drop on port 11434.
- [x] `aux-local` calls register €0 (`x-litellm-response-cost-original: 0.0`).
- [x] `curator` path verified: dry-run runs clean (`llm: skipped`, no skills yet) and its backend `private` route (Mac 27B) answers through the gateway (~6 s). Full agentic loop within 1800 s is only measurable once agent-created skills exist.
- [x] Firewall persistence resolved (see below).
- [x] A real Hermes turn titled via `aux-local` in live use: `agent.auxiliary_client: Auxiliary title_generation: using custom (aux-local)` at 14:10:14 local = the 103-token, €0 gateway spend-log entry at 12:10:14Z. Full loop (Hermes → gateway → Arch 4B) confirmed on-device and free.

*(That "Could not detect context length … defaulting to 256,000" log turned out to be more than cosmetic — it pointed at a real silent-truncation bug and drove the §10 follow-up below.)*

**Firewall persistence — RESOLVED.** The box runs no general firewall (stock Arch ships none enabled), so rather than enable the default-deny skeleton we persisted only the surgical `ollama_guard` table via a boot-time oneshot unit. Captured in [`arch/`](../../../arch/): `ollama-lan.conf` (LAN bind), `nftables-ollama-guard.nft` (the fence), `ollama-guard.service` (`enabled`, loads it after the network is up without flushing libvirt's tables). Verified `enabled + active`. Debugging story (an `iptables`-nft-shim trap silently dropped `:11434`) in [`docs/journal/2026-05-21-firewall-iptables-nft-shim-trap.md`](../../journal/2026-05-21-firewall-iptables-nft-shim-trap.md).

## 9. Non-goals

Changing route roles (`main` stays default brain); GDPR/Presidio; the autocomplete implementation (future); replacing the Mac main model.

## 10. Follow-up — context-length detection & compression re-route (2026-05-21)

The aux work shipped with a warning that Hermes "could not detect context length … defaulting to 256,000." Investigating it surfaced a real **silent-truncation** bug and an unsafe compression route. Four findings:

1. **Every alias probes-down to 256k.** Hermes reads context from the endpoint's `/v1/models`; LiteLLM's omits it, and the gateway aliases aren't in models.dev — so all default to 262144.
2. **Silent truncation (local).** Hermes budgeted `private` against 256k while Ollama loaded `qwen3.6:27b` at **32k** → Ollama truncated the prompt with no error. Lost context on `private` in normal use.
3. **`private` below Hermes' minimum.** Hermes requires **≥64k** for any main-agent model; `private` is the `main` budget-cap fallback, so it qualifies. It was at 32k.
4. **Compression route unsafe.** The summary model must have a context window **≥ the main model's**, and Hermes enforces a **64k hard floor** on it; below either, the middle turns are dropped *without* a summary (silent context loss). `compression` was on the 4B at 32k — failing both.

**Fixes (all verified):**

- **Per-model `context_length` declared in Hermes** (`~/.hermes/config.yaml`, gateway `custom_providers` `models:` map) — `main`/`deep` = 400000 (GPT-5-mini/GPT-5, true windows from the OpenRouter API), `private` = 65536, `aux-local` = 32768. This override is consulted **before** the probe/cache (`get_model_context_length` step 0b), so it always wins; verified by calling Hermes' own resolver against the live config. The fix is **Hermes-side, not LiteLLM** (LiteLLM's `/v1/models` can't carry it). Stale `context_length_cache.yaml` blanked.
- **Mac `private` bumped to `num_ctx: 65536`** (`mac/litellm-config.yaml`) so the 65536 declaration is truthful. Measured **26 GB / 100% GPU** on the 32 GB Mac (default f16 KV — Qwen3 GQA keeps the KV small); no `q8_0` or `iogpu.wired_limit` change needed. Verified live: a gateway `private` request, then `ollama ps` → `CONTEXT 65536`, `UNTIL Forever`.
- **`compression` re-routed `aux-local` → `main`** (≥ main always, clears the 64k floor). The other three aux tasks stay on `aux-local` — they're short single-shot tasks, not whole-conversation summaries, so the rule doesn't apply.

Story: [`docs/journal/2026-05-21-context-length-silent-truncation.md`](../../journal/2026-05-21-context-length-silent-truncation.md).
