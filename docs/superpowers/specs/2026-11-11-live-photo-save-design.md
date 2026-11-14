# Live photo save in the existing workspace

## Intent and scope

Connect the existing React photo workspace to the Frappe lab so a clinician can manually select and save a photo, and verify that flow in a real browser. The current workspace and proxy use read-only fixtures; the native publication path now has a successful synthetic lab result.

## Approaches

- **Existing workspace with an authenticated live proxy (selected):** delivers the requested workflow in the application clinicians use, while keeping Frappe credentials and private files behind server-side authorization. Requires a limited live adapter and UI work.
- **Lab-only Frappe page:** smaller, but does not make the existing React workspace live.
- **Browser API invocation:** useful for diagnostics, but is not a product-UI save.

## Requirements

- Provide accessible, explicit manual selection and save; never claim a save is complete until the server confirms it. Show pending, saved, and failure or unknown states accurately.
- Default to Thai, retain English, and match the existing workspace's responsive and accessible patterns.
- Authenticate and authorize against Frappe, preserving clinician-wide saved reads, author-only draft edits, immutable originals, private File delivery, and independently versioned annotations. Do not expose session credentials or private file bytes through logs.
- Preserve metadata-only database persistence and private filesystem staging. Do not replay or delete failed operations or clear queues.
- Keep fixture mode working and keep live access explicitly configured for the lab; do not silently enable unsupported writes elsewhere.
- Verify the real browser flow with synthetic lab data, as well as focused automated tests. A pending or unknown outcome is not proof of a save.
- Do not stage, commit, or push changes. Preserve existing backups.

## Non-goals

- General production rollout, migration to another site, destructive cleanup, or changes to unrelated workflows.
- Treating standard Desk File upload or a mocked fixture as proof of protected photo publication.
