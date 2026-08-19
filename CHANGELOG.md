# Changelog

All notable changes to this skill are documented here. Versions follow SemVer; the skill's behavior contract (what gates block, what runs by default) is the API.

## [1.1.0] — 2026-08-20

Driven by two rounds of real-project field testing. Theme: **scoped by default, parallel where safe** — and every discipline that used to be prose now has a tool-level enforcement point.

### Scoped execution — a full run is now an explicit choice

- **BREAKING**: `run_smoke.sh` refuses bare runs. A scope flag is required: `--changed` / `--only <kw>` / `--failed` / `--from-list <lane>` / `--all` (CI via `$CI`, and `--attach`, are exempt). Field data motivated this: with "bare = full suite" as the silent default, scoped runs were *never* chosen — the same failure set got two full re-runs in a row.
  - Migration: scripts that ran `run_smoke.sh --platform android` should now state intent — `--all` if they truly meant the full suite.
- `--failed`: repair-loop re-runs read the last round's JUnit results and re-run only the failures (+ cold-start anchor). Refuses when the last round was green. Web uses Playwright's native `--last-failed`.
- `select_flows.py` hardening:
  - Keyword matching is restricted to **filenames and test names** — full-text matching made every login-subflow reference match "login", degrading scoped runs to near-full.
  - **Zero-match keyword now hard-refuses (exit 2)** and lists available case names. Previously it silently degraded to cold-start-only — which nudged agents to "helpfully" escalate to `--all`. The laziest path must never be the wrong path.
  - `--changed` falls back to feature-directory mapping (layer dirs like `data/`, `ui/` stripped) when changed files carry no instrumented ids — logic-layer edits no longer produce a false-green cold-start-only run.
- Disambiguation rule in the skill: **"run the full tests of feature X" = `--only X`** (all cases of that feature), never `--all`. "Full" without a feature qualifier is the only thing that means the whole suite. (A real session pattern-matched the word "full" and ran everything.)
- Mass-failure circuit breaker: when >50% of cases fail, the run prints an *environment-level fault* signature warning (broken login precondition, dead simulator, unreachable backend) instead of inviting per-case fixes or another full re-run.

### In-platform parallel lanes (large suites)

- **`shard_flows.py`** — parallelism is *declared, not assumed*: flows tag their resource usage (`mutates-posts`, `readonly`); flows writing the same resource (transitive closure) share a lane and run serially; readonly cases spread across lanes; undeclared cases are conservatively serialized together, with a reminder.
- **`device_pool.py`** — cross-session simulator ownership registry at `~/.flutter-smoke/device-pool.json`:
  - `claim` refuses devices owned by another session; `assign --pin` reserves a device so only an explicit user `release --unpin` frees it; stale locks (>2h) are flagged `STALE` in `list` but never auto-stolen.
  - Caps: per-platform limit (default 2, `FSA_MAX_PER_PLATFORM`) plus a live memory budget (Android ≈3 GB, iOS ≈2.5 GB per simulator, 8 GB reserved; `FSA_MEM_GB` override). Claims beyond budget are refused (`--force` to override).
- `run_smoke.sh`: new `--from-list <lane.txt>` (counts as a scope), `--device <serial|udid>` (explicit target for lanes), `--workers N` / `SMOKE_WORKERS` (Playwright parallelism).
- **Multi-device correctness fixes**: all `adb` calls now pass `-s <serial>`, and `simctl install`/`log stream` target an explicit UDID instead of `booted` — with two devices online the old commands either failed or drove the wrong device.
- Web: per-worker browser-context isolation documented (cookies/storage/HTTP cache never shared — the real contention is backend accounts, not the browser); new `laneEnv()` helper in `helpers.ts` assigns per-worker test accounts (`TEST_PHONE_LANE1..N`).
- Multi-platform acceptance now **parallelizes by default**: one read-only subagent per platform (execute + triage only; all fixes centralized and deduped by root cause). Web-first probing policy — web is the cheapest detector, but **web green ≠ mobile green**; release verdicts still require each platform's own run.

### iOS stability

- Single-driver gate: residual maestro/xcodebuild/idb driver processes are killed before every run — SpringBoard's `XCTAutomationSession` init has a concurrency race (stacked automation sessions segfault SpringBoard inside the simulator). Full acceptance runs shut the simulator down afterwards; scoped runs keep it booted for speed.

### Tests

- 58 red-first regression tests across four suites (38 gate + 9 screen + 6 device pool + 5 sharding), each reproducing a real cheat path or field-observed failure before the fix.

## [1.0.0] — 2026-08-18

Initial public release.

- Six-phase pipeline: static scan → instrumentation (`Semantics(identifier:)`, one registry for three platforms) → journey selection → suite generation (Maestro flows + Playwright specs) → execution & triage → self-heal/repair loop → report.
- Anti-cheating integrity gates as executable code: `check_test_integrity.py` (framework-agnostic diff gate) and `check_registry.py` (selector contract + quoted-source audit).
- Slash commands: `/smoke-all`, `/smoke-android`, `/smoke-ios`, `/smoke-web`.
- Deterministic CI (GitHub Actions template), pre-commit hook, L1→L3 locator fallback with pixel-diff verification.
