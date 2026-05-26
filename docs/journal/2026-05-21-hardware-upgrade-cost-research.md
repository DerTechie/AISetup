# 2026-05-21 — Researching a local-inference hardware upgrade: cost, real ingest gains, and cloud amortization

## Context / where this started

The current inference box is the **MacBook Pro M2 Max (32 GB)** running headless — which is exactly why I lost a mobile machine. The open question: if I genuinely want to upgrade (compression running locally, faster *cold ingest*, generally more capable local models), what does each option actually cost, and is it worth it versus just paying the cloud? Constraints to respect: the box must keep the Arch workstation free, I can sell assets to fund it (M2 Max MacBook, Apple Studio Display, an older M1 Mac Studio), the replacement laptop does **not** need macOS (Linux is the better daily driver now), and iOS builds are a *maybe-later* (revenue-first on Android). I explicitly wanted options at and **above** the sweet spot, and a check on the Intel Arc 32 GB cards.

This entry is the research, the two corrections that mattered, and the verdict. Nothing in the system was changed.

## The first correction — the ingest numbers were sloppy, and I caught it

My initial cross-vendor comparison mixed model sizes (a 7B-F16 prefill figure against a 32B figure), which inflated/distorted the gap. Re-researched, **model-matched to my actual ~27–34B Q4 workload**, anchored on like-for-like llama.cpp Q4_K_M data (XiongjieDai harness, 8B + 70B brackets):

- My measured **~144 tok/s prefill / ~11 tok/s gen on the M2 Max is confirmed normal** for a 27B Q4 model — it sits cleanly between the 8B and 70B Apple numbers.
- **Cold ≠ warm, and the tok/s number hides it.** `prompt_eval_rate` *excludes* model-load time by definition. "Cold ingest on first usage" is really two stacked costs: (1) a one-time **TTFT** hit — weights loading from SSD into unified memory (~5–30 s) + first-run Metal shader compile — and (2) the prefill rate itself, which **sags at long context and collapses if model + KV cache approaches the 32 GB ceiling**. So the felt slowness is TTFT + long-context droop, not a lower throughput figure.
- **Real, model-matched advantage of NVIDIA for *this* class:** prefill ~**5–8× (RTX 3090)** / ~**10–15× (RTX 4090)**; generation only ~**2–3×**. The win is concentrated exactly in the cold/long-context ingest I care about. Strix Halo (~2×) and Apple-pre-M5 (~1.3×) barely move ingest; only **discrete NVIDIA** or the new **M5 Max** (Apple's claimed 4× prefill) actually fixes it.

Lesson, again: don't compare across model sizes; isolate the variable. The skeptic's caveat stands — no source benchmarks a dense 30B with separated pp/tg on *both* an M2 Max and a single 3090, so the 5–15× is interpolated, not directly measured. `llama-bench -p 512,4096 -n 128` on the real GGUF would close it; I chose not to bother for now.

## The second correction — a GPU is not a server (and the 2026 DRAM crisis)

I'd hand-waved the host at "~€700". Wrong. Real itemized builds on geizhals (2026-05-21), and the dominant finding: **an AI-driven DRAM/NAND price spike has made RAM the most expensive part of a build** (64 GB DDR5 ≈ €626, 128 GB ≈ €854).

- **Single-GPU host (new, efficient, ~40 W idle):** Ryzen 5 9600X + B650 + 64 GB DDR5 + 2 TB Gen4 NVMe + 850 W Platinum + airflow case ≈ **€1,560**.
- **Dual-GPU host (new):** ASUS ProArt X670E-Creator (x8/x8, spaced) + 128 GB + 1300 W Titanium ≈ **€2,537**.
- **The smart move given the RAM spike:** a **used 64 GB gaming PC (~€400–500)** brings RAM + PSU + case in one shot and undercuts a new build massively. Build new only to win the ~€100/yr idle-power difference (Ryzen ~40 W vs used Xeon ~80 W).

## Costs, with the host folded in

Server only (a separate mobility laptop is extra for the non-laptop options). Complete systems (Mac Studio, Strix mini/laptop, DGX) need no host.

| Option | All-in (server) | Yr-1 power |
|---|---|---|
| **3090 + used host** | ~€1,350 | ~€170 |
| Arc B70 32GB + used host | ~€1,400 | ~€130 |
| 3090 + new build | ~€2,460 | ~€170 |
| **Dual 3090 + used workstation** | ~€2,650 | ~€240 |
| Strix Halo mini 128GB | €3,175 | ~€40 |
| Mac Studio M4 Max 64GB | €3,209 | ~€30 |
| Strix Halo **laptop** 128GB (also the laptop) | €3,414 | ~€40 |
| DGX Spark 128GB | ~€3,640 | ~€90 |
| Dual 3090 + new build | ~€4,340 | ~€240 |
| Mac Studio M3 Ultra 96GB | €4,399 | ~€35 |
| RTX 5090 + new build | ~€5,140 | ~€180 |

War chest from selling MBP + Studio Display + M1 Studio ≈ **€3,500–4,800** (swings ~€800 on whether the Studio is M1 Max or Ultra).

## The amortization finding — the financial case is weak, and that's the honest answer

Computed break-even as the monthly cloud spend the box would have to displace to pay back in 12 months, **full hardware cost + power, no war-chest offset** (as asked). Anchored against the existing **€100/month LiteLLM budget cap** — so the most any box can amortize in a year is ~€1,200.

- Cheapest build (used 3090) break-even ≈ **€127/mo** — already *above* my own €100 cap.
- The models a local 24–32 GB box actually replaces are **Tier-1 open models in the cloud (~€0.04–0.28/M tokens)** — absurdly cheap. To break even in a year against Tier-1 you'd need to push **~850M tokens/month (~28M/day)**. Implausible for one person ⇒ on the *fair* comparison, payback is **3–8+ years**.
- Only if the box genuinely displaces **Tier-2 / GPT-5-mini-class** paid usage (~€0.65/M blended) does break-even drop to **~195M tokens/month (~6.5M/day)** — reachable by a heavy daily user. In *that* story, the **used 3090 (~€1,350)** and **used-workstation dual 3090 (~€2,650)** amortize in ~12–18 months; nothing pricier does.

**Verdict: buy for privacy, an unmetered workstation, and latency — not to save euros.** The cheaper the box, the less you have to believe the optimistic Tier-2 story to justify it.

## What we decided / recommend

- **Sweet spot: used-host single RTX 3090 (~€1,350).** Delivers the 5–8× cold-ingest win and ~2.7× generation, runs 32B dense, fully funded with ~€2,250 left from sales toward a Linux laptop. Cost: 24/7 power (~€170/yr) and the LLM box stays home.
- **One step up: used-workstation dual 3090 (48 GB, ~€2,650)** for fast 70B-dense ingest *and* generation.
- **Above that:** RTX 5090 (32 GB, fastest single card) or new-build dual 3090 — capability buys, not payback buys.
- **Capacity (not ingest) play:** Strix Halo 128 GB runs big MoE (gpt-oss 120B ~53 t/s); the **HP ZBook Ultra 128 GB laptop (~€3,414)** is the only "one box = server + mobility" option, at ~net-zero after sales.

## What we rejected / considered

- **Intel Arc (incl. the 32 GB B70, the dual-B60 48 GB):** real and cheap VRAM-per-euro, but bandwidth is only ~Mac-level and the IPEX-LLM/SYCL stack has a documented **prefill-slowdown-under-load bug that needs a container restart** — the exact failure mode that wrecks a 24/7 headless ingest gateway. The "32 GB Arc" I'd half-remembered is the Arc Pro B70 (608 GB/s, ~$949). **Rejected** for production; a €250 B580 only as a throwaway experiment.
- **DGX Spark / Strix dense-70B:** generation is bandwidth-starved (~7 t/s) — big models *fit* but run too slow. **Rejected** as the fast box.
- **Apple M3 Ultra Studio / M5 Max MacBook:** M5 Max is the only Apple path that fixes prefill (~4–6×) but it's a laptop, priciest single buy; an M5 *Studio* (ideal headless) isn't out yet (rumored late 2026, DRAM-shortage risk). Deferred, not chosen.
- **Selling assets to make the amortization look good:** explicitly excluded — the war chest funds the purchase, but payback is judged on cloud savings alone.

## How the used / resale prices were determined (methodology)

So the numbers are defensible if anyone asks — the exact flow:

1. **Used asking prices came from live kleinanzeigen.de search pages**, one query per item (e.g. `MacBook Pro 16 M2 Max`, `Apple Studio Display`, `Mac Studio M1 Max` / `M1 Ultra`, `RTX 3090`, `Dell/HP workstation`). I read the *current asking prices* off the live listings at the time of research (2026-05-21).
2. **Buyback/Ankauf portals were used as the price floor** — e.g. maconline Ankauf quoted a Mac Studio M1 Max at ~€858, rebuy/wirkaufens for the MacBook. These pay deliberately *below* private sale, so they bracket the bottom of the range.
3. **Realistic private-sale figure = between the two**, leaning *below* the asking prices, because kleinanzeigen shows what sellers *ask*, not what items *close* at (the platform doesn't expose completed-sale prices). That gap is why every resale number is a range, not a point.
4. **Used GPUs were cross-checked against a price-history tracker** (bestvaluegpu.com, EU used + new series) in addition to kleinanzeigen, since GPU prices move fast and trackers smooth out single-listing noise.
5. **Used host machines** (gaming PC / workstation) came from kleinanzeigen.de plus a refurb-workstation dealer (it-versand.com) for the HP/Dell/Lenovo tier.
6. **New components** (the build BOM) are **geizhals.de lowest-price** entries, which track street price in Germany.

**Known limitations, stated up front:** these are *asking-price snapshots*, not closed-sale data — kleinanzeigen does not publish sold prices, so the resale figures are estimates (treat as ±15%, and expect achieved prices to land toward the lower end of each range). The German used market also runs noticeably higher than the US used market, so US trackers were used only for direction, not for the euro figures. Everything is a single-day snapshot in a fast-moving (DRAM-spiked) market.

## State / pending

- Research only — **nothing in the system changed.** No purchase made.
- If pulling the trigger on the 3090 path: decide used host vs new build (idle-power trade), and source a used 64 GB PC + RTX 3090 on kleinanzeigen.
- Open empirical gap: a direct `llama-bench` cold-vs-warm prefill capture on the current M2 Max would replace the interpolated 5–15× ratio with a measured one.
- All prices are 2026-05 snapshots in an actively rising DRAM/NAND market — re-check RAM/SSD/GPU at purchase time.
