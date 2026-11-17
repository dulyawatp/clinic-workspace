# Medication ordering deferral

## Intent and scope

Keep medication ordering unavailable until its clinical contract, native lifecycle and permissions are reviewed. Align the native React surface, local SIT verifier and SIT documentation with the existing pre-dispatch refusal. No permission changes, deployment or live writes are authorized by this design.

## Approaches

1. **Selected:** explicitly defer medication ordering; prevent native UI entry, assert the capability remains disabled, and mark the medication SIT case blocked. This preserves the current safety boundary.
2. Implement medication ordering now. This requires unresolved clinical, native cascade and authorization decisions, so it is not appropriate without a separate reviewed contract.

## Requirements

- Preserve backend refusal for nonempty medication rows and avoid discarding existing data silently.
- Show medication ordering as unavailable on the native React surface without changing unrelated workflows.
- The verifier must require medication ordering to be disabled, including when the encounter gate is enabled; it must not silently stop checking the capability.
- Mark the medication-order SIT case blocked, not passed; preserve prior evidence.
- Add focused local regression tests. Do not deploy, push, change permissions, access other environments, or perform SIT writes.
