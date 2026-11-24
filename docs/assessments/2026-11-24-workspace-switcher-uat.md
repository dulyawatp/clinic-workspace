# Workspace switcher user acceptance

**Target:** Final local `feat/staff-workspace-switcher` feature snapshot based on `fa8f11a`, deployed only to fresh synthetic `marley-sit-workspace-uat` (`http://127.0.0.1:18086/clinic`). Acceptance was performed through the React UI with Playwright MCP as fictional staff. No production access, deployment, or payment.

| User journey | Observed result |
|---|---|
| Sign in as Reception + Cashier | The chooser showed only Cashier and Receptionist; selecting Receptionist opened `/clinic/reception`. |
| Account → Switch workspace → Cashier | Opened `/clinic/cashier` without signing out. Cashier navigation opened Payments and showed Payment review. No payment was submitted. |
| Two tabs under the same sign-in | A second tab selected Reception while the first retained Cashier and its Payments page after reload. |
| Switch back to Reception | Returned to `/clinic/reception`, restored Reception navigation, and removed the Cashier Payment review from the current screen. |
| Remove the Cashier role from the fictional multi-role fixture and reload | The chooser reported the workspace was unavailable, offered only Receptionist, and did not display Cashier navigation. Selecting Receptionist restored access to `/clinic/reception`. |
| Sign in as Reception-only and open `/clinic/payments` | Account remained a profile link, no switcher was offered, and React showed Permission denied without Payment review. |

These results establish the tested browser journeys, not every Clinic Photos action, live accounting, or production rollout. Generic Frappe resources still apply their own permissions. The Profile screen showed a separate service-unavailable message for its account-details read; sign-out continued to work. That read was not changed by this feature.
