# Read-only native clinic UI lab rollout

**Authorization:** User approved a verified backup, installation of the
read-only clinic app, and a brief lab app-service restart. Production is
excluded. No account or permission changes, clinical writes, record deletion,
queue clearing, or uncertain-operation retries are authorized.

**Target:** The existing Marley lab compose project and site on the user's lab
machine. Confirm project, site, image and published port privately before
acting; never use the separate photo-acceptance stack or the stale repository
compose file.

## Preconditions

- [x] Build and independently inspect a new image derived from the currently
  running photo-enabled image, including the native UI app and built assets.
- [x] Capture a fresh private database, public-files, private-files and site
  configuration backup without pruning existing backups. Verify archive
  integrity and host/container copy checksums. The copy is on the same host;
  no restore rehearsal or off-host disaster-recovery claim has been made.
- [ ] Immediately before cutover verify the exact target, baseline image,
  backed-up compose configuration, absence of running or queued publication
  work, and publication gate off. A snapshot cannot rule out an operation
  starting just afterward; stop on unexpected activity.
- [ ] Preserve existing proxy intent databases and their mappings. No old
  uncertain intent is unlocked or migrated by this read-only rollout.

## Controlled cutover

- [ ] Update only the lab compose image reference to the inspected image,
  preserving a private copy of the original configuration. Never print secrets.
- [ ] Run and check the existing configurator and create-site one-shot services
  separately before starting app services. Confirm their exit statuses; they
  rewrite existing configuration files even when values are unchanged. Stop
  on an unexpected change or failure; do not retry uncertain operations.
- [ ] Recreate only the six Marley application services, not MariaDB, Redis or
  any other project. Verify service health and queue/job state. Stop on error.
- [ ] Install `clinic_ui` only after image and module import checks. Installation
  adds Frappe app bookkeeping; no clinic records, accounts, roles, permissions,
  photo app or publication flags are changed.
- [ ] Verify shell/deep-link/asset routing and guest 401, plus existing
  `/api`, `/desk` and `/login` behavior. Keep clinic-specific configuration
  disabled until the installation smoke checks pass.
- [ ] Configure only the app's required native role map, secret and synthetic
  patient allow-list privately, retaining all write capabilities disabled.
  Test synthetic accounts' login, logout and clinic-directory scope; never
  print credentials, tokens or record identifiers.

## Rollback and stop rules

- Never remove the site volume, restore a stale database, uninstall an app,
  erase a record, clear a queue or re-enable the old proxy writer.
- If installation has not started, the original image and private compose copy
  can be used to undo the image change, subject to a fresh in-flight check.
  Once `clinic_ui` is installed, do not switch to an image missing that app:
  retain the new image and disable the feature while assessing recovery.
- Treat any interrupted install or ambiguous operation as unknown: stop,
  preserve evidence and do not retry automatically.
- Report HTTP/status and role-boundary evidence separately from clinical
  write workflows and production readiness.
