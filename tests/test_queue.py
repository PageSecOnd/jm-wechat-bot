import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from jmwxbot.models import DownloadJob
from jmwxbot.runtime import PeerJobQueue


class QueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_remove_queued_job(self):
        q = PeerJobQueue()
        a = DownloadJob("1", "p", "c", "pdf")
        b = DownloadJob("2", "p", "c", "zip")
        await q.put(a)
        await q.put(b)
        removed = await q.remove(lambda j: j.jm_id == "1")
        self.assertEqual([x.jm_id for x in removed], ["1"])
        self.assertEqual([x.jm_id for x in await q.snapshot()], ["2"])


if __name__ == "__main__":
    unittest.main()
