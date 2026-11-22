# Offline visit publication boundary probe

This is **not a complete integration harness**, authorization to run one, or
proof of transaction visibility. The probe locates four source calls, checking their
uniqueness and lexical order inside `_publish_prepared_visit`. Review control
flow and deployed source separately; lexical order is not proof of execution.
It performs no network, Frappe, database, queue, or site access.

From repository root:

```sh
python3 -m unittest discover -s frappe-apps/clinic_photos/tests/unit -p test_visit_precommit_probe.py
python3 -m unittest discover -s frappe-apps/clinic_photos/tests/unit -p test_visit_precommit_child.py
python3 frappe-apps/clinic_photos/tests/unit/visit_precommit_probe.py
python3 -m py_compile frappe-apps/clinic_photos/tests/unit/visit_precommit_probe.py frappe-apps/clinic_photos/tests/unit/test_visit_precommit_probe.py
```

Current source: slot insert 663, `journal.publish` 664, final-commit arm 665,
`writer.finish_visit_transaction(..., "commit")` 674. A future trace must
stop immediately **before** the call at 674, after 663–665 execute in the
same operation and transaction. Source line numbers can change: rerun the
probe and verify deployed source identity, not just numbers. Neither the probe
does not install a trace. The separate `visit_precommit_child.py` wrapper
installs an operation-scoped trace and delegates to the real `process.run_visit`.
Its DB-free tests use a fake function; no native child has been exercised.

The wrapper accepts only the exact disposable site and a 32-character
lowercase hexadecimal operation ID. It verifies loaded coordinator bytecode
against installed source, probes the commit line, and verifies the traced
frame's prepared operation ID, site and PREPARED state before signaling. A
private, same-UID directory with mode 0700 must exist, with neither sentinel
present. It creates `ready` (0600), waits at most the supplied timeout
(maximum 30 seconds) for a same-UID regular single-link `release` (0600), and
restores any previous trace. Timeout or mismatch raises before the commit
call. If the normal child catches that exception, the wrapper still refuses.
No-hit and repeat-hit also refuse; no-hit can only be diagnosed after the
delegate returns and is **not** proof no other path committed.

After review and authorization, invoke inside the same environment and working
directory as the normal child, using absolute paths to reviewed test files
available there:

```sh
python /REVIEWED/TESTS/visit_precommit_child.py \
  --site marley-photo-acceptance.local --operation-id "$OPERATION_ID" \
  --directory "$PRIVATE_BARRIER_DIRECTORY" --timeout 15
```

Use a new directory per operation, mode 0700 and owned by the child UID.
Controller waits boundedly for `ready`, creates `release` mode 0600 using
exclusive creation, and does so in `finally` even on assertion failure. Await
the wrapper with a separate deadline. These files are coordination, not
durable evidence; remove only these test-owned files after the child exits.
Wrapper must be launched where installed source and reviewed sibling probe are
accessible. Do not pass credentials on its command line.

## Blocked manual integration sequence, not yet authorized

Target only `marley-photo-acceptance.local`, `127.0.0.1:18082`, Compose
project `marley-photo-acceptance`, scratch root
`/private/var/folders/6q/by0mbghd2t174h60mvntrs800000gn/T/opencode/photo-acceptance-phase1`.
Do not display `credentials.json` or `compose.real.env`. Do not use fixtures
00001/00002. Parent must first provision fresh synthetic identities, inspect
instrumentation, approve the run and specify validated fixture IDs.

1. Verify site, project, source identity, gate, synthetic actor and
   appointment/encounter identity; check empty requested slot, no operation for
   fresh key, no existing target File or Photo, and idle worker/queue. Abort on
   ambiguity. No fixture creation or persistent-evidence deletion belongs here.
2. Arrange reviewed test-only interception of that request's notification,
   before capture, so no ordinary worker starts the same operation first.
   Interception must preserve the committed submission, identify only its exact
   operation, and start exactly one wrapper as the child; it must not modify
   product code, permissions, transaction behavior or unrelated queue jobs.
   **No notification interception or queue isolation is provided here.**
   Stop until parent implements and reviews it; manually launching a duplicate
   after an uncontrolled notification does not satisfy this.
3. Using an authenticated native session, call
   `clinic_photos.api.visit_upload.capture` once with fresh request key,
   appointment, slot, body area, viewpoint and synthetic image; After also
   requires encounter. Capture returns `pending` and enqueues after commit.
   Identify its exact operation privately without logging secrets or image.
   The actual launcher command order is
   `python -m clinic_photos.publication.process --visit --site marley-photo-acceptance.local --operation-id <validated-id>`
   from sites. The trailing `--visit` order is rejected by `process.main`.
4. Await the target child barrier with deadline. On an independent connection B
   with a fresh read transaction, verify durable operation PREPARED, no visible
   new File/Photo/slot/PUBLISHED row, and status queried using the original
   `(appointment, slot, request_key)` is `unknown`, not `saved`. End B's
   snapshot before later checks. If barrier absent, abort; no claim from timing.
5. While A remains paused, independently attempt only the specifically
   approved duplicate/overlap and record bounded completion. A second worker
   must not publish or take over A's locks; its refusal/busy/unknown is not
   itself proof of which lock blocked it. Confirm no additional operation,
   File, Photo or slot outcome unless the reviewed overlap intentionally uses
   a different key; do not assume its HTTP status without measuring it.
6. In `finally`, release the barrier, await child/worker exit with deadlines,
   and report timeout/uncertainty rather than claiming rollback. From fresh
   reads verify one PUBLISHED operation, one bound File/Photo/slot, status
   `saved`, and no competing publication. Verify lock release by a reviewed
   non-destructive check; never unlink stable lock objects. Keep all persistent
   evidence. Close sessions/connections and remove only test-owned ephemeral
   signaling resources; never clean fixture records or protected files.

Notification interception and isolated queue execution remain concrete
blockers. Do not run the sequence until parent reviews a complete
implementation and explicitly authorizes it.
