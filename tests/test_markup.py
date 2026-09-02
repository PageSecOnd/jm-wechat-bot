import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from jmwxbot.markup import md_bold, md_code, md_code_block, md_safe


class MarkupTests(unittest.TestCase):
    def test_external_markdown_is_neutralized(self):
        self.assertEqual(md_safe("a*b_c`d~e"), "a＊b＿cˋd～e")

    def test_helpers_emit_supported_constructs(self):
        self.assertEqual(md_bold("标题"), "**标题**")
        self.assertEqual(md_code("JM123"), "`JM123`")
        self.assertEqual(md_code_block("a\nb"), "```\na\nb\n```")


if __name__ == "__main__":
    unittest.main()
