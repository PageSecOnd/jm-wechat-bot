from __future__ import annotations


def md_safe(value: object) -> str:
    """Neutralize Markdown control characters in untrusted/external text."""
    text = str(value if value is not None else "")
    return (
        text.replace("<", "＜")
        .replace("`", "ˋ")
        .replace("*", "＊")
        .replace("_", "＿")
        .replace("~", "～")
    )


def md_code(value: object) -> str:
    text = str(value if value is not None else "").replace("`", "ˋ").replace("\n", " ")
    return f"`{text}`"


def md_bold(value: object) -> str:
    return f"**{md_safe(value)}**"


def md_code_block(value: object) -> str:
    text = str(value if value is not None else "").replace("```", "'''" )
    return f"```\n{text}\n```"
