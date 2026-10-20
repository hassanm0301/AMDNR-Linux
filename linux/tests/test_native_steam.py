"""Local fixtures only: never read or write the user's Steam configuration."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_launcher import steam


def executable(path, x64=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    header = bytearray(128)
    header[:2] = b'MZ'
    header[60:64] = (64).to_bytes(4, 'little')
    header[64:70] = b'PE\0\0' + (b'\x64\x86' if x64 else b'\x4c\x01')
    path.write_bytes(header)


def config(options=None, newline='\n'):
    option = '' if options is None else '\t\t\t\t\t"LaunchOptions"\t\t' + steam._quote(options) + '\n'
    text = '''// Keep this comment, spacing, and all unrelated private values.
"UserLocalConfigStore"
{
  "Other" { "apps" { "123" { "LaunchOptions" "unrelated nested app" } } }
  "Software"
  {
    "vAlVe"
    {
      "Steam"
      {
        "aPpS"
        {
          "123"
          {
            "LastPlayed" "before" // preserve comment
OPTION          }
          "456" { "LaunchOptions" "other game" }
        }
      }
    }
  }
}
'''.replace('OPTION', option)
    return text.replace('\n', newline).encode()


class SteamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='amdnr steam test ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.steam = self.root / 'Steam'
        self.steam.mkdir()
        self.library = self.root / 'another drive/SteamLibrary'
        self.install = self.library / 'steamapps/common/Test Game'
        self.install.mkdir(parents=True)
        self.wrapper = self.install / 'SB/Binaries/Win64/amdnr-linux/run.sh'
        self.wrapper.parent.mkdir(parents=True)
        self.wrapper.write_text('#!/bin/sh\nexec "$@"\n')
        self.wrapper.chmod(0o755)
        self.game = steam.SteamGame('123', 'Test Game', self.install, self.steam, self.library, self.wrapper.parent.parent)
        self.state = self.root / 'private launcher state'
        gate = patch.object(steam, 'steam_is_running', return_value=False)
        gate.start()
        self.addCleanup(gate.stop)

    def account(self, options=None, account='1001', newline='\n'):
        path = self.steam / 'userdata' / account / 'config/localconfig.vdf'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(config(options, newline))
        return path

    def option(self, path):
        _, node = steam._options(steam._decode(path.read_bytes()), '123')
        return node.value.value if node else None

    def manifest(self, library=None, appid='123', folder='Test Game', name='Test Game'):
        library = library or self.library
        path = library / f'steamapps/appmanifest_{appid}.acf'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('"AppState" { "appid" ' + steam._quote(appid) + ' "name" '
                        + steam._quote(name) + ' "installdir" ' + steam._quote(folder) + ' }')
        return path

    def library_index(self, entries, root=None, location='steamapps/libraryfolders.vdf'):
        path = (root or self.steam) / location
        path.parent.mkdir(parents=True, exist_ok=True)
        values = []
        for index, (library, apps) in enumerate(entries):
            body = '"path" ' + steam._quote(str(library))
            if apps is not None:
                body += ' "apps" { ' + ' '.join(steam._quote(appid) + ' "100"' for appid in apps) + ' }'
            values.append(steam._quote(str(index)) + ' { ' + body + ' }')
        path.write_text('"libraryfolders" { ' + ' '.join(values) + ' }')
        return path

    def test_vdf_multiline_comments_case_escapes_and_quoted_braces(self):
        text = '\ufeff/* header */ "ROOT" { // comment\n "Brace" "}" "path" "a\\\\b\\\"c\\nnext"\n bare "{" }'
        root = steam._child(steam.parse_vdf(text), 'root')
        self.assertEqual(steam._value(root.children, 'brace'), '}')
        self.assertEqual(steam._value(root.children, 'path'), 'a\\b"c\nnext')
        self.assertEqual(steam._value(root.children, 'bare'), '{')
        token = root.children[1].value
        self.assertEqual(text[token.start:token.end], '"a\\\\b\\\"c\\nnext"')
        for bad in ('"unterminated', '{', '"a" {', '"a" }', '/* bad', '"a" "b" }'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                steam.parse_vdf(bad)

    def test_discover_external_libraries_and_ignore_malformed_manifest_paths(self):
        folders = self.steam / 'steamapps/libraryfolders.vdf'
        folders.parent.mkdir()
        folders.write_text('"libraryfolders" { "0" { "path" ' + steam._quote(str(self.steam))
                           + ' } "1" { "path" ' + steam._quote(str(self.library)) + ' } }')
        self.manifest()
        self.manifest(appid='999', folder='../escape')
        self.manifest(appid='998', folder='bad\\path')
        executable(self.install / 'SB/Binaries/Win64/Game.exe')
        alias = self.root / 'steam alias'
        alias.symlink_to(self.steam, target_is_directory=True)
        games = steam.discover_games([self.steam, alias])
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0].appid, '123')
        self.assertEqual(games[0].library, self.library)
        self.assertEqual(games[0].exe_dir, self.install / 'SB/Binaries/Win64')
        self.assertEqual(games[0].steam_root, self.steam)

    def test_discovery_legacy_library_format_and_flatpak_default(self):
        fake_home = self.root / 'home'
        root = fake_home / '.var/app/com.valvesoftware.Steam/.local/share/Steam'
        (root / 'config').mkdir(parents=True)
        (root / 'config/libraryfolders.vdf').write_text('"LibraryFolders" { "1" ' + steam._quote(str(self.library)) + ' }')
        self.manifest()
        with patch.object(steam.Path, 'home', return_value=fake_home):
            games = steam.discover_games()
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0].steam_root, root)

    def test_discovery_excludes_steam_compatibility_tools(self):
        self.manifest(self.steam, appid='3658110', folder='Proton', name='Proton')
        tool = self.steam / 'steamapps/common/Proton'
        executable(tool / 'files/winecfg.exe')
        (tool / 'toolmanifest.vdf').write_text('Steam tool')
        self.manifest(self.steam, appid='228980', folder='Steamworks Shared', name='Redistributables')
        (self.steam / 'steamapps/common/Steamworks Shared').mkdir()
        self.assertEqual(steam.discover_games([self.steam]), [])

    def test_duplicate_manifest_uses_indexed_owner_even_when_stale_sorts_first(self):
        stale = self.root / 'aaa stale library'
        self.manifest(stale, name='Stale Test Game')
        executable(stale / 'steamapps/common/Test Game/Old.exe')
        self.manifest()
        executable(self.install / 'Binaries/Win64/Game.exe')
        self.library_index([(stale, []), (self.library, ['123'])])
        games = steam.discover_games([self.steam])
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0].library, self.library)
        self.assertEqual(games[0].install_dir, self.install)
        self.assertEqual(games[0].name, 'Test Game')
        self.assertEqual(games[0].exe_dir, self.install / 'Binaries/Win64')
        self.assertFalse(games[0].library_ambiguous)

    def test_current_library_index_wins_over_stale_config_copy(self):
        stale = self.root / 'old library'
        self.manifest(stale)
        executable(stale / 'steamapps/common/Test Game/Old.exe')
        self.manifest()
        executable(self.install / 'Game.exe')
        self.library_index([(self.library, ['123'])])
        self.library_index([(stale, ['123'])], location='config/libraryfolders.vdf')
        games = steam.discover_games([self.steam])
        self.assertEqual([game.library for game in games], [self.library])
        self.assertFalse(games[0].library_ambiguous)

    def test_config_library_index_is_used_when_current_file_is_absent(self):
        stale = self.root / 'aaa stale library'
        self.manifest(stale)
        executable(stale / 'steamapps/common/Test Game/Old.exe')
        self.manifest()
        executable(self.install / 'Game.exe')
        self.library_index([(stale, []), (self.library, ['123'])], location='config/libraryfolders.vdf')
        games = steam.discover_games([self.steam])
        self.assertEqual([game.library for game in games], [self.library])

    def test_unindexed_duplicate_manifests_remain_ambiguous_and_deterministic(self):
        other = self.root / 'another library'
        self.manifest(other)
        executable(other / 'steamapps/common/Test Game/Other.exe')
        self.manifest()
        executable(self.install / 'Game.exe')
        for apps in (None, []):
            with self.subTest(apps=apps):
                self.library_index([(other, apps), (self.library, apps)])
                first = steam.discover_games([self.steam])[0]
                self.library_index([(self.library, apps), (other, apps)])
                second = steam.discover_games([self.steam])[0]
                self.assertEqual(first, second)
                self.assertTrue(first.library_ambiguous)
                self.assertIsNone(first.exe_dir)

    def test_conflicting_explicit_owners_are_not_arbitrarily_selected(self):
        other = self.root / 'other library'
        self.manifest(other)
        executable(other / 'steamapps/common/Test Game/Other.exe')
        self.manifest()
        executable(self.install / 'Game.exe')
        self.library_index([(other, ['123']), (self.library, ['123'])])
        game = steam.discover_games([self.steam])[0]
        self.assertTrue(game.library_ambiguous)
        self.assertIsNone(game.exe_dir)

    def test_missing_indexed_owner_does_not_activate_a_stale_manifest(self):
        missing = self.root / 'missing active library'
        self.manifest()
        executable(self.install / 'Game.exe')
        self.library_index([(self.library, []), (missing, ['123'])])
        self.assertEqual(steam.discover_games([self.steam]), [])

    def test_library_ownership_is_independent_across_steam_roots(self):
        second_root = self.root / 'Flatpak Steam'
        second_library = self.root / 'Flatpak library'
        self.manifest()
        executable(self.install / 'Game.exe')
        self.manifest(second_library)
        other_install = second_library / 'steamapps/common/Test Game'
        executable(other_install / 'Game.exe')
        self.library_index([(self.library, ['123']), (second_library, [])])
        self.library_index([(self.library, []), (second_library, ['123'])], root=second_root)
        games = steam.discover_games([self.steam, second_root])
        self.assertEqual(len(games), 2)
        self.assertEqual({game.steam_root: game.library for game in games},
                         {self.steam: self.library, second_root: second_library})
        self.assertTrue(all(not game.library_ambiguous for game in games))
        self.assertEqual(games, steam.discover_games([second_root, self.steam]))

    def test_empty_current_index_does_not_inherit_obsolete_ownership(self):
        other = self.root / 'other library'
        self.manifest(other)
        executable(other / 'steamapps/common/Test Game/Other.exe')
        self.manifest()
        executable(self.install / 'Game.exe')
        self.library_index([(other, []), (self.library, [])])
        self.library_index([(other, ['123'])], location='config/libraryfolders.vdf')
        game = steam.discover_games([self.steam])[0]
        self.assertTrue(game.library_ambiguous)
        self.assertIsNone(game.exe_dir)

    def test_directory_resolution_prefers_dx12_then_win64_and_refuses_ambiguity(self):
        executable(self.install / 'bin/x64/game.exe')
        executable(self.install / 'bin/x64_dx12/game.exe')
        self.assertEqual(steam.resolve_exe_directory(self.install), self.install / 'bin/x64_dx12')
        (self.install / 'bin/x64_dx12/game.exe').unlink()
        executable(self.install / 'SB/Binaries/Win64/Game.exe')
        executable(self.install / 'Engine/Binaries/Win64/CrashReportClient.exe')
        self.assertEqual(steam.resolve_exe_directory(self.install), self.install / 'SB/Binaries/Win64')
        executable(self.install / 'Other/Binaries/Win64/Game.exe')
        self.assertIsNone(steam.resolve_exe_directory(self.install))
        empty = self.root / 'x86 only'
        executable(empty / 'game.exe', x64=False)
        self.assertIsNone(steam.resolve_exe_directory(empty))

    def test_directory_resolution_distinguishes_crash_games_from_helpers(self):
        executable(self.install / 'CrashBandicoot4.exe')
        executable(self.install / 'tools/CrashReportClient.exe')
        executable(self.install / 'tools/crashpad_handler.exe')
        executable(self.install / 'tools/UnityCrashHandler64.exe')
        executable(self.install / 'amdnr-linux/source/linux/tests/fixture.exe')
        executable(self.install / '.amdnr-linux-backup/old/game.exe')
        self.assertEqual(steam.resolve_exe_directory(self.install), self.install)

    def test_directory_resolution_does_not_follow_external_executable_links(self):
        executable(self.install / 'game.exe')
        external = self.root / 'external tools'
        executable(external / 'game.exe')
        (self.install / 'external').symlink_to(external, target_is_directory=True)
        (self.install / 'linked.exe').symlink_to(external / 'game.exe')
        self.assertEqual(steam.resolve_exe_directory(self.install), self.install)

    def test_configure_preserves_all_other_bytes_and_original_wrappers_arguments(self):
        original = 'MANGOHUD=1 gamemoderun %command% --launcher-skip --name "A B"'
        path = self.account(original, newline='\r\n')
        before = path.read_bytes()
        launch = steam.configure_launch(self.game, self.wrapper, self.state)
        self.assertEqual(launch, 'MANGOHUD=1 gamemoderun ' + steam.shlex.quote(str(self.wrapper))
                         + ' %command% --launcher-skip --name "A B"')
        expected = before.replace(steam._quote(original).encode(), steam._quote(launch).encode())
        self.assertEqual(path.read_bytes(), expected)
        journal = steam.journal_path(self.game, self.state)
        self.assertEqual(journal.stat().st_mode & 0o777, 0o600)
        backup = journal.parent / '123-backups/1001.vdf'
        self.assertEqual(backup.read_bytes(), before)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        steam.configure_launch(self.game, self.wrapper, self.state)
        self.assertEqual(path.read_bytes(), expected)
        steam.restore_launch(self.game, self.state)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(journal.exists())

    def test_absent_and_argument_only_options_roundtrip(self):
        for original in (None, '', '--launcher-skip --rt-only'):
            with self.subTest(original=original):
                path = self.account(original)
                before = path.read_bytes()
                steam.configure_launch(self.game, self.wrapper, self.state)
                self.assertIn('%command%', self.option(path))
                steam.restore_launch(self.game, self.state)
                self.assertEqual(path.read_bytes(), before)

    def test_compact_vdf_absent_options_roundtrip(self):
        path = self.account()
        path.write_bytes(b'"UserLocalConfigStore" { "Software" { "Valve" { "Steam" { "apps" { "123" {} } } } } }')
        before = path.read_bytes()
        steam.configure_launch(self.game, self.wrapper, self.state)
        steam.restore_launch(self.game, self.state)
        self.assertEqual(path.read_bytes(), before)

    def test_each_account_uses_own_options_and_unrelated_apps_unchanged(self):
        first = self.account('gamemoderun %command%', account='1001')
        second = self.account('--different', account='1002')
        other = self.account('not this game', account='1003')
        other.write_bytes(other.read_bytes().replace(b'"123"', b'"999"'))
        originals = {path: path.read_bytes() for path in (first, second, other)}
        steam.configure_launch(self.game, self.wrapper, self.state)
        self.assertTrue(self.option(first).startswith('gamemoderun '))
        self.assertTrue(self.option(second).endswith('%command% --different'))
        self.assertEqual(other.read_bytes(), originals[other])
        steam.restore_launch(self.game, self.state)
        for path, before in originals.items():
            self.assertEqual(path.read_bytes(), before)

    def test_restore_preserves_later_unrelated_settings(self):
        path = self.account('MANGOHUD=1 %command%')
        original = path.read_bytes()
        steam.configure_launch(self.game, self.wrapper, self.state)
        path.write_bytes(path.read_bytes().replace(b'"before"', b'"after"'))
        steam.restore_launch(self.game, self.state)
        self.assertEqual(path.read_bytes(), original.replace(b'"before"', b'"after"'))

    def test_restore_refuses_later_user_launch_option_changes(self):
        path = self.account('%command%')
        steam.configure_launch(self.game, self.wrapper, self.state)
        path.write_bytes(path.read_bytes().replace(b'%command%', b'%command% --new-user-arg'))
        changed = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'edited later'):
            steam.restore_launch(self.game, self.state)
        self.assertEqual(path.read_bytes(), changed)
        self.assertTrue(steam.journal_path(self.game, self.state).exists())

    def test_leftover_wrapper_is_found_and_removed_with_or_without_journal(self):
        path = self.account('MANGOHUD=1 %command% --x')
        before = path.read_bytes()
        self.assertFalse(steam.wrapper_leftover(self.game, self.wrapper, self.state))
        self.assertFalse(steam.remove_wrapper(self.game, self.wrapper, self.state))
        steam.configure_launch(self.game, self.wrapper, self.state)
        self.wrapper.unlink()  # its files are already gone
        self.assertTrue(steam.wrapper_leftover(self.game, self.wrapper, self.state))
        self.assertTrue(steam.remove_wrapper(self.game, self.wrapper, self.state))
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(steam.journal_path(self.game, self.state).exists())
        self.assertFalse(steam.wrapper_leftover(self.game, self.wrapper, self.state))
        # Added by hand, as the command-line guide describes: only the wrapper goes.
        quoted = steam.shlex.quote(str(self.wrapper))
        for options, expected in ((f'MANGOHUD=1 {quoted} %command% --x', 'MANGOHUD=1 %command% --x'),
                                  (f'{quoted} %command%', None)):
            with self.subTest(options=options):
                path = self.account(options)
                self.assertTrue(steam.wrapper_leftover(self.game, self.wrapper, self.state))
                self.assertTrue(steam.remove_wrapper(self.game, self.wrapper, self.state))
                self.assertEqual(self.option(path), expected)
        self.assertEqual(path.read_bytes(), config(None))  # the whole LaunchOptions line went

    def test_leftover_wrapper_keeps_later_edits_and_other_installations(self):
        path = self.account('%command%')
        steam.configure_launch(self.game, self.wrapper, self.state)
        path.write_bytes(path.read_bytes().replace(b'%command%', b'%command% --new-user-arg'))
        self.assertTrue(steam.remove_wrapper(self.game, self.wrapper, self.state))
        self.assertEqual(self.option(path), '%command% --new-user-arg')
        self.assertFalse(steam.journal_path(self.game, self.state).exists())
        self.assertFalse((steam.journal_path(self.game, self.state).parent / '123-backups').exists())
        unsafe = self.account(f'%command% {steam.shlex.quote(str(self.wrapper))}')
        before = unsafe.read_bytes()
        with self.assertRaisesRegex(ValueError, 'cannot be edited safely'):
            steam.remove_wrapper(self.game, self.wrapper, self.state)
        self.assertEqual(unsafe.read_bytes(), before)
        # Another folder's installation owns the options its journal records.
        path = self.account('%command%')
        steam.configure_launch(self.game, self.wrapper, self.state)
        configured = path.read_bytes()
        elsewhere = self.install / 'Elsewhere/amdnr-linux/run.sh'
        self.assertFalse(steam.wrapper_leftover(self.game, elsewhere, self.state))
        self.assertFalse(steam.remove_wrapper(self.game, elsewhere, self.state))
        self.assertEqual(path.read_bytes(), configured)
        self.assertTrue(steam.journal_path(self.game, self.state).exists())

    def test_malformed_or_multiple_command_options_do_not_write(self):
        for option in ('%command% %command%', 'prefix%command%', '"%command%"',
                       '"unterminated', '%command%; echo bad', '$(bad) %command%',
                       'echo # %command%', '`bad` %command%'):
            with self.subTest(option=option):
                path = self.account(option)
                before = path.read_bytes()
                with self.assertRaises(ValueError):
                    steam.configure_launch(self.game, self.wrapper, self.state)
                self.assertEqual(path.read_bytes(), before)
                self.assertFalse(steam.journal_path(self.game, self.state).exists())

    def test_duplicate_relevant_keys_are_refused(self):
        path = self.account('%command%')
        path.write_bytes(path.read_bytes().replace(b'"LastPlayed"', b'"launchoptions" "duplicate"\n"LastPlayed"'))
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            steam.configure_launch(self.game, self.wrapper, self.state)
        self.assertEqual(path.read_bytes(), before)

    def test_steam_running_blocks_configure_restore_and_transaction(self):
        path = self.account()
        original = path.read_bytes()
        with patch.object(steam, 'steam_is_running', return_value=True):
            with self.assertRaisesRegex(ValueError, 'Close Steam'):
                steam.configure_launch(self.game, self.wrapper, self.state)
        self.assertEqual(path.read_bytes(), original)
        steam.configure_launch(self.game, self.wrapper, self.state)
        configured = path.read_bytes()
        with patch.object(steam, 'steam_is_running', return_value=True):
            with self.assertRaisesRegex(ValueError, 'Close Steam'):
                steam.restore_launch(self.game, self.state)
        self.assertEqual(path.read_bytes(), configured)

    def test_exact_user_owned_main_process_detection(self):
        proc = self.root / 'proc'
        proc.mkdir()
        process = proc / '1234'
        process.mkdir()
        (process / 'comm').write_text('steamwebhelper\n')
        (process / 'exe').symlink_to('/somewhere/steamwebhelper')
        # Use the original function while the fixture's normal gate is patched.
        actual = self.original_running
        self.assertFalse(actual(proc))
        (process / 'comm').write_text('steam\n')
        self.assertFalse(actual(proc))
        (process / 'exe').unlink()
        (process / 'exe').symlink_to('/somewhere/ubuntu12_32/steam')
        self.assertTrue(actual(proc))
        self.assertFalse(actual(proc, uid=os.getuid() + 1))

    original_running = staticmethod(steam.steam_is_running)

    def test_missing_account_and_symlinked_config_fail_without_changes(self):
        with self.assertRaisesRegex(ValueError, 'Launch this game once'):
            steam.configure_launch(self.game, self.wrapper, self.state)
        path = self.account()
        outside = self.root / 'outside.vdf'
        path.rename(outside)
        path.symlink_to(outside)
        before = outside.read_bytes()
        with self.assertRaisesRegex(ValueError, 'userdata'):
            steam.configure_launch(self.game, self.wrapper, self.state)
        self.assertEqual(outside.read_bytes(), before)

    def test_atomic_compare_detects_concurrent_settings_change(self):
        path = self.account('%command%')
        original_write = steam._atomic_write
        altered = None

        def change_before_write(destination, data, expected, **kwargs):
            nonlocal altered
            if destination == path:
                altered = path.read_bytes().replace(b'"before"', b'"concurrent"')
                path.write_bytes(altered)
            return original_write(destination, data, expected, **kwargs)

        with patch.object(steam, '_atomic_write', side_effect=change_before_write):
            with self.assertRaisesRegex(ValueError, 'changed during'):
                steam.configure_launch(self.game, self.wrapper, self.state)
        self.assertEqual(path.read_bytes(), altered)
        steam.restore_launch(self.game, self.state)
        self.assertEqual(path.read_bytes(), altered)

    def test_concurrent_launcher_operation_is_rejected(self):
        self.account()
        with steam._lock(self.game, self.state):
            with self.assertRaisesRegex(ValueError, 'Another launcher'):
                steam.configure_launch(self.game, self.wrapper, self.state)

    def test_existing_exact_wrapper_is_adopted_without_rewriting(self):
        original = 'MANGOHUD=1 gamemoderun "' + str(self.wrapper) + '" %command% --foo'
        path = self.account(original)
        before = path.read_bytes()
        self.assertEqual(steam.configure_launch(self.game, self.wrapper, self.state), original)
        self.assertEqual(path.read_bytes(), before)
        steam.restore_launch(self.game, self.state)
        self.assertEqual(self.option(path), 'MANGOHUD=1 gamemoderun %command% --foo')

    def test_failed_install_transaction_restores_exact_options_and_no_journal(self):
        for option in (None, '%command%', 'gamemoderun "' + str(self.wrapper) + '" %command%'):
            with self.subTest(option=option):
                path = self.account(option)
                before = path.read_bytes()
                with self.assertRaisesRegex(RuntimeError, 'backend failed'):
                    with steam.launch_transaction(self.game, self.wrapper, self.state):
                        raise RuntimeError('backend failed')
                self.assertEqual(path.read_bytes(), before)
                self.assertFalse(steam.journal_path(self.game, self.state).exists())

    def test_failed_install_preserves_already_owned_options_and_journal(self):
        path = self.account('%command%')
        steam.configure_launch(self.game, self.wrapper, self.state)
        before = path.read_bytes()
        journal = steam.journal_path(self.game, self.state)
        previous = journal.read_bytes()
        with self.assertRaises(RuntimeError):
            with steam.launch_transaction(self.game, self.wrapper, self.state):
                raise RuntimeError('backend failure')
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(journal.read_bytes(), previous)

    def test_failed_uninstall_restores_options_journal_backups_and_later_other_edits(self):
        path = self.account('%command% --foo')
        steam.configure_launch(self.game, self.wrapper, self.state)
        before = path.read_bytes()
        journal = steam.journal_path(self.game, self.state)
        previous = journal.read_bytes()
        with self.assertRaises(RuntimeError):
            with steam.restore_launch_transaction(self.game, self.state):
                self.assertEqual(self.option(path), '%command% --foo')
                path.write_bytes(path.read_bytes().replace(b'"before"', b'"later unrelated"'))
                raise RuntimeError('backend failure')
        self.assertEqual(path.read_bytes(), before.replace(b'"before"', b'"later unrelated"'))
        self.assertEqual(journal.read_bytes(), previous)
        self.assertTrue((journal.parent / '123-backups/1001.vdf').exists())

    def test_failed_transaction_does_not_overwrite_later_option_edits(self):
        path = self.account('%command%')
        with self.assertRaisesRegex(ValueError, 'newer options'):
            with steam.launch_transaction(self.game, self.wrapper, self.state):
                path.write_bytes(path.read_bytes().replace(b'%command%', b'%command% --later'))
                raise RuntimeError('backend failure')
        self.assertTrue(self.option(path).endswith('--later'))
        self.assertTrue(steam.journal_path(self.game, self.state).exists())

    def test_partial_multi_account_configuration_and_restoration_roll_back(self):
        first = self.account('%command%', account='1001')
        second = self.account('%command% --second', account='1002')
        original = {path: path.read_bytes() for path in (first, second)}
        real_write = steam._atomic_write
        failed = False

        def fail_second(destination, data, expected, **kwargs):
            nonlocal failed
            if destination == second and not failed:
                failed = True
                raise OSError('simulated write failure')
            return real_write(destination, data, expected, **kwargs)

        with patch.object(steam, '_atomic_write', side_effect=fail_second):
            with self.assertRaisesRegex(OSError, 'simulated'):
                with steam.launch_transaction(self.game, self.wrapper, self.state):
                    self.fail('Failed configuration must not run backend')
        for path, before in original.items():
            self.assertEqual(path.read_bytes(), before)
        self.assertFalse(steam.journal_path(self.game, self.state).exists())
        steam.configure_launch(self.game, self.wrapper, self.state)
        configured = {path: path.read_bytes() for path in (first, second)}
        failed = False
        with patch.object(steam, '_atomic_write', side_effect=fail_second):
            with self.assertRaisesRegex(OSError, 'simulated'):
                with steam.restore_launch_transaction(self.game, self.state):
                    self.fail('Failed restoration must not run backend')
        for path, before in configured.items():
            self.assertEqual(path.read_bytes(), before)
        steam.restore_launch(self.game, self.state)

    def test_transaction_does_not_overwrite_concurrently_changed_journal(self):
        self.account('%command%')
        journal = steam.journal_path(self.game, self.state)
        newer = None
        with self.assertRaisesRegex(ValueError, 'newer journal'):
            with steam.launch_transaction(self.game, self.wrapper, self.state):
                record = json.loads(journal.read_bytes())
                record['newer'] = 'keep this'
                newer = json.dumps(record).encode()
                journal.write_bytes(newer)
                raise RuntimeError('backend failure')
        self.assertEqual(journal.read_bytes(), newer)

    def crash_transaction(self, *, restore=False, partial=False):
        script = '''import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from native_launcher import steam
values = json.loads(sys.argv[2])
game = steam.SteamGame("123", "Test Game", Path(values["install"]), Path(values["steam"]), Path(values["library"]), Path(values["exe"]))
steam.steam_is_running = lambda: False
if values["partial"]:
    original = steam._atomic_write
    def write(path, data, expected, **kwargs):
        original(path, data, expected, **kwargs)
        if path.name == "localconfig.vdf" and path.parent.parent.name == "1001":
            os._exit(73)
    steam._atomic_write = write
transaction = (steam.restore_launch_transaction(game, Path(values["state"])) if values["restore"] else
               steam.launch_transaction(game, Path(values["wrapper"]), Path(values["state"])))
with transaction:
    os._exit(73)
'''
        values = {'install': str(self.install), 'steam': str(self.steam), 'library': str(self.library),
                  'exe': str(self.game.exe_dir), 'state': str(self.state), 'wrapper': str(self.wrapper),
                  'restore': restore, 'partial': partial}
        result = subprocess.run([sys.executable, '-c', script, str(Path(steam.__file__).resolve().parents[1]),
                                 json.dumps(values)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 73, result.stderr)
        self.assertTrue(steam.transaction_path(self.game, self.state).exists())

    def test_planned_wrapper_is_allowed_only_in_game_install_transaction(self):
        path = self.account('%command%')
        self.wrapper.unlink()
        with self.assertRaisesRegex(ValueError, 'missing'):
            steam.configure_launch(self.game, self.wrapper, self.state)
        with steam.launch_transaction(self.game, self.wrapper, self.state):
            self.wrapper.write_text('#!/bin/sh\nexec "$@"\n')
            self.wrapper.chmod(0o755)
        self.assertIn(str(self.wrapper), self.option(path))
        self.assertFalse(steam.transaction_path(self.game, self.state).exists())

    def test_hard_exit_before_game_install_rolls_back_missing_wrapper_options(self):
        path = self.account('%command% --foo')
        original = path.read_bytes()
        self.wrapper.unlink()
        self.crash_transaction()
        with self.assertRaisesRegex(ValueError, 'interrupted Steam'):
            steam.configure_launch(self.game, self.wrapper, self.state)
        steam.recover_launch_transaction(self.game, self.state, keep_changes=False)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(steam.journal_path(self.game, self.state).exists())
        self.assertFalse(steam.transaction_path(self.game, self.state).exists())
        self.assertFalse(self.wrapper.exists())

    def test_hard_exit_after_backend_commit_keeps_steam_plan(self):
        path = self.account('%command%')
        self.crash_transaction()
        configured = path.read_bytes()
        steam.recover_launch_transaction(self.game, self.state, keep_changes=True)
        self.assertEqual(path.read_bytes(), configured)
        self.assertTrue(steam.journal_path(self.game, self.state).exists())
        self.assertFalse(steam.transaction_path(self.game, self.state).exists())

    def test_hard_exit_during_steam_changes_rolls_back_every_account(self):
        first = self.account('%command%', account='1001')
        second = self.account('%command% --second', account='1002')
        originals = {path: path.read_bytes() for path in (first, second)}
        self.crash_transaction(partial=True)
        steam.recover_launch_transaction(self.game, self.state, keep_changes=False)
        for path, original in originals.items():
            self.assertEqual(path.read_bytes(), original)
        self.assertFalse(steam.journal_path(self.game, self.state).exists())

    def test_hard_exit_during_uninstall_restores_options_journal_and_backups(self):
        first = self.account('%command%', account='1001')
        second = self.account('%command% --second', account='1002')
        steam.configure_launch(self.game, self.wrapper, self.state)
        configured = {path: path.read_bytes() for path in (first, second)}
        journal = steam.journal_path(self.game, self.state)
        previous_journal = journal.read_bytes()
        previous_backups = {path.name: path.read_bytes() for path in (journal.parent / '123-backups').iterdir()}
        for partial in (True, False):
            with self.subTest(partial=partial):
                self.crash_transaction(restore=True, partial=partial)
                steam.recover_launch_transaction(self.game, self.state, keep_changes=False)
                for path, before in configured.items():
                    self.assertEqual(path.read_bytes(), before)
                self.assertEqual(journal.read_bytes(), previous_journal)
                self.assertEqual({path.name: path.read_bytes() for path in (journal.parent / '123-backups').iterdir()}, previous_backups)

    def test_hard_exit_after_uninstall_commit_keeps_restored_options(self):
        path = self.account('%command%')
        original = path.read_bytes()
        steam.configure_launch(self.game, self.wrapper, self.state)
        self.crash_transaction(restore=True)
        steam.recover_launch_transaction(self.game, self.state, keep_changes=True)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(steam.journal_path(self.game, self.state).exists())

    def test_recovery_keeps_later_unrelated_edits_but_refuses_new_launch_options(self):
        path = self.account('%command%')
        original = path.read_bytes()
        self.crash_transaction()
        path.write_bytes(path.read_bytes().replace(b'"before"', b'"later"'))
        steam.recover_launch_transaction(self.game, self.state, keep_changes=False)
        self.assertEqual(path.read_bytes(), original.replace(b'"before"', b'"later"'))
        self.crash_transaction()
        path.write_bytes(path.read_bytes().replace(b'%command%', b'%command% --keep-user-edit'))
        changed = path.read_bytes()
        for keep in (True, False):
            with self.assertRaisesRegex(ValueError, 'newer options'):
                steam.recover_launch_transaction(self.game, self.state, keep_changes=keep)
            self.assertEqual(path.read_bytes(), changed)
        self.assertTrue(steam.transaction_path(self.game, self.state).exists())

    def test_deferred_exception_keeps_receipt_until_backend_commit_decision(self):
        path = self.account('%command%')
        original = path.read_bytes()
        with self.assertRaisesRegex(OSError, 'after commit'):
            with steam.launch_transaction(self.game, self.wrapper, self.state, defer_recovery=True):
                raise OSError('reporting failed after commit')
        configured = path.read_bytes()
        self.assertNotEqual(configured, original)
        self.assertTrue(steam.transaction_path(self.game, self.state).exists())
        steam.recover_launch_transaction(self.game, self.state, keep_changes=True)
        self.assertEqual(path.read_bytes(), configured)
        with self.assertRaisesRegex(OSError, 'after commit'):
            with steam.restore_launch_transaction(self.game, self.state, defer_recovery=True):
                raise OSError('reporting failed after commit')
        self.assertEqual(path.read_bytes(), original)
        self.assertTrue(steam.transaction_path(self.game, self.state).exists())
        steam.recover_launch_transaction(self.game, self.state, keep_changes=True)
        self.assertEqual(path.read_bytes(), original)

    def test_deferred_precommit_exception_can_still_roll_back(self):
        path = self.account('%command%')
        original = path.read_bytes()
        with self.assertRaisesRegex(OSError, 'before commit'):
            with steam.launch_transaction(self.game, self.wrapper, self.state, defer_recovery=True):
                raise OSError('failed before commit')
        self.assertTrue(steam.transaction_path(self.game, self.state).exists())
        steam.recover_launch_transaction(self.game, self.state, keep_changes=False)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(steam.journal_path(self.game, self.state).exists())


if __name__ == '__main__':
    unittest.main()
