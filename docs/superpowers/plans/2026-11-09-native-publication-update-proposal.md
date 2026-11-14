# Native Publication Update — Preparation and Approval Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans only after explicit approval of the applicable task. Steps use checkbox syntax. This document is a proposal, not permission to execute remote changes.

**Goal:** Prepare a reversible update of the disposable acceptance environment without consuming, altering or losing its existing submissions.

**Architecture:** Establish a verified recovery point and a fully isolated rehearsal before changing the acceptance stack. Separate source deployment, schema synchronization and publication activation into independently approved steps; leave publication inactive throughout the update.

**Tech Stack:** Existing Frappe/MariaDB/Docker acceptance stack; no new dependency or service is approved.

**Spec:** `docs/superpowers/plans/2026-11-09-photo-publication-blockers-and-validation-approvals.md`; approved revocation design and plan. **Context/reference only:** `docs/2026-11-08-photo-publication-recovery-runbook.md` is not a migration, backup or restore specification. Fresh observations: `.superpowers/sdd/2026-11-09-synthetic-actor-revocation-controls/native-preflight-progress.md` and referenced raw logs.

## Global constraints

- Proposal only. No copy, migrate, ALTER, restart, backup export, restore, queue manipulation, gate change, User mutation or native test is authorized by this document.
- Preserve the exact synthetic stack; never replace or swap its active database as a test technique.
- No staging, commits or pushes. No production/core patch, permission bypass, latch reset, uncertain-write retry, automatic replay or deletion.
- Native acceptance remains UNRUN. The 292 local focused checks establish local behavior only.
- Existing observations expire when source/environment/state changes; re-identify immediately before an approved window.

## Current evidence and limits

- Three participating containers run the same image and have matching sampled source files across their mounted and container-local copies. Eight files were sampled, not the complete application.
- Deployed `process.py`, `submission.py`, `journal.py` and operation DocType JSON differ from the reviewed local candidate. Semantic compatibility is not established.
- Installed operation metadata has 14 of the candidate's 22 fields. Both physical columns and DocFields lack `generation`, `expected_sha256`, `expected_size`, `published_file`, `published_domain_doctype`, `published_domain_name`, `published_version`, `creation_identity`.
- Aggregate state count is 28 SUBMITTED. This does not prove that these records are synthetic, compatible, quiescent, replayable or safe to discard. Their payloads/identities have not been read.
- Observed database profile: REPEATABLE-READ, innodb_snapshot_isolation OFF. Gate settings, queues, effective process imports, canonical users, control-directory setup and native adoption remain unverified.

## Approach recommendation

**Recommend preserving this stack and rehearsing the candidate against an independently isolated recovery copy.** This retains the 28 submissions and exposes migration side effects before touching the acceptance environment. Creating/copying into that rehearsal environment requires its own privacy, storage and execution approval.

Updating in place without tested recovery is rejected. Replacing the stack with an empty one would avoid compatibility work but erase the pending-operation question; it is not approved and is not equivalent acceptance evidence. The user has not yet chosen or approved a recovery-copy method.

## Review focus

1. Startup hooks, schedulers or migration patches unexpectedly publish old submissions: rehearsal must demonstrate zero existing operation transitions and zero notifications/publications.
2. Backup is internally inconsistent or fails to restore: recovery-point validation must cover database, private files, site configuration and source together, with protected credentials retained privately.
3. Restored file identities differ: restored dev:inode values cannot be represented as original publication evidence; coherent restored data is not automatically a `saved` result.
4. Code rollback assumes reversible DDL: no destructive schema downgrade; failed update stays inactive and preserved pending separately authorized recovery.
5. Operator/target/source drift between approvals: each execution packet binds exact target, versions, hashes, actor authority, stopped consumers, time window and stop conditions.

## Deliverables and owners

Planning artifacts only in `docs/superpowers/plans/` and the existing revocation evidence directory. No application or infrastructure code changes are required by this proposal. Parent owns approvals/evidence reconciliation; discovery specialists own narrow read-only facts; an architecture reviewer checks migration/recovery risks. Standards and Spec remain separate reviews.

### Task 1: Complete the bounded change specification

**Consumes:** retained preflight logs and reviewed candidate source. **Produces:** `native-update-change-spec.md` in the revocation evidence directory; no remote changes.

- [ ] Compare the complete deployable candidate with the actual deployed tree using separately approved read-only hashes/static source reads. Pin all participants and framework versions without executing unchecked package/site code.
- [ ] Identify the exact framework-native schema synchronization entry, migration hooks/patches, startup scripts and scheduler/worker launch behavior from pinned source. Do not invent a safe `bench migrate` command or treat this runbook as an execution recipe.
- [ ] Record the required source/schema ordering: consumers and ingress must be isolated before new code is exposed; operation readers/writers stay unavailable until schema synchronization and postchecks succeed.
- [ ] Classify existing submissions through an owner-approved private investigation plan. Default is preservation and no processing. Any payload read or per-record audit needs additional authority; aggregate counts are insufficient.
- [ ] Deliver an exact command-level proposal with target set, paths, expected reads/writes, timeouts and stop conditions. If a safe synchronization path cannot be established, retain a blocker rather than provide executable guesses.

### Task 2: Approve and prove the recovery method

**Consumes:** Task1 change specification. **Produces:** approved recovery manifest and isolated restore-test evidence, only under a new explicit execution approval.

- [ ] Obtain owner decisions on backup destination, encryption/access, retention, deletion authority, rehearsal resources and private-data handling. No backup export or new infrastructure is implicitly authorized.
- [ ] Specify how ingress, publication launchers, queues and scheduled consumers are made quiescent. Capture before/after evidence; a gate value or one process listing alone is not quiescence proof.
- [ ] Account for every DB/private-file writer, including in-flight transactions/jobs and external writers. Define the exact consistency boundary and evidence for the later capture procedure. If consistent capture cannot be established, backup approval is blocked.
- [ ] Capture one consistent recovery point spanning DB, private files, site configuration and exact deployed source/image. Record tool exits and checksums without logging payloads, secrets or user identities.
- [ ] Restore only into an independently approved isolated destination with notifications, network side effects, workers and publication disabled. Establish isolation before any restored application code executes and retain it through startup. Never swap the active acceptance DB or restore an older backup over active acceptance state.
- [ ] Verify restore integrity, schema, state counts and private internal equality evidence; distinguish restore success from immutable inode/publication-proof portability. Failure blocks deployment.

### Task 3: Rehearse the exact update without processing old work

**Consumes:** validated isolated recovery point and exact approved candidate. **Produces:** rehearsal report; source/schema synchronization still needs its own authorization.

- [ ] Before launch, prove isolation from acceptance DB/Redis/sites, outbound delivery and publication consumers. No fixture mutation or publication tests are included.
- [ ] Execute only the independently reviewed source/schema procedure against the rehearsal target. Preserve raw outputs and stop on callbacks, unexpected SQL/data changes or state transitions.
- [ ] Compare operation counts and private canonical row equality before/after, excluding only an exact, justified field-exclusion allowlist approved in advance. Retain original private comparison evidence securely. All 28 existing submissions remain unprocessed; no synthetic claim inferred from count equality.
- [ ] Verify all 22 declared fields, corresponding physical columns, unique nullable reservation_key, identity_digest uniqueness, DB profile and identical complete candidate hashes across participants.
- [ ] Rehearse stop/recovery decisions without destructive schema downgrade. A code rollback alone is not a complete DB rollback. Any uncertain commit preserves state and ends execution, with no retries.
- [ ] Any later recovery approval must identify its destination, preserve failed-state evidence and account for intervening writes. Never restore an older backup over active acceptance state. Restart an earlier code version only after compatibility with the resulting schema/data is proved; otherwise remain inactive.
- [ ] Obtain separate Standards and Spec PASS on the exact procedure/evidence; rehearsal is not native publication acceptance.

### Task 4: Request a bounded acceptance-stack update window

**Consumes:** reviewed change specification, proven recovery method and passing rehearsal. **Produces:** execution approval request, not execution.

- [ ] Name exact operator, target/site, command allowlist, downtime window, private evidence location, verified recovery point and exact candidate hashes.
- [ ] Request separate authority for consumer/ingress isolation, source deployment, schema synchronization and controlled restart. Any gate change must be explicit; neither enabling publication nor running pending jobs is included.
- [ ] Require durable, verified exclusion of all pre-existing submissions throughout restart and later validation: scheduler repair, after-commit notifications and worker startup must not consume them. Do not mutate operation states to achieve isolation. If selective isolation cannot be proved, keep consumers stopped and block native validation; any processing or disposition requires a separate owner decision.
- [ ] Define postchecks and failure thresholds from Task3. Keep the site inactive if source/schema integrity fails; preserve all submissions and uncertain artifacts, no automatic rollback/delete/replay.
- [ ] After a later successful update, request a different native-validation window for synthetic actor/verifier setup and both revocation scenarios. Pre-existing submissions must not be swept into that test window by notifications or scheduler repair.

## Verification budget and handoff

No tests or remote execution are needed to approve this proposal. Task1 owns static facts; Task2 owns consistent recovery/restore proof; Task3 owns migration/no-processing evidence; Task4 binds approvals to those artifacts. Reuse the 292 local focused results only while relevant source remains unchanged. Broaden tests only for actual changed behavior, not to generate reassuring totals.

**Approval requested now:** review the recommended preservation/rehearsal approach and permit Task1 preparation. Tasks2–4 require the concrete procedures and additional approvals described above. This proposal does not establish backup capability, safe migration, native acceptance, production readiness or real-data activation.
