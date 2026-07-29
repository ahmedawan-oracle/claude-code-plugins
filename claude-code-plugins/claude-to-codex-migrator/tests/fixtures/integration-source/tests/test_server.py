import os
import unittest


class ServerTests(unittest.TestCase):
    def test_plugin_root_is_configured(self) -> None:
        self.assertIn("CLAUDE_PLUGIN_ROOT", os.environ)
