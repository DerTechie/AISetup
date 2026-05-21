# 2026-05-21 — Bringing Hermes' auxiliary models on-device (on the Arch GPU)

## Context

Two days earlier we'd routed Hermes' text auxiliary tasks (`title_generation`, `compression`,
`triage_specifier`, `profile_describer`, `curator`) to the **cloud** to stop them evicting the
Mac's main-model KV cache (see [the eviction journal](2026-05-21-prefix-cache-aux-eviction.md)).
That was a workaround, not a preference. With a **Radeon RX 7900 XTX (24 GB)** sitting in the
Arch workstation, we revisited the deferred "small local model for aux" alternative — this time
on *dedicated* hardware that never touches the Mac's cache.

Full design: [`../superpowers/specs/2026-05-21-local-aux-models-design.md`](../superpowers/specs/2026-05-21-local-aux-models-design.md).

## The decision arc (this is the story)

**1. "Specialized models or one bigger model?" — research, not memory.** The instinct to grab
task-specific fine-tunes off Hugging Face was wrong. Evidence: small *general* instruct models
match 70B on summarization with simple prompts ([arXiv 2502.00641](https://arxiv.org/abs/2502.00641));
the dedicated chat-summary fine-tunes have ~47 downloads/month; and hot-swapping many models on
one GPU is a named anti-pattern (~15–45 s cold loads each). **One general model, kept resident.**

**2. The misconception that reset the whole sizing question.** We assumed `triage_specifier`
was a *request router* (decide local vs cloud) — which would demand reliable JSON and good
judgment. Reading the installed source (`kanban_specify.py`) proved it is a **Kanban spec-writer**,
off the hot path, with **lenient** JSON parsing. The "3B JSON cliff" worry largely evaporated.
Lesson, again: **read the actual code before sizing the model.**

**3. Only the curator is truly capability-hungry — and it's rare.** Mapping every aux task to
{hot-path?, output, capability} showed the workload splits cleanly. `curator` is a weekly,
idle-triggered agentic loop (`interval_hours: 168`, `min_idle_hours: 2`). So it doesn't belong
on the workstation GPU at all — it goes to the Mac's `private` 27B (free, on-device, capable);
its cache-bust is harmless because it only fires when you're away, costing one ~84 s re-ingest
on return.

**4. Prove it, don't argue it.** Rather than trust benchmark tables, we ran the **exact**
`triage_specifier` prompt through the local `qwen3:4b-instruct-2507` on three real one-liners:
**3/3 valid JSON, all sections, actionable specs, 1.9–3.1 s at ~74–125 tok/s, 100% GPU.** The 4B
clears the hardest aux task. Benchmark-and-verify discipline paid off a third time.

**5. The observability reversal.** First lean was direct-to-localhost Ollama (lowest latency).
Reconsidered: observability is a pillar of this project (and the talk). Routing aux **through the
LiteLLM gateway** puts every call in one pane for ~1–2 ms of LAN round trip — negligible. So we
trade a hair of latency for a single source of truth.

**6. The headroom constraint that locked in "small".** A future **local coding-autocomplete**
model (`Qwen2.5-Coder-7B`, ~4.7 GB — *not* Qwen3-Coder, which is large-only) must share the
24 GB card with the desktop. A ~3 GB 4B aux model leaves ~12–14 GB free; an 18 GB 30B-A3B would
not. The working-machine budget, not raw quality, decided the size.

## Outcome

A single ~4B on the Arch GPU serves the hot-path/light aux tasks on-device and free; the curator
rides the Mac 27B; everything flows through LiteLLM for visibility; cloud stays available but is
no longer the aux default. The 30B-A3B stays free as a Mac main-model candidate.

## Rejected

- **Many task-specialized small models** — obsolete for titling/summarization; multi-model
  swapping is an anti-pattern.
- **8B / 14B / 30B-A3B local for aux** — marginal tool-use gain over 4B, slower (14B) or VRAM-hungry
  (30B-A3B ~18 GB), and they'd starve the future autocomplete model.
- **Direct-to-localhost Ollama** — kills observability for a LAN-negligible latency win.
- **Curator on Arch** — would force a big resident model onto the daily-driver GPU.
