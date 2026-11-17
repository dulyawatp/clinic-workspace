# Encounter write deferral implementation plan

> **For agentic workers:** Use focused subagent-driven tasks; test first and report exact commands and statuses. Do not commit or deploy.

**Goal:** Make new encounter and lab mutations fail closed while preserving recovery.

**Architecture:** A server-side pre-dispatch refusal is authoritative, and capability publication plus the SIT verifier expose the same deferred state. Documentation records SIT cases as blocked.

**Tech Stack:** Python unittest, Frappe gateway and local SIT verifier.

**Spec:** `docs/superpowers/specs/2026-11-18-encounter-deferral-design.md`

## Global constraints

- Local work only: no deployment, live writes, permission changes, automatic retries, automatic unlocks, fabricated events or remote pushes.
- Preserve acknowledgement, reconciliation and historical fence semantics. Never modify existing patient, encounter, appointment, medication or ledger data.
- Keep unrelated write families and their capabilities unchanged.

## Review focus

- Enabled encounter gate and authorized actor: each new draft, complete and amend request refuses before fence dispatch.
- Existing acknowledgement/recovery: remains reachable and does not erase or unlock uncertain work.
- Lab ordering: cannot dispatch via the draft payload and is not advertised enabled.
- Verifier: detects any deferred capability published as enabled, including with encounter gate enabled.
- Unrelated write families: existing capability and endpoint tests continue to pass.

### Task 1: Server-side write boundary and capabilities

**Files:** `frappe-apps/clinic_ui/clinic_ui/endpoints/encounters_write.py`, `frappe-apps/clinic_ui/clinic_ui/core/capabilities.py`, and focused `frappe-apps/clinic_ui/tests/unit/` tests.

- [ ] Add tests for refusal of draft/create, draft/update, complete and amend with the encounter gate enabled and authorized roles; prove no lease dispatch or native mutation, and preserve acknowledgement/recovery tests. Run to show the tests fail for the expected behavior.
- [ ] Add tests that encounter draft, complete, amend and lab capabilities remain `read_only` when their gates are enabled; unrelated capability behavior remains unchanged.
- [ ] Implement the smallest fail-closed boundary and capability changes. Do not alter eager fence semantics or historical receipts.
- [ ] Run the focused Python tests and report exact commands and statuses.

### Task 2: Verifier and SIT status

**Files:** `deploy/local-sit/bin/verify.py`, `deploy/local-sit/tests/test_verify_medication_deferral.py` or a focused sibling, and `docs/assessments/2026-11-14-marley-native-sit-test-cases.md`.

- [ ] Add tests proving the verifier rejects each enabled deferred encounter/lab capability while retaining all other gate checks; run and record the failing results.
- [ ] Update the explicit deferred capability set and its evidence, and mark the affected encounter and lab SIT cases blocked. Preserve earlier evidence.
- [ ] Run focused tests and verifier `--selfcheck`; never run the live verifier.

### Final verification

- [ ] Independently review both changes and their integration, including recovery paths.
- [ ] Run the complete local Python unit suite, focused verifier tests and selfcheck, and `git diff --check`.
- [ ] Report remaining clinical decisions and request separate approval before any deployment or SIT writes.
