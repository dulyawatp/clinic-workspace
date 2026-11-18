# Native development writes

## Intent

In the isolated synthetic SIT development site, use existing Frappe and Marley
document permissions, validation, lifecycle and transactions instead of the
clinic adapter's persistent write fences and browser locks.

## Approaches

- A second development API and a new alias registry would duplicate the
  existing integration and add machinery the user explicitly does not want.
- Retaining the existing fence with its global check disabled would leave the
  receipt, acknowledgement and budget machinery on the active write path.
- Convert the existing native paths, one complete workflow at a time, while
  preserving historical receipt reads where required for existing links.
  This is the chosen approach.

## Constraints

- Preserve the existing ledger and its unresolved entries without fabricating
  an outcome or mutating its historical evidence.
- Retain session, CSRF, native document permissions, site restrictions,
  supported-field validation and native controller operations.
- Apply development behavior only to the isolated synthetic site; do not
  represent native identifiers as anonymized or this approach as production
  ready.
- Do not enable encounter draft, completion, amendment, lab ordering or
  medication ordering merely because the custom fence is removed.
- Do not automatically retry an uncertain mutation. Tell the user its outcome
  is unknown and to inspect current records before repeating it.
- Native transactions do not guarantee exactly-once behavior after a lost
  response.
- Preserve the existing local appointment-clock fix and its tests.

## Delivery

Convert patient writes and discovery as the first complete backend/frontend
slice. Convert appointment and other enabled native write families in further
complete slices. Unconverted workflows remain under their existing behavior
and must not be advertised as converted.
