# jm-wechat-bot

一个不依赖 OpenClaw、直接适配微信 iLink Bot HTTP JSON 协议的轻量机器人。收到 JM ID 后，先发送封面和本子详情，再下载/导出文件并通过微信 CDN 回传。

> 仅用于你有权访问、下载和传输的内容；请遵守内容源条款、版权要求及当地法律。

## 功能

- 无 AI / 无 LLM / 无 Token 消耗，全部为固定规则。
- 多微信账号绑定；`jmwxbot run` 并发运行全部绑定。
- 强隔离：`account_id + peer_id` 为隔离边界，每个联系人独立队列、历史和文件缓存。
- 收到 JM ID 后按“封面 → 详情 → 下载/导出 → 文件”回复。
- 封面发送前使用 Pillow 完整解码校验并统一重编码为标准 RGB JPEG（最长边 ≤ 2048px）；旧的损坏/非标准封面缓存会自动修复或重新下载，降低微信显示“请稍后再试”的概率。
- 本子详情采用紧凑纯文本排版，不堆 Emoji。
- `/search` 站内搜索：默认按爱心数量排序，支持爱心 / 阅读量 / 最新 / 页数排序，并为当前页结果补充阅读量、爱心、评论、页数、章节、作者等详情。
- `/rank` 日/周/月排行榜。
- `/category` 分类浏览。
- `/comments` 本子评论和回评。
- `/login` 直接登录 JM；保存登录响应产生的完整 Cookie 集与认证 API 域名，不保存密码；每个微信联系人独立登录态。认证请求固定走该域名，避免 Cookie 跨域后随机 401。
- 成功 `/login` 后自动启用每日签到；默认每天北京时间 08:00 后检查，失败最多重试 3 次/天。
- `/daily` 可手动立即检查/执行今日签到。后台自动签到不依赖微信 `context_token`，也不会主动刷通知。
- `/fav` 浏览自己的 JM 收藏夹，`/fav-add` / `/collect` 将本子加入收藏。
- PDF / ZIP / 长图 PNG 三种导出格式。
- 不主动发送实时下载百分比；`/status` 可按需查看当前阶段和页数进度。
- `/cancel` 取消当前任务或指定 JM ID，排队任务也可移除。
- 重复请求合并：同一用户、同一 JM ID、同一格式不会重复入队。
- `/history` 查看最近成功、失败、取消或超大文件任务。
- `/cache` 查看当前联系人的隔离缓存占用。
- 自动缓存清理：TTL、缓存总量、磁盘剩余空间三层约束。
- 文件大小保护：超过配置上限时保留缓存，但不上传微信。
- 大文件上传采用流式 AES-128-ECB，不一次性把整个文件读入内存。
- 机器人回复使用微信/iLink 当前可用的 Markdown 子集增强可读性：粗体、行内代码、代码框和分隔线；不使用中文斜体。
- 新增只读 Web 管理控制台：查看绑定微信、联系人使用量、JM 登录/签到状态、最近任务/异常、实时队列、缓存与磁盘占用；不会显示 bot token、AVS、context token 或密码。

## 本子详情排版

```text
**JM123456｜《标题》**

作者：xxx
页数：186 · 章节：3
标签：xxx / xxx / xxx
浏览：12.3万 · 喜欢：8421 · 评论：126
作品：xxx
角色：xxx
上架：2026-01-01 · 更新：2026-01-02

**简介**
xxxxxxxxxxxxxxxx
```

## 微信命令

直接发送：

```text
123456
JM123456
jm 123456
#123456
```

以上默认等价于：

```text
/pdf 123456
```

完整命令：

```text
/pdf 123456                  导出 PDF
/zip 123456                  导出 ZIP
/long 123456                 导出长图 PNG

/search 关键词                         搜索第一页，默认按爱心数量排序
/search 关键词 --sort views            按阅读量排序
/search 关键词 --sort latest           按最新排序
/search 关键词 --sort pages            按页数排序
/search 关键词 --sort likes --page 2   按爱心排序并查看第 2 页

/rank                         周排行第一页
/rank day                     日排行
/rank week --page 2           周排行第 2 页
/rank month                   月排行

/category                     查看支持的分类
/category 同人                浏览同人分类
/category 韩漫 --page 2       浏览韩漫第 2 页

/comments 123456              查看本子评论
/comments 123456 --page 2     查看评论第 2 页

/login JM用户名 JM密码         登录自己的 JM 账号，并自动启用每日签到
/daily                        立即检查/执行今日签到
/logout                       清除本机保存的 JM 登录态并停止自动签到
/fav                          查看全部收藏第一页
/fav 7                        查看收藏夹 ID=7
/fav 7 --page 2               查看收藏夹 ID=7 第 2 页
/fav-add 123456               将 JM123456 加入收藏
/collect 123456               /fav-add 的别名

/status                       当前任务、按需进度和等待队列
/cancel                       取消当前任务；空闲时取消队首任务
/cancel 123456                取消当前或排队中的指定 JM ID
/history                      最近 10 条任务
/history 20                   最近 20 条任务（最多 20）
/cache                        当前联系人的缓存占用
/profile                      查看自己的 JM 登录状态和配置路径
/help                         帮助
```

当前分类别名：

```text
全部 / 同人 / 单本 / 短篇 / 其他 / 韩漫 / 美漫 / Cosplay / 3D / 英文
```

## 微信 Markdown 排版

微信 iLink 当前不是完整 CommonMark。本项目只使用已经确认能保留的语法：**粗体**、`行内代码`、三反引号代码框和 `---` 分隔线。

例如机器人可能回复：

````text
**JM 登录成功**

账号：`alice`
UID：`9988`

```
/data/jm_profiles/xxx/yyy.yml
```

---
````

腾讯当前过滤器会移除包裹中文内容的斜体标记，因此本项目不对中文使用 `*斜体*`，避免用户看到失效或不一致的排版。

## 下载状态

机器人不会主动发送 `10% / 20% / 30% ...` 这类进度消息。

如果想看当前状态，主动发送：

```text
/status
```

可能返回：

```text
机器人在线。
当前：JM123456 · PDF · 下载中 73/186（39%） · 21s
等待：JM654321/ZIP，JM777777/PDF
```

因此进度统计仍然存在，但只在用户查询时展示，不刷屏。

## 搜索、排行和分类

搜索结果会显示 JM ID、标题、作者、阅读量、爱心数、评论数、页数、章节数和标签。搜索默认按爱心数量排序；可通过 `--sort likes|views|latest|pages` 切换排序，并用 `--page N` 翻页。排行和分类保持简洁列表。随后直接发送对应 JM ID 即可加入下载队列。

`jmcomic 2.7.5` 当前提供 `search_site`、`day_ranking`、`week_ranking`、`month_ranking` 和 `categories_filter`，本项目直接调用这些公开接口。

## 评论

```text
/comments 123456
/comments 123456 --page 2
```

机器人显示主评论、有限数量的回评、点赞数、时间和剧透标记。消息会限制长度，避免超过微信文本消息的实用长度。

## JM 登录与收藏

登录态继续按 `account_id + peer_id` 隔离。每个微信联系人都有自己的 AVS 配置，因此 A 用户登录 JM 后，B 用户不会获得 A 的收藏夹权限。

推荐直接在微信里登录：

```text
/login JM用户名 JM密码
```

成功后机器人会把 JM 登录响应中的 `AVS` 写入该用户自己的：

```text
/data/jm_profiles/<account-hash>/<peer-hash>.yml
```

配置文件权限会尽量设置为 `0600`。**密码不会写入 SQLite、YAML 或任务历史**；只用于当前登录请求。不过 `/login ...` 这条消息本身仍会存在于微信聊天记录，因此不要在群聊或不可信设备上使用。

登录后可使用：

```text
/fav
/fav 7
/fav 7 --page 2
/fav-add 123456
/collect 123456
```

`/fav-add` 使用 `jmcomic 2.7.5` 当前公开的 `add_favorite_album()`。移动端 API 只能加入默认收藏，当前库没有公开稳定的“取消收藏”方法，因此本项目不会猜测私有删除接口。

查看登录状态：

```text
/profile
```

退出本机登录态：

```text
/logout
```

`/logout` 只删除本机器人保存的该用户 AVS，不会删除 JM 服务器上的收藏，同时会停止该用户的自动签到。原先手工配置 AVS 的方式仍然兼容；如果对应 profile 已存在，`/fav` 仍可直接使用。

### 每日自动签到

成功执行一次 `/login JM用户名 JM密码` 后，机器人会保存签到所需的 JM UID，并自动纳入每日签到。默认策略：

- 每天北京时间 08:00 以后开始检查。
- 后台每 30 分钟巡检一次，但同一个用户当天签到成功或确认已签到后不会再次请求。
- 当天失败最多重试 3 次。
- 自动签到只写日志/数据库状态，不主动发送微信消息，因此不会依赖可能过期的 `context_token`。
- `/daily` 可以随时手动测试签到；`/profile` 可以查看最近一次签到日期与结果。
- `/logout` 会删除该微信用户的 JM 状态，因此自然停止自动签到。

从 v0.4.0 升级时，如果用户以前只保存了 AVS，机器人并不知道 JM UID。该用户需要重新执行一次 `/login 用户名 密码`；之后每天自动签到无需再输入密码。

## 多格式导出

当前使用 `jmcomic` Feature 机制：

- `Feature.export_pdf`
- `Feature.export_zip`
- `Feature.export_long_img`

分别缓存到：

```text
<peer workspace>/pdf/JM123.pdf
<peer workspace>/zip/JM123.zip
<peer workspace>/long/JM123.png
```

同一 JM ID 的不同格式视为不同任务；同一格式的重复请求会合并。

## 多用户隔离

```text
data/
├── state.sqlite3
└── workspaces/
    ├── <hash(account A)>/
    │   ├── <hash(peer 1)>/
    │   │   ├── cover/JM123.jpg
    │   │   ├── pdf/JM123.pdf
    │   │   ├── zip/JM123.zip
    │   │   └── long/JM123.png
    │   └── <hash(peer 2)>/...
    └── <hash(account B)>/...
```

SQLite 中联系人 context、消息判重和任务历史也包含 `account_id` 与 `peer_id`。因此不同微信账号和不同联系人不会串队列、串文件或串回复。

## 任务历史

状态包括：

```text
sent       成功发送
failed     下载/导出/发送失败
cancelled  用户取消
too_large  文件生成成功，但超过发送上限
```

例如：

```text
最近 3 条任务：
JM123456 · PDF · 成功 · 86.2 MB · 42.8s｜标题
JM654321 · ZIP · 已取消｜标题
JM777777 · 长图 · 文件过大 · 612.4 MB · 55.1s｜标题
```

## 缓存策略

默认：

- 文件 7 天未使用后可清理。
- 所有 workspace 总缓存上限 20 GB。
- VPS 磁盘剩余空间低于 20% 时，从最旧缓存开始删除。
- 每 6 小时执行一次清理。
- 启动时也会清理一次。
- 最近 1 小时有写入/访问的文件在周期清理时有 grace period 保护。
- 命中缓存会 `touch` 文件，因此常用文件 TTL 会续期。

手动清理：

```bash
jmwxbot clean-cache
```

Docker：

```bash
docker compose run --rm bot clean-cache
```

## 文件大小保护

默认：

```text
JMWXBOT_MAX_SEND_MB=500
```

这是本程序自己的保护阈值，不代表微信 iLink 官方公布了固定 500 MB 上限。超过阈值时不上传微信，但已经生成的文件仍保留在该联系人的独立缓存中。设置为 `0` 可关闭本程序限制。

## Web 管理控制台

只读管理控制台。默认未设置管理 token 时完全禁用。

在项目目录创建或编辑 `.env`：

```text
JMWXBOT_ADMIN_TOKEN=请改成一段足够长的随机字符串
```

然后重建并启动：

```bash
docker compose down
docker compose build --no-cache
docker compose up -d
```

Compose 默认只把控制台暴露到 VPS 本机：

```text
127.0.0.1:8787
```

推荐从自己的电脑建立 SSH 隧道：

```bash
ssh -L 8787:127.0.0.1:8787 user@你的VPS
```

然后浏览器访问 `http://127.0.0.1:8787`，输入 `JMWXBOT_ADMIN_TOKEN` 登录。若你自行配置 HTTPS 反向代理，可把 `JMWXBOT_ADMIN_SECURE_COOKIE=true`。

控制台总览包括：

- 绑定微信账号数量、备注、`account_id`、Weixin `user_id`。
- 每个绑定微信下的已交互联系人数量、已验证 JM 登录人数。
- 7 天 / 累计命令量、搜索量、成功/失败下载数、累计发送字节。
- 实时 Active / Queue、当前下载阶段和页数进度。
- 每个联系人对应的 JM 用户名、UID、最近签到状态。
- 最近任务、最近异常、缓存占用和磁盘剩余空间。

命令统计从 v0.6.0 开始记录，只保存命令类型，不保存搜索关键词、登录密码或原始聊天文本。微信 iLink 当前没有稳定的好友昵称查询，因此未登录 JM 的联系人主要以 `peer_id` 标识。

## Linux VPS：Docker 部署

```bash
docker compose build
```

绑定微信账号：

```bash
docker compose run --rm bot login --name "微信1"
docker compose run --rm bot login --name "微信2"
```

查看绑定：

```bash
docker compose run --rm bot accounts
```

运行：

```bash
docker compose up -d
docker compose logs -f bot
```

停止：

```bash
docker compose down
```

`./data` 映射到容器 `/data`，重建镜像不会删除微信绑定、历史和缓存。

## 从旧版本升级

不用重新扫码。保留原来的 `data/`：

```bash
docker compose down
docker compose build --no-cache
docker compose up -d
docker compose logs -f bot
```

数据库结构向后兼容；旧账号、token、cursor、peer context、历史和已下载文件都会保留。

从旧版本升级时，微信 Bot 绑定、历史记录和下载缓存可以直接保留。JM 登录态方面：v0.5.2 或更早的登录态会因串号安全修复被标记为未验证；v0.6.1 或更早的 AVS-only profile 又缺少认证域名。升级到 v1.0.0 后，请每位已经登录 JM 的微信用户重新执行一次 `/login 用户名 密码`，无需先执行 `/profile` 或 `/logout`。

## 主要环境变量

```text
JMWXBOT_DATA_DIR=/data
JMWXBOT_DOWNLOAD_CONCURRENCY=2
JMWXBOT_SEARCH_LIMIT=8
JMWXBOT_MAX_SEND_MB=500
JMWXBOT_CACHE_TTL_DAYS=7
JMWXBOT_CACHE_MAX_GB=20
JMWXBOT_CACHE_MIN_FREE_PERCENT=20
JMWXBOT_CACHE_CLEANUP_INTERVAL_HOURS=6
JMWXBOT_DAILY_SIGNIN_HOUR=8
JMWXBOT_DAILY_SIGNIN_CHECK_INTERVAL_MINUTES=30
JMWXBOT_DAILY_SIGNIN_MAX_ATTEMPTS=3
JMWXBOT_ADMIN_TOKEN=
JMWXBOT_ADMIN_HOST=0.0.0.0
JMWXBOT_ADMIN_PORT=8787
JMWXBOT_ADMIN_TIMEZONE=Asia/Shanghai
JMWXBOT_ADMIN_SECURE_COOKIE=false
JMWXBOT_JM_OPTION_FILE=/config/jmcomic.yml
```

## Python / venv

要求 Python 3.12+：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install .

jmwxbot login --name "微信1"
jmwxbot run
```


## 开源许可与第三方项目

本项目主体代码采用 [MIT License](LICENSE)。第三方依赖和参考实现仍受各自许可证约束，完整说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 与 `third_party/licenses/`。

特别说明：

- 微信 iLink/CDN 兼容层参考了 Tencent `openclaw-weixin` 的公开实现与协议行为；该项目采用 MIT License。
- JMComic 功能通过 `JMComic-Crawler-Python` 实现；该项目采用 MIT License。
- `img2pdf` 使用 LGPL-3.0。源码仓库并未把该库源码合并进 jm-wechat-bot；构建/分发包含依赖的 Docker 镜像时仍应遵守相应第三方许可义务。
- `jm-wechat-bot` 与 Tencent、Weixin/WeChat、JMComic 及上述第三方项目均无官方隶属或背书关系。

## 发布到 GitHub

这个源码包已包含 `.gitignore`、MIT `LICENSE`、第三方声明和 GitHub Actions 测试。创建空仓库后可直接执行：

```bash
git init
git add .
git commit -m "Initial release: v1.0.0"
git branch -M main
git remote add origin git@github.com:YOUR_NAME/jm-wechat-bot.git
git push -u origin main
```

建议 GitHub About：

```text
Description: A self-hosted WeChat bot for JMComic with search, downloads, account isolation, daily check-in, and a web admin dashboard.
Topics: python, wechat, weixin, jmcomic, bot, self-hosted, docker, aiohttp, sqlite, manga-downloader
```

公开 push 前建议最后执行一次：

```bash
git status --ignored
git diff --cached
```

确认 `.env`、`data/`、SQLite、JM Cookie/profile 和真实微信凭据没有进入 staged files。
