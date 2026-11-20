"""Best-effort private notification after the native transaction commits."""


def schedule(frappe, appointment, patient):
    try:
        observed_at = str(frappe.utils.now_datetime())
        actor = frappe.session.user

        def notify():
            try:
                if getattr(frappe.local.flags, "in_migrate", False):
                    return
                for method in frappe.get_hooks("clinic_native_checkin_observer") or ():
                    try:
                        frappe.enqueue(method, appointment=appointment, patient=patient, actor=actor,
                                       observed_at=observed_at)
                    except Exception:
                        continue
            except Exception:
                pass

        frappe.db.after_commit.add(notify)
    except Exception:
        pass
