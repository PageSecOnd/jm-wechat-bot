import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from jmwxbot.util import peer_jm_profile, peer_workspace, protocol_client_version


class UtilTests(unittest.TestCase):
    def test_client_version(self):
        self.assertEqual(protocol_client_version("2.4.6"), (2 << 16) | (4 << 8) | 6)

    def test_workspace_isolation(self):
        root = Path("/tmp/x")
        self.assertNotEqual(peer_workspace(root, "a1", "p"), peer_workspace(root, "a2", "p"))
        self.assertNotEqual(peer_workspace(root, "a1", "p1"), peer_workspace(root, "a1", "p2"))
        self.assertNotEqual(peer_jm_profile(root, "a1", "p"), peer_jm_profile(root, "a2", "p"))
        self.assertNotEqual(peer_jm_profile(root, "a1", "p1"), peer_jm_profile(root, "a1", "p2"))


if __name__ == "__main__":
    unittest.main()
