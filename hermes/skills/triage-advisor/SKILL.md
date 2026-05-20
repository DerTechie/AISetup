---
name: triage-advisor
description: Use BEFORE starting any market/strategic/business-research task, multi-source synthesis, or other heavy-reasoning request — and whenever unsure whether to use main, private, or deep. Run it FIRST, before any web search or analysis, to recommend main vs private vs deep. Recommend-only: the user confirms the switch; it never changes models itself. (Skip only if already deliberately on the recommended model.)
---

# Triage Advisor

Recommend whether the current task should stay on the fast default **main** (cloud) model, drop to the on-device **private** model, or escalate to the **deep** cloud model. **Run this at the START of a qualifying task — before doing web searches or analysis — not after.** The **main** model judges; you present its recommendation; the user decides. **Never run `/model` yourself without the user's explicit go-ahead.**

## How to run

1. Write a one-line description of the task being routed.
2. Run (the script reads gateway creds from `~/.hermes/config.yaml` automatically; override with `LITELLM_BASE_URL` + `LITELLM_MASTER_KEY` env vars if needed):

   ```bash
   python3 ~/.hermes/skills/triage-advisor/triage_advisor.py "<task description>"
   ```

3. Show the script's recommendation to the user verbatim and wait for their decision. On their explicit confirmation: `/model deep` for deep, `/model private` for private. If the recommendation is `main`, that is the default — no switch is needed.

## Rules

- **Recommend-only.** Do not switch models automatically.
- Each call is logged to `~/.hermes/triage-advisor.jsonl` automatically — you don't need to log anything.
