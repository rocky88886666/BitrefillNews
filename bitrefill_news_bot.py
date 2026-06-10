#!/usr/bin/env python3
"""Post Bitrefill-related news links to a Telegram channel on a schedule."""

from __future__ import annotations

import argparse
import email.utils
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional


DEFAULT_FEEDS = [
    "https://news.google.com/rss/search?q=Bitrefill&hl=en-US&gl=US&ceid=US:en",
]
DEFAULT_KEYWORDS = ["bitrefill"]


@dataclass(frozen=True)
class NewsItem:
    title: str
    link: str
    source: str
    published: Optional[datetime]
    summary: str = ""


def env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    parts = re.split(r"[\n,]+", raw)
    return [part.strip() for part in parts if part.strip()]


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"warning: {name}={raw!r} is not an integer; using {default}", file=sys.stderr)
        return default


def fetch_text(location: str, timeout: int = 20) -> str:
    path = Path(location)
    if path.exists():
        return path.read_text(encoding="utf-8")

    request = urllib.request.Request(
        location,
        headers={
            "User-Agent": "BitrefillNewsBot/1.0 (+https://github.com/)",
            "Accept": "application/rss+xml, application/atom+xml, text/xml, */*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode(response.headers.get_content_charset() or "utf-8")


def strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def child_text(element: ET.Element, names: Iterable[str]) -> str:
    wanted = {name.lower() for name in names}
    for child in element:
        if strip_namespace(child.tag) in wanted and child.text:
            return child.text.strip()
    return ""


def child_attr(element: ET.Element, name: str, attr: str) -> str:
    for child in element:
        if strip_namespace(child.tag) == name.lower():
            value = child.attrib.get(attr)
            if value:
                return value.strip()
    return ""


def parse_date(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        pass

    iso_value = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def parse_feed(xml_text: str, feed_url: str) -> list[NewsItem]:
    root = ET.fromstring(xml_text)
    root_name = strip_namespace(root.tag)

    if root_name == "feed":
        return parse_atom(root, feed_url)
    return parse_rss(root, feed_url)


def parse_atom(root: ET.Element, feed_url: str) -> list[NewsItem]:
    items: list[NewsItem] = []
    for entry in root.iter():
        if strip_namespace(entry.tag) != "entry":
            continue
        link = child_attr(entry, "link", "href") or child_text(entry, ["link"])
        item = NewsItem(
            title=child_text(entry, ["title"]),
            link=normalize_link(link),
            source=feed_url,
            published=parse_date(child_text(entry, ["published", "updated"])),
            summary=child_text(entry, ["summary", "content"]),
        )
        if item.title and item.link:
            items.append(item)
    return items


def parse_rss(root: ET.Element, feed_url: str) -> list[NewsItem]:
    items: list[NewsItem] = []
    for entry in root.iter():
        if strip_namespace(entry.tag) != "item":
            continue
        item = NewsItem(
            title=child_text(entry, ["title"]),
            link=normalize_link(child_text(entry, ["link", "guid"])),
            source=child_text(entry, ["source"]) or feed_url,
            published=parse_date(child_text(entry, ["pubDate", "published", "updated"])),
            summary=child_text(entry, ["description", "summary"]),
        )
        if item.title and item.link:
            items.append(item)
    return items


def normalize_link(link: str) -> str:
    return html.unescape(link.strip())


def is_relevant(item: NewsItem, keywords: list[str]) -> bool:
    haystack = f"{item.title} {item.summary} {item.link}".lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def load_cache(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    if isinstance(data, list):
        return {str(value) for value in data}
    if isinstance(data, dict) and isinstance(data.get("sent_links"), list):
        return {str(value) for value in data["sent_links"]}
    return set()


def save_cache(path: Path, links: set[str], limit: int = 500) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"sent_links": sorted(links)[-limit:]}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def collect_items(feeds: list[str], keywords: list[str], max_age_days: int) -> list[NewsItem]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    items: list[NewsItem] = []
    seen_links: set[str] = set()

    for feed in feeds:
        try:
            feed_items = parse_feed(fetch_text(feed), feed)
        except (ET.ParseError, OSError, urllib.error.URLError) as exc:
            print(f"warning: failed to read feed {feed}: {exc}", file=sys.stderr)
            continue

        for item in feed_items:
            if item.link in seen_links:
                continue
            if item.published and item.published < cutoff:
                continue
            if not is_relevant(item, keywords):
                continue
            seen_links.add(item.link)
            items.append(item)

    return sorted(items, key=lambda item: item.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)


def format_published(item: NewsItem) -> str:
    if not item.published:
        return ""
    return item.published.strftime("%Y-%m-%d %H:%M UTC")


def env_bool(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def format_message(items: list[NewsItem], *, batch: tuple[int, int] | None = None, historical: bool = False) -> str:
    now = datetime.now()
    date_label = now.strftime("%Y-%m-%d")
    time_label = now.strftime("%H:%M")
    header = "📚 <b>Bitrefill 历史新闻</b>" if historical else "📰 <b>Bitrefill News</b>"
    lines = [
        header,
        f"📅 {date_label}  ·  🕐 {time_label}",
    ]
    if batch:
        lines.append(f"第 {batch[0]}/{batch[1]} 批")
    lines.append(f"共 {len(items)} 条")
    lines.extend(["─────────────────", ""])
    for index, item in enumerate(items, start=1):
        title = html.escape(item.title)
        source = html.escape(clean_source(item.source))
        when = format_published(item)
        lines.append(f"<b>{index}.</b> {title}")
        meta: list[str] = []
        if source:
            meta.append(f"📌 {source}")
        if when:
            meta.append(f"🕒 {html.escape(when)}")
        if meta:
            lines.append(f"    {'  ·  '.join(meta)}")
        lines.append("")
    lines.extend(["─────────────────", "#Bitrefill #Bitcoin #Crypto"])
    return "\n".join(lines)


def format_status_message() -> str:
    now = datetime.now()
    return "\n".join([
        "📰 <b>Bitrefill News</b>",
        f"📅 {now.strftime('%Y-%m-%d')}  ·  🕐 {now.strftime('%H:%M')}",
        "✅ 检查完成，暂无新新闻",
    ])


def clean_source(source: str) -> str:
    parsed = urllib.parse.urlparse(source)
    return parsed.netloc or source


def deliver_message(message: str, dry_run: bool) -> int:
    if dry_run or os.getenv("DRY_RUN") == "1":
        print(message)
        return 0
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required.", file=sys.stderr)
        return 2
    post_to_telegram(token, chat_id, message)
    time.sleep(1)
    return 0


def send_batches(
    items: list[NewsItem],
    *,
    limit: int,
    dry_run: bool,
    historical: bool,
) -> int:
    batch_size = max(limit, 1)
    batches = [items[i : i + batch_size] for i in range(0, len(items), batch_size)]
    total = len(batches)
    for index, batch in enumerate(batches, start=1):
        message = format_message(batch, batch=(index, total), historical=historical)
        result = deliver_message(message, dry_run)
        if result != 0:
            return result
        if index < total:
            time.sleep(2)
    return 0


def post_to_telegram(token: str, chat_id: str, message: str) -> None:
    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API returned an error: {result}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Send Bitrefill-related news to Telegram.")
    parser.add_argument("--dry-run", action="store_true", help="Print the message instead of posting to Telegram.")
    parser.add_argument("--ignore-cache", action="store_true", help="Include already-sent links.")
    parser.add_argument("--cache", default=os.getenv("CACHE_FILE", "data/sent.json"), help="Path to sent-link cache.")
    parser.add_argument("--limit", type=int, default=env_int("POST_LIMIT", 10), help="Maximum links per post.")
    parser.add_argument("--backfill", action="store_true", help="Send historical items in multiple batches.")
    parser.add_argument("--max-age-days", type=int, default=env_int("MAX_AGE_DAYS", 7), help="Only include recent items.")
    args = parser.parse_args()

    feeds = env_list("NEWS_FEEDS", DEFAULT_FEEDS)
    keywords = env_list("KEYWORDS", DEFAULT_KEYWORDS)
    cache_path = Path(args.cache)
    sent_links = load_cache(cache_path)
    dry_run = args.dry_run or env_bool("DRY_RUN")
    ignore_cache = args.ignore_cache or env_bool("IGNORE_CACHE")
    backfill = args.backfill or env_bool("BACKFILL")

    items = collect_items(feeds, keywords, args.max_age_days)

    if backfill:
        if not items:
            print("No historical Bitrefill-related items found.")
            return 0
        print(f"Backfilling {len(items)} historical items in batches of {args.limit}.")
        result = send_batches(items, limit=args.limit, dry_run=dry_run, historical=True)
        if result != 0:
            return result
        if not dry_run:
            sent_links.update(item.link for item in items)
            save_cache(cache_path, sent_links)
        return 0

    if not ignore_cache:
        items = [item for item in items if item.link not in sent_links]

    selected = items[: max(args.limit, 1)]

    if not selected:
        print("No new Bitrefill-related items found.")
        if env_bool("SEND_STATUS_WHEN_EMPTY"):
            return deliver_message(format_status_message(), dry_run)
        return 0

    message = format_message(selected)
    result = deliver_message(message, dry_run)
    if result != 0:
        return result

    if not dry_run:
        sent_links.update(item.link for item in selected)
        save_cache(cache_path, sent_links)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
