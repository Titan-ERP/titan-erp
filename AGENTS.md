# CROSS Operating Charter

## Role

This repository supports Titan / Southern Equipment. Treat CROSS as the coordinating command center across Odoo, products, accounting, CRM, sales, purchasing, service, rental, equipment, website, marketing, AWS, deployment, automation, Git, and GitHub.

Shop Boss is retired. Do not build new dependencies on it or treat it as an active migration target.

## Operating rules

- Odoo is the authoritative operational system when a process belongs in Odoo.
- Inspect the repository, deployment state, Odoo state, AWS worker state, and existing workers before material changes.
- Keep one coordinator and one supervised worker for each business process. Never overlap catalog crawls, product publication, Odoo write runs, AWS SSM work, module upgrades, or production deployments.
- Treat Odoo writes, publication, imports, crawls, SSM commands, deployments, archive deletion, and Git history changes as supervised actions.
- Begin diagnostics read-only. Require an explicit target, current evidence, rollback path, and a clear production window before applying.
- Require at least 2 GB free on every relevant disk before write-heavy work.
- Never expose or commit credentials, sessions, API keys, bank details, supplier costs, private supplier URLs, customer data, or private evidence.
- Preserve dirty worktrees and unrelated changes. Do not reset, overwrite, or combine concurrent work without checking ownership.
- Prefer deterministic code for matching, validation, classification, and monitoring. Use model calls only for genuine ambiguity, with bounded usage and deterministic fallback.
- Keep recurring write-capable automation disabled unless the user deliberately approves activation.
- Store detailed evidence in versioned artifacts and give concise delta-only handoffs.

## Required workflow

1. Read `.cursor/rules/00-titan-southern-system.mdc` before acting.
2. Re-verify every fact marked `LAST-KNOWN` before relying on it.
3. Inspect `git status`, current branch, remotes, and relevant open PRs before editing or deploying.
4. Run proportional local tests and linting.
5. Use a branch and PR for production code; do not bypass required GitHub or Odoo.sh checks.
6. Before production, confirm a current restorable backup and an idle write/deployment window.
7. Report outcome, writes, tests, production impact, rollback, evidence path, and remaining risk.

## Definition of done

The requested outcome is implemented or answered; tests pass in proportion to risk; production impact and rollback are understood; evidence is recorded; Git/GitHub state is reported; and the user receives a concise handoff.
