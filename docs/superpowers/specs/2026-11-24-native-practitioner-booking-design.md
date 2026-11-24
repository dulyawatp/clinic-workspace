# Native practitioner booking design

Extend the existing React Reception booking form on `sit-integration` so staff can choose a practitioner and date, see the same bookable slots as Frappe Desk, and create the same native `Patient Appointment` with the practitioner assigned. Keep department-only booking available.

**Approach:** Reuse Marley’s native practitioner lookup, availability method and appointment save path. Mirror Desk’s slot presentation where Marley returns schedule windows and occupancy rather than finished choices. A separate calendar would add scope; an independent availability engine would duplicate policy and risk diverging from Desk.

Use existing production booking access and native permissions. Do not add a feature gate, role, date cutoff, or scheduling rule beyond Frappe Desk’s design. Preserve native slot metadata that affects booking, do not expose booked patient details, and show native save failures without creating a second appointment. No production deployment or live data changes are part of implementation.
