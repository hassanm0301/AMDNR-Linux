"""Release precedence shared by manifests, installation checks, and updates."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_launcher.versions import version_key


class VersionTests(unittest.TestCase):
    def test_standard_prerelease_precedence_and_numeric_identifiers(self):
        ordered = ['1.0.0-alpha', '1.0.0-alpha.1', '1.0.0-alpha.beta', '1.0.0-beta',
                   '1.0.0-beta.2', '1.0.0-beta.11', '1.0.0-rc.1', '1.0.0', '1.0.1-preview']
        keys = [version_key(value) for value in ordered]
        self.assertTrue(all(key is not None for key in keys))
        self.assertEqual(keys, sorted(keys))
        self.assertLess(version_key('0.2.2-preview'), version_key('0.2.2'))
        self.assertGreater(version_key('0.2.2-preview.10'), version_key('0.2.2-preview.2'))

    def test_malformed_and_noncanonical_versions_are_rejected(self):
        for value in (None, [], 'linux-v0.2.2', '01.2.3', '1.02.3', '1.2.03', '1.2',
                      '1.2.3-', '1.2.3-preview..1', '1.2.3-preview.01', '1.2.3+local',
                      '1.2.3-preview/path', '1.2.3\n', '1.2.3-π'):
            with self.subTest(value=value):
                self.assertIsNone(version_key(value))


if __name__ == '__main__':
    unittest.main()
