# Visit-invoice disposable SIT preparation — blocked, not deployment-ready

Scope is **only** project `clinic-visit-invoice-disposable`, site
`clinic-visit-invoice.local`. Do not run Docker, provision, grant permissions,
enable a gateway, or send live requests under this preparation. Parent owns
approval and deployment after implementation, full suite and push.

The override includes an executable Python gateway in an inactive
`visit-invoice-gateway` profile. Selecting that profile publishes its port at
`127.0.0.1:18082`; it is not selected automatically. It has not been tested
against Docker, Frappe or real traffic and is not deployment acceptance.
Never publish the inherited frontend directly. Do not start this stack until
the blockers below have been resolved and separately approved.

## Why isolation needs more than a project name

`deploy/local-sit/compose.yaml` hardcodes `marley-sit` volume names, frontend
port 18081, image tag, default site and provision mount. Its scripts and
`provision.py` target the Marley SIT and its existing synthetic setup; do not
run or adapt their secrets, preflight, verification or provisioning against
this site without independent review. This override replaces volume names,
image references and site header, removes the frontend publication with
Compose `!reset`, and places inherited provisioning behind an inactive profile.
Do not select that profile: its script is not reviewed for this workflow.
Backend, database, Redis and frontend must remain unpublished.
The inherited `queue-short`, `queue-long` and `scheduler` now use the inactive
`disabled-background` profile. Never select it or explicitly start those
services during this disposable period: doing so could run alternate billing
or queued work. Base frontend depends on backend and websocket, not workers or
scheduler; websocket depends on configurator. Confirm this dependency graph in
the effective model before any start. Disabling workers can break application
features and leave jobs queued: do not later process those jobs without review.
Clear inherited `COMPOSE_PROFILES`; do not use `--profile "*"` or explicitly
target disabled services, since Compose profiles are not access controls.
Preflight must also refuse already-running project workers or schedulers.

The inherited configurator is replaced to add global `use_mysqlclient=0`
before site creation, with fail-fast shell handling. This is intended to select
PyMySQL; verify against the actual pinned Frappe image and inspect effective
site configuration privately after approval.

## Inputs and private state

Base image is `frappe/erpnext:v16.52.1@sha256:88654c9f5fb8e47266b492932270239d4d7c3edd4af3966d0768616c0cd20a42`;
MariaDB is `mariadb:11.8@sha256:d36f18d65a0459510b8b0f643b226a6d299e3206486e2b64ff20754a6d291707`;
Redis is `redis:6.2-alpine@sha256:3f9c6e23ed6bea3b41d0ef709fc164c1a0b7c00d5a21a60e0e9c33ada5496881`.
Inherited Dockerfile enforces Marley `v16.6.1` commit
`dda107b26e3a0fe1dbd65c7257371235de054a04` and expected Frappe/ERPNext
versions. Record approved full commit SHA as `CLINIC_SOURCE_REV`; a build arg
is only a label, not proof that copied working-tree files match it. Build from
that clean exact revision, verify source and generated native bundle provenance
independently, and record resulting image digest. Do not assume current worktree
or an ignored native bundle matches the eventual pushed revision.

Use a new owner-only private state directory outside the repository, with
mode 700 and mode 600 secrets/evidence; use an explicit private `--env-file`.
Never print interpolated Compose config, credentials, site config or secrets.
Compose interpolates the base file before overrides and even inactive profiles.
The private env file must therefore contain nonempty `SIT_EPOCH_SECRET`,
`SIT_DOC_PASSWORD`, `SIT_NUR_PASSWORD`, and `SIT_REC_PASSWORD` values, plus
`SIT_ADMIN_PASSWORD`, `DB_ROOT_PASSWORD`, `CLINIC_SOURCE_REV`, and
`CLINIC_VISIT_INVOICE_IMAGE`. For inactive provision only, use independent,
disposable synthetic placeholder values for the first four; they are not
accounts, permissions or production credentials. Never copy Marley SIT state.
Use separate securely generated disposable admin/database passwords for any
approved site creation. Set `CLINIC_VISIT_INVOICE_IMAGE` to a synthetic
`sha256:` plus 64 zeroes for model checks and image build only; replace with
the verified image ID below before any service start.
No generator here creates credentials. Site setup, synthetic accounts,
least-privilege permissions, invoice prerequisites and capability settings
remain unspecified and require reviewed instructions and authorization.

## Required read-only preflight, after approval

1. Confirm current project, exact approved source SHA, clean tracked and relevant
   untracked sources; confirm native bundle provenance. Verify Docker context.
2. Inspect existing container, network and volume names **and ownership
   labels**, including all four `clinic-visit-invoice-disposable-{sites,logs,db-data,redis-queue-data}`
   volumes and the project network. Refuse every collision, even resources
   with matching labels: this run requires fresh volumes. Never delete or
   reuse anything to resolve a collision.
3. Inspect host listeners and Docker published ports; candidate
   `127.0.0.1:18082` is usable only if free and collision-free. Recheck just
   before binding; another process can race the check. Never touch 18080/18081.
4. With only synthetic private interpolation values, validate merged Compose
   using `docker compose -p clinic-visit-invoice-disposable --env-file
   <private-env> -f deploy/local-sit/compose.yaml -f
   deploy/visit-invoice-sit/compose.override.yaml config -q`. Review the
   effective model privately: exact site, fresh distinct volumes, expected
   immutable images/source, no published ports, no active provisioning.
    Base paths in merged files resolve relative to `deploy/local-sit/`.
    Confirm runtime services have no build configuration, all use the same
    verified image ID, and worker/scheduler/provision/builder profiles are
    inactive. The model check alone does not validate image provenance.

## Source and image provenance — after separate approval

Use a dedicated clean checkout at the approved full commit SHA; reject dirty
tracked files and unexpected untracked **and ignored** build-context files.
The Dockerfile copies both Frappe app trees including ignored files, so checking
only `git status` is insufficient. Rebuild the ignored native bundle in that
checkout from the approved source and locked dependencies; record its hash and
build inputs privately. Verify no other ignored app-tree artifacts enter the
context. Review generated bundle provenance independently.

With private env values set as above and explicit Compose files, build **only**
the builder service after approval:

```sh
test "$(git rev-parse HEAD)" = "$CLINIC_SOURCE_REV"
git diff --quiet && git diff --cached --quiet
docker compose -p clinic-visit-invoice-disposable --env-file "$PRIVATE_ENV" \
  -f deploy/local-sit/compose.yaml -f deploy/visit-invoice-sit/compose.override.yaml \
  build builder
```

The environment variable in the shell must match the private env file; Compose
shell variables override `--env-file`. These two git checks do not check
untracked or ignored files; perform the separate context review above. Inspect
the local build image ID and its `org.opencontainers.image.revision` label
without printing environment/configuration or secrets; compare label to the
approved SHA. Label is not independent proof of source contents. Record the
image ID and provenance evidence privately, then set
`CLINIC_VISIT_INVOICE_IMAGE` in the private env file to that exact
`sha256:<64 lowercase hex>` local image ID. Verify it exists and its label
matches again immediately before any start; reject mismatches:

```sh
IMAGE_ID="$(docker image inspect --format '{{.Id}}' clinic-visit-invoice-disposable/build:local)"
test "$IMAGE_ID" = "$CLINIC_VISIT_INVOICE_IMAGE"
test "$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$IMAGE_ID")" = "$CLINIC_SOURCE_REV"
```

Here `CLINIC_VISIT_INVOICE_IMAGE` and `CLINIC_SOURCE_REV` must be loaded
privately into the shell from the same reviewed values as the env file.
Do not print image inspection JSON. Runtime services reference that image ID,
not the mutable build tag, and have inherited builds reset. Any later approved
Compose `up` must use `--no-build`, never activate the builder or background
profiles, and recheck the effective model and image ID. Image labels and local
ID still do not attest the build context; source and bundle evidence remains
an explicit approval blocker.

## Gateway and dispatch blockers

The gateway allows GET Clinic shell and bundle assets, login/logout, bootstrap,
directory, patient and appointment reads, and exactly
`clinic_ui.api.visit_invoice.{view,preview,submit}`. JSON bodies and query keys
are allowlisted; duplicate JSON keys and unknown keys are rejected. Preview and
submit additionally require an exact string `vat_mode` of `vat_7` or `no_vat`;
the gateway does not infer tax mode or No VAT eligibility. Cookies and
CSRF headers pass through; upstream remains responsible for authentication,
authorization and CSRF. Everything else is denied, including Desk and generic
Frappe APIs. This is deliberately limited to the observed target transport, not
all Clinic features. Reconcile routes against the final pushed source.

Frappe may dispatch `cmd` from query or body, independently of the apparent
path. A path-only allowlist is insufficient. Enforce a single unambiguous
dispatch target for every allowed request, including JSON and form bodies,
duplicate keys, encoded keys and content types; fail closed on ambiguity.
Reject encoded/normalized path bypasses before upstream routing and test raw
request targets, traversal, double encoding, alternate verbs, login, CSRF,
API parameters and asset paths against the actual gateway and pinned Frappe.
Python's standard-library HTTP server is not a hardened production reverse
proxy. Independently review HTTP parsing, request smuggling, response handling,
network isolation, authorization and body/dispatch behavior before any runtime
approval; it must not be exposed beyond the local disposable environment.
The static tests do not establish actual Frappe behavior or gateway safety.
Independently verify no alternate service or network route bypasses it.

Offline tests only: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest
discover -s deploy/visit-invoice-sit -p test_policy.py`. Passing does not
assert gateway enforcement, successful login, invoice acceptance, permission
safety or clinical correctness. Runtime validation requires a separate
approved plan. No teardown or volume deletion is authorized here.
