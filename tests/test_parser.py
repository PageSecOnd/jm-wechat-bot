import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from jmwxbot.parser import parse_command


class ParserTests(unittest.TestCase):
    def test_ids_default_to_pdf(self):
        for text in ["123456", "JM123456", "jm 123456", "#123456"]:
            cmd = parse_command(text)
            self.assertIsNotNone(cmd)
            self.assertEqual(cmd.kind, "download")
            self.assertEqual(cmd.value, "123456")
            self.assertEqual(cmd.arg, "pdf")

    def test_export_commands(self):
        self.assertEqual(parse_command("/pdf JM123").arg, "pdf")
        self.assertEqual(parse_command("/zip #123").arg, "zip")
        self.assertEqual(parse_command("/long 123").arg, "long")

    def test_search_cancel_history(self):
        cmd = parse_command("/search 甘雨 --page 2")
        self.assertEqual((cmd.kind, cmd.value, cmd.arg, cmd.option), ("search", "甘雨", "2", "likes"))
        cmd = parse_command("/search 甘雨 --sort views --page 3")
        self.assertEqual((cmd.value, cmd.arg, cmd.option), ("甘雨", "3", "views"))
        cmd = parse_command("/search 甘雨 --page 4 --sort 爱心")
        self.assertEqual((cmd.value, cmd.arg, cmd.option), ("甘雨", "4", "likes"))
        self.assertEqual(parse_command("/search 甘雨 --sort nonsense").kind, "search_sort_help")
        self.assertEqual(parse_command("/cancel").kind, "cancel")
        self.assertEqual(parse_command("/cancel JM123").value, "123")
        self.assertEqual(parse_command("/history 20").value, "20")

    def test_browse_commands(self):
        self.assertEqual((parse_command("/rank").kind, parse_command("/rank").value, parse_command("/rank").arg), ("rank", "week", "1"))
        self.assertEqual((parse_command("/rank 月 --page 3").value, parse_command("/rank 月 --page 3").arg), ("month", "3"))
        self.assertEqual((parse_command("/category 同人 --page 2").value, parse_command("/category 同人 --page 2").arg), ("同人", "2"))
        self.assertEqual(parse_command("/category").kind, "category")
        self.assertIsNone(parse_command("/category").value)
        self.assertEqual((parse_command("/comments JM123 --page 2").value, parse_command("/comments JM123 --page 2").arg), ("123", "2"))
        self.assertEqual((parse_command("/fav 7 --page 2").value, parse_command("/fav 7 --page 2").arg), ("7", "2"))
        self.assertEqual((parse_command("/fav").value, parse_command("/fav").arg), ("0", "1"))

    def test_login_and_favorite_commands(self):
        cmd = parse_command("/login alice s3cr3t")
        self.assertEqual((cmd.kind, cmd.value, cmd.arg), ("login", "alice", "s3cr3t"))
        cmd = parse_command("/login alice password with spaces")
        self.assertEqual((cmd.value, cmd.arg), ("alice", "password with spaces"))
        self.assertEqual(parse_command("/login").kind, "login")
        self.assertEqual(parse_command("/logout").kind, "logout")
        self.assertEqual(parse_command("/daily").kind, "daily")
        self.assertEqual(parse_command("签到").kind, "daily")
        self.assertIsNone(parse_command("/tasks"))
        self.assertIsNone(parse_command("/daily-auto on"))
        self.assertEqual((parse_command("/fav-add JM123").kind, parse_command("/fav-add JM123").value), ("fav_add", "123"))
        self.assertEqual((parse_command("/collect #456").kind, parse_command("/collect #456").value), ("fav_add", "456"))

    def test_commands(self):
        self.assertEqual(parse_command("/help").kind, "help")
        self.assertEqual(parse_command("状态").kind, "status")
        self.assertEqual(parse_command("缓存").kind, "cache")
        self.assertEqual(parse_command("/profile").kind, "profile")
        self.assertIsNone(parse_command("hello 123"))


if __name__ == "__main__":
    unittest.main()
