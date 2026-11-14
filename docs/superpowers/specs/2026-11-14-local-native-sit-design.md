# Local native React SIT design

## Intent and scope

Exercise the new React `/clinic` application against a real, isolated Marley
backend, including create, edit, and view workflows, and report each SIT
variant honestly. The first implementation slice is patient create, edit, and
view; subsequent slices cover the remaining catalogue. A fresh local OrbStack
Docker deployment uses fictional data only and must not connect to the existing
Marley lab or its data.

## Approaches

1. **Isolated local stack (selected):** deploy the backend and React app locally.
   This supports synthetic write and recovery testing without changing the
   existing lab, at the cost of provisioning and maintaining a separate stack.
2. **Existing Marley lab:** reuse the installed app, but its existing rollout
   authorizes read-only operation and does not establish the isolation required
   for fault and concurrency tests.
3. **Proxy or fixtures:** useful supplemental evidence but do not exercise the
   native React integration and cannot establish SIT completion.

## Design and constraints

- Use the existing native Frappe integration and same-origin React build;
  do not route native operations through the legacy proxy.
- Provision a separate local site, private credentials, synthetic accounts,
  fictional records, and private evidence. Do not expose credentials in logs.
- Keep mutations disabled until native authorization, validation, durable
  operation-bound receipts, transaction and fencing behavior, unknown-outcome
  locks, and applicable legacy-intent continuity are proven. A fresh site must
  establish its independence rather than assume an empty legacy store.
- Verify real Frappe Patient metadata and effective permissions before deciding
  the supported create and edit fields. Do not invent status or field mappings.
- Record each applicable SIT variant and UI plus independently verified backend
  result; distinguish failures, blockers and unexecuted cases. Proxy, fixture
  and unit results are supplemental, not native-browser passes.
- The existing lab, real patient data, and production are out of scope.
  Photo publication and other high-risk write categories have separate gates.
- Verify recovery on an isolated target before fault injection or concurrency
  scenarios. Never retry or unlock an uncertain write without proven terminality.

The user approved this approach on 2026-11-14 and selected local OrbStack.
