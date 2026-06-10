# Bitrefill Telegram News Bot

每天自动把 Bitrefill 相关公开新闻推送到 Telegram 频道。

## 工作方式

- 从 RSS feed 抓取新闻，默认使用 Google News 的 Bitrefill 搜索 RSS。
- 只保留包含 `KEYWORDS` 的条目。
- 使用 `data/sent.json` 记录已经发过的链接，避免重复推送。
- 通过 Telegram Bot API 的 `sendMessage` 发到频道。
- GitHub Actions 每天 09:30（Asia/Shanghai）自动运行，也支持手动触发。

## Telegram 设置

1. 在 Telegram 找 `@BotFather` 创建 bot，拿到 token。
2. 把 bot 加到你的频道，并设为管理员，至少需要发消息权限。
3. 如果频道有公开用户名，`TELEGRAM_CHAT_ID` 可以直接填 `@your_channel_username`。

## GitHub Secrets

在仓库 `Settings -> Secrets and variables -> Actions` 添加：

- Secret: `TELEGRAM_BOT_TOKEN`
- Secret: `TELEGRAM_CHAT_ID`

可选 Variables：

- `NEWS_FEEDS`: RSS 地址，多个地址用英文逗号或换行分隔
- `KEYWORDS`: 关键词，默认 `bitrefill`
- `POST_LIMIT`: 每次最多发送几条，默认 `5`
- `MAX_AGE_DAYS`: 只发送最近几天的内容，默认 `7`

## 本地测试

先用示例 RSS 预览消息：

```bash
NEWS_FEEDS=samples/sample_feed.xml python bitrefill_news_bot.py --dry-run --ignore-cache
```

真实发送前可以先预览线上 feed：

```bash
python bitrefill_news_bot.py --dry-run --ignore-cache
```

真实发送：

```bash
export TELEGRAM_BOT_TOKEN="你的 bot token"
export TELEGRAM_CHAT_ID="@你的频道用户名"
python bitrefill_news_bot.py
```

## 调整发布时间

GitHub Actions cron 使用 UTC。当前配置是：

```yaml
cron: "30 1 * * *"
```

也就是北京时间/上海时间每天 09:30。
