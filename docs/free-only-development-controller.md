# KD Checker — free-only development controller (draft)

## Hard budget rules
- Zero additional paid charges beyond existing ChatGPT Plus. No OpenAI API keys, paid credits, purchased minutes, hosted paid agents or paid subscriptions.
- Do not assert that Plus includes unlimited Codex, or that GitHub Actions on a **private** repository is unlimited.
- Fail closed until the owner verifies GitHub Actions billing/budget controls and available Codex usage. The connected GitHub app cannot read billing settings.
- No paid-call fallback. Never create a workflow that calls an AI service, uses a PAT, or triggers recurring CI runs.
- Only synthetic data in tests. No company drawings, passwords, JWT, refresh tokens, project secrets or documents in chat/Issues/CI.
- No auto-merge, direct pushes to main, deployments, changes to Supabase/EPLAN Checklist, changes to Windows stable installation, or production database migrations.
- CI on PR is currently defined in .github/workflows/tests.yml; it consumes private-repository included minutes. No extra schedule is added by this PR.

## Control flow
1. Human/authorized developer approves a task and writes a *reviewed, synthetic* JSON snapshot (not credentials).
2. Offline `free_cycle.py` inspects tasks. It does not contact GitHub, ChatGPT, Codex, Sites, or Supabase.
3. Default is `pause=true`; selection is blocked unless `budget_verified=true`. A human must set this only after checking the actual settings. No file in the repo can prove a spending cap.
4. A task is selectable only with `state=ready`, `human_approval_required=false`, and all dependencies marked `done`. Unknown dependencies and duplicate IDs fail.
5. Selected task is **advisory only**: the script never starts Codex. Codex execution using Plus access, its session/usage limits and any handoff remains a separate explicitly configured capability.
6. Proposed implementation goes to a feature branch and PR; review and tests precede any merge. A blocker or exhausted free quota stops the cycle.

Example (safe, paused by default):
```json
{
  "pause": true,
  "budget_verified": false,
  "active_agent": false,
  "tasks": [
    {"id": "docs-inventory", "state": "ready", "dependencies": [], "priority": 10, "human_approval_required": false}
  ]
}
```

Run: `python free_cycle.py tasks.json` (prints one JSON result); `python -m unittest -q test_free_cycle.py`.

## Reporting site
Existing dashboard: https://kd-checker-development-hub.grownitem1.chatgpt.site

It is currently a manually authored snapshot, **not** a live integration. This PR does not claim to update it or attach credentials. A future **read-only, explicitly authorized** adapter may publish sanitized PR/check summaries only when supported by the Sites platform. Never expose the private repository contents, company KД or secrets via a public feed.

## Multi-PC gate
Still not ready for LAN: authorization, RBAC/object checks, TLS/CSRF, storage/transactions, recovery and two-PC acceptance need separate review (PR #37/#38). PR #23 is design only.

## Verification / adoption
- This draft PR contains only the offline selector, seven synthetic unit tests and docs.
- Check GitHub billing limits and whether paid overages can be disabled, verify Codex credits/usage and prevent spending, and enable suitable branch protections **in GitHub settings**. These settings are outside this connector's verified access.
- When budgets are not verified, remain paused. Nothing here runs an unattended AI agent or changes the Sites dashboard.
