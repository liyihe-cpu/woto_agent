import unittest
from agent_core import Plan, collector_command, local_plan


class AgentCoreTests(unittest.TestCase):
    def test_iso_request_becomes_plan(self):
        plan = local_plan("优先跑 br id，YouTube，粉丝100万到11亿，最近30天，跨度十亿")
        self.assertEqual(plan.platform, "youtube")
        self.assertEqual(plan.countries[0], "br")
        self.assertIn("id", plan.countries)
        self.assertEqual((plan.follower_min, plan.follower_max, plan.span, plan.recent), (1_000_000, 1_100_000_000, "1b", 30))

    def test_command_uses_all_plan_fields(self):
        command = collector_command(Plan("tiktok", ["br", "id"], 1_000_000, 1_100_000_000, "1b", 90).validate())
        self.assertIn("tiktok", command)
        self.assertIn("br", command)
        self.assertIn("1b", command)
        self.assertIn("90", command)
        self.assertIn("--preserve-country-order", command)

    def test_invalid_plan_is_rejected(self):
        with self.assertRaises(ValueError):
            Plan(countries=["br"], follower_min=100, follower_max=100).validate()

    def test_iso_country_order_is_preserved(self):
        plan = local_plan("gb fr de it es youtube粉丝2k-300k")
        self.assertEqual(plan.countries, ["gb", "fr", "de", "it", "es"])
        self.assertEqual((plan.follower_min, plan.follower_max), (2_000, 300_000))


if __name__ == "__main__":
    unittest.main(verbosity=2)
