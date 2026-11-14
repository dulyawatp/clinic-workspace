/**
 * Reception drilldown scopes. A scope names the rows behind one KPI count, so
 * the fixture counts and the filtered preview can be checked against the same
 * definition instead of drifting apart.
 */
export type ReceptionScope = "all" | "checkedIn" | "waiting" | "upcoming";

export const RECEPTION_SCOPES: readonly ReceptionScope[] = [
  "all",
  "checkedIn",
  "waiting",
  "upcoming",
];

export function isReceptionScope(
  value: string | null,
): value is ReceptionScope {
  return value !== null && RECEPTION_SCOPES.includes(value as ReceptionScope);
}

/** `null` means "every appointment row"; no scope widens to an unknown state. */
export const SCOPE_STATUSES: Readonly<
  Record<ReceptionScope, readonly string[] | null>
> = {
  all: null,
  checkedIn: ["arrived", "waiting", "called", "in_consultation", "completed"],
  waiting: ["waiting"],
  upcoming: ["scheduled"],
};

/**
 * KPI key → the scope its drilldown applies. A key that is missing here has no
 * drilldown, and the surface skips it rather than guessing a destination.
 */
export const SCOPE_BY_KPI: Readonly<Record<string, ReceptionScope>> = {
  todayAppointments: "all",
  checkedIn: "checkedIn",
  waiting: "waiting",
  upcoming: "upcoming",
};
