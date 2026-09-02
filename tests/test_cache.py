import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from jmwxbot.cache import cache_stats, cleanup_cache
from jmwxbot.settings import Settings


class CacheTests(unittest.TestCase):
    def test_ttl_cleanup(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            old = data / "workspaces" / "a" / "p" / "pdf" / "old.pdf"
            new = data / "workspaces" / "a" / "p" / "pdf" / "new.pdf"
            old.parent.mkdir(parents=True)
            old.write_bytes(b"old")
            new.write_bytes(b"new")
            past = time.time() - 10 * 86400
            os.utime(old, (past, past))
            settings = Settings(
                data_dir=data,
                cache_ttl_days=7,
                cache_max_gb=0,
                cache_min_free_percent=0,
            )
            result = cleanup_cache(settings, grace_seconds=0)
            self.assertEqual(result.removed_files, 1)
            self.assertFalse(old.exists())
            self.assertTrue(new.exists())
            self.assertEqual(cache_stats(data / "workspaces").files, 1)


if __name__ == "__main__":
    unittest.main()
