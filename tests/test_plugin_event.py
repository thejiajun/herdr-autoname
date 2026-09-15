import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import plugin_event


class PluginEventTests(unittest.TestCase):
    def test_counts_turns_after_previous_fingerprint(self):
        self.assertEqual(plugin_event.new_turn_count(["a", "b", "c"], "a"), 2)

    def test_empty_previous_fingerprint_has_no_historical_delta(self):
        self.assertEqual(plugin_event.new_turn_count(["a"], ""), 0)

    def test_missing_previous_turn_counts_current_turn(self):
        self.assertEqual(plugin_event.new_turn_count(["b"], "a"), 1)

    def test_filters_injected_user_turns(self):
        messages = [
            ("user", "<task-notification>done"),
            ("user", "请修复登录问题"),
            ("assistant", "已完成"),
        ]
        self.assertEqual(len(plugin_event.user_turn_fingerprints(messages)), 1)


if __name__ == "__main__":
    unittest.main()
