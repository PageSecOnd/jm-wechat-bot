import base64
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from jmwxbot.ilink import ILinkClient, ITEM_IMAGE, UPLOAD_IMAGE


class ILinkImageSendTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_image_uses_native_image_item_and_cipher_size(self):
        client = object.__new__(ILinkClient)
        captured = {}
        key = bytes(range(16))

        async def fake_upload(to_user_id, file_path, *, media_type):
            self.assertEqual(to_user_id, "peer")
            self.assertEqual(media_type, UPLOAD_IMAGE)
            return {
                "download_param": "enc-param",
                "aes_key": key,
                "raw_size": 101,
                "cipher_size": 112,
            }

        async def fake_send_item(to_user_id, context_token, item):
            captured.update(to=to_user_id, context=context_token, item=item)

        client._upload_media = fake_upload
        client._send_item = fake_send_item

        with tempfile.TemporaryDirectory() as td:
            image = Path(td) / "cover.jpg"
            image.write_bytes(b"fake")
            await client.send_image("peer", "ctx", image)

        item = captured["item"]
        self.assertEqual(item["type"], ITEM_IMAGE)
        self.assertEqual(item["image_item"]["mid_size"], 112)
        self.assertEqual(item["image_item"]["media"]["encrypt_query_param"], "enc-param")
        self.assertEqual(
            base64.b64decode(item["image_item"]["media"]["aes_key"]),
            key.hex().encode("ascii"),
        )


if __name__ == "__main__":
    unittest.main()
