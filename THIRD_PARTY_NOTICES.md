# Third-Party Notices

`jm-wechat-bot` is licensed under the MIT License. This repository also depends on or
references third-party open-source software. Each third-party component remains
subject to its own license; the MIT license for `jm-wechat-bot` does not replace those
licenses.

## Implementation references

### Tencent/openclaw-weixin

- Project: https://github.com/Tencent/openclaw-weixin
- License: MIT
- Copyright: Copyright (C) 2026 Tencent. All rights reserved.
- Use in jm-wechat-bot: the Weixin iLink HTTP/JSON and CDN interoperability layer was
  implemented with reference to publicly available protocol behavior and
  implementation details in `openclaw-weixin`, including message shapes,
  context-token handling, media upload behavior and AES wire-format details.
- License copy: `third_party/licenses/Tencent-openclaw-weixin-LICENSE`

`jm-wechat-bot` is not an official Tencent product and is not endorsed by Tencent.

### JMComic-Crawler-Python

- Project: https://github.com/hect0x7/JMComic-Crawler-Python
- License: MIT
- Copyright: Copyright (c) 2023 hect0x7
- Use in jm-wechat-bot: runtime dependency for JMComic API access, search, metadata,
  account operations and downloads.
- License copy: `third_party/licenses/JMComic-Crawler-Python-LICENSE`

`jm-wechat-bot` is an independent project and is not affiliated with JMComic or the
JMComic-Crawler-Python maintainers.

## Direct runtime dependencies

The source tree does not vendor the following Python packages. They are
installed by `pip` when the application or Docker image is built. License
copies are included here as a convenience for source and container
redistributors; always check the exact package version you redistribute.

| Package | Role | License / notice |
| --- | --- | --- |
| `httpx` | HTTP client | BSD-3-Clause (`third_party/licenses/httpx-LICENSE`) |
| `pycryptodome` | AES media encryption | Public Domain + BSD-2-Clause (`third_party/licenses/PyCryptodome-LICENSE`) |
| `qrcode` | Weixin binding QR output | BSD-3-Clause with upstream notices (`third_party/licenses/python-qrcode-LICENSE`) |
| `jmcomic` | JMComic integration | MIT (`third_party/licenses/JMComic-Crawler-Python-LICENSE`) |
| `img2pdf` | PDF generation | LGPL-3.0 (`third_party/licenses/img2pdf-LGPL-3.0`); GPL-3.0 text also included |
| `Pillow` | image validation/conversion | MIT-CMU (`third_party/licenses/Pillow-LICENSE`) |
| `aiohttp` | Web admin console | Apache-2.0 (`third_party/licenses/aiohttp-LICENSE`; full Apache-2.0 text in `third_party/licenses/Apache-2.0`) |

Transitive dependencies may carry additional licenses. If you distribute a
prebuilt Docker image or other bundle containing dependencies, review the
license metadata for the exact resolved dependency set in that artifact.

## Content and service trademarks

Names such as Weixin/WeChat, Tencent, JMComic and related marks belong to their
respective owners. References in this project are descriptive and do not imply
affiliation, sponsorship or endorsement.
