import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from jmwxbot.models import ComicInfo, CommentEntry, CommentResults, FavoriteResults, JmDailyResult, JmDailyStatus, SearchHit, SearchResults
from jmwxbot.runtime import format_browse_results, format_comic_info, format_comments, format_daily, format_favorites, format_search_results


class FormatInfoTests(unittest.TestCase):
    def test_compact_info_format(self):
        info = ComicInfo(
            jm_id="123456",
            title="测试标题",
            authors=("作者A", "作者B"),
            works=("作品A",),
            actors=("角色A",),
            tags=("标签1", "标签2"),
            page_count=88,
            chapter_count=3,
            pub_date="2026-01-01",
            update_date="2026-02-01",
            views="12K",
            likes="1K",
            comment_count=42,
            description="这是简介。",
        )
        text = format_comic_info(info)
        self.assertTrue(text.startswith("**JM123456｜《测试标题》**"))
        self.assertIn("作者：作者A / 作者B", text)
        self.assertIn("页数：88 · 章节：3", text)
        self.assertIn("标签：标签1 / 标签2", text)
        self.assertIn("浏览：12K · 喜欢：1K · 评论：42", text)
        self.assertIn("**简介**\n这是简介。", text)
        self.assertNotIn("📖", text)

    def test_daily_format_uses_markdown_without_cjk_italics(self):
        result = JmDailyResult(
            message="签到成功",
            already_signed=False,
            status=JmDailyStatus("67", "每日签到", "1/7", "150", "150", "500", "500"),
        )
        text = format_daily(result)
        self.assertTrue(text.startswith("**每日签到**"))
        self.assertIn("状态：签到成功", text)
        self.assertIn("7 日累计：金币 500 · 经验 500", text)
        self.assertNotIn("*状态", text)

    def test_message_is_bounded(self):
        info = ComicInfo(jm_id="1", title="x" * 3000)
        self.assertLessEqual(len(format_comic_info(info)), 1800)

    def test_search_and_browse_format(self):
        result = SearchResults(
            query="测试",
            page=1,
            total=2,
            page_count=1,
            items=(
                SearchHit("1", "标题一", ("A", "B"), ("作者甲",), "12K", "888", 33, 86, 2),
                SearchHit("2", "标题二", ()),
            ),
            sort_by="likes",
            sort_label="爱心",
        )
        text = format_search_results(result)
        self.assertIn("搜索：测试", text)
        self.assertIn("排序：爱心", text)
        self.assertIn("`JM1`｜标题一", text)
        self.assertIn("作者：作者甲", text)
        self.assertIn("阅读 12K · 爱心 888 · 评论 33", text)
        self.assertIn("86 页 · 2 章节", text)
        self.assertIn("--sort views", text)
        ranked = SearchResults("周排行", 1, 2, 1, result.items)
        self.assertIn("周排行", format_browse_results(ranked))

    def test_favorites_format(self):
        result = FavoriteResults("0", 1, 1, 1, (SearchHit("1", "收藏标题"),), (("0", "全部"), ("2", "稍后看")))
        text = format_favorites(result)
        self.assertIn("收藏夹 0", text)
        self.assertIn("`JM1`｜收藏标题", text)
        self.assertIn("`2`｜稍后看", text)

    def test_comments_format(self):
        result = CommentResults(
            "123", 1, 1, 1,
            (CommentEntry("c1", "用户", "好看", 7, "2026-01-01", False, (CommentEntry("c2", "回复者", "同意"),)),),
        )
        text = format_comments(result)
        self.assertIn("JM123 评论", text)
        self.assertIn("用户 · 7赞", text)
        self.assertIn("好看", text)
        self.assertIn("回复者", text)


if __name__ == "__main__":
    unittest.main()
