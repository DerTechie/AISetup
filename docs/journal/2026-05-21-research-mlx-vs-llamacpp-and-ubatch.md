# 2026-05-21 — Research: would MLX help ingest, and did WS2 test the right batch knob?

## Context

After [WS2](2026-05-21-ws2-cold-ingest-memory-tuning.md) returned a negative result on cold ingest,
before committing to the next workstream we did a web sweep on two questions: (1) what else could
speed prompt ingest on an M2 Max, and (2) would the MLX backend (WS4 in the spec) improve ingest?
The point was to avoid sinking effort into a lever that the hardware can't benefit from.

## Finding 1 — MLX would not help ingest here; it would likely hurt it. WS4 dropped.

MLX's reputation for speed is about **decode (generation)**, not **prefill (ingest)**. For our exact
case — M2 Max, ~27B model, ~16k-token prefix — the evidence runs the other way:

- MLX prefill is **slower** than llama.cpp + FlashAttention at long context (measured ~49 s vs ~38 s
  at 8.5k; effective throughput collapses to ~3 tok/s once prefill dominates).
- **M2 has no native bf16.** MLX models ship in bf16, so M1/M2 fall back to software emulation
  *during prefill specifically* — a penalty M3+/M5 don't pay. It targets our weakest spot.
- MLX's decode advantage **shrinks to ~zero at 27B**, where both runtimes hit the memory-bandwidth
  ceiling.
- **Prompt caching is broken/unreliable** in MLX implementations — adopting it would risk the
  [WS1](2026-05-21-prefix-cache-aux-eviction.md) win, our single biggest gain.
- Ollama's own MLX backend posts large gains, but only on **M5** (GPU Neural Accelerators), requires
  **>32 GB** (we have exactly 32 → excluded), and targets one specific model. Not applicable.

**Decision: drop WS4 (MLX) for this hardware.** Revisit only on an M3+/M5 / >32 GB machine.

## Finding 2 — WS2a may have tested the wrong knob (and what was rejected)

llama.cpp has two batch parameters, and they are not the same lever:
- `n_batch` (`-b`) — *logical* batch, an application-level buffer cap. **This is what Ollama's
  `num_batch` maps to** — i.e. the one I swept in WS2a (flat, 138→140 tok/s).
- `n_ubatch` (`-ub`) — *physical* micro-batch, the unit fed to the Metal kernels. This is the one the
  literature credits with 2–3× prefill speedups, because larger matmuls saturate the GPU better.

So WS2a's "num_batch is not a lever" is **true but possibly a false negative for the real question**:
I never moved `n_ubatch`. Worse, our model runs on Ollama's *new engine* (`--ollama-engine`), which
may not expose `n_ubatch` at all.

Rejected shortcuts:
- **Declaring ingest "bandwidth-bound, case closed"** — rejected: the sources disagree on whether
  prefill is compute- or bandwidth-bound, and I hadn't actually tested the parameter that would
  decide it. Honest state is "open", not "closed".
- **Promising a 2–3× ingest win** — rejected: `n_ubatch` interacts unpredictably with backend/quant
  (one report saw Qwen3.5-27B *peak at ub=64* and crater at 128 — on AMD ROCm, not Metal), and at
  27B the ceiling may genuinely be bandwidth. The result could be nothing.

**Decision: settle it empirically** with an `-ub` sweep run under **llama.cpp directly**
(`llama-bench`), since Ollama's engine won't let us set it. If a value clearly wins, the follow-on
question is whether Ollama can use it — and if not, whether serving `private` via `llama-server`
(preserving prompt cache) is worth it. Plan is in
`docs/superpowers/plans/2026-05-21-local-speed-RESUME.md` → NEXT ACTION. Deferred to 2026-05-22 by
the owner.

## Lesson

"We measured X and it didn't help" is only as strong as *which* X you measured. A 20-minute literature
check caught that the headline knob (`num_batch`) wasn't the kernel-level lever (`n_ubatch`) — cheap
insurance against closing a question prematurely.

## CORRECTION (later same day) — the "MLX is wrong for us" call was itself wrong

The owner pushed back on the "drop MLX" conclusion, citing a guide claiming MLX is beneficial, and
asked for deeper research. I read two **rigorous arxiv papers** in full — 2511.05502 (Persistent
Systems: MLX/MLC-LLM/llama.cpp/Ollama/MPS head-to-head on an M2 Ultra) and 2512.23029 (Qwen3-30B on
consumer hardware). They overturned the blog-based call:

- **Prompt caching works in MLX** (on-disk prompt cache + rotating KV) — the "broken cache" was *LM
  Studio's* implementation, not MLX. The WS1 win can survive.
- **No bf16 requirement** — MLX uses mixed 3/4/6/8-bit quant; a 4-bit MLX avoids bf16 emulation.
- **MLX is the *fastest* Apple-native decode**, not at parity: ~230 tok/s vs MLC ~190, llama.cpp ~150,
  and **Ollama just 20–40** (what we run). The decode win is real and large — *especially vs Ollama*.
- The one round-1 point that survives: for **ingest/prefill specifically**, MLX is *not* the leader
  (full prefill, no chunked prefill; MLC-LLM has better TTFT ≤16k). MLX's win is decode, not ingest.

And the bigger discovery: **our `qwen3.6:27b` is a *dense* model.** Every fast "Qwen 30B" benchmark is
**Qwen3-30B-A3B**, an **MoE with ~3B active params/token** — it reads ~2 GB/token vs our ~16 GB, so it
could plausibly hit 40–80 tok/s on the same M2 Max, and it scores MMLU 83% / AIME 73–90% (≈ Claude 3.7
Sonnet). That model swap is likely a *bigger* generation lever than the backend choice.

**Revised decisions:** MLX **un-dropped** (kept as a backend test); tomorrow leads with **benchmarking
the MoE swap**, with the owner's explicit requirement to **document the intelligence loss** vs the
dense model (MoE knowledge ≈ 30B, but hard reasoning may track its ~3B active size — must be measured,
not assumed). Plan in `docs/superpowers/plans/2026-05-21-local-speed-RESUME.md` → NEXT ACTION.

## Lesson (revised)

Two lessons, the second sharper than the first. (1) "We measured X" is only as good as *which* X.
(2) **Match source rigor to decision weight.** I let three secondary blogs talk me into dropping a
whole workstream; two controlled studies reversed it within an hour. When a finding is about to *kill*
an option, spend the extra 20 minutes on primary sources before writing it down — and treat an owner's
"are you sure?" as a signal to re-open, not defend. The reconstructable path here includes the wrong
turn *and* the correction; both belong in the story.
