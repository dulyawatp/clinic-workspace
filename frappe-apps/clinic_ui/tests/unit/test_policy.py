"""Pure policy tests: session validity and single-role resolution.

No Frappe import and no I/O. These tests prove the fail-closed decisions only;
they do not claim installed-site, permission-engine or HTTP behaviour.
"""

import unittest

from clinic_ui.core import policy

ROLE_MAP = {
    "DOC": ("Physician",),
    "REC": ("Clinic Reception",),
    "NUR": ("Nurse",),
}


def view(**overrides):
    values = {
        "sid": "sid-abc123",
        "user": "clinician@example.test",
        "full_name": "Clinician Example",
        "enabled": True,
        "roles": ("Physician",),
    }
    values.update(overrides)
    return policy.SessionView(**values)


class SessionValidityTests(unittest.TestCase):
    def test_current_enabled_user_is_valid(self):
        self.assertTrue(policy.is_valid_session(view()))

    def test_missing_or_guest_sid_is_invalid(self):
        for sid in ("", "   ", None, "Guest"):
            with self.subTest(sid=sid):
                self.assertFalse(policy.is_valid_session(view(sid=sid)))

    def test_guest_and_administrator_users_are_invalid(self):
        for user in ("", "   ", "Guest", "guest", "Administrator", None):
            with self.subTest(user=user):
                self.assertFalse(policy.is_valid_session(view(user=user)))

    def test_disabled_user_is_invalid(self):
        self.assertFalse(policy.is_valid_session(view(enabled=False)))

    def test_missing_session_is_invalid(self):
        self.assertFalse(policy.is_valid_session(None))


class AnonymousClassificationTests(unittest.TestCase):
    """Anonymous/expired is a sign-in state, never an authorization decision."""

    def test_missing_or_guest_identity_is_anonymous(self):
        for candidate in (
            None,
            view(sid=""),
            view(sid="   "),
            view(sid="Guest"),
            view(user=""),
            view(user="   "),
            view(user="Guest"),
            view(user="guest"),
        ):
            with self.subTest(view=candidate):
                self.assertTrue(policy.is_anonymous(candidate))

    def test_administrator_and_disabled_users_are_authenticated(self):
        for view_ in (
            view(user="Administrator"),
            view(user="Administrator", roles=("Administrator",)),
            view(enabled=False),
        ):
            with self.subTest(view=view_):
                self.assertFalse(policy.is_anonymous(view_))
                self.assertFalse(policy.is_valid_session(view_))

    def test_live_session_is_not_anonymous(self):
        self.assertFalse(policy.is_anonymous(view()))

    def test_anonymous_session_never_resolves_a_role(self):
        self.assertIsNone(
            policy.resolve_role(view(sid="Guest", roles=("Nurse",)), ROLE_MAP)
        )

    def test_anonymous_requires_authorization_even_for_a_mapped_role(self):
        anonymous = view(sid="Guest", roles=("Nurse",))
        self.assertIsNone(
            policy.authorize(anonymous, ROLE_MAP, policy.DIRECTORY_ROLE_CODES)
        )


class RoleResolutionTests(unittest.TestCase):
    def test_single_mapped_role_resolves(self):
        self.assertEqual(policy.resolve_role(view(), ROLE_MAP), "DOC")
        self.assertEqual(
            policy.resolve_role(view(roles=("Nurse",)), ROLE_MAP), "NUR"
        )

    def test_unmapped_user_resolves_to_nothing(self):
        self.assertIsNone(
            policy.resolve_role(view(roles=("Accounts User",)), ROLE_MAP)
        )
        self.assertIsNone(policy.resolve_role(view(roles=()), ROLE_MAP))

    def test_two_mapped_clinic_codes_are_ambiguous(self):
        self.assertIsNone(
            policy.resolve_role(view(roles=("Nurse", "Physician")), ROLE_MAP)
        )

    def test_invalid_session_never_resolves_a_role(self):
        self.assertIsNone(
            policy.resolve_role(view(sid="Guest", roles=("Physician",)), ROLE_MAP)
        )
        self.assertIsNone(
            policy.resolve_role(
                view(user="Administrator", roles=("Physician",)), ROLE_MAP
            )
        )

    def test_ambiguous_role_map_is_refused(self):
        ambiguous = {"DOC": ("Physician",), "NUR": ("Physician",)}
        self.assertIsNone(
            policy.resolve_role(view(roles=("Physician",)), ambiguous)
        )

    def test_patient_write_codes_are_doc_and_nur_only(self):
        self.assertEqual(
            policy.PATIENT_WRITE_ROLE_CODES, frozenset({"DOC", "NUR"})
        )
        self.assertTrue(
            policy.PATIENT_WRITE_ROLE_CODES.issubset(policy.SUPPORTED_ROLE_CODES)
        )

    def test_directory_codes_are_the_first_slice_codes(self):
        self.assertEqual(
            policy.DIRECTORY_ROLE_CODES, frozenset({"DOC", "REC", "NUR"})
        )
        for code in policy.DIRECTORY_ROLE_CODES:
            self.assertIn(code, policy.SUPPORTED_ROLE_CODES)


class RoleCodeSetTests(unittest.TestCase):
    """The full write-contract wave publishes seven clinic role codes.

    The names are consumed verbatim by the per-family gates; the exact literals
    are the contract, so they are asserted here rather than derived.
    """

    EXPECTED = {
        "APPOINTMENT_CANCEL_ROLE_CODES": {"DOC", "REC"},
        "APPOINTMENT_NOSHOW_ROLE_CODES": {"DOC", "REC"},
        "ENCOUNTER_ROLE_CODES": {"DOC", "NUR"},
        "ENCOUNTER_COMPLETE_ROLE_CODES": {"DOC"},
        "ENCOUNTER_AMEND_ROLE_CODES": {"DOC"},
        "PROCEDURE_ROLE_CODES": {"DOC"},
        "STOCK_ROLE_CODES": {"PHA"},
        "ADJUST_ROLE_CODES": {"ADM"},
        "PO_ROLE_CODES": {"PHA", "MGR"},
        "INVOICE_ROLE_CODES": {"CAS"},
        "INVOICE_VOID_ROLE_CODES": {"MGR"},
        "PAYMENT_ROLE_CODES": {"CAS"},
        "ADMIN_ROLE_CODES": {"ADM"},
    }

    def test_supported_codes_are_the_seven_clinic_roles(self):
        self.assertEqual(
            policy.SUPPORTED_ROLE_CODES,
            frozenset({"DOC", "REC", "NUR", "PHA", "CAS", "MGR", "ADM"}),
        )

    def test_each_family_gate_is_the_frozen_frozenset(self):
        for name, codes in self.EXPECTED.items():
            with self.subTest(name=name):
                gate = getattr(policy, name)
                self.assertIsInstance(gate, frozenset)
                self.assertEqual(gate, frozenset(codes))

    def test_every_family_gate_is_supported(self):
        for name in self.EXPECTED:
            with self.subTest(name=name):
                gate = getattr(policy, name)
                self.assertTrue(gate.issubset(policy.SUPPORTED_ROLE_CODES))


if __name__ == "__main__":
    unittest.main()
