# Isolated local native SIT stack

Runs a **synthetic-only** Marley (Frappe/ERPNext/Healthcare) backend plus the
native `clinic_ui` React shell at `/clinic` on this machine, separate from the
existing Marley lab, its tunnel, its data and every existing Docker project.

This directory is the only deployment surface. It does not change
`frappe-apps/`, the existing lab, the `127.0.0.1:18080` tunnel, or any user
data. The stack is a fresh Compose project with new named volumes; it starts
empty and is seeded only with fictional records.

## Isolation

| Item | Value |
| --- | --- |
| Compose project | `marley-sit` (explicit, not the directory name) |
| Site | `marley-sit.local` |
| URL | `http://127.0.0.1:18081` (frontend only, loopback) |
| Volumes | `marley-sit-sites`, `marley-sit-logs`, `marley-sit-db-data`, `marley-sit-redis-queue-data` |
| Existing lab tunnel | `127.0.0.1:18080` — untouched, never referenced |
| Database / Redis | not published |
| Native writes | `write.patient.create` and `write.patient.update` enabled for DOC/NUR; every other capability `read_only` |

`FRAPPE_SITE_NAME_HEADER` is pinned to `marley-sit.local`, so requests arriving
on `127.0.0.1:18081` resolve only to the new site. There is no fallback site.

## Pinned inputs

| Component | Pin |
| --- | --- |
| Base image | `frappe/erpnext:v16.52.1@sha256:88654c9f5fb8e47266b492932270239d4d7c3edd4af3966d0768616c0cd20a42` |
| MariaDB | `mariadb:11.8@sha256:d36f18d65a0459510b8b0f643b226a6d299e3206486e2b64ff20754a6d291707` |
| Redis | `redis:6.2-alpine@sha256:3f9c6e23ed6bea3b41d0ef709fc164c1a0b7c00d5a21a60e0e9c33ada5496881` |
| Marley Healthcare | `earthians/marley` tag `v16.6.1`, commit enforced as `dda107b26e3a0fe1dbd65c7257371235de054a04` |
| clinic_ui / clinic_photos | this repository's working tree at the recorded source revision |

All three images are pinned by immutable digest. The Dockerfile also asserts
the expected base versions and the Marley commit; a mismatch fails the build.
Verified inside the running stack on 2026-11-14: **Frappe 16.54.0, ERPNext
16.52.1, Healthcare 16.6.1, clinic_ui 0.0.1**. The image tag names the ERPNext
release only — the Frappe version inside it differs, so it is read from the
stack rather than assumed. clinic_photos is present in the image/bench but is
not installed on the site.

Frappe/ERPNext provenance: base image digest above, versions asserted at build
and re-read from the running backend. The resolved values are recorded in
`/opt/sit/revisions.txt` in the image and in the private verification evidence.

## Prerequisites

- OrbStack Docker (Compose v2 `docker compose`)
- Node and pnpm for the host-side native build
- Network access for the first image pull and app install

## Bring-up

Run from the repository root. Every step is idempotent except the one-shot
create/provision services, whose scripts skip work that already exists.

```bash
# 1. Private secrets, outside the repository
python3 deploy/local-sit/bin/gen-secrets.py

# 2. Refuse to touch resources this project does not own (read-only)
python3 deploy/local-sit/bin/preflight.py

# 3. Native React bundle into frappe-apps/clinic_ui/clinic_ui/public/ui
pnpm --filter @clinic/app build:native

# 4. Validate the Compose model (project name is forced; -p wins over any
#    inherited COMPOSE_PROJECT_NAME)
docker compose -p marley-sit -f deploy/local-sit/compose.yaml config -q

# 5. Build the backend image (Marley + both clinic apps + built assets)
docker compose -p marley-sit -f deploy/local-sit/compose.yaml build backend

# 6. Start the stack; create-site and provision run once
docker compose -p marley-sit -f deploy/local-sit/compose.yaml up -d
docker compose -p marley-sit -f deploy/local-sit/compose.yaml wait create-site provision

# 7. Verify identity, versions, shell, assets, session, allowlist, capabilities
python3 deploy/local-sit/bin/verify.py
```

`deploy/local-sit/bin/deploy.sh` runs steps 1–7 and appends each command and
exit status to the private deployment log. It stops before any deploy step if
`preflight.py` finds a resource collision. Every Compose command uses
`-p marley-sit`; an inherited `COMPOSE_PROJECT_NAME` is reported and ignored.

Nothing in this workflow deletes containers or volumes. `down -v` below is
documented only for a deliberate, separate teardown.

The Compose file requires values from `deploy/local-sit/.env`, which is a
symlink to the private `compose.env` created by step 1. Running
`docker compose -p marley-sit -f deploy/local-sit/compose.yaml config` **without**
`-q` prints those values; do not paste its output anywhere.

## Private runtime state

Created outside the repository with owner-only permissions:

```
$MARLEY_SIT_STATE_DIR (default ~/.local/state/marley-sit, mode 700)
  compose.env        Compose interpolation values (DB root, site admin, epoch secret, synthetic passwords), mode 600
  credentials.json   site, origin, admin and synthetic account credentials, mode 600
  deployment.log     command/exit/version log for this deployment, mode 600
  evidence/          redacted verification JSON per run, mode 600
```

`deploy/local-sit/.env` is a symlink into that directory and stays untracked.
Site secrets also live only on the `marley-sit-sites` volume
(`sites/marley-sit.local/site_config.json`, written mode 600 by the provision
script).

## Synthetic data

Only fictional records are created, and only on `marley-sit.local`:

| Account | Native role | Clinic code |
| --- | --- | --- |
| `sit.doc@example.invalid` | Physician | `DOC` |
| `sit.nur@example.invalid` | Nursing User | `NUR` |
| `sit.rec@example.invalid` | Healthcare Administrator | `REC` |

One fictional `Patient` (`SITSYNTH Alpha`) is created and exposed through the
`clinic_ui_synthetic_patients` allowlist as workspace alias `WS-SIT-1`. The
native patient identifier is written only to the site configuration; it is not
printed by the scripts or recorded in the repository.

Passwords are generated locally and exist only in `credentials.json`.

`gen-secrets.py --force` rotates those values. The database volume keeps the
password it was initialised with, so rotate secrets only together with fresh
volumes (`docker compose -p marley-sit -f deploy/local-sit/compose.yaml down -v`,
an explicitly destructive step on this isolated project only).

## What "verified" means here

`bin/verify.py` asserts, against the running stack:

- Compose model validates under the forced project name, publishes exactly one
  port on `127.0.0.1`, and preflight confirms every container, volume, network
  and port listener is owned by `marley-sit` (no foreign collision).
- The base, MariaDB and Redis images are digest-pinned; running database and
  Redis containers use the pinned image IDs; the Dockerfile pins the base digest
  and enforces the Marley commit.
- The backend container has site `marley-sit.local`, no `marley.local` site, and
  `clinic_ui`/`healthcare`/`erpnext` installed at their actual versions.
- `/clinic` and `/clinic/<deep-link>` return the no-store shell referencing
  manifest-built `/assets/clinic_ui/ui/assets/...` files, and those assets load.
- Anonymous `bootstrap` returns the fixed 401; anonymous directory access is a
  403.
- A synthetic clinician can log in; `bootstrap` returns `mode: "live"`, one
  clinic role, a CSRF token and a 64-hex epoch; the directory resolves the seeded
  allowlisted patient plus the locally created patient (two items, no native
  identifiers).
- Every capability from `contracts/capabilities.json` is present; only
  `write.patient.create` and `write.patient.update` are `enabled` and every other
  capability is `read_only`; an authenticated POST with a valid CSRF token is
  refused on both read-only endpoints (and an anonymous POST on bootstrap).
- The site holds exactly the three non-system synthetic users (emails, single
  native roles, enabled) and exactly two approved Patients: the original
  `SITSYNTH Alpha` fixture (whose `first_name`, `last_name`, `patient_name`,
  `sex`, `dob` and `status` exactly equal `provision.py`) and the locally
  created `UISYNTH Bravo` owned by `sit.doc@example.invalid`. The allowlist has
  one entry whose workspace alias is `WS-SIT-1`, whose native identifier is the
  seeded Patient, and whose returned display name equals the stored
  `patient_name`. Site config identity, role map and epoch secret satisfy the
  app's own rules.

A pass is scoped to those checks. It is not clinical validation, write
authorization, or production readiness.

## Day-to-day

```bash
docker compose -p marley-sit -f deploy/local-sit/compose.yaml ps
docker compose -p marley-sit -f deploy/local-sit/compose.yaml logs --tail=100 backend
docker compose -p marley-sit -f deploy/local-sit/compose.yaml restart backend
python3 deploy/local-sit/bin/verify.py
```

Re-running `up -d` restarts the existing site; `create-site` skips creation and
`provision` re-applies the same synthetic configuration. Run `preflight.py`
first if any other Compose work happened on this machine.

## Teardown

`docker compose -p marley-sit -f deploy/local-sit/compose.yaml down` stops this
project only and **keeps** the named volumes and the site. Removing volumes or
the private state directory is a separate, explicitly destructive action and is
not part of this deployment task.
