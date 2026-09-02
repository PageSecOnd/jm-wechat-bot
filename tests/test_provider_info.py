import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from jmwxbot.provider import JmComicProvider, ProviderError
from jmwxbot.settings import Settings


class FakeAlbum:
    id = "123"
    name = "标题"
    authors = ["A", "B"]
    works = ["W"]
    actors = ["C"]
    tags = ["T1", "T2"]
    page_count = 99
    episode_list = [("1", "1", "x"), ("2", "2", "y")]
    pub_date = "2026-01-01"
    update_date = "2026-01-02"
    views = "10K"
    likes = "2K"
    comment_count = 3
    description = "desc"


class FakeSearchPage:
    total = 22
    page_count = 2
    page_number = 1

    def iter_id_title_tag(self):
        yield "123", "标题一", ["T1", "T2"]
        yield "456", "标题二", ["T3"]


class FakeFavoritePage:
    page_size = 20

    def __init__(self, ids=None, page_number=1, total=None):
        self.ids = list(ids or ["123", "456"])
        self.page_number = page_number
        self.total = len(self.ids) if total is None else total
        self.page_count = max(1, (self.total + self.page_size - 1) // self.page_size)

    def iter_id_title_tag(self):
        start = (self.page_number - 1) * self.page_size
        end = start + self.page_size
        for aid in self.ids[start:end]:
            yield str(aid), f"收藏{aid}", ["T"]

    def iter_folder_id_name(self):
        yield "0", "全部"
        yield "7", "收藏七"


class FakeComment:
    def __init__(self, cid="c1", author="昵称", content="内容", replies=None):
        self.comment_id = cid
        self.nickname = author
        self.username = "user"
        self.content = content
        self.likes = 5
        self.created_at = "2026-01-01"
        self.is_spoiler = False
        self.replies = replies or []


class FakeCommentPage:
    total = 11
    page_count = 2
    page_number = 1

    def __iter__(self):
        yield FakeComment(replies=[FakeComment("c2", "回复者", "回复")])


class FakeLoginResp:
    res_data = {
        "s": "AVS-TEST-VALUE",
        "uid": "9988",
        "username": "alice",
        "fname": "Alice",
        "level_name": "Lv.7",
        "album_favorites": 42,
        "coin": 88,
    }


class FakeClient:
    def __init__(self, domain_list=None, initial_cookies=None):
        self.favorite_ids = ["123", "456"]
        self.domain_list = list(domain_list or ["api-a.example", "api-b.example", "api-c.example"])
        self._cookies = dict(initial_cookies or {})

    def get_domain_list(self):
        return list(self.domain_list)

    def get_meta_data(self, key=None, default=None):
        data = {"cookies": self._cookies}
        if key is None:
            return data
        return data.get(key, default)

    def login(self, username, password):
        self.login_username = username
        self.login_password = password
        domain = self.domain_list[0] if self.domain_list else "api-a.example"
        if username == "domain-retry" and domain == "api-a.example":
            raise RuntimeError('{"code":401,"data":[],"errorMsg":"無效的用戶名和/或密碼！"}')
        if username == "mismatch-request":
            data = {
                "s": "AVS-WRONG-ACCOUNT",
                "uid": "9988",
                "username": "alice",
            }
        elif username == "alice":
            data = dict(FakeLoginResp.res_data)
        else:
            actual_username = "domain-retry" if username == "domain-retry" else username
            data = {
                "s": f"AVS-{actual_username.upper()}",
                "uid": "7788" if actual_username in {"bob", "domain-retry"} else "6677",
                "username": actual_username,
                "fname": actual_username.title(),
                "level_name": "Lv.1",
                "album_favorites": 0,
                "coin": 0,
            }
        # Mirror upstream JmApiClient.login(): response cookies + AVS replace the
        # bootstrap cookie jar.
        self._cookies = {
            "session": f"SESSION-{domain}",
            "device": f"DEVICE-{domain}",
            "AVS": str(data["s"]),
        }
        return types.SimpleNamespace(res_data=data)

    def get_album_detail(self, jm_id):
        self.jm_id = jm_id
        return FakeAlbum()

    def download_album_cover(self, jm_id, path):
        self.jm_id = jm_id
        # Deliberately save PNG bytes to a .jpg path. fetch_cover() must
        # normalize the source before it is sent to WeChat.
        from PIL import Image
        Image.new("RGBA", (3000, 1200), (255, 0, 0, 128)).save(path, format="PNG")

    def search_site(self, search_query, page=1, order_by=None):
        self.query = search_query
        self.page = page
        self.order_by = order_by
        return FakeSearchPage()

    def week_ranking(self, page):
        return FakeSearchPage()

    def day_ranking(self, page):
        return FakeSearchPage()

    def month_ranking(self, page):
        return FakeSearchPage()

    def categories_filter(self, page, time, category, order_by):
        self.category = category
        return FakeSearchPage()

    def favorite_folder(self, page=1, folder_id="0"):
        self.folder_id = folder_id
        return FakeFavoritePage(self.favorite_ids, page_number=page, total=len(self.favorite_ids))

    def album_pagination(self, jm_id, page=1):
        return FakeCommentPage()

    def req_api(self, url, get=True, require_success=True, **kwargs):
        if url == "/daily":
            self.daily_uid = (kwargs.get("params") or {}).get("user_id")
            return types.SimpleNamespace(res_data={
                "daily_id": 67,
                "three_days_coin": "150",
                "three_days_exp": "150",
                "seven_days_coin": "500",
                "seven_days_exp": "500",
                "event_name": "每日签到",
                "current_progress": "1/7",
            })
        if url == "/daily_chk":
            data = kwargs.get("data") or {}
            self.daily_uid = data.get("user_id")
            self.daily_id = data.get("daily_id")
            return types.SimpleNamespace(res_data={"msg": "签到成功"})
        if url == "/favorite":
            aid = str((kwargs.get("data") or {}).get("aid"))
            if aid in self.favorite_ids:
                self.favorite_ids.remove(aid)
            else:
                self.favorite_ids.insert(0, aid)
            # Deliberately omit `status`: this mirrors the response shape that
            # triggered the upstream KeyError in production.
            return types.SimpleNamespace(res_data={"msg": "ok"}, model_data={"msg": "ok"})
        raise AssertionError(f"unexpected req_api URL: {url}")


class FakeOption:
    def __init__(self, config=None):
        self.config = config or {}

    def new_jm_client(self, **kwargs):
        initial = (((self.config.get("client") or {}).get("postman") or {}).get("meta_data") or {}).get("cookies") or {}
        return FakeClient(domain_list=kwargs.get("domain_list"), initial_cookies=initial)


class FakeJmOption:
    construct_configs = []

    @staticmethod
    def default():
        return FakeOption()

    @classmethod
    def construct(cls, config):
        cls.construct_configs.append(config)
        return FakeOption(config)


class FakeConstants:
    TIME_ALL = "a"
    ORDER_BY_LATEST = "mr"
    ORDER_BY_VIEW = "mv"
    ORDER_BY_PICTURE = "mp"
    ORDER_BY_LIKE = "tf"


class ProviderInfoTests(unittest.TestCase):
    def fake_module(self):
        return types.SimpleNamespace(
            JmOption=FakeJmOption,
            JmMagicConstants=FakeConstants,
            create_option_by_file=lambda path: FakeOption(),
        )

    def test_fetch_info_maps_album_fields(self):
        settings = Settings(data_dir=Path("/tmp/jmwxbot-test"))
        with patch.dict(sys.modules, {"jmcomic": self.fake_module()}):
            info = asyncio.run(JmComicProvider(settings).fetch_info("123"))
        self.assertEqual(info.jm_id, "123")
        self.assertEqual(info.title, "标题")
        self.assertEqual(info.authors, ("A", "B"))
        self.assertEqual(info.page_count, 99)
        self.assertEqual(info.chapter_count, 2)
        self.assertEqual(info.tags, ("T1", "T2"))

    def test_search_maps_results(self):
        settings = Settings(data_dir=Path("/tmp/jmwxbot-test"))
        with patch.dict(sys.modules, {"jmcomic": self.fake_module()}):
            result = asyncio.run(JmComicProvider(settings).search("测试", page=1, limit=1))
        self.assertEqual(result.total, 22)
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].jm_id, "123")
        self.assertEqual(result.items[0].tags, ("T1", "T2"))
        self.assertEqual(result.sort_by, "likes")
        self.assertEqual(result.sort_label, "爱心")
        self.assertEqual(result.items[0].views, "10K")
        self.assertEqual(result.items[0].likes, "2K")
        self.assertEqual(result.items[0].comment_count, 3)
        self.assertEqual(result.items[0].page_count, 99)
        self.assertEqual(result.items[0].chapter_count, 2)

    def test_search_supports_sort_modes(self):
        settings = Settings(data_dir=Path("/tmp/jmwxbot-test"))
        with patch.dict(sys.modules, {"jmcomic": self.fake_module()}):
            provider = JmComicProvider(settings)
            for mode, label in (("likes", "爱心"), ("views", "阅读量"), ("latest", "最新"), ("pages", "页数")):
                result = asyncio.run(provider.search("测试", page=1, limit=1, sort_by=mode))
                self.assertEqual(result.sort_by, mode)
                self.assertEqual(result.sort_label, label)

    def test_rank_category_favorite_comment(self):
        settings = Settings(data_dir=Path("/tmp/jmwxbot-test"))
        with patch.dict(sys.modules, {"jmcomic": self.fake_module()}):
            provider = JmComicProvider(settings)
            rank = asyncio.run(provider.ranking("week", page=1, limit=1))
            self.assertEqual(rank.query, "周排行")
            category = asyncio.run(provider.category("同人", page=1, limit=1))
            self.assertEqual(category.query, "分类：同人")
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".yml", mode="w+", encoding="utf-8") as profile:
                profile.write("# jmwxbot-auth-profile: 2\nversion: '2.1'\n")
                profile.flush()
                fav = asyncio.run(provider.favorites("7", page=1, limit=1, option_file=Path(profile.name)))
            self.assertEqual(fav.folder_id, "7")
            self.assertEqual(fav.folders[1], ("7", "收藏七"))
            comments = asyncio.run(provider.comments("123", page=1))
            self.assertEqual(comments.total, 11)
            self.assertEqual(comments.items[0].author, "昵称")
            self.assertEqual(comments.items[0].replies[0].author, "回复者")

    def test_login_persists_full_cookie_jar_and_pinned_domain(self):
        settings = Settings(data_dir=Path("/tmp/jmwxbot-test"))
        import tempfile
        with tempfile.TemporaryDirectory() as td, patch.dict(sys.modules, {"jmcomic": self.fake_module()}):
            profile = Path(td) / "peer.yml"
            provider = JmComicProvider(settings)
            result = asyncio.run(provider.login("alice", "super-secret", option_file=profile))
            self.assertEqual(result.username, "alice")
            self.assertEqual(result.uid, "9988")
            self.assertEqual(result.favorite_count, 42)
            self.assertEqual(result.coin, 88)
            text = profile.read_text(encoding="utf-8")
            self.assertIn("# jmwxbot-auth-profile: 2", text)
            self.assertIn("AVS-TEST-VALUE", text)
            self.assertIn("SESSION-api-a.example", text)
            self.assertIn("DEVICE-api-a.example", text)
            self.assertIn('api-a.example', text)
            self.assertNotIn("super-secret", text)
            self.assertNotIn("jmwxbot_login", text)
            self.assertEqual(profile.stat().st_mode & 0o777, 0o600)
            self.assertTrue(asyncio.run(provider.add_favorite("789", option_file=profile)))


    def test_login_uses_unique_isolated_cookie_and_two_users_do_not_share_avs(self):
        settings = Settings(data_dir=Path("/tmp/jmwxbot-test"))
        import tempfile
        FakeJmOption.construct_configs.clear()
        with tempfile.TemporaryDirectory() as td, patch.dict(sys.modules, {"jmcomic": self.fake_module()}):
            provider = JmComicProvider(settings)
            alice_profile = Path(td) / "alice.yml"
            bob_profile = Path(td) / "bob.yml"
            alice = asyncio.run(provider.login("alice", "pw-a", option_file=alice_profile))
            bob = asyncio.run(provider.login("bob", "pw-b", option_file=bob_profile))

            self.assertEqual(alice.username, "alice")
            self.assertEqual(bob.username, "bob")
            self.assertNotEqual(alice.uid, bob.uid)
            self.assertIn("AVS-TEST-VALUE", alice_profile.read_text(encoding="utf-8"))
            self.assertIn("AVS-BOB", bob_profile.read_text(encoding="utf-8"))
            self.assertNotEqual(alice_profile.read_text(), bob_profile.read_text())

            # Each login performs an isolated domain probe plus an isolated
            # per-domain login attempt. Neither seed may contain AVS.
            cookies = [
                cfg["client"]["postman"]["meta_data"]["cookies"]
                for cfg in FakeJmOption.construct_configs
            ]
            self.assertGreaterEqual(len(cookies), 4)
            self.assertTrue(all("AVS" not in item for item in cookies))
            login_seeds = [x for x in cookies if "jmwxbot_login" in x]
            self.assertEqual(len(login_seeds), 2)
            self.assertNotEqual(login_seeds[0]["jmwxbot_login"], login_seeds[1]["jmwxbot_login"])

    def test_login_rejects_cross_account_response_without_overwriting_profile(self):
        settings = Settings(data_dir=Path("/tmp/jmwxbot-test"))
        import tempfile
        with tempfile.TemporaryDirectory() as td, patch.dict(sys.modules, {"jmcomic": self.fake_module()}):
            profile = Path(td) / "peer.yml"
            profile.write_text("KEEP-OLD-PROFILE", encoding="utf-8")
            provider = JmComicProvider(settings)
            with self.assertRaisesRegex(ProviderError, "安全检查失败"):
                asyncio.run(provider.login("mismatch-request", "pw", option_file=profile))
            self.assertEqual(profile.read_text(encoding="utf-8"), "KEEP-OLD-PROFILE")

    def test_login_retries_next_domain_after_node_401_and_pins_successful_domain(self):
        settings = Settings(data_dir=Path("/tmp/jmwxbot-test"))
        import tempfile
        FakeJmOption.construct_configs.clear()
        with tempfile.TemporaryDirectory() as td, patch.dict(sys.modules, {"jmcomic": self.fake_module()}):
            profile = Path(td) / "peer.yml"
            result = asyncio.run(JmComicProvider(settings).login("domain-retry", "pw", option_file=profile))
            self.assertEqual(result.username, "domain-retry")
            text = profile.read_text(encoding="utf-8")
            self.assertIn("api-b.example", text)
            self.assertNotIn("SESSION-api-a.example", text)
            self.assertIn("SESSION-api-b.example", text)

    def test_old_avs_only_profile_is_rejected_for_authenticated_calls(self):
        settings = Settings(data_dir=Path("/tmp/jmwxbot-test"))
        import tempfile
        with tempfile.TemporaryDirectory() as td, patch.dict(sys.modules, {"jmcomic": self.fake_module()}):
            profile = Path(td) / "peer.yml"
            profile.write_text("version: '2.1'\nclient:\n  postman:\n    meta_data:\n      cookies:\n        AVS: old\n", encoding="utf-8")
            provider = JmComicProvider(settings)
            with self.assertRaisesRegex(ProviderError, "v0.6.1"):
                asyncio.run(provider.favorites(option_file=profile))

    def test_daily_checkin_uses_saved_profile(self):
        settings = Settings(data_dir=Path("/tmp/jmwxbot-test"))
        import tempfile
        with tempfile.TemporaryDirectory() as td, patch.dict(sys.modules, {"jmcomic": self.fake_module()}):
            profile = Path(td) / "peer.yml"
            profile.write_text("# jmwxbot-auth-profile: 2\nversion: '2.1'\n", encoding="utf-8")
            provider = JmComicProvider(settings)
            result = asyncio.run(provider.daily_checkin("9988", option_file=profile))
            self.assertFalse(result.already_signed)
            self.assertEqual(result.message, "签到成功")
            self.assertEqual(result.status.daily_id, "67")
            self.assertEqual(result.status.seven_days_coin, "500")

    def test_add_favorite_is_idempotent_when_already_present(self):
        settings = Settings(data_dir=Path("/tmp/jmwxbot-test"))
        import tempfile
        with tempfile.TemporaryDirectory() as td, patch.dict(sys.modules, {"jmcomic": self.fake_module()}):
            profile = Path(td) / "peer.yml"
            profile.write_text("# jmwxbot-auth-profile: 2\nversion: '2.1'\n", encoding="utf-8")
            provider = JmComicProvider(settings)
            self.assertFalse(asyncio.run(provider.add_favorite("123", option_file=profile)))

    def test_favorites_uses_bot_level_eight_item_paging(self):
        settings = Settings(data_dir=Path("/tmp/jmwxbot-test"))
        import tempfile

        class ManyFavoritesClient(FakeClient):
            def __init__(self):
                self.favorite_ids = [str(1000 + i) for i in range(18)]

        class ManyFavoritesOption:
            def new_jm_client(self, **kwargs):
                return ManyFavoritesClient()

        with tempfile.NamedTemporaryFile(suffix=".yml", mode="w+", encoding="utf-8") as profile, patch.object(
            JmComicProvider, "_create_option", return_value=ManyFavoritesOption()
        ):
            profile.write("# jmwxbot-auth-profile: 2\nversion: '2.1'\n")
            profile.flush()
            provider = JmComicProvider(settings)
            p1 = asyncio.run(provider.favorites("0", page=1, limit=8, option_file=Path(profile.name)))
            p2 = asyncio.run(provider.favorites("0", page=2, limit=8, option_file=Path(profile.name)))
            p3 = asyncio.run(provider.favorites("0", page=3, limit=8, option_file=Path(profile.name)))
            self.assertEqual((p1.total, p1.page_count, len(p1.items)), (18, 3, 8))
            self.assertEqual((p2.total, p2.page_count, len(p2.items)), (18, 3, 8))
            self.assertEqual((p3.total, p3.page_count, len(p3.items)), (18, 3, 2))
            self.assertEqual(p2.items[0].jm_id, "1008")
            self.assertEqual(p3.items[0].jm_id, "1016")

    def test_favorites_bot_page_can_cross_upstream_twenty_item_boundary(self):
        settings = Settings(data_dir=Path("/tmp/jmwxbot-test"))
        import tempfile

        class ManyFavoritesClient(FakeClient):
            def __init__(self):
                self.favorite_ids = [str(2000 + i) for i in range(25)]

        class ManyFavoritesOption:
            def new_jm_client(self, **kwargs):
                return ManyFavoritesClient()

        with tempfile.NamedTemporaryFile(suffix=".yml", mode="w+", encoding="utf-8") as profile, patch.object(
            JmComicProvider, "_create_option", return_value=ManyFavoritesOption()
        ):
            profile.write("# jmwxbot-auth-profile: 2\nversion: '2.1'\n")
            profile.flush()
            provider = JmComicProvider(settings)
            p3 = asyncio.run(provider.favorites("0", page=3, limit=8, option_file=Path(profile.name)))
            self.assertEqual((p3.total, p3.page_count, len(p3.items)), (25, 4, 8))
            self.assertEqual([x.jm_id for x in p3.items], [str(2000 + i) for i in range(16, 24)])

    def test_add_favorite_requires_login_profile(self):
        settings = Settings(data_dir=Path("/tmp/jmwxbot-test"))
        with patch.dict(sys.modules, {"jmcomic": self.fake_module()}):
            with self.assertRaises(ProviderError):
                asyncio.run(JmComicProvider(settings).add_favorite("123", option_file=Path("/no/such/profile.yml")))

    def test_invalid_category(self):
        settings = Settings(data_dir=Path("/tmp/jmwxbot-test"))
        with patch.dict(sys.modules, {"jmcomic": self.fake_module()}):
            with self.assertRaises(ProviderError):
                asyncio.run(JmComicProvider(settings).category("不存在"))

    def test_fetch_cover_is_normalized_and_cached_in_workspace(self):
        settings = Settings(data_dir=Path("/tmp/jmwxbot-test"))
        import tempfile
        from PIL import Image
        with tempfile.TemporaryDirectory() as td, patch.dict(sys.modules, {"jmcomic": self.fake_module()}):
            workspace = Path(td) / "peer"
            provider = JmComicProvider(settings)
            cover = asyncio.run(provider.fetch_cover("123", workspace))
            self.assertEqual(cover, workspace / "cover" / "JM123.jpg")
            with Image.open(cover) as image:
                image.load()
                self.assertEqual(image.format, "JPEG")
                self.assertEqual(image.mode, "RGB")
                self.assertLessEqual(max(image.size), provider.COVER_MAX_EDGE)
            before = cover.stat().st_mtime_ns
            self.assertEqual(asyncio.run(provider.fetch_cover("123", workspace)), cover)
            self.assertGreaterEqual(cover.stat().st_mtime_ns, before)

    def test_fetch_cover_discards_corrupt_old_cache_and_redownloads(self):
        settings = Settings(data_dir=Path("/tmp/jmwxbot-test"))
        import tempfile
        from PIL import Image
        with tempfile.TemporaryDirectory() as td, patch.dict(sys.modules, {"jmcomic": self.fake_module()}):
            workspace = Path(td) / "peer"
            cover_dir = workspace / "cover"
            cover_dir.mkdir(parents=True)
            old = cover_dir / "JM123.jpg"
            old.write_bytes(b"<html>temporary CDN error</html>")

            provider = JmComicProvider(settings)
            cover = asyncio.run(provider.fetch_cover("123", workspace))
            self.assertNotIn(b"temporary CDN error", cover.read_bytes())
            with Image.open(cover) as image:
                image.load()
                self.assertEqual(image.format, "JPEG")
                self.assertEqual(image.mode, "RGB")

    def test_fetch_cover_repairs_valid_nonstandard_cached_image_without_jm_download(self):
        settings = Settings(data_dir=Path("/tmp/jmwxbot-test"))
        import tempfile
        from PIL import Image
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td) / "peer"
            cover_dir = workspace / "cover"
            cover_dir.mkdir(parents=True)
            old = cover_dir / "JM123.jpg"
            # Valid PNG in the legacy .jpg cache. It should be repaired locally,
            # so no jmcomic import/download is needed.
            Image.new("RGBA", (64, 64), (0, 255, 0, 120)).save(old, format="PNG")

            provider = JmComicProvider(settings)
            cover = asyncio.run(provider.fetch_cover("123", workspace))
            with Image.open(cover) as image:
                image.load()
                self.assertEqual(image.format, "JPEG")
                self.assertEqual(image.mode, "RGB")

    def test_cached_export_paths(self):
        provider = JmComicProvider(Settings(data_dir=Path("/tmp/x")))
        root = Path("/tmp/w")
        self.assertEqual(provider.cached_export(root, "123", "pdf"), root / "pdf" / "JM123.pdf")
        self.assertEqual(provider.cached_export(root, "123", "zip"), root / "zip" / "JM123.zip")
        self.assertEqual(provider.cached_export(root, "123", "long"), root / "long" / "JM123.png")


class ProviderExportTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_export_formats_and_internal_status_progress(self):
        import tempfile
        from types import SimpleNamespace

        class FakeDirRule:
            def __init__(self, rule, base_dir):
                self.rule = rule
                self.base_dir = base_dir

        class FakeAsyncDownloader:
            def __init__(self, option):
                self.option = option

            async def before_album(self, album):
                pass

            async def after_image(self, image, path):
                pass

            async def after_album(self, album):
                pass

        class FeatureObj:
            def __init__(self, kind, **kwargs):
                self.kind = kind
                self.kwargs = kwargs

        class FakeFeature:
            @staticmethod
            def export_pdf(**kwargs):
                return FeatureObj("pdf", **kwargs)

            @staticmethod
            def export_zip(**kwargs):
                return FeatureObj("zip", **kwargs)

            @staticmethod
            def export_long_img(**kwargs):
                return FeatureObj("long", **kwargs)

        class Manifest:
            def __init__(self, path):
                self.path = path

            def get_export_filepath_list(self, suffix):
                return [str(self.path)]

        async def fake_download_album_async(jm_id, option, downloader, extra):
            dler = downloader(option)
            album = SimpleNamespace(page_count=2)
            await dler.before_album(album)
            await dler.after_image(SimpleNamespace(), "1.jpg")
            await dler.after_image(SimpleNamespace(), "2.jpg")
            await dler.after_album(album)
            suffix = {"pdf": "pdf", "zip": "zip", "long": "png"}[extra.kind]
            out = Path(extra.kwargs[{"pdf": "pdf_dir", "zip": "zip_dir", "long": "img_dir"}[extra.kind]]) / f"JM{jm_id}.{suffix}"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"exported")
            return SimpleNamespace(manifest=Manifest(out), duration=1.25)

        fake_module = SimpleNamespace(
            JmOption=FakeJmOption,
            create_option_by_file=lambda path: FakeOption(),
            DirRule=FakeDirRule,
            Feature=FakeFeature,
            JmAsyncDownloader=FakeAsyncDownloader,
            download_album_async=fake_download_album_async,
        )

        with tempfile.TemporaryDirectory() as td, patch.dict(sys.modules, {"jmcomic": fake_module}):
            provider = JmComicProvider(Settings(data_dir=Path(td)))
            workspace = Path(td) / "workspace"
            for fmt, suffix in [("pdf", "pdf"), ("zip", "zip"), ("long", "png")]:
                events = []

                async def progress(stage, done, total):
                    events.append((stage, done, total))

                path, duration = await provider.fetch_export("123", workspace, fmt, progress=progress)
                self.assertEqual(path, workspace / fmt / f"JM123.{suffix}")
                self.assertEqual(path.read_bytes(), b"exported")
                self.assertEqual(duration, 1.25)
                self.assertIn(("downloading", 2, 2), events)
                self.assertTrue(any(x[0] == "exporting" for x in events))


if __name__ == "__main__":
    unittest.main()
