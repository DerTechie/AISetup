---
name: triage-advisor
description: Use when the user is unsure whether a task should run on the local or deep (cloud) model, OR when a task involves large context, explicit market/strategic/business research, or needs current/web information. Recommends deep vs local — the user confirms; it does NOT switch models itself.
---

# Triage Advisor

Recommend whether the current task should run on the fast **local** model or the **deep** cloud model. The LOCAL model judges; you present its recommendation; the user decides. **Never run `/model` yourself without the user's explicit go-ahead.**

## How to run

1. Write a one-line description of the task being routed.
2. Run (the script reads gateway creds from `~/.hermes/config.yaml` automatically; override with `LITELLM_BASE_URL` + `LITELLM_MASTER_KEY` env vars if needed):

   ```bash
   python3 ~/.hermes/skills/triage-advisor/triage_advisor.py "<task description>"
   ```

3. Show the script's recommendation to the user verbatim and wait for their decision. On their explicit confirmation: `/model deep` for deep, `/model local` for local.

## Rules

- **Recommend-only.** Do not switch models automatically.
- Each call is logged to `~/.hermes/triage-advisor.jsonl` automatically — you don't need to log anything.
