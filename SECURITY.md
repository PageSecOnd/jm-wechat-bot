# Security Policy

## Sensitive data

Never include the following in a GitHub issue, discussion, log paste, commit or
screenshot:

- Weixin/iLink bot tokens or account secrets
- `context_token` values
- JM `AVS` cookies or other authenticated cookies
- JM passwords
- `.env`, `data/state.sqlite3`, `/data/jm_profiles/`, or other runtime state

The repository `.gitignore` excludes the normal local locations for these
values, but you should still inspect `git diff --cached` before every public
push.

## Reporting a vulnerability

If you discover a vulnerability that could expose another user's JM session,
Weixin credentials, admin-console token, or private runtime data, do not post
working credentials or private data in a public issue. Contact the repository
maintainer privately if a private reporting channel is available.
