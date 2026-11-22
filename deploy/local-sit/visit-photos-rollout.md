# Native visit photos: shared SIT rollout checklist

This is for the local synthetic `marley-sit` project and
`marley-sit.local` site on `127.0.0.1:18081`. It does not authorize a
production rollout. Deploying the code is separate from enabling visit photos
or drawings. Keep both features off while preparing and verifying the site.

## Before enabling either feature

1. Verify the expected candidate revision, exact site, existing volume
   ownership, and approved backups. Keep all volumes and existing credentials.
2. Verify the running site has both `clinic_ui` and `clinic_photos` installed.
   An image containing both applications is not sufficient. The standard SIT
   provisioner does not install `clinic_photos` on an existing site. Treat any
   necessary app installation or migration as an explicit site change; verify
   the resulting photo DocTypes and hooks before proceeding.
3. On the confirmed site, run the managed-share check from the deployed
   backend:

   ```sh
   docker compose -p marley-sit -f deploy/local-sit/compose.yaml exec -T backend \
     bench --site marley-sit.local execute clinic_photos.access.share.assert_no_managed_shares
   ```

   A nonzero exit is a stop condition. Do not remove shares automatically or
   bypass the check. Existing native shares can affect read decisions
   independently of the visit-photo controller; the hook that blocks new
   shares does not remove historical ones. This check is for managed photo,
   drawing, and File shares; it does not revoke ordinary Patient shares.
4. Confirm that the web services, short worker, and scheduler run the expected
   image and that queue workers are registered. Check the effective native
   permissions of ordinary synthetic reception and physician accounts; app
   role labels alone are not evidence.

## Enable and check

- For a separately approved activation, set `clinic_visit_photos_site` to the
  exact site name and `clinic_visit_photos_enabled` to the boolean `true`.
  Set `clinic_visit_photo_annotations_enabled` to boolean `true` only after
  the visit workflow and drawing acceptance prerequisites are confirmed.
  Do not enable legacy photo publication for this rollout.
- Run the native SIT Playwright scenario described in
  `app/e2e-sit/README.md` with a fresh, unshared synthetic visit and ordinary
  staff. Verify Before and After save, original-key status, reopened drawings,
  unchanged original photo IDs and bytes, and read-only History. Separately
  confirm that revocation preserves authorized reads but refuses a new save,
  and that a nonauthor who can view the originals cannot read the drawings.
- If any check fails, stop new drawing writes. Keep evidence and reconcile a
  pending or uncertain save using its original request key. Do not replay a
  write, delete residue, or rekey to work around it.
