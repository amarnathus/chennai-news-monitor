#!/usr/bin/env python3
"""
Standalone Chennai Reddit News Monitor

Scrapes r/chennai and r/chennaicity RSS feeds, filters posts
through DeepSeek V4 Flash for newsworthiness, and delivers
results to Telegram.

Designed to run on GitHub Actions (free, public repo) every 15 minutes.
Zero Hermes dependency — just Python + pip.

Environment variables (set as GitHub Secrets):
  OPENROUTER_API_KEY   — OpenRouter API key
  TELEGRAM_BOT_TOKEN   — Telegram Bot token from @BotFather
  TELEGRAM_CHAT_ID     — Your Telegram chat ID (e.g. 350790755)
"""

import json
import os
import re
import ssl
import sys
import time
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

# macOS Python often has SSL cert issues. Try certifi, fall back gracefully.
try:
    import certifi
    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    try:
        SSL_CONTEXT = ssl.create_default_context()
    except Exception:
        SSL_CONTEXT = ssl._create_unverified_context()

# ── Configuration ──────────────────────────────────────────────────
STATE_FILE = os.path.join(os.path.dirname(__file__), "chennai_reddit_state.json")
FEEDS = {
    "r/chennai": "https://www.reddit.com/r/chennai/.rss",
    "r/chennaicity": "https://www.reddit.com/r/chennaicity/.rss",
}
MAX_AGE_HOURS = 24

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
DEEPSEEK_MODEL = "deepseek/deepseek-v4-flash"

NS = {"atom": "http://www.w3.org/2005/Atom"}

# ── Filtering prompt (replicates the Hermes cron prompt) ─────────
FILTER_PROMPT = """You are a news filter for Chennai and Tamil Nadu. Below are new Reddit posts from r/chennai and r/chennaicity.

Your job: identify genuinely newsworthy posts. Include only what a professional news page would publish about Chennai/TN.

INCLUDE:
- Infrastructure updates (roads, metro, bridges, water, power)
- Civic issues (garbage, flooding, public services)
- Weather warnings and significant weather events
- Government policy, municipal decisions, public notices
- Transport and traffic updates
- Crime and public safety incidents
- Major Tamil Nadu politics (elections, coalitions, party shifts, scandals)
- Community events of city-wide significance

EXCLUDE:
- Memes, jokes, shitposts, meta-discussions about Reddit
- Personal rants, relationship advice, "I need help finding X"
- Restaurant/cafe/food recommendations and reviews
- Movie reviews, celebrity gossip, cinema discussions
- Buying/selling, classifieds, job postings
- Low-effort tourist questions

OUTPUT FORMAT:
- If posts qualify: a bullet list with title, subreddit, and URL for each
- If nothing qualifies: just a single dash: —
- Keep it concise — one line per post, 3-5 posts max

Example output:
• Waterlogging reported across Velachery after heavy rains — r/chennai — https://reddit.com/...
• CM announces new metro line extension to Avadi — r/chennaicity — https://reddit.com/...
• Power cuts scheduled for Anna Nagar this weekend — r/chennai — https://reddit.com/...

Now evaluate these posts:

[POSTS]"""


# ── State management ──────────────────────────────────────────────
def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"seen_ids": [], "last_fetch": None}


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ── RSS fetching ──────────────────────────────────────────────────
def fetch_feed(url: str) -> list[dict]:
    """Fetch and parse an RSS feed, returning list of post dicts."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "ChennaiNewsBot/2.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20, context=SSL_CONTEXT) as resp:
            data = resp.read().decode("utf-8")
    except Exception as e:
        print(f"[ERROR] Failed to fetch {url}: {e}", file=sys.stderr)
        return []

    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        print(f"[ERROR] XML parse error for {url}: {e}", file=sys.stderr)
        return []

    posts = []
    for entry in root.findall("atom:entry", NS):
        post = {}

        id_el = entry.find("atom:id", NS)
        post["id"] = id_el.text.strip() if id_el is not None and id_el.text else ""

        title_el = entry.find("atom:title", NS)
        post["title"] = title_el.text.strip() if title_el is not None and title_el.text else ""

        link_el = entry.find("atom:link", NS)
        post["url"] = link_el.get("href", "") if link_el is not None else ""

        author_el = entry.find("atom:author/atom:name", NS)
        post["author"] = author_el.text.strip() if author_el is not None and author_el.text else ""

        pub_el = entry.find("atom:published", NS)
        post["published"] = pub_el.text.strip() if pub_el is not None and pub_el.text else ""

        # Flair/category
        categories = []
        for cat in entry.findall("atom:category", NS):
            label = cat.get("label") or cat.get("term") or ""
            if label:
                categories.append(label)
        post["flair"] = ", ".join(categories) if categories else ""

        # Self-text preview
        content_el = entry.find("atom:content", NS)
        if content_el is not None and content_el.text:
            text = re.sub(r"<[^>]+>", "", content_el.text)
            text = re.sub(r"&(amp|lt|gt|quot|#39);", "", text)
            text = re.sub(r"\s+", " ", text).strip()
            post["self_text"] = text[:300]
        else:
            post["self_text"] = ""

        posts.append(post)

    return posts


# ── LLM filtering via OpenRouter ──────────────────────────────────
def filter_posts(posts: list[dict]) -> str:
    """Send posts to DeepSeek V4 Flash for newsworthiness filtering."""
    if not posts:
        return "—"

    # Build post listing for the prompt
    posts_text = []
    for i, p in enumerate(posts, 1):
        extra = f" | Flair: {p['flair']}" if p["flair"] else ""
        body = f" | Preview: {p['self_text'][:150]}" if p["self_text"] else ""
        posts_text.append(
            f"{i}. [{p['subreddit']}] {p['title']}{extra}{body}\n   URL: {p['url']}"
        )
    prompt = FILTER_PROMPT.replace("[POSTS]", "\n".join(posts_text))

    body = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1,
        "max_tokens": 800,
        "reasoning": {"enabled": False},  # Disable thinking to get direct output
    }

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/chennai-news-monitor",
            "X-Title": "Chennai News Monitor",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return result["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[ERROR] OpenRouter call failed: {e}", file=sys.stderr)
        return None


# ── Telegram delivery ─────────────────────────────────────────────
def send_telegram(text: str) -> bool:
    """Send a message via Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[ERROR] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set", file=sys.stderr)
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    body = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=15, context=SSL_CONTEXT) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        if not result.get("ok"):
            print(f"[ERROR] Telegram API error: {result}", file=sys.stderr)
            return False
        return True
    except Exception as e:
        print(f"[ERROR] Telegram send failed: {e}", file=sys.stderr)
        return False


# ── HTML escaping for Telegram (minimal) ─────────────────────────
def escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── Main ──────────────────────────────────────────────────────────
def main():
    if not OPENROUTER_API_KEY:
        print("FATAL: OPENROUTER_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    state = load_state()
    seen_ids = set(state.get("seen_ids", []))
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=MAX_AGE_HOURS)

    all_new = []
    new_ids = []

    for sub_name, feed_url in FEEDS.items():
        posts = fetch_feed(feed_url)
        for post in posts:
            pid = post["id"]
            if not pid or pid in seen_ids:
                continue

            # Check age
            try:
                pub_dt = datetime.fromisoformat(post["published"].replace("Z", "+00:00"))
                if pub_dt < cutoff:
                    continue
            except (ValueError, TypeError):
                pass

            post["subreddit"] = sub_name
            all_new.append(post)
            new_ids.append(pid)

    # Update state
    state["seen_ids"] = list(seen_ids | set(new_ids))
    state["last_fetch"] = now.isoformat()

    # Always save state (even if no new posts)
    save_state(state)

    # Print new-post flag for GitHub Actions commit logic
    state_changed = len(new_ids) > 0

    print(f"[INFO] {len(new_ids)} new posts found (total seen: {len(state['seen_ids'])})")

    if not all_new:
        # No new posts — don't send anything (avoid spamming "—" every 15 min)
        print("[INFO] No new posts — skipping LLM call and Telegram delivery")
        return

    # Filter with LLM
    print("[INFO] Calling DeepSeek V4 Flash for filtering...")
    result = filter_posts(all_new)

    if result is None:
        print("[ERROR] Filtering failed", file=sys.stderr)
        sys.exit(1)

    # Don't send if result is just "—" (nothing newsworthy)
    if result.strip() == "—":
        print("[INFO] No newsworthy posts found — skipping Telegram delivery")
    else:
        # Add header
        header = "📰 <b>Chennai News Update</b>\n"
        message = header + result

        if send_telegram(message):
            print("[INFO] Sent to Telegram successfully")
        else:
            print("[ERROR] Failed to send to Telegram", file=sys.stderr)
            sys.exit(1)

    # Write state-changed marker file for GitHub Actions
    if state_changed:
        marker = os.path.join(os.path.dirname(__file__) or ".", ".state_changed")
        with open(marker, "w") as f:
            f.write("1")

    print("[INFO] Done")


if __name__ == "__main__":
    main()
