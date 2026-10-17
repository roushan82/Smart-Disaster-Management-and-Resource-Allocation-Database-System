import unittest

from core.validation import FormValidationError
from modules.disasters.validation import DISASTER_TYPES, validate_disaster
from modules.emergency_requests.validation import validate_request
from modules.personnel.validation import PERSONNEL_ROLES, validate_personnel
from modules.resources.validation import validate_resource
from modules.teams.validation import TEAM_TYPES, validate_team


class ValidationTests(unittest.TestCase):
    def test_disaster_categories_allowlisted(self):
        valid = {
            "name": "River flood",
            "type": DISASTER_TYPES[0],
            "severity": "HIGH",
            "description": "",
            "location_id": "1",
        }
        self.assertEqual(validate_disaster(valid)["type"], "Flood")
        valid["type"] = "Alien invasion"
        with self.assertRaises(FormValidationError):
            validate_disaster(valid)

    def test_team_category_and_required_fields(self):
        valid = {
            "team_name": "North team",
            "team_type": TEAM_TYPES[0],
            "location_id": "2",
        }
        self.assertEqual(validate_team(valid)["location_id"], 2)
        valid["team_type"] = "Medical"
        with self.assertRaises(FormValidationError):
            validate_team(valid)
        with self.assertRaises(FormValidationError):
            validate_team({"team_name": "", "team_type": "", "location_id": ""})

    def test_personnel_role_and_phone_types_are_separate(self):
        valid = {
            "name": "Asha",
            "role": "Doctor",
            "contact": "9876543210",
            "team_id": "1",
        }
        self.assertEqual(validate_personnel(valid)["contact"], "9876543210")
        for role, contact in (("9876543210", "9876543210"), ("Doctor", "Doctor")):
            invalid = dict(valid, role=role, contact=contact)
            with self.assertRaises(FormValidationError):
                validate_personnel(invalid)

    def test_phone_requires_exact_indian_mobile_format(self):
        valid = {
            "name": "Asha",
            "role": PERSONNEL_ROLES[0],
            "contact": "9876543210",
            "team_id": "1",
        }
        for phone in (
            "1234567890",
            "987654321",
            "98765432100",
            "+919876543210",
            "987 654 3210",
            " 9876543210 ",
            "abcdefghij",
        ):
            with self.subTest(phone=phone), self.assertRaises(FormValidationError):
                validate_personnel(dict(valid, contact=phone))

    def test_quantities_and_request_priorities(self):
        self.assertEqual(
            validate_resource(
                {"type_id": "1", "location_id": "1", "total_quantity": "0"}
            )["total_quantity"],
            0,
        )
        with self.assertRaises(FormValidationError):
            validate_resource(
                {"type_id": "1", "location_id": "1", "total_quantity": "-1"}
            )
        with self.assertRaises(FormValidationError):
            validate_resource(
                {"type_id": "1", "location_id": "1", "total_quantity": "9" * 5000}
            )
        request = {
            "disaster_id": "1",
            "resource_type_id": "1",
            "requested_quantity": "3",
            "priority": "URGENT",
        }
        with self.assertRaises(FormValidationError):
            validate_request(request)


if __name__ == "__main__":
    unittest.main()