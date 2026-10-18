"""Heroic and Lutris discovery on synthetic files only; never the user's own launchers."""
import json
import os
from pathlib import Path
import shlex
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from native_launcher import controller, steam, stores
import test_native_backend as backend_fixture
from test_native_steam import executable

try:
    import yaml
except ImportError:
    yaml = None


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def make_prefix(path):
    (path / "drive_c/windows/system32").mkdir(parents=True, exist_ok=True)
    (path / "user.reg").write_text("WINE REGISTRY Version 2\n")
    return path


def snapshot(root):
    """Every path with its bytes and modification time, to prove nothing was written."""
    return {str(path): "dir" if path.is_dir() else (path.read_bytes(), path.stat().st_mtime_ns)
            for path in sorted(root.rglob("*")) if path.is_dir() or path.is_file()}


def heroic_settings(root, app_name, **settings):
    write_json(root / "GamesConfig" / f"{app_name}.json", {app_name: settings, "version": "v0", "explicit": True})


class Fixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="native stores ")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.library = self.home / "Games/Heroic"

    def heroic(self, root):
        """Epic, GOG, Amazon and sideloaded games as Heroic and its helpers record them."""
        # Epic: an Unreal bootstrapper beside the real game in Binaries/Win64.
        unreal = self.library / "UnrealFixture"
        executable(unreal / "UnrealFixture.exe")
        executable(unreal / "UnrealFixture/Binaries/Win64/UnrealFixture-Win64-Shipping.exe")
        executable(unreal / "Engine/Binaries/Win64/CrashReportClient.exe")
        mac = self.library / "MacOnly"
        executable(mac / "Mac.exe")
        write_json(root / "legendaryConfig/legendary/installed.json", {
            "a1b2": {"app_name": "a1b2", "title": "Unreal Fixture", "install_path": str(unreal),
                     "executable": "UnrealFixture.exe", "platform": "Windows", "is_dlc": False},
            "dlc1": {"app_name": "dlc1", "title": "Fixture DLC", "install_path": str(unreal),
                     "executable": "UnrealFixture.exe", "platform": "Windows", "is_dlc": True},
            "mac1": {"app_name": "mac1", "title": "Mac Fixture", "install_path": str(mac),
                     "executable": "Mac.exe", "platform": "Mac", "is_dlc": False}})
        proton = self.home / "Games/Heroic/Prefixes/default/Unreal Fixture"
        make_prefix(proton / "pfx")
        heroic_settings(root, "a1b2", winePrefix=str(proton),
                        wineVersion={"type": "proton", "name": "GE-Proton", "bin": "/opt/proton/proton"},
                        wrapperOptions=[{"exe": "/usr/bin/gamemoderun", "args": ""}])
        # GOG: two x64 folders, so the primary play task decides; its case differs on disk.
        witcher = self.library / "Witcher Fixture"
        executable(witcher / "bin/x64/witcher3.exe")
        executable(witcher / "tools/x64/editor.exe")
        write_json(witcher / "goggame-1207658924.info", {"gameId": "1207658924", "name": "Info name", "playTasks": [
            {"type": "FileTask", "path": "tools\\x64\\editor.exe", "isPrimary": False},
            {"type": "FileTask", "path": "BIN\\X64\\Witcher3.exe", "isPrimary": True}]})
        write_json(root / "gog_store/installed.json", {"installed": [
            {"appName": "1207658924", "install_path": str(witcher), "platform": "windows", "executable": ""}]})
        write_json(root / "store_cache/gog_library.json",
                   {"games": [{"app_name": "1207658924", "title": "The Witcher Fixture"}]})
        wine = make_prefix(self.home / "Games/Heroic/Prefixes/default/Witcher")
        heroic_settings(root, "1207658924", winePrefix=str(wine), wineVersion={"type": "wine"},
                        wrapperOptions=[{"exe": str(witcher / "bin/x64/amdnr-linux/run.sh"), "args": ""}])
        # Amazon through nile, with its own fuel.json launch target.
        amazon = self.library / "Amazon Fixture"
        executable(amazon / "Binaries/AmazonFixture.exe")
        write_json(amazon / "fuel.json", {"SchemaVersion": "2", "Main": {"Command": "Binaries/AmazonFixture.exe"}})
        write_json(root / "nile_config/nile/installed.json",
                   [{"id": "amzn1.adg.product.fixture", "path": str(amazon), "version": "1"}])
        write_json(root / "nile_config/nile/library.json",
                   [{"id": "amzn1.adg.product.fixture", "product": {"title": "Amazon Fixture"}}])
        # A sideloaded app run through CrossOver, which passes no WINEPREFIX.
        side = self.home / "Apps/Sideloaded Fixture"
        executable(side / "Sideloaded.exe")
        write_json(root / "sideload_apps/library.json", {"games": [
            {"app_name": "side1", "title": "Sideloaded Fixture", "folder_name": str(side), "is_installed": True,
             "install": {"executable": str(side / "Sideloaded.exe"), "platform": "Windows"}}]})
        heroic_settings(root, "side1", winePrefix=str(self.home / "bottle"), wineVersion={"type": "crossover"})
        return {"unreal": unreal, "witcher": witcher, "amazon": amazon, "side": side, "proton": proton, "wine": wine}

    def lutris(self, config, data):
        """A pga.db and PyYAML-style game options as Lutris writes them."""
        folded = self.home / "Games/lutris/folded game with a rather long folder name"
        executable(folded / "Binaries/Win64/Game With A Long Name.exe")
        folded_prefix = make_prefix(self.home / "Games/lutris/prefixes/folded")
        write_text(config / "games/folded-fixture-1.yml", (
            "game:\n"
            f"  exe: {folded}/Binaries/Win64/Game With A Long\n"
            "    Name.exe\n"
            f"  prefix: '{folded_prefix}'\n"
            "  args: -dx12 # trailing comment\n"
            "system:\n"
            f"  prefix_command: '\"{folded}/Binaries/Win64/amdnr-linux/run.sh\" gamemoderun'\n"
            "  env:\n"
            "    DXVK_HUD: fps\n"
            "wine:\n"
            "  version: lutris-GE-Proton8-26-x86_64\n"
            "  overrides: {}\n"))
        whole = make_prefix(self.home / "Games/lutris/prefix-fixture")
        game = whole / "drive_c/Program Files/Prefix Fixture/game.exe"
        executable(game)
        executable(whole / "drive_c/windows/system32/notepad.exe")
        write_text(config / "games/prefix-fixture-2.yml", f'game:\n  exe: "{game}"\nwine: {{}}\n')
        ea = make_prefix(self.home / "Games/lutris/ea-app")
        executable(ea / "drive_c/Program Files/Electronic Arts/EA Desktop/EA Desktop/EADesktop.exe")
        write_text(config / "games/ea-app-3.yml",
                   f"game:\n  exe: {ea}/drive_c/Program Files/Electronic Arts/EA Desktop/EA Desktop/EADesktop.exe\n"
                   f"  prefix: {ea}\n")
        bare = self.home / "Games/lutris/no-prefix"
        executable(bare / "game.exe")
        write_text(config / "games/no-prefix-4.yml", "game:\n  exe: game.exe\n")
        write_text(config / "system.yml", "system:\n  prefix_command: gamemoderun\n")
        data.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(data / "pga.db")
        connection.execute("CREATE TABLE games (id INTEGER PRIMARY KEY, name TEXT, sortname TEXT, slug TEXT, "
                           "installer_slug TEXT, parent_slug TEXT, platform TEXT, runner TEXT, executable TEXT, "
                           "directory TEXT, updated DATETIME, lastplayed INTEGER, installed INTEGER, "
                           "installed_at INTEGER, year INTEGER, configpath TEXT, has_custom_banner INTEGER, "
                           "playtime REAL, service TEXT, service_id TEXT)")
        rows = [
            (1, "Folded Fixture", "folded-fixture", "Windows", "wine", str(folded), 1, "folded-fixture-1", "gog"),
            (2, "Prefix Fixture", "prefix-fixture", "Windows", "wine", str(whole), 1, "prefix-fixture-2", None),
            (3, "EA app", "ea-app", "Windows", "wine", str(ea), 1, "ea-app-3", None),
            (4, "No Prefix Fixture", "no-prefix", "Windows", "wine", str(bare), 1, "no-prefix-4", None),
            (5, "Native Fixture", "native", "Linux", "linux", str(bare), 1, "native-5", None),
            (6, "Removed Fixture", "removed", "Windows", "wine", str(bare), 0, "removed-6", None),
            (7, "Escaping Fixture", "escape", "Windows", "wine", None, 1, "../escape", None)]
        connection.executemany("INSERT INTO games (id, name, slug, platform, runner, directory, installed, "
                               "configpath, service) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        connection.commit()
        connection.close()
        return {"folded": folded, "folded_prefix": folded_prefix, "whole": whole, "game": game, "ea": ea, "bare": bare}


class HeroicTests(Fixture):
    def test_epic_gog_amazon_and_sideloaded_games_like_manual_games(self):
        root = self.home / ".config/heroic"
        paths = self.heroic(root)
        before = snapshot(self.home)
        games = {game.name: game for game in stores.discover_games([root], [])}
        self.assertEqual(snapshot(self.home), before)
        self.assertEqual(sorted(games), ["Amazon Fixture", "Sideloaded Fixture", "The Witcher Fixture", "Unreal Fixture"])
        for game in games.values():
            self.assertEqual((game.appid, game.steam_root, game.store), ("", Path(), "heroic"))
        unreal = games["Unreal Fixture"]
        # Found as Steam discovery would: the shipping executable, not the bootstrapper.
        self.assertEqual(unreal.exe_dir, paths["unreal"] / "UnrealFixture/Binaries/Win64")
        self.assertEqual(unreal.executable, paths["unreal"] / "UnrealFixture.exe")
        self.assertEqual((unreal.runner, unreal.prefix), ("proton", paths["proton"] / "pfx"))
        self.assertEqual(stores.label(unreal), "HEROIC  /  EPIC GAMES")
        witcher = games["The Witcher Fixture"]
        self.assertEqual(witcher.exe_dir, paths["witcher"] / "bin/x64")
        self.assertEqual(witcher.prefix, paths["wine"])
        self.assertEqual(stores.label(witcher), "HEROIC  /  GOG")
        self.assertEqual(stores.wrapper_state(witcher), "current")
        amazon = games["Amazon Fixture"]
        self.assertEqual((amazon.exe_dir, amazon.prefix, amazon.source), (paths["amazon"] / "Binaries", None, "Amazon"))
        side = games["Sideloaded Fixture"]
        self.assertEqual((side.install_dir, side.exe_dir, side.runner, side.prefix),
                         (paths["side"], paths["side"], "crossover", None))
        # Heroic's launch links name the store backend that installed the game.
        self.assertEqual({name: stores.launch_url(game) for name, game in games.items()}, {
            "Unreal Fixture": "heroic://launch/legendary/a1b2",
            "The Witcher Fixture": "heroic://launch/gog/1207658924",
            "Amazon Fixture": "heroic://launch/nile/amzn1.adg.product.fixture",
            "Sideloaded Fixture": "heroic://launch/sideload/side1"})

    def test_native_flatpak_and_xdg_locations(self):
        self.assertEqual(stores.heroic_roots(self.home, {}), [
            self.home / ".config/heroic", self.home / ".var/app/com.heroicgameslauncher.hgl/config/heroic"])
        self.assertEqual(stores.heroic_roots(self.home, {"XDG_CONFIG_HOME": "/xdg/config"})[0], Path("/xdg/config/heroic"))
        self.assertEqual(stores.heroic_roots(self.home, {"XDG_CONFIG_HOME": "relative"})[0], self.home / ".config/heroic")
        flatpak = self.home / ".var/app/net.lutris.Lutris"
        self.assertEqual(stores.lutris_roots(self.home, {"XDG_DATA_HOME": "/xdg/data"}), [
            (self.home / ".config/lutris", Path("/xdg/data/lutris")), (flatpak / "config/lutris", flatpak / "data/lutris")])
        paths = self.heroic(self.home / ".var/app/com.heroicgameslauncher.hgl/config/heroic")
        games = stores.discover_games(stores.heroic_roots(self.home, {}), stores.lutris_roots(self.home, {}))
        self.assertIn(paths["witcher"], [game.install_dir for game in games])

    def test_unusable_metadata_is_skipped_without_blocking(self):
        root = self.home / ".config/heroic"
        write_text(root / "gog_store/installed.json", "{not json")
        write_json(root / "nile_config/nile/installed.json", {"unexpected": "shape"})
        write_json(root / "sideload_apps/library.json", {"games": ["text", {"install": "text"}, {"install": {}}]})
        kept, escape = self.library / "Kept", self.library / "Escape"
        executable(kept / "Kept.exe")
        executable(escape / "Escape.exe")
        write_json(root / "legendaryConfig/legendary/installed.json", {
            "kept": {"app_name": "kept", "title": "Kept", "install_path": str(kept), "executable": "Kept.exe"},
            "gone": {"app_name": "gone", "title": "Gone", "install_path": str(self.home / "missing")},
            "escape": {"app_name": "../../escape", "title": "Escape", "install_path": str(escape),
                       "executable": "../Kept/Kept.exe"},
            "list": ["not", "a", "game"]})
        heroic_settings(root, "kept", wrapperOptions=5, wineVersion="proton", winePrefix=7)
        write_text(root / "GamesConfig/gone.json", "[1, 2")
        write_json(root / "nile_config/nile/library.json", 5)
        write_json(root / "store_cache/nile_library.json", {"library": 3})
        os.mkfifo(root / "store_cache/gog_library.json")
        result = []
        worker = threading.Thread(target=lambda: result.extend(stores.discover_games([root], [])), daemon=True)
        worker.start()
        worker.join(10)
        self.assertFalse(worker.is_alive(), "A FIFO in the launcher's settings blocked discovery")
        self.assertEqual(sorted(game.name for game in result), ["Escape", "Kept"])
        found = next(game for game in result if game.name == "Escape")
        self.assertIsNone(found.executable)
        self.assertEqual(found.exe_dir, escape)  # The scan still finds the folder's only executable.

    def test_a_failing_launcher_never_hides_the_others(self):
        root = self.home / ".config/heroic"
        paths = self.heroic(root)
        broken = self.home / "broken heroic"
        broken.mkdir()
        original = stores._heroic_games

        def heroic_games(path):
            if path == broken:
                raise TypeError("unexpected file shape")
            return original(path)

        with patch.object(stores, "_heroic_games", side_effect=heroic_games):
            games = stores.discover_games([broken, root], [])
        self.assertIn(paths["unreal"], [game.install_dir for game in games])


class LutrisTests(Fixture):
    def test_wine_games_prefixes_and_command_prefix(self):
        config, data = self.home / ".config/lutris", self.home / ".local/share/lutris"
        paths = self.lutris(config, data)
        before = snapshot(self.home)
        games = {game.name: game for game in stores.discover_games([], [(config, data)])}
        self.assertEqual(snapshot(self.home), before)
        self.assertFalse(list(data.glob("pga.db-*")))
        self.assertEqual(sorted(games), ["EA app", "Folded Fixture", "No Prefix Fixture", "Prefix Fixture"])
        folded = games["Folded Fixture"]
        self.assertEqual(folded.install_dir, paths["folded"])
        self.assertEqual(folded.exe_dir, paths["folded"] / "Binaries/Win64")
        self.assertEqual(folded.executable, paths["folded"] / "Binaries/Win64/Game With A Long Name.exe")
        self.assertEqual(folded.prefix, paths["folded_prefix"])
        self.assertEqual(stores.label(folded), "LUTRIS  /  GOG")
        self.assertEqual(folded.wrappers, (str(paths["folded"] / "Binaries/Win64/amdnr-linux/run.sh"), "gamemoderun"))
        self.assertEqual(stores.wrapper_state(folded), "current")
        # A game inside a whole-prefix install: the prefix is found as Lutris does, and never scanned.
        inside = games["Prefix Fixture"]
        self.assertEqual((inside.install_dir, inside.exe_dir, inside.prefix),
                         (paths["game"].parent, paths["game"].parent, paths["whole"]))
        self.assertEqual(stores.label(inside), "LUTRIS  /  WINE")
        # Lutris' own shortcuts start games by database id, not by slug.
        self.assertEqual((inside.store_id, inside.lutris_id), ("prefix-fixture", "2"))
        self.assertEqual(stores.launch_url(folded), "lutris:rungameid/1")
        # A store client is not the game: the user chooses the game inside its prefix.
        client = games["EA app"]
        self.assertEqual((client.install_dir, client.exe_dir, client.executable, client.prefix),
                         (paths["ea"], None, None, paths["ea"]))
        bare = games["No Prefix Fixture"]
        self.assertEqual((bare.exe_dir, bare.prefix, bare.wrappers), (paths["bare"], None, ("gamemoderun",)))
        self.assertIn("no Wine prefix", stores.notices(bare)[0]["text"])

    def test_configuration_falls_back_to_the_data_folder(self):
        data = self.home / ".local/share/lutris"
        self.lutris(data, data)
        games = stores.discover_games([], stores.lutris_roots(self.home, {}))
        self.assertIn("Folded Fixture", [game.name for game in games])

    def test_unreadable_database_is_skipped(self):
        data = self.home / "lutris"
        data.mkdir()
        (data / "pga.db").write_bytes(b"not a database" * 100)
        self.assertEqual(stores.discover_games([], [(data, data)]), [])


class YamlTests(unittest.TestCase):
    def test_folded_quoted_and_skipped_values(self):
        text = ("%YAML 1.1\r\n---\r\n# Lutris game\r\n"
                "game:\r\n  exe: /games/a b\r\n    c.exe\r\n"
                '  prefix: "/games/Pok\\xE9mon \\u00e9\\\r\n    \\ tail\\tx"\r\n'
                "  single: 'it''s\r\n\r\n    folded'\r\n"
                "  list:\r\n  - one\r\n  - 'two\r\n    lines'\r\n"
                "  notes: |\r\n    block: text\r\n"
                "  after: kept # comment\r\n"
                "  empty: ''\r\n  none: ~\r\n"
                "'quoted key': value\r\n")
        self.assertEqual(stores.parse_yaml(text), {
            "game": {"exe": "/games/a b c.exe", "prefix": "/games/Pok\xe9mon \xe9 tail\tx", "single": "it's\nfolded",
                     "list": None, "notes": None, "after": "kept", "empty": "", "none": None},
            "quoted key": "value"})

    def test_unsupported_structure_raises(self):
        for text in ("game:\n  exe: 'unterminated\n", "game:\n\texe: tab\n", "just text\n", 'a: "bad \\q"\n',
                     "a: 'x' trailing\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                stores.parse_yaml(text)

    @unittest.skipUnless(yaml, "PyYAML is not installed")
    def test_matches_pyyaml_dumps(self):
        sample = {
            "game": {"exe": "/home/u/Games/" + "A very long folder name " * 6 + "/Game-Win64-Shipping.exe",
                     "prefix": "/home/u/Pokémon: x #y", "args": "-dx12  -foo", "working_dir": " lead",
                     "empty": "", "quote": "it's \"q\" \\ back", "tab": "a\tb", "multi": "one\ntwo\n\nfour",
                     "trail": "x ", "dash": "- not a list", "number": "123", "flag": "true"},
            "system": {"prefix_command": "'/path with space/amdnr-linux/run.sh' gamemoderun",
                       "env": {"*weird key": "1", "A": "b"}},
            "wine": {"version": "lutris-GE-Proton8-26-x86_64", "overrides": {}},
            "list": ["a" * 50 + " " + "b" * 50, "c"], "nested": {"seq": ["x " * 60], "after": "kept"}}

        def expected(value):
            if isinstance(value, dict):
                return {key: expected(item) for key, item in value.items()} if value else None
            return value if isinstance(value, str) else None

        for width in (20, 80, 200):
            for unicode in (False, True):
                text = yaml.safe_dump(sample, default_flow_style=False, allow_unicode=unicode, width=width)
                with self.subTest(width=width, unicode=unicode):
                    parsed = stores.parse_yaml(text)
                    self.assertEqual(parsed, expected(yaml.safe_load(text)))
                    self.assertEqual(parsed["game"], expected(sample["game"]))


class NoticeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="store notices ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.game_dir = self.root / "Game with spaces"
        self.game_dir.mkdir()
        self.prefix = make_prefix(self.root / "prefix")

    def game(self, store="heroic", **details):
        details.setdefault("prefix", self.prefix)
        details.setdefault("store_id", "1")
        return stores.StoreGame("", "Fixture", self.game_dir, Path(), self.game_dir, self.game_dir,
                                store=store, source="GOG", **details)

    def texts(self, game, installed):
        return [(item["level"], item["text"]) for item in stores.notices(game, installed)]

    def test_wrapper_line_and_state(self):
        wrapper = str(self.game_dir / "amdnr-linux/run.sh")
        heroic, lutris = self.game(), self.game("lutris")
        self.assertEqual(stores.wrapper_line(heroic), wrapper)
        self.assertEqual(shlex.split(stores.wrapper_line(lutris)), [wrapper])
        added = self.texts(heroic, installed=True)
        self.assertEqual(added[0], ("info", f"Heroic gives the wrapper this game's prefix: {self.prefix}"))
        self.assertEqual(added[1][0], "warning")
        self.assertIn("Add the wrapper in Heroic", added[1][1])
        self.assertIn(wrapper, added[1][1])
        current = self.game(wrappers=("/usr/bin/gamemoderun", wrapper))
        self.assertEqual(stores.wrapper_state(current), "current")
        self.assertIn("starts this game through the AMDNR wrapper", self.texts(current, True)[1][1])
        moved = self.game(wrappers=(str(self.root / "elsewhere/amdnr-linux/run.sh"),))
        self.assertEqual(stores.wrapper_state(moved), "other")
        self.assertIn("Replace the AMDNR wrapper", self.texts(moved, True)[1][1])
        self.assertIn("not installed", self.texts(current, False)[1][1])
        self.assertEqual(len(self.texts(heroic, False)), 1)

    def test_prefix_problems(self):
        crossover = self.game(runner="crossover", prefix=None)
        self.assertIn("CrossOver", self.texts(crossover, False)[0][1])
        self.assertIn("no Wine prefix", self.texts(self.game("lutris", prefix=None), False)[0][1])
        self.assertEqual(self.texts(self.game(prefix=None), False), [])
        missing = self.game(prefix=self.root / "not yet created")
        self.assertIn("Launch the game once from Heroic", self.texts(missing, False)[0][1])

    def test_results(self):
        wrapper = str(self.game_dir / "amdnr-linux/run.sh")
        result = stores.install_result(self.game("lutris"))
        self.assertEqual((result["title"], result["launch_options"]), ("Add the wrapper in Lutris", shlex.quote(wrapper)))
        self.assertIn("Command prefix", result["message"])
        self.assertNotIn("launch_options", stores.install_result(self.game(wrappers=(wrapper,))))
        self.assertIn("Remove the AMDNR wrapper", stores.removed_message(self.game(wrappers=(wrapper,))))
        self.assertIn("If you added", stores.removed_message(self.game()))
        self.assertEqual(stores.notices(steam.SteamGame("1", "Steam", self.game_dir, self.root, self.root)), [])

    def test_launch_links_wait_for_this_installs_wrapper(self):
        wrapper = str(self.game_dir / "amdnr-linux/run.sh")
        heroic = self.game(store_runner="gog", wrappers=("/usr/bin/gamemoderun", wrapper))
        self.assertEqual(stores.launch_url(heroic), "heroic://launch/gog/1")
        self.assertIsNone(stores.launch_issue(heroic, installed=True))
        self.assertIn("wrapper that is not installed", stores.launch_issue(heroic, installed=False))
        bare = self.game(store_runner="gog")
        self.assertEqual(stores.launch_issue(bare, True), "Add the wrapper in Heroic, then rescan.")
        self.assertEqual(stores.launch_issue(bare, False), "Install neural rendering and add the wrapper in Heroic first.")
        moved = self.game(store_runner="gog", wrappers=(str(self.root / "elsewhere/amdnr-linux/run.sh"),))
        self.assertEqual(stores.launch_issue(moved, True), "Replace the AMDNR wrapper in Heroic, then rescan.")
        # Heroic reads the link's path undecoded, so names that need escaping get no link.
        for details in (dict(store_runner="gog", store_id="a b"), dict(store_runner="gog", store_id=".."),
                        dict(store_runner="gog", store_id="x/y"), dict(store_runner="gog", store_id="é"),
                        dict(store_runner="steam"), {}):
            with self.subTest(details=details):
                game = self.game(wrappers=(wrapper,), **details)
                self.assertIsNone(stores.launch_url(game))
                self.assertEqual(stores.launch_issue(game, True),
                                 "Heroic has no launch link for this game. Start it from Heroic.")
        lutris = self.game("lutris", store_id="fixture-slug", lutris_id="12", wrappers=(wrapper,))
        self.assertEqual(stores.launch_url(lutris), "lutris:rungameid/12")
        self.assertIsNone(stores.launch_issue(lutris, True))
        for number in ("", "0", "01", "1a", "١"):
            with self.subTest(number=number):
                self.assertIsNone(stores.launch_url(self.game("lutris", lutris_id=number)))


class StoreControllerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = backend_fixture.BackendTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.game_dir = self.fixture.root.resolve(), self.fixture.game.resolve()
        executable(self.game_dir / "Game.exe")
        self.initial = backend_fixture.snapshot(self.game_dir)
        release = self.root / "release"
        (release / "linux").mkdir(parents=True)
        for name in ("launch.py", "manage.py", "setup.py", "proxies.py"):
            shutil.copy2(ROOT / name, release / "linux" / name)
        (release / "RELEASE-MANIFEST.json").write_text(json.dumps({"release": "linux-v0.2.0"}))
        self.heroic = self.root / "heroic"
        write_json(self.heroic / "gog_store/installed.json", {"installed": [
            {"appName": "42", "install_path": str(self.game_dir), "platform": "windows"}]})
        write_json(self.heroic / "store_cache/gog_library.json", {"games": [{"app_name": "42", "title": "Store Game"}]})
        self.prefix = make_prefix(self.root / "heroic prefix")
        self.settings(wrapperOptions=[])
        self.app = controller.Controller(release, self.root / "controller state", self.root / "controller cache",
                                         [self.root / "no steam"], heroic_roots=[self.heroic], lutris_roots=[])
        self.package = self.fixture.a
        for replacement in (patch.object(steam, "steam_is_running", return_value=False),
                            patch.object(controller, "available_release", return_value=None),
                            patch.object(self.app.backend, "_running", return_value=[]),
                            patch.object(self.app, "component_choices", return_value={}),
                            patch.object(self.app, "prepare", side_effect=lambda *args: self.package)):
            replacement.start()
            self.addCleanup(replacement.stop)

    def settings(self, **values):
        heroic_settings(self.heroic, "42", winePrefix=str(self.prefix), wineVersion={"type": "wine"}, **values)

    def discover(self):
        games = self.app.discover()
        self.assertEqual(len(games), 1)
        return games[0]

    def test_store_game_is_listed_once_like_a_manual_game(self):
        self.app.settings["manual"] = [str(self.game_dir)]
        game = self.discover()
        self.assertEqual((game.appid, game.name, game.exe_dir, stores.label(game)),
                         ("", "Store Game", self.game_dir, "HEROIC  /  GOG"))
        steam_game = steam.SteamGame("7", "Steam copy", self.game_dir, self.root, self.root, self.game_dir)
        with patch.object(steam, "discover_games", return_value=[steam_game]):
            self.assertEqual(self.app.discover(), [steam_game])
        with patch.object(stores, "_heroic_games", side_effect=TypeError("unexpected file")):
            self.assertEqual([item.install_dir for item in self.app.discover()], [self.game_dir])  # The manual entry.

    def test_install_shows_the_wrapper_without_touching_heroic_or_steam(self):
        game = self.discover()
        heroic = snapshot(self.heroic)
        with patch.object(steam, "steam_is_running") as running:
            result = self.app.operate("install", game)
        running.assert_not_called()
        self.assertEqual(snapshot(self.heroic), heroic)
        wrapper = str(self.game_dir / "amdnr-linux/run.sh")
        self.assertEqual((result["title"], result["launch_options"]), ("Add the wrapper in Heroic", wrapper))
        self.assertIn("Wrapper field", result["message"])
        notices = self.app.inspect(game)["notices"]
        self.assertTrue(any("Add the wrapper in Heroic" in item["text"] for item in notices))
        self.settings(wrapperOptions=[{"exe": wrapper, "args": ""}])  # What the user then does in Heroic.
        heroic = snapshot(self.heroic)
        game = self.discover()
        self.assertTrue(any("starts this game through" in item["text"] for item in self.app.inspect(game)["notices"]))
        self.assertNotIn("launch_options", self.app.operate("repair", game))
        removed = self.app.operate("uninstall", game)["message"]
        self.assertIn("Remove the AMDNR wrapper", removed)
        self.assertEqual(snapshot(self.heroic), heroic)
        self.assertEqual(backend_fixture.snapshot(self.game_dir), self.initial)
        self.assertFalse(self.app.pending_operation(game))

    def test_chosen_executable_stays_inside_the_store_install(self):
        game = self.discover()
        outside = self.root / "outside/Other.exe"
        executable(outside)
        with self.assertRaisesRegex(ValueError, "inside this game's Heroic installation"):
            self.app.set_executable(game, outside)
        inner = self.game_dir / "bin/x64/Game.exe"
        executable(inner)
        self.assertEqual(self.app.set_executable(game, inner).exe_dir, inner.parent)
        self.assertEqual(self.discover().exe_dir, inner.parent)

    def test_compiler_search_uses_the_store_prefix(self):
        game = self.discover()
        whole = make_prefix(self.root / "lutris prefix")
        other = stores.StoreGame("", "Inside a prefix", whole, Path(), whole, None, store="lutris", source="Wine")
        self.app.games = [game, other]
        with patch.object(self.app.backend, "prepare_package", return_value=self.package) as prepare, \
                patch.object(controller, "persistent_python", return_value=None):
            controller.Controller.prepare(self.app, game)
        roots = prepare.call_args[0][1]
        self.assertEqual(roots[:2], [self.game_dir, self.prefix / "drive_c/windows/system32"])
        self.assertNotIn(whole, roots)


try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMessageBox
    from native_launcher.app import STYLE, Window
    from test_native_gui import FakeController
    QT = True
except ImportError:
    QT = False


@unittest.skipUnless(QT, "Optional GUI tests need PySide6")
class StoreGuiTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.app.setStyleSheet(STYLE)
        temporary = tempfile.TemporaryDirectory(prefix="store gui ")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        offline = patch("native_launcher.artwork_widgets.Store.load", return_value=None)
        offline.start()
        self.addCleanup(offline.stop)
        self.controller = FakeController(root)
        self.controller.games = [self.store_game()]
        self.wrapper = str(root / "amdnr-linux/run.sh")
        self.window = Window(self.controller)
        self.addCleanup(self.close)
        self.window.show()
        self.window.refresh()
        self.wait_idle()

    def close(self):
        self.wait_idle()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def wait_idle(self):
        deadline, quiet = time.monotonic() + 10, 0
        while time.monotonic() < deadline and quiet < 3:
            self.app.processEvents()
            QTest.qWait(10)
            quiet = quiet + 1 if not self.window.busy else 0
        self.assertGreaterEqual(quiet, 3, "GUI worker did not settle")

    def store_game(self, store="heroic", **details):
        root = self.controller.root
        details.setdefault("store_runner", "gog" if store == "heroic" else "")
        return stores.StoreGame("", f"{stores.STORES[store]} fixture", root, Path(), root, root,
                                store=store, source="GOG", store_id="1", **details)

    def show(self, game):
        self.controller.games = [game]
        self.window.refresh()
        self.wait_idle()

    def reselect(self):
        self.window.select_game(self.window.library.currentItem())
        self.wait_idle()

    def test_store_eyebrow_description_and_wrapper_dialog(self):
        self.assertEqual(self.window.platform.text(), "HEROIC  /  GOG")
        self.assertIn("shows the wrapper to add in Heroic", self.window.description.text())
        self.assertFalse(self.window.launch.isEnabled())
        self.assertEqual(self.window.launch.toolTip(), "Install neural rendering and add the wrapper in Heroic first.")
        with patch.object(QMessageBox, "information") as dialog:
            self.window.operation_done({"message": "Installed.", "title": "Add the wrapper in Heroic",
                                        "launch_options": "/games/amdnr-linux/run.sh"})
        self.assertEqual(dialog.call_args[0][1], "Add the wrapper in Heroic")
        self.assertIn("/games/amdnr-linux/run.sh", dialog.call_args[0][2])

    def test_launch_opens_heroic_and_lutris_links(self):
        self.controller.installed = True
        for game, text, url in (
                (self.store_game(wrappers=(self.wrapper,)), "Launch in Heroic ↗", "heroic://launch/gog/1"),
                (self.store_game(store_runner="legendary", wrappers=(self.wrapper,)),
                 "Launch in Heroic ↗", "heroic://launch/legendary/1"),
                (self.store_game("lutris", lutris_id="7", wrappers=(self.wrapper, "gamemoderun")),
                 "Launch in Lutris ↗", "lutris:rungameid/7")):
            with self.subTest(url=url):
                self.show(game)
                self.assertEqual(self.window.launch.text(), text)
                self.assertTrue(self.window.launch.isEnabled())
                self.assertEqual(self.window.launch.toolTip(), "")
                with patch.object(QDesktopServices, "openUrl", return_value=True) as open_url:
                    QTest.mouseClick(self.window.launch, Qt.MouseButton.LeftButton)
                self.assertEqual([call.args[0].toString() for call in open_url.call_args_list], [url])
        root = self.controller.root
        self.show(steam.SteamGame("12", "Steam fixture", root, root, root, root))
        self.assertEqual(self.window.launch.text(), "Launch in Steam ↗")
        with patch.object(QDesktopServices, "openUrl", return_value=True) as open_url:
            QTest.mouseClick(self.window.launch, Qt.MouseButton.LeftButton)
        self.assertEqual(open_url.call_args.args[0].toString(), "steam://rungameid/12")

    def test_launch_waits_for_the_wrapper_and_a_safe_install(self):
        current = self.store_game(wrappers=(self.wrapper,))
        self.show(current)
        self.assertFalse(self.window.launch.isEnabled())  # Heroic would run a wrapper that is not there.
        self.assertIn("wrapper that is not installed", self.window.launch.toolTip())
        self.controller.installed = True
        for game, reason in ((self.store_game(), "Add the wrapper in Heroic, then rescan."),
                             (self.store_game(wrappers=(str(self.controller.root / "old/amdnr-linux/run.sh"),)),
                              "Replace the AMDNR wrapper in Heroic, then rescan."),
                             (self.store_game(store_runner="", wrappers=(self.wrapper,)),
                              "Heroic has no launch link for this game. Start it from Heroic."),
                             (self.store_game("lutris", wrappers=(self.wrapper,)),
                              "Lutris has no launch link for this game. Start it from Lutris.")):
            with self.subTest(reason=reason):
                self.show(game)
                self.assertFalse(self.window.launch.isEnabled())
                self.assertEqual(self.window.launch.toolTip(), reason)
        self.show(current)
        self.assertTrue(self.window.launch.isEnabled())
        # The same unsafe states that keep a Steam game from launching.
        for field, value in (("conflicts", ["Original backup is missing"]), ("missing", ["amdhip64.dll"]),
                             ("modified", ["amdhip64.dll"]), ("health_issues", ["The Python runtime is missing."]),
                             ("pending", True)):
            with self.subTest(field=field):
                previous = getattr(self.controller, field)
                setattr(self.controller, field, value)
                self.reselect()
                self.assertFalse(self.window.launch.isEnabled())
                setattr(self.controller, field, previous)
        self.reselect()
        self.assertTrue(self.window.launch.isEnabled())
        with patch.object(QDesktopServices, "openUrl", return_value=False):
            QTest.mouseClick(self.window.launch, Qt.MouseButton.LeftButton)
        self.assertEqual(self.window.activity.text(), "Could not open Heroic. Start the game from Heroic instead.")


if __name__ == "__main__":
    unittest.main()
