# RESUME — Local-speed optimization (state as of 2026-05-21 ~06:30)

Pick up here after a context clear. Read this top-to-bottom, then continue at **"NEXT ACTION"**.

> **Tomorrow's job (2026-05-22):** **benchmark the MoE model swap first** — pull a Qwen3 *30B-A3B*
> (Mixture-of-Experts, ~3B active) and compare **decode speed AND intelligence** head-to-head against
> the current dense `qwen3.6:27b`. Owner explicitly wants the **quality/intelligence loss documented**,
> not just the speed gain. See NEXT ACTION. MLX (WS4) is **un-dropped** (kept as the next backend test);
> `n_ubatch` ingest test demoted to "only if cold ingest still matters after the generation work".
>
> **Why this changed (2026-05-21 deeper research):** two rigorous arxiv papers overturned the earlier
> "drop MLX" call and surfaced a bigger lever. See the "Web research" section below — read it before
> acting.

## Where we are

Executing the local-speed optimization for the `private` route (local `qwen3.6:27b` on the Mac
M2 Max `10.63.0.32`). Method = Approach A (diagnose → tune → model bake-off), subagent-driven
for coded tasks, direct execution for live ops.

- **Spec:** `docs/superpowers/specs/2026-05-21-local-speed-optimization-design.md`
- **Plan:** `docs/superpowers/plans/2026-05-21-local-speed-optimization.md`
- **Results log:** `mac/bench/results.md`
- **Journal:** `docs/journal/2026-05-21-prefix-cache-aux-eviction.md`
- **Runbook section:** `docs/runbook.md` → "Prefix-cache hygiene"
- **Bench harness (committed, Phase 0 DONE):** `mac/bench/` — `metrics.py`(+test), `bench_local.py`
  (`raw`/`e2e`), `cache_probe.py`.

### DONE
- **Phase 0** (benchmark harness): complete, two-stage reviewed, committed.
- **WS1 (prefix-cache fix): COMPLETE & VERIFIED.** Root cause was NOT prompt mutation — the agent
  system prompt is byte-identical across turns. The real cause: Hermes' `title_generation` (and
  other text aux) calls ran on the single-slot local model **between** turns and evicted the cached
  18k prefix, forcing full cold re-ingest every turn. Fix: routed `title_generation`, `compression`,
  `triage_specifier`, `profile_describer`, `curator` to the cloud `main` route in
  `~/.hermes/config.yaml` (under `auxiliary:`, set `provider: custom`, `model: main`,
  `base_url: http://10.63.0.32:4000/v1`, `api_key: <gateway master key>`).
  - **Result:** subsequent local turn **133s → 15.9s (~8.4×)**; first turn when prefix resident 8.3s.
  - Config backup: `~/.hermes/config.yaml.bak-20260521-034951`.

### DONE — WS2 (cold-ingest & memory tuning): COMPLETE, negative result
Measured both knobs by taking over the Mac Ollama (`OLLAMA_HOST=0.0.0.0` serve). **No speedup.**
Full numbers in `mac/bench/results.md` → "After WS2"; story in
`docs/journal/2026-05-21-ws2-cold-ingest-memory-tuning.md`.
- **WS2a — num_batch sweep:** 512/1024/2048 → 138/140/140 tok/s. Flat; not a lever. (The earlier
  `ConnectionRefused` was NOT an outage — `open -a Ollama` binds **loopback-only**, so the
  workstation couldn't reach `:11434`. Taking over with `OLLAMA_HOST=0.0.0.0` fixed reachability.)
- **WS2b — KV quant `q8_0`:** ingest ~131 tok/s (≈ baseline), generation 11.1 tok/s (unchanged),
  KV 5.7→4.7 GiB, total VRAM 24.4→21.8 GiB (~2.6 GiB freed), quality coherent. **Not persisted** —
  its only value is headroom for WS3, and persisting needs a managed `ollama serve` (the app
  ignores the env var). App restored; production back on f16/loopback and verified healthy.

## Key constraints / gotchas learned
- **The macOS Ollama app IGNORES `launchctl setenv`** (it curates its own env). So
  `OLLAMA_DEBUG_LOG_REQUESTS`, `OLLAMA_KV_CACHE_TYPE`, etc. do NOT take effect via launchctl.
  To control env, quit the app and run the bundled binary directly:
  `OLLAMA_DEBUG_LOG_REQUESTS=true OLLAMA_KEEP_ALIVE=-1 /Applications/Ollama.app/Contents/Resources/ollama serve`
  Restore afterward with `open -a Ollama`. (We did this during WS1 capture, then restored the app.)
- `num_batch` CAN be set per-request via `options.num_batch` to `/api/generate` (changing it
  reloads the runner) — so the num_batch sweep does NOT require taking over the server, only a
  reachable Ollama.
- **Cannot run two 27B Ollama instances** (32GB unified can't hold two ~17GB models) — WS2 env
  experiments must use the single instance (take it over, don't run a parallel one).
- `OLLAMA_NUM_PARALLEL=2` is NOT a fix for the cache thrash: it splits the 32k context to 16k/slot,
  below the 18k agent prompt.
- Flash attention is ALREADY ON (new engine auto-enables it; env var unset is misleading).

## Baseline numbers (qwen3.6:27b, Mac M2 Max, Ollama 0.24.0)
- Generation ~11 tok/s (raw), ~10 e2e — memory-bandwidth bound, the next big lever (WS3/WS4).
- Cold ingest ~128 tok/s synthetic; real cold first agent turn ≈ **1m24s**.
- Cache-hit ingest ~1870 tok/s (14.6×).
- Gateway: `http://10.63.0.32:4000/v1`; key in `~/.hermes/config.yaml` `model.api_key`.
  Health: `curl http://10.63.0.32:4000/health/liveliness`.

## Web research (2026-05-21) — what it changed (TWO ROUNDS; round 2 overturned round 1)

### Round 1 (blogs) — led to a wrong "drop MLX" call
First pass on blogs concluded MLX would hurt ingest (bf16 emulation on M2, slow prefill, broken
prompt cache) and recommended dropping it. **Round 2 corrected most of this — do not trust round 1.**

### Round 2 (two rigorous arxiv papers) — the corrected picture
Read in full: **arxiv 2511.05502** (Persistent Systems — head-to-head of MLX/MLC-LLM/llama.cpp/
Ollama/PyTorch-MPS on an M2 Ultra) and **arxiv 2512.23029** (Qwen3-30B on consumer hardware).

1. **MLX (WS4) is UN-DROPPED.** Three round-1 objections were wrong:
   - *Prompt caching works in MLX* — it has prompt-cache files on disk + a rotating KV cache (paper
     Table 2). The "broken cache" was **LM Studio's** impl specifically, not MLX. WS1 win can survive.
   - *No bf16 requirement* — MLX uses mixed **3/4/6/8-bit** quant; a 4-bit MLX avoids bf16 entirely.
   - *MLX is the FASTEST Apple-native decode*, not at parity: **~230 tok/s (MLX) > ~190 (MLC) >
     ~150 (llama.cpp) > 20–40 (Ollama) > 7–9 (MPS)** for decode, most stable 11–12 ms/token. Note how
     badly **Ollama** (what we run) trails — moving off Ollama is itself a likely win.
   - **BUT** for *ingest/prefill specifically*, MLX is **not** the leader: it does full prefill (no
     chunked prefill — no Apple runtime has it), TTFT rises with length, and MLC-LLM beats it on TTFT
     ≤16k. **MLX's win is decode, not ingest.** Both papers: prefill dominates long-context cost.

2. **THE BIG LEVER — our model is DENSE; the fast "30B" everyone benchmarks is an MoE.**
   `qwen3.6:27b` is a **dense 27.8B** → reads all ~16 GB of weights per token → that *is* the 11 tok/s
   bandwidth wall. The Qwen "30B" in every fast benchmark (Apple's, paper #2) is **Qwen3-30B-A3B**, a
   **Mixture-of-Experts with ~3B active params/token** → reads ~2 GB/token. On the same M2 Max that
   could plausibly hit **~40–80 tok/s** decode (vs our 11) — a *bigger* lever than backend choice or
   spec-decoding. And quality is high: paper #2 reports 30B-A3B (Q6) at **MMLU 83%, AIME 73–90%**,
   ≈ Claude 3.7 Sonnet. **This is why tomorrow leads with the MoE swap.**
   - *Open quality question (owner wants this measured):* MoE knowledge ≈ its 30B total, but hard
     multi-step/agentic reasoning may sit closer to its 3B active size. Must test head-to-head, not
     assume. History bar: 4B inadequate, 14B mediocre for agentic use.

3. **`n_ubatch` ingest knob (from round 1) is still untested but DEMOTED.** WS2a swept Ollama's
   `num_batch` = llama.cpp's *logical* `n_batch` (`-b`); the prefill lever is the *physical*
   `n_ubatch` (`-ub`), which I never moved (Ollama's new engine may not expose it). Worth a llama.cpp
   `-ub` sweep *only if* cold ingest still matters after the generation work — but WS1 already made
   ingest a rare cost, and generation is the real pain. Plan retained at the bottom under "Deferred".

Sources — round 2 (primary): arxiv.org/abs/2511.05502, arxiv.org/abs/2512.23029; also
github.com/raullenchai/Rapid-MLX (MLX engine WITH working prefix prompt cache, ~0.1–0.3s cached TTFT).
Round 1 (weaker, kept for the story): famstack.dev mlx-vs-gguf, groundy.com mlx-vs-llamacpp,
medium @michael.hannecke, vijay.eu, llama.cpp #6328, insights.marvin-42.com.

## NEXT ACTION (2026-05-22) — benchmark the MoE swap (speed AND intelligence)

Goal: find out if **Qwen3 30B-A3B (MoE, ~3B active)** gives a large generation-speed win over the
dense `qwen3.6:27b` **without an unacceptable intelligence drop**. Owner's explicit requirement:
**document how much less intelligent the MoE is** vs the dense model — speed alone does not decide it.
Contained benchmark via the gateway; only swap production if it clearly wins on both axes.

Memory note: a 30B-A3B Q4 (~17–18 GB) and the dense 27B (~16 GB) can't both be resident in 32 GB —
load one at a time (`ollama stop <other>` between phases).

**Step 0 — coordinate.** Ask owner to pause Hermes (we'll be loading/unloading models and the cache
will churn). Use `TERM=xterm-256color ssh 10.63.0.32`.

**Step 1 — pick & pull the MoE.** Find the right tag: `ssh 10.63.0.32 'ollama list'` and check the
Ollama library for a Qwen3 30B-A3B (e.g. `qwen3:30b-a3b`, or a `qwen3.6`-family A3B if one exists —
match our current family/quant Q4_K_M where possible). Pull it: `ollama pull <tag>`. Confirm active
vs total params with `ollama show <tag>` (want ~3B active / ~30B total, MoE).

**Step 2 — SPEED benchmark (decode is the point).** Reuse `mac/bench/bench_local.py` (raw + e2e)
against the MoE, same prompts as the dense baseline. Capture: generation tok/s (raw + e2e via
gateway), cold ingest tok/s, and whether prompt cache behaves (re-send identical prefix → fast).
Baseline to beat: dense **~11 tok/s** generation. Record both in `mac/bench/results.md`.
(Run with `think:false` semantics — prod uses `reasoning_effort: none` — else responses look empty.)

**Step 3 — INTELLIGENCE comparison (the required part).** Run a fixed prompt set head-to-head,
dense vs MoE, by pointing the gateway `private` route at each in turn (edit `mac/litellm-config.yaml`
model, `restart litellm`). Use **real Hermes-style tasks** per the spec's hand-judged bar, not a
synthetic suite:
  - a tool-calling / function-call task (does it pick the right tool + valid args?),
  - an email-triage / summarize-and-classify task,
  - a small coding edit (correctness + follows instructions),
  - 2–3 multi-step reasoning prompts (where MoE's ~3B active compute is most likely to show).
Judge by hand; **write up the quality delta explicitly** — where the MoE keeps up, where it's
visibly worse, and a go/no-go recommendation. This is the deliverable the owner asked for.

**Step 4 — decide & record.** New `mac/bench/results.md` section "MoE swap (Qwen3-30B-A3B) vs dense":
speed table + an honest intelligence-delta writeup. If it wins both → propose swapping the `private`
model in `mac/litellm-config.yaml` (gate the swap with owner). If speed wins but quality drops too
much → document the tradeoff and fall back to MLX (Step below) / spec-decoding for the dense model.
Restore the dense model as the live `private` route before finishing unless owner approves the swap.

### Deferred backend/ingest experiments (after the MoE question is settled)
- **WS4 — MLX backend (un-dropped).** Stand up `mlx_lm.server` (or mlx-openai-server) with a 4-bit
  MLX model; head-to-head **decode + prefill + prompt-cache** vs Ollama. Expect MLX to win decode;
  verify prompt cache survives (WS1). Note: needs a thin OpenAI-compat wrapper for the gateway.
  Could be combined with the MoE (a 4-bit MLX of 30B-A3B = both levers at once).
- **WS3 — speculative decoding** with the pulled `qwen3:4b` draft, if we stay on the dense model.
  q8_0's ~2.6 GiB headroom would feed it; persisting q8_0 needs a managed `ollama serve`/LaunchAgent.
- **`n_ubatch` ingest sweep (llama.cpp).** Only if cold ingest still matters. Plan: `brew install
  llama.cpp`; point `llama-bench` at Ollama's GGUF blob
  (`/Users/dertechie/.ollama/models/blobs/sha256-83c54730a5fea8a0958598c01617c1419c431e93b33bacf980b49a420c798926`,
  confirm via `ollama show --modelfile qwen3.6:27b | grep FROM`); run
  `llama-bench -m "$GGUF" -p 8192 -n 0 -fa 1 -ngl 99 -b 2048 -ub 64,128,256,512,1024,2048` and read
  the `pp` tok/s per `-ub`. Beat Ollama's ~140 tok/s? If a `-ub` wins and Ollama can't set it, the
  path is serving via `llama-server` (verify prompt cache). Unload Ollama first (memory).

### Takeover/restore cheat-sheet
- Reachable serve: `OLLAMA_HOST=0.0.0.0:11434 OLLAMA_KEEP_ALIVE=-1 [OLLAMA_KV_CACHE_TYPE=q8_0] \
  nohup /Applications/Ollama.app/Contents/Resources/ollama serve >/tmp/oll.log 2>&1 &` after
  `osascript -e 'quit app "Ollama"'` + `pkill -f "Ollama.app/Contents/MacOS/Ollama"`.
- Restore steady state: `pkill -f "ollama serve"; open -a Ollama` (binds loopback, f16 KV).
- Quality spot-check must pass `think:false` (prod runs `reasoning_effort: none`), else `response`
  is empty (all tokens go to the thinking field).
- Use `TERM=xterm-256color ssh 10.63.0.32` (runbook) to avoid a garbled terminal.

## Task list
WS2 complete. **Tomorrow (2026-05-22): MoE swap benchmark — speed + documented intelligence delta**
(NEXT ACTION above). Deferred after: MLX (un-dropped), WS3 spec-decoding, `n_ubatch` ingest sweep.
