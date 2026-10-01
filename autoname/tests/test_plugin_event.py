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

    def test_pane_without_agent_label_needs_sidebar_metadata(self):
        self.assertTrue(plugin_event.needs_agent_label({"pane_id": "w1:p1"}))
        self.assertTrue(plugin_event.needs_agent_label({"tokens": {"task_idle": " 修复排版"}}))
        self.assertFalse(plugin_event.needs_agent_label({"tokens": {
            "task_idle": " 修复排版", "title_idle": "🏷️ Herdr自动命名",
        }}))

    def test_untracked_pane_uses_visible_task_when_label_is_blank(self):
        pane = {"pane_id": "w1:p1", "label": None,
                "terminal_title_stripped": "修复登录错误 | app", "cwd": "/tmp/app"}
        with mock.patch.object(plugin_event, "invoke_autoname", return_value=0) as invoke:
            self.assertEqual(plugin_event.name_untracked_pane(pane, "w1"), 0)
        invoke.assert_called_once_with("--workspace", "w1")

    def test_untracked_bare_prompt_gets_idle_label(self):
        pane = {"pane_id": "w1:p1", "label": None,
                "terminal_title_stripped": "app", "cwd": "/tmp/app"}
        with mock.patch.object(plugin_event.autoname, "run", return_value=(0, "", "")) as run, \
             mock.patch.object(plugin_event, "invoke_autoname", return_value=0) as invoke:
            self.assertEqual(plugin_event.name_untracked_pane(pane, "w1"), 0)
        run.assert_called_once_with(["herdr", "pane", "rename", "w1:p1", plugin_event.WAITING_LABEL])
        invoke.assert_called_once_with("configure")

    def test_labeled_untracked_pane_is_not_renamed_again(self):
        pane = {"pane_id": "w1:p1", "label": "已有任务"}
        with mock.patch.object(plugin_event, "invoke_autoname") as invoke:
            self.assertEqual(plugin_event.name_untracked_pane(pane, "w1"), 0)
        invoke.assert_not_called()

    def test_waiting_pane_gets_task_label_when_title_changes(self):
        pane = {"pane_id": "w1:p1", "label": plugin_event.WAITING_LABEL,
                "terminal_title_stripped": "修复登录错误 | app", "cwd": "/tmp/app"}
        with mock.patch.object(plugin_event, "invoke_autoname", return_value=0) as invoke:
            self.assertEqual(plugin_event.name_untracked_pane(pane, "w1"), 0)
        invoke.assert_called_once_with("--workspace", "w1")


class SidebarLayoutTests(unittest.TestCase):
    def test_default_workspace_label_does_not_repeat_project_heading(self):
        autoname = plugin_event.autoname
        row = {"project": "pika_work", "panes": [{"agent": "codex"}]}
        self.assertEqual(autoname.sidebar_workspace_name(row, "pika_work"), "新会话")
        row["panes"] = [{"agent": "shell"}]
        self.assertEqual(autoname.sidebar_workspace_name(row, "pika_work"), "终端")
        self.assertEqual(autoname.sidebar_workspace_name(row, "🔍 调研项目"), "🔍 调研项目")

    def test_tree_branches_connect_workspaces_under_each_project(self):
        rows = [
            {"workspace_id": "a1", "group_key": "path:/a", "remote_host": ""},
            {"workspace_id": "b1", "group_key": "path:/b", "remote_host": ""},
            {"workspace_id": "a2", "group_key": "path:/a", "remote_host": ""},
            {"workspace_id": "r1", "group_key": "remote:server", "remote_host": "server"},
        ]
        self.assertEqual(plugin_event.autoname.sidebar_tree_branches(rows), {
            "a1": "├", "a2": "└", "b1": "└", "r1": "└",
        })

    def test_project_heading_is_shown_once_above_its_workspaces(self):
        autoname = plugin_event.autoname
        rows = [
            {"workspace_id": "a1", "group_key": "path:/a", "project": "project-a",
             "remote_host": "", "git_status": "main"},
            {"workspace_id": "a2", "group_key": "path:/a", "project": "project-a",
             "remote_host": "", "git_status": "main"},
        ]
        self.assertEqual(autoname.grouped_sidebar_contexts(rows), {
            "a1": " project-a · main", "a2": "",
        })

    def test_tree_branches_keep_one_column_with_one_project_heading(self):
        autoname = plugin_event.autoname
        first = autoname.sidebar_tree_label("├", "🔍 首项", has_context=True)
        later = autoname.sidebar_tree_label("└", "💬 后项", has_context=False)
        self.assertEqual(first, "├ 🔍 首项")
        self.assertEqual(later, "⠀⠀└ 💬 后项")

    def test_agent_title_matches_space_and_task_is_below(self):
        autoname = plugin_event.autoname
        row = {
            "workspace_id": "w1", "group_key": "path:/projects", "project": "projects",
            "remote_host": "", "git_status": "—", "current_workspace": "🏷️ Herdr自动命名",
            "panes": [{"pane_id": "p1", "agent": "codex", "status": "working",
                       "current_label": "🏷️ 修复树线对齐位置"}],
        }
        names = [{"workspace_id": "w1", "workspace": "🏷️ Herdr自动命名",
                  "panes": {"p1": "🏷️ 修复树线对齐位置"}}]
        preview = autoname.sidebar_preview_data(names, [row])
        self.assertEqual(preview["spaces"][0]["label"], preview["agents"][0]["label"])
        self.assertIn("修复树线对齐位置", preview["agents"][0]["context"])
        self.assertEqual(preview["agents"][0]["context"], " 修复树线对齐位置")

    def test_agent_state_switch_preserves_title_and_task(self):
        autoname = plugin_event.autoname
        tokens = {"title_idle": "🏷️ Herdr自动命名", "task_idle": " 修复排版"}
        with mock.patch.object(autoname, "run", return_value=(0, "", "")) as run:
            self.assertTrue(autoname.sync_agent_state("p1", "working", tokens))
        command = run.call_args.args[0]
        self.assertIn("title_working=🏷️ Herdr自动命名", command)
        self.assertIn("task_working= 修复排版", command)
        self.assertIn("title_idle", command)

    def test_emoji_names_remain_unchanged_without_sidebar_brackets(self):
        autoname = plugin_event.autoname
        row = {
            "workspace_id": "w1", "current_workspace": "🔍 旧主题",
            "tabs": [{"tab_id": "w1:t1"}],
            "panes": [{"pane_id": "w1:p1"}],
        }
        names = autoname.normalize_item(
            ["🔍 新主题", ["🔍 短标签"], ["🔍 排查错误"]], row
        )
        self.assertEqual(names["workspace"], "🔍 新主题")
        self.assertEqual(names["tabs"]["w1:t1"], "🔍 短标签")
        self.assertEqual(names["panes"]["w1:p1"], "🔍 排查错误")

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
