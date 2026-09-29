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

    def test_new_session_is_named_after_first_turn(self):
        self.assertTrue(plugin_event.should_name({"pending_turns": 1, "named": False}, 3))

    def test_named_session_waits_for_interval(self):
        entry = {"pending_turns": 2, "named": True}
        self.assertFalse(plugin_event.should_name(entry, 3))
        entry["pending_turns"] = 3
        self.assertTrue(plugin_event.should_name(entry, 3))

    def test_no_new_turn_does_not_name(self):
        self.assertFalse(plugin_event.should_name({"pending_turns": 0, "named": False}, 3))

    def test_filters_injected_user_turns(self):
        messages = [
            ("user", "<task-notification>done"),
            ("user", "请修复登录问题"),
            ("assistant", "已完成"),
        ]
        self.assertEqual(len(plugin_event.user_turn_fingerprints(messages)), 1)

    def test_workspace_without_label_token_needs_sidebar_label(self):
        self.assertTrue(plugin_event.needs_sidebar_label({"workspace_id": "w1"}))
        self.assertTrue(plugin_event.needs_sidebar_label({"tokens": {"context": "repo"}}))
        self.assertFalse(plugin_event.needs_sidebar_label({"tokens": {"workspace_label": "[a]"}}))
        self.assertFalse(plugin_event.needs_sidebar_label(None))


class SidebarLayoutTests(unittest.TestCase):
    def test_partial_run_lays_out_every_workspace(self):
        everything = [{"workspace_id": "w1"}, {"workspace_id": "w2"}]
        with mock.patch.object(plugin_event.autoname, "collect", return_value=(everything, set())) as collect:
            self.assertEqual(plugin_event.autoname.sidebar_rows([everything[1]], True), everything)
        collect.assert_called_once_with(with_content=False)

    def test_full_run_reuses_its_rows(self):
        rows = [{"workspace_id": "w1"}]
        self.assertIs(plugin_event.autoname.sidebar_rows(rows, False), rows)

    def test_groups_same_project_together(self):
        rows = [
            {"workspace_id": "a1", "group_key": "path:/a"},
            {"workspace_id": "b1", "group_key": "path:/b"},
            {"workspace_id": "a2", "group_key": "path:/a"},
        ]
        self.assertEqual(plugin_event.autoname.grouped_workspace_ids(rows), ["a1", "a2", "b1"])


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



class SharedSettingsTests(unittest.TestCase):
    def test_plugin_values_move_into_shared_file(self):
        with tempfile.TemporaryDirectory() as directory:
            plugin_dir = os.path.join(directory, "plugin")
            os.makedirs(plugin_dir)
            source = os.path.join(plugin_dir, "autoname.env")
            target = os.path.join(directory, "autoname.env")
            with open(source, "w", encoding="utf-8") as handle:
                handle.write("HERDR_AUTONAME_PROVIDER=pi\nHERDR_AUTONAME_TRIGGER_EVERY=5\n")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("HERDR_AUTONAME_PROVIDER=codex\nOPENROUTER_API_KEY=keep\n")

            self.assertTrue(plugin_event.autoname.migrate_plugin_settings(plugin_dir, target))

            values = plugin_event.autoname.read_env_file(target)
            self.assertEqual(values["HERDR_AUTONAME_PROVIDER"], "pi")
            self.assertEqual(values["HERDR_AUTONAME_TRIGGER_EVERY"], "5")
            self.assertEqual(values["OPENROUTER_API_KEY"], "keep")
            self.assertFalse(os.path.exists(source))
            self.assertTrue(os.path.exists(source + ".migrated"))
            self.assertFalse(plugin_event.autoname.migrate_plugin_settings(plugin_dir, target))

    def test_without_plugin_settings_nothing_moves(self):
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, "autoname.env")
            self.assertFalse(plugin_event.autoname.migrate_plugin_settings("", target))
            self.assertFalse(plugin_event.autoname.migrate_plugin_settings(directory, target))
            self.assertFalse(os.path.exists(target))

    def test_cli_and_plugin_resolve_same_paths(self):
        autoname = plugin_event.autoname
        self.assertEqual(autoname.AUTONAME_ENV, os.path.expanduser("~/.config/herdr/autoname.env"))
        self.assertTrue(autoname.REQUEST_LOG_DB.startswith(autoname.STATE_DIR))


if __name__ == "__main__":
    unittest.main()
