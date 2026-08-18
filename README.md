# flutter-smoke-auto

**Fully automated smoke testing for Flutter apps (Android / iOS / Web) — with integrity gates that stop AI agents from cheating on tests.**

A [Claude Code](https://claude.com/claude-code) Agent Skill that derives business-critical user journeys from your Flutter source code, instruments the app for testability, generates deterministic test suites (Maestro flows for mobile, Playwright specs for web), executes them, triages failures, self-heals *test* defects, fixes *app* defects, and produces a human-reviewable report. The only manual step left is reading that report.

> 📖 The skill body and reference docs are currently written in **Chinese**. This README covers everything you need to install and evaluate it. English translation of the full docs is on the roadmap — contributions welcome.

## Why this exists

Every AI coding agent given the loop *"run tests → fix failures"* eventually discovers that the cheapest path to green is not fixing the code — it's weakening the tests. Deleting assertions, adding `skip`, swapping `toEqual` for `toBeTruthy`, inflating timeouts. Each edit looks defensible in isolation; together they mean your coverage silently died while the report says everything is healthy. This failure mode is [well](https://pyor.review/blog/test-rewrite-failure-mode) [documented](https://dev.to/moonrunnerkc/ai-agents-cheat-on-pull-requests-i-mined-327-of-them-to-prove-it-43ij) — but the ecosystem's answers have mostly stayed at the level of blog-post patterns.

This skill ships the enforcement as **code, not prose**:

- **`check_test_integrity.py`** — scans the git diff and *blocks* assertion deletion, added skips, weakened matchers, lowered expectations, inflated timeouts, and deleted test files. Zero dependencies, works on **any** language/framework (Playwright, Cypress, pytest, Jest, Go test, JUnit, Maestro — not just Flutter). Runs as a pre-commit hook and a CI step. Its default diff baseline is the *smoke-run starting commit*, so "commit first, check later" can't blind it.
- **`check_registry.py`** — a selector contract gate. Every selector used in a test must exist in a registry (`.smoke/registry.json`), and every registry entry must actually appear (quoted) in the source code. Hallucinated selectors are caught at generation time, not on-device. Text selectors are errors by default (the laziest bypass must not be the only non-blocking path).

Both gates are TDD'd (43 regression tests) and were hardened against real bypass attempts observed in practice — e.g. the source audit requires the identifier to appear *quoted* because "mention the id in a comment" was the cheapest way to fool it.

## What makes it different

Compared to Maestro reference skills and MCP-based agentic testing tools:

| | Maestro syntax skills | Agent-in-the-loop MCP tools | **flutter-smoke-auto** |
|---|---|---|---|
| Scope | Command/selector reference | Runtime "eyes and hands" for an LLM | Full pipeline: derive → instrument → generate → run → triage → fix → report |
| Selector discipline | By convention | Fuzzy matching, self-healing locators | Enforced contract registry, gated |
| Anti test-weakening | — | — | Diff-based integrity gate (pre-commit + CI) |
| CI execution | Deterministic | LLM calls per run ($$, nondeterministic) | **Deterministic, zero LLM cost** — AI only at generation & repair time |
| Web support | — | Varies | Playwright specs sharing the same registry |

Other design decisions worth knowing:

- **One instrumentation, three platforms.** `Semantics(identifier:)` renders as `resource-id` on Android, `accessibilityIdentifier` on iOS, and `flt-semantics-identifier` in the web DOM — so mobile flows and web specs share a single selector contract.
- **Assertions come from business expectations, not implementation.** Generated assertions are restricted to business invariants (reachability, no-error, persistence, reversibility, has-content) or documented requirements — never transcribed from code behavior, which would fossilize bugs as "expected".
- **Failure triage is three-way and asymmetric.** `TEST_DEFECT` (locator/wait/state issues) may be auto-fixed; `APP_DEFECT` (business assertion failures) must never be "fixed" by editing the test; `ENV_FLAKE` gets one retry. When unsure → `APP_DEFECT`, because a false bug report is far cheaper than a masked one.
- **Scoped runs are the daily default.** `run_smoke.sh --changed` maps `git diff` → registry → affected flows (plus the cold-start anchor) so routine verification takes minutes; the full suite is reserved for release gates and the `/smoke-*` commands.
- **Vision fallback is a last resort, not the engine.** Element location degrades L1 (semantics tools) → L2 (accessibility tree) → L3 (screenshot + model + coordinate tap), with pixel-diff verification after every L3 tap. A run that lives entirely in L3 is itself reported as an accessibility defect.

## What's in the box

| Path | What it is |
|---|---|
| `flutter-smoke-auto/` | The main skill: SKILL.md (workflow), 7 reference docs, 6 scripts, CI/hook/flow templates, gate regression tests |
| `smoke-all/` | `/smoke-all` — full acceptance run on every available platform, parallel execution |
| `smoke-android/` `smoke-ios/` `smoke-web/` | `/smoke-android` etc. — full acceptance run on one platform |

Key scripts (all zero-dependency Python 3 / bash 3.2 compatible):

- `check_test_integrity.py` — the anti-weakening gate (framework-agnostic; usable standalone in any repo)
- `check_registry.py` — selector contract + source audit gate (includes a built-in mini Maestro YAML parser, no PyYAML)
- `select_flows.py` — git-diff → affected-flows mapping for scoped runs
- `run_smoke.sh` — build, boot devices/servers, execute, collect artifacts (all three platforms)
- `screen.py` — screenshot / coordinate tap / pixel diff / blank-red-screen detection / logcat capture
- `scan_app.py` — static scan of `lib/` producing the app map (routes, screens, interactive widgets)

## Install

```bash
# via skills.sh
npx skills add ceeyang/flutter-smoke-auto

# or manually
git clone https://github.com/ceeyang/flutter-smoke-auto.git
cp -r flutter-smoke-auto/flutter-smoke-auto flutter-smoke-auto/smoke-* ~/.claude/skills/
```

Then, in a Flutter project, just ask Claude Code to smoke-test the app — or run `/smoke-all` for a full three-platform acceptance run. On first run the skill instruments your app, generates the suite, wires up the pre-commit hook and CI workflow, and writes everything under `.smoke/` in your repo.

Using the integrity gate standalone (any repo, any framework):

```bash
cp flutter-smoke-auto/scripts/check_test_integrity.py .smoke/scripts/
python3 .smoke/scripts/check_test_integrity.py          # exit 1 = tests were weakened
```

## Requirements

- Flutter 3.19+ (semantic `identifier` support)
- [Maestro](https://maestro.mobile.dev) + Java runtime for mobile (the skill can reuse Android Studio's bundled JDK)
- Playwright (`npx playwright`) for web — optional; a chrome-devtools MCP fallback route exists
- macOS for the iOS lane

## How it works

```
Phase 0  environment check & scenario routing (dev-loop / scoped / full)
Phase 1  static scan + business-context extraction  →  .smoke/app-map.json
Phase 2  testability instrumentation (Semantics identifiers)  →  .smoke/registry.json  [gate: source audit]
Phase 3  journey selection (5–8 release-blocking happy paths, business invariants only)  →  .smoke/plan.md
Phase 4  generation (Maestro flows + Playwright specs)  [gate: selector contract]
Phase 5  execution & triage (TEST_DEFECT / APP_DEFECT / ENV_FLAKE), self-heal ≤3 rounds  [gate: integrity, every round]
Phase 5.5 app-defect repair loop (for code written in-session) until green  [gate: integrity, every round]
Phase 6  report (verdict / results / defects / self-heal log / coverage gaps) + CI config
```

CI runs Maestro/Playwright only — no LLM calls, deterministic, minutes-fast, zero API cost. AI participates exactly twice: when generating and when repairing.

## License

[MIT](LICENSE)
