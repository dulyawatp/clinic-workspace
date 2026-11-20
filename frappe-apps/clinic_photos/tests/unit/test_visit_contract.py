import unittest

from clinic_photos.access import visit_contract as visit


class VisitContractTests(unittest.TestCase):
    def test_consent_requires_evidence_and_cannot_be_rewritten_or_restored(self):
        with self.assertRaises(ValueError):
            visit.grant("A", "P", "  ", "user", "time")
        granted = visit.grant("A", "P", "signed form", "user", "time")
        revoked = visit.revoke(granted, "other", "later")
        self.assertEqual(revoked.evidence, "signed form")
        self.assertEqual(revoked.granted_by, "user")
        with self.assertRaises(ValueError):
            visit.revoke(revoked, "other", "again")
        with self.assertRaises(ValueError):
            visit.require_capture(revoked, "REC", "Before", "A", "P")

    def test_before_requires_matching_identity_and_approved_role(self):
        consent = visit.grant("A", "P", "form", "u", "t")
        visit.require_capture(consent, "REC", "Before", "A", "P")
        visit.require_capture(consent, "DOC", "Before", "A", "P")
        for role, appointment, patient in (("NUR", "A", "P"), ("REC", "B", "P"), ("DOC", "A", "Q")):
            with self.assertRaises(ValueError):
                visit.require_capture(consent, role, "Before", appointment, patient)

    def test_after_requires_doctor_observation_and_unique_matching_draft(self):
        consent = visit.grant("A", "P", "form", "u", "t")
        observation = visit.observe_check_in("A", "P", "Checked In")
        encounter = visit.Encounter("E", "A", "P", 0)
        visit.require_capture(consent, "DOC", "After", "A", "P", observation, [encounter])
        for role, proof, encounters in (
            ("REC", observation, [encounter]),
            ("DOC", None, [encounter]),
            ("DOC", observation, []),
            ("DOC", observation, [encounter, encounter]),
            ("DOC", observation, [visit.Encounter("E", "A", "Q", 0)]),
            ("DOC", observation, [visit.Encounter("E", "A", "P", 1)]),
        ):
            with self.assertRaises(ValueError):
                visit.require_capture(consent, role, "After", "A", "P", proof, encounters)

    def test_closed_is_not_check_in(self):
        with self.assertRaises(ValueError):
            visit.observe_check_in("A", "P", "Closed")

    def test_slot_identity_is_unambiguous(self):
        self.assertNotEqual(visit.slot_identity("a:b", "Before"), visit.slot_identity("a", "Before"))
        self.assertNotEqual(visit.slot_identity("a", "Before"), visit.slot_identity("a", "After"))
        with self.assertRaises(ValueError):
            visit.slot_identity("a", "Other")
