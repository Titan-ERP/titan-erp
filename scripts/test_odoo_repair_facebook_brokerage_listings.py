import unittest

from odoo_repair_facebook_brokerage_listings import equipment_type_key


class TestFacebookBrokerageEquipmentType(unittest.TestCase):
    def test_tracked_excavator_maps_to_excavator(self):
        self.assertEqual(
            equipment_type_key({"Equipment Type": "Tracked Excavator"}),
            "excavator",
        )

    def test_plain_excavator_still_maps_to_excavator(self):
        self.assertEqual(
            equipment_type_key({"Equipment Type": "Excavator"}),
            "excavator",
        )


if __name__ == "__main__":
    unittest.main()
