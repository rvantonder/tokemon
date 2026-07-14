# Agent Notes

## Tokemon credential updates

When asked to update a Codex bearer token in this repository, update Tokemon's
own config, not the Codex CLI config.

- Primary Codex account: `~/.config/tokemon/config.json` at
  `codex.bearer_token`.
- Extra Codex accounts, such as `Codex2`: `codex_accounts[]` entries in the
  same file.
- Tokemon stores the raw JWT only. If the user provides a value beginning with
  `Bearer `, strip that prefix before writing it because `app.py` adds
  `Authorization: Bearer ...` when making requests.
- Do not edit `~/.codex/auth.json` for Tokemon usage-display requests.
- After changing the config, restart the running app from
  `/Users/rvt/tokemon/dist/Tokemon.app` or the installed Tokemon app bundle.
