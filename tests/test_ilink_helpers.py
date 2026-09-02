import base64
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from jmwxbot.ilink import encode_image_aes_key, encode_media_aes_key


class ILinkHelperTests(unittest.TestCase):
    def test_file_aes_key_is_base64_of_hex_ascii(self):
        key = bytes(range(16))
        encoded = encode_media_aes_key(key)
        decoded = base64.b64decode(encoded)
        self.assertEqual(decoded, key.hex().encode("ascii"))
        self.assertEqual(bytes.fromhex(decoded.decode("ascii")), key)

    def test_image_aes_key_matches_current_tencent_hex_ascii_wire_format(self):
        key = bytes(range(16))
        encoded = encode_image_aes_key(key)
        self.assertEqual(base64.b64decode(encoded), key.hex().encode("ascii"))
        self.assertEqual(encoded, encode_media_aes_key(key))


if __name__ == "__main__":
    unittest.main()
