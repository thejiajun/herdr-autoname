import os
import sys
import tempfile
import unittest
from unittest import mock


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import plugin_event
import settings_ui


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


class SettingsTests(unittest.TestCase):
    def test_fit_truncates_long_values(self):
        self.assertEqual(settings_ui.fit("abcdef", 4), "a...")

    def test_fit_respects_wide_terminal_characters(self):
        self.assertEqual(settings_ui.fit("设置面板", 6), "设...")

    def test_menu_exposes_expected_controls(self):
        self.assertEqual(
            settings_ui.MENU,
            ("provider", "model", "interval", "enabled", "preview", "rename", "save", "cancel"),
        )

    def test_save_settings_persists_plugin_values(self):
        values = {"provider": "pi", "model": "", "interval": 5, "enabled": True}
        with tempfile.TemporaryDirectory() as directory:
            config = os.path.join(directory, "autoname.env")
            with mock.patch.object(settings_ui.autoname, "AUTONAME_ENV", config):
                settings_ui.save_settings(values)
            with open(config, encoding="utf-8") as handle:
                saved = handle.read()
        self.assertIn("HERDR_AUTONAME_PROVIDER=pi", saved)
        self.assertIn("HERDR_AUTONAME_TRIGGER_EVERY=5", saved)
        self.assertIn("HERDR_AUTONAME_ENABLED=true", saved)


if __name__ == "__main__":
    unittest.main()
