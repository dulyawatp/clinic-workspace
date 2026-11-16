"""App patches for the clinic_ui module.

Each module is referenced once from ``clinic_ui/patches.txt`` and runs during
``bench migrate``. No patch creates a DocType; the patient write groundwork adds
only the two raw MariaDB tables described in
``docs/assessments/2026-11-14-local-native-patient-contracts.md`` §8.2.1.
"""
