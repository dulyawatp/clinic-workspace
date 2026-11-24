# Named Clinic Owner deployment

Only a site administrator should run this explicit, private Bench command, after
authorizing the site and confirming a recoverable backup. Run deployments
serially: prevent concurrent permission, role, and User edits through Desk,
other Bench commands, and other automation until this command finishes.

```sh
bench --site SITE execute clinic_ui.owner_deployment.provision_owner --args '["owner@example.invalid"]'
```

Replace both placeholders. The exact User must already exist, be enabled, be a
System User, and have no native role profile. Guest and Administrator are not
eligible. Required native roles must exist and be enabled. Bench connects as
Administrator; the command requires that session and uses normal native
document permission checks. It is not an HTTP endpoint.

The first invocation creates `Clinic Owner Practitioner Editor` and
`Clinic Owner Practitioner Creator`. Repeating it for the same Owner is a no-op
when configuration and assignments match; an explicitly named additional Owner
can use the same roles. Existing role metadata,
every role reference, dedicated permissions and existing assignees must match
the expected configuration, otherwise the command refuses without overwriting
them. Investigate conflicts before proceeding; do not delete other configuration
to force the command. Output is `{"status": "provisioned"}` or
`{"status": "unchanged"}`. Bench commits successful calls; unsuccessful calls
are rolled back. Frappe's Bench exception fallback can obscure the original
error, so inspect operator logs carefully.

The normal native User save preserves existing roles and adds System Manager,
Healthcare Administrator, and both dedicated roles. These are native privileges,
including Desk access and broad administration from the first two roles; this
is not limited to the clinic UI. The Editor role retains only level-0 Healthcare
Practitioner read/write; the Creator role adds only level-0 create. Together they
grant read/write/create, without delete, share, or other DocType grants.
Previously provisioned Owners receive no Creator assignment automatically:
explicitly run the command again for each named Owner approved for create.
No shared role is modified. Workspace selection, login, installation and upgrades
do not invoke this command.

Frappe uses Custom DocPerm rows *instead of* stock DocPerm rows when any exist.
The command retains existing custom rows or copies the complete stock set using
Frappe's native permission function before adding its rows, and refuses if the
permission snapshot changes. After deployment, inspect Role Permission Manager
for Healthcare Practitioner and verify all pre-existing stock or custom grants
still match the site's pre-deployment record, alongside the dedicated read/write
and create rows. Review these custom permissions after application upgrades: copied stock
permissions do not automatically follow later stock permission changes.

To remove Owner access, use native User administration to remove the four roles
that were explicitly added, retaining any roles the user already needed. Removing
System Manager or Healthcare Administrator affects all native Desk access those
roles grant. If other users need any of these roles, do not remove their roles.
Remove the dedicated permission rows and roles only after checking no user or other
configuration references it; preserve every other Custom DocPerm row. Do not
reset all practitioner permissions, since that discards site customizations.
