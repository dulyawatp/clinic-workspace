import unittest
from unittest.mock import patch

from clinic_ui.endpoints import linked_encounter
from clinic_ui.endpoints import production_read
from .fake_gateway import Denied, FakeGateway


class LinkedProductionBoundaryTests(unittest.TestCase):
    def test_production_never_enters_legacy_linked_writes(self):
        gateway = FakeGateway()
        for operation, args in (
            (linked_encounter.save_linked, ({},)),
            (linked_encounter.complete_linked, ({},)),
        ):
            with self.subTest(operation=operation.__name__), \
                    patch.object(production_read, "authorize_if_selected", return_value=object()):
                with self.assertRaises(Denied):
                    operation(gateway, *args)
