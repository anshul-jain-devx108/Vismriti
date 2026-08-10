# Slack setup

Vismriti's Slack interface is where the human-in-the-loop approval actually
happens. The agent posts the erasure plan into a channel, renders one approve
and reject control per planned action, and only executes what a person clicks
through. This document covers creating the app, wiring the endpoints, and
verifying the loop.

Slack is opt-in. With `SLACK_ENABLED=false` the Slack module is never
imported, so you can run and demo everything else without a workspace.

---

## 1. Create the Slack app

1. Go to <https://api.slack.com/apps> and choose **Create New App**, then
   **From scratch**.
2. Name it `Vismriti` and pick the workspace you want to test in.

### Bot token scopes

Under **OAuth & Permissions**, add these bot token scopes:

| Scope | Why |
|---|---|
| `app_mentions:read` | Receive `@vismriti erase ...` mentions. |
| `chat:write` | Post plans, approval cards, and results. |
| `chat:write.public` | Post into channels the bot has not been invited to. |
| `commands` | Optional, only if you add a slash command. |
| `im:history`, `im:read`, `im:write` | Direct-message usage. |
| `files:write` | Attach the generated Markdown report. |

Install the app to the workspace. Copy the **Bot User OAuth Token**; it starts
with `xoxb-`.

### Signing secret

Under **Basic Information**, copy the **Signing Secret**. Vismriti uses it to
verify that inbound requests really came from Slack.

---

## 2. Configure Vismriti

In `.env`:

```bash
SLACK_ENABLED=true
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_SIGNING_SECRET=your-signing-secret
SLACK_REPLY_TO_MENTIONS_ONLY=true
```

Both secrets must be non-empty. If `SLACK_ENABLED=true` and either is blank,
the service raises at startup rather than booting a half-wired bot that fails
silently on the first message.

`SLACK_REPLY_TO_MENTIONS_ONLY=true` keeps the bot quiet in busy channels. Set
it to `false` only in a dedicated channel.

---

## 3. Expose the service

Slack needs a public HTTPS URL. Start Vismriti:

```bash
./.venv/bin/python run.py --no-reload
```

For local development, tunnel it:

```bash
ngrok http 7777          # or whatever AGENTOS_PORT you set
```

Two endpoints get mounted when Slack is enabled:

| Endpoint | Slack setting |
|---|---|
| `POST /slack/events` | Event Subscriptions, Request URL |
| `POST /slack/interactions` | Interactivity & Shortcuts, Request URL |

### Event subscriptions

Under **Event Subscriptions**, enable events and set the Request URL to
`https://<your-host>/slack/events`. Slack sends a one-off `url_verification`
challenge; the service answers it automatically, and the field turns green.

Subscribe to these bot events:

- `app_mention`
- `message.im`

### Interactivity

Under **Interactivity & Shortcuts**, turn interactivity on and set the Request
URL to `https://<your-host>/slack/interactions`. This is the callback that
carries approve and reject clicks. Without it the buttons render but do
nothing.

Reinstall the app after changing scopes or events.

---

## 4. Verify the loop

Invite the bot to a channel and send:

```
@vismriti Show me every PII-tagged column DataHub knows about.
```

That exercises a read-only tool, so it should answer without any approval
prompt. Then:

```
@vismriti erase priya.sharma@example.com
```

Expected sequence:

1. The bot acknowledges and streams its progress while it walks lineage.
2. It posts the plan: one row per affected asset, with the action and the
   reason for it.
3. For each destructive action it renders approve and reject controls. Nothing
   has run at this point.
4. You approve some and reject others. Each approval triggers exactly one
   action, and rejections are recorded as rejections rather than dropped.
5. After the last decision, the bot writes the audit trail and replies with the
   outcome, including anything that failed and why.

Residual-risk assets are deliberately not approvable from Slack. They need a
human decision about delete versus anonymize, which is a policy question, not a
button click.

---

## 5. Safety notes

- Approval state is persisted through `AGENTOS_DB_URL`, and the runtime
  checkpoints at tool boundaries, so a restart mid-approval does not lose the
  plan. A DPO who comes back two hours later picks up where they left off.
- `ERASURE_AGENT_DRY_RUN=true` still applies. Approving an action in Slack
  while dry-run is on generates the SQL and records the approval without
  committing anything. Turn dry-run off deliberately, not by accident.
- The signing secret is the only thing standing between your workspace and
  anyone who can reach the endpoint. Treat it like a production credential and
  rotate it if it leaks.

---

## Troubleshooting

**Request URL will not verify.** The service is not reachable from the public
internet, or it is not running. Curl the URL yourself first.

**Bot sees nothing in channel.** It was not invited, or `app_mention` is not
subscribed, or `SLACK_REPLY_TO_MENTIONS_ONLY=true` and you did not mention it.

**Buttons render but clicking does nothing.** The Interactivity Request URL is
missing or wrong. That is a separate setting from Event Subscriptions.

**`RuntimeError` at boot naming the Slack env vars.** Exactly what it says:
`SLACK_ENABLED=true` with one of the secrets empty.

**Signature verification failures.** The signing secret does not match, or a
proxy is rewriting the request body. Slack signs the raw body, so anything that
re-encodes it in transit breaks verification.
