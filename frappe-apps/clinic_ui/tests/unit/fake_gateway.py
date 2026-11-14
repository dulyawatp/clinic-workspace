"""In-memory gateway stub for endpoint tests.

The stub records calls and never performs I/O. It is a local fixture, not a
Frappe simulation: no session store, permission engine, HTTP layer or site
database is claimed. Endpoint tests only prove gate ordering, DTO shape and
fail-closed decisions.
"""

from clinic_ui.core import dto
from clinic_ui.core.policy import SessionView


class Denied(Exception):
    """Stand-in for the framework permission error raised by the gateway."""


class FakeGateway:
    def __init__(
        self,
        *,
        site="marley.test",
        settings=None,
        current_session=None,
        csrf_token="csrf-token-value",
    ):
        self.site = site
        self._settings = dict(settings or {})
        self.current_session = current_session
        self._csrf_token = csrf_token
        self.no_store_calls = 0
        self.denials = []
        self.anonymous_calls = 0
        self.csrf_calls = 0
        self.patient_reads = []
        self.patient_authority = {}
        self.patient_read_error = None

    def setting(self, key):
        return self._settings.get(key)

    def csrf_token(self):
        self.csrf_calls += 1
        return self._csrf_token

    def mark_no_store(self):
        self.no_store_calls += 1

    def respond_anonymous(self):
        self.anonymous_calls += 1
        return dto.build_anonymous()

    def deny(self, code="denied"):
        self.denials.append(code)
        raise Denied(code)

    def native_patient_display_name(self, native_patient_id):
        self.patient_reads.append(native_patient_id)
        if self.patient_read_error is not None:
            raise self.patient_read_error
        return self.patient_authority.get(native_patient_id)


def session_view(
    *,
    sid="sid-abc123",
    user="nurse@example.test",
    full_name="Nurse Example",
    enabled=True,
    roles=("Nurse",),
):
    return SessionView(
        sid=sid,
        user=user,
        full_name=full_name,
        enabled=enabled,
        roles=tuple(roles),
    )


SECRET = "0123456789abcdef0123456789abcdef"


def settings(**overrides):
    """Valid default site settings for the fictional synthetic lab site."""

    values = {
        "clinic_ui_enabled": 1,
        "clinic_ui_synthetic_site": "marley.test",
        "clinic_ui_epoch_secret": SECRET,
        "clinic_ui_role_map": {
            "DOC": ["Physician"],
            "REC": ["Clinic Reception"],
            "NUR": ["Nurse"],
        },
        "clinic_ui_synthetic_patients": [
            {
                "workspace_patient_id": "WS-1",
                "native_patient_id": "PAT-1",
            }
        ],
    }
    values.update(overrides)
    return values
