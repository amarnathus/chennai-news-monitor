# Chennai News Monitor

Automated news scraper for r/chennai and r/chennaicity. Runs on GitHub Actions every 15 minutes, filters posts through DeepSeek V4 Flash, and delivers newsworthy items to Telegram.

**Cost: ~$0.22/month** (essentially free).

## Setup

### 1. Create a GitHub repo

```bash
# Clone this template
git init chennai-news-monitor
cd chennai-news-monitor
# Add the files from this directory
git add .
git commit -m "Initial commit"
```

Push to a **public** GitHub repo (free unlimited Actions minutes).

### 2. Create a Telegram bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow the prompts
3. Copy the bot token (looks like `123456:ABC-DEF1234gh...`)
4. Message your new bot (just say "hi") so it can send you messages
5. Get your chat ID: send a message to your bot, then visit:
   ```
   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   ```
   Look for `"chat":{"id":350790755}` in the response

### 3. Set GitHub Secrets

Go to your repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

Add these three secrets:

| Secret | Value |
|---|---|
| `OPENROUTER_API_KEY` | Your OpenRouter API key (from [openrouter.ai/keys](https://openrouter.ai/keys)) |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Your Telegram numeric chat ID |

### 4. Enable the workflow

The workflow runs automatically on push. To trigger it manually:
- Go to **Actions** → **Chennai News Monitor** → **Run workflow**

It will then run every 15 minutes automatically.

## How it works

```
r/chennai RSS ──┐
                 ├──> monitor.py ──> DeepSeek V4 Flash ──> Telegram
r/chennaicity RSS┘        │              (filtering)         (delivery)
                           │
                      state.json (tracks seen posts)
```

- Fetches both subreddit RSS feeds
- Deduplicates using `chennai_reddit_state.json`
- Sends new posts to DeepSeek V4 Flash for newsworthiness filtering
- Delivers filtered news to your Telegram
- Commits updated state back to the repo

## Filtering criteria

**Included:** infrastructure, civic issues, weather, government policy, transport, crime, major TN politics

**Excluded:** memes, personal rants, restaurant recs, movie talk, classifieds

## Costs

| Item | Monthly |
|---|---|
| GitHub Actions | $0 (public repo, unlimited) |
| DeepSeek V4 Flash API | ~$0.22 |
| Telegram Bot API | Free |
| **Total** | **~$0.22/month** |

## Local testing

```bash
export OPENROUTER_API_KEY="sk-or-..."
export TELEGRAM_BOT_TOKEN="123:abc..."
export TELEGRAM_CHAT_ID="350790755"
python monitor.py
```
