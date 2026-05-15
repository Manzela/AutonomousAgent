# Telegram Bot Setup

This is a one-time manual step. The bot acts as the agent's messaging interface.

## Steps

1. Open Telegram, search for `@BotFather`, start a chat
2. Send `/newbot`
3. Follow prompts: pick a display name (e.g. `Hermes Local`) and a username ending in `bot` (e.g. `your_hermes_local_bot`)
4. BotFather returns a token like `123456789:ABCdefGhIJklmnoPQRstuVwxyZ-abc12345678`
5. Send `/setprivacy` to BotFather, choose your bot, set to `Disable` (so the bot can read all messages, not just commands)
6. Find your own Telegram numeric user ID:
   - Search `@userinfobot`, start a chat, send any message
   - It replies with your numeric ID

## Save the values

Run from the project root:

```bash
cat > secrets/telegram.env <<EOF
TELEGRAM_BOT_TOKEN=<paste-token-here>
TELEGRAM_ALLOWED_USER_IDS=<your-numeric-id>
EOF

sops -e secrets/telegram.env > secrets/telegram.env.sops
rm secrets/telegram.env
```

## Update `config/limits.yaml`

Open `config/limits.yaml`, find `notify_channels.telegram_chat_id`, set it to your numeric user ID:

```yaml
notify_channels:
  telegram_chat_id: <your-numeric-id>
  ...
```

Re-run `python -m lib.limits_validator config/limits.yaml` to confirm it still validates.

## Verify the bot is reachable

```bash
TOKEN=$(sops -d secrets/telegram.env.sops | grep TELEGRAM_BOT_TOKEN | cut -d= -f2)
curl -fsS "https://api.telegram.org/bot${TOKEN}/getMe" | jq .
```

Expected: JSON describing your bot (name, id, username).

## Troubleshooting

- `401 Unauthorized` -> bad token, regenerate via BotFather `/token`
- Bot doesn't respond -> make sure you sent it `/start` first; bots can't message you until you initiate
