import unittest
from free_cycle import plan


class PlannerTests(unittest.TestCase):
    def test_default_pause(self):
        self.assertEqual(plan({"tasks": []})["status"], "paused")

    def test_budget_gate(self):
        self.assertEqual(plan({"tasks": [], "pause": False})["status"], "blocked")

    def test_agent_lock(self):
        self.assertEqual(plan({"tasks": [], "pause": False, "budget_verified": True, "active_agent": True})["status"], "blocked")

    def test_dependency_and_approval(self):
        data = {"pause": False, "budget_verified": True, "tasks": [
            {"id": "a", "state": "done"},
            {"id": "b", "state": "ready", "dependencies": ["a"], "human_approval_required": False, "priority": 1},
            {"id": "c", "state": "ready", "human_approval_required": True, "priority": 0},
        ]}
        self.assertEqual(plan(data)["next_task"], "b")

    def test_unknown_dependencies_fail_closed(self):
        with self.assertRaises(ValueError):
            plan({"tasks": [{"id": "b", "state": "ready", "dependencies": ["missing"]}]})

    def test_duplicate_id_fail_closed(self):
        with self.assertRaises(ValueError):
            plan({"tasks": [{"id": "a", "state": "ready"}, {"id": "a", "state": "done"}]})

    def test_no_approved_tasks(self):
        data = {"pause": False, "budget_verified": True, "tasks": [{"id": "a", "state": "ready"}]}
        self.assertEqual(plan(data)["status"], "idle")


if __name__ == "__main__":
    unittest.main()
