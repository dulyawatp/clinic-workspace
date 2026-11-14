"""Actual read-only HTTP acceptance cases for protected photo delivery (Task 4).

Standard library only: the client does not import Frappe. The suite is mandatory-
configuration: ``CLINIC_PHOTO_TEST_BASE_URL`` and ``CLINIC_PHOTO_TEST_MANIFEST`` must
name the explicitly approved disposable target and an access-restricted untracked
manifest. Missing prerequisites are a configuration error, never a green skip. Every
case is an explicit read-only GET; there is no flag or path by which a caller can make
this module send a mutation, a raw-bank/finance POST or an arbitrary request.

No request is sent during import or configuration validation. Session values are read
from environment variables named by the manifest and are never printed. Actual
execution belongs to the separately authorized disposable-site run; the local unit
suite (``tests/unit/test_http_harness.py``) drives this logic with a fake transport.
"""

import os
import unittest

from clinic_photos.tests import http_harness

_CONFIG = None


def setUpModule():
    """Fail closed: configuration problems are errors, not skips."""
    global _CONFIG
    try:
        _CONFIG = http_harness.load_config(os.environ)
    except http_harness.ConfigError as error:
        raise RuntimeError(
            "HTTP acceptance prerequisites are not satisfied: " + str(error)
        ) from None


def _run_case(test, case_id):
    evidence = http_harness.execute_case(_CONFIG, case_id)
    test.assertEqual(evidence["case"], case_id)
    test.assertEqual(evidence["cache_control"], "no-store")
    test.assertEqual(evidence["pragma"], "no-cache")
    test.assertEqual(evidence["expires"], "0")
    test.assertNotIn("Cookie", evidence)
    return evidence


class HttpReadMatrixTests(unittest.TestCase):
    def test_manifest_declares_exactly_the_required_read_only_cases(self):
        declared = {case["id"] for case in _CONFIG.manifest["cases"]}
        self.assertEqual(declared, set(http_harness.REQUIRED_CASE_IDS))


def _case_test(case_id):
    def test(self):
        _run_case(self, case_id)

    test.__name__ = "test_" + case_id.replace(".", "_")
    test.__doc__ = "Read-only HTTP acceptance case: " + case_id
    return test


for _case_id in http_harness.REQUIRED_CASE_IDS:
    setattr(HttpReadMatrixTests, "test_" + _case_id.replace(".", "_"), _case_test(_case_id))
del _case_id


if __name__ == "__main__":
    unittest.main()
