# Staff workspace switcher

Staff with more than one assigned clinic role should be able to switch jobs in React using the same Frappe account. Put **Switch workspace** under the header profile icon, show the current workspace, and list only workspaces the signed-in account is assigned. Selecting one updates navigation and takes the user to its home. A single-role account keeps its current behavior.

**Approach:** One active workspace at a time, rather than combining every role’s tools in one navigation menu. This keeps the app focused and preserves role-specific workspaces. A profile-menu-only implementation is not enough: the current native backend denies React sessions with multiple matching clinic roles.

Derive eligible workspace roles from the current Frappe session. The native backend must verify that the chosen workspace is still assigned wherever it authorizes a role-specific request; a browser preference is not a grant. Keep Frappe document/field permissions unchanged. On switch, discard cached role-specific state and do not silently run a request in a different workspace. Do not ship Owner account management, role assignment, or broader Frappe permissions as part of this change.

Verify a fictional user assigned Reception and Cashier can use both workspaces without signing out; one assigned only Reception cannot select Cashier or reach its protected actions; revoking a role takes effect; ordinary single-role sessions still work.
