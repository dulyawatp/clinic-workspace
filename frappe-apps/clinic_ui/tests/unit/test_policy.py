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

    def test_directory_codes_are_the_first_slice_codes(self):
        self.assertEqual(
            policy.DIRECTORY_ROLE_CODES, frozenset({"DOC", "REC", "NUR"})
        )
        for code in policy.DIRECTORY_ROLE_CODES:
            self.assertIn(code, policy.SUPPORTED_ROLE_CODES)


if __name__ == "__main__":
    unittest.main()
