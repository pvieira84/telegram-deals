# Telegram deal collector

A personal Docker service that reads selected Telegram channels you already belong to and sends deduplicated Amazon Spain offers to **your Saved Messages**. Bot delivery is optional; no AI service or public web port is required.

## Install on your server

Requires Docker with the Compose plugin (`docker compose version`). Extract this package and open its directory. Run all commands below there.

1. Get your own Telegram application credentials at https://my.telegram.org under API development tools. See https://core.telegram.org/api/obtaining_api_id.
2. Copy the settings template:

   ```sh
   cp .env.example .env
   chmod 600 .env
   ```

   Edit `.env` locally on the server and enter your numeric API ID and API hash. Never send login codes, passwords, API hashes or session files to someone else.

3. Build and authorise interactively:

   ```sh
   docker compose build
   docker compose run --rm collector login
   ```

   Enter your phone number, Telegram login code and two-step verification password if requested. These are entered directly into the server terminal. Telegram stores the reusable session in the named data volume. Never run login while the collector is running.

4. List your groups/channels:

   ```sh
   docker compose run --rm collector channels
   ```

   Find the example channel in that list. Copy its numeric ID into `config.json`, keeping the minus sign, for example:

   ```json
   "channels": [-1001234567890, -1009876543210]
   ```

   Use IDs from your own list, not these examples. The invite link alone is not the configuration ID. The collector does not join channels for you. Select channels or supergroups; original-post links for legacy basic groups are not supported.

5. Start:

   ```sh
   docker compose up -d
   docker compose logs --tail=50 -f
   ```

On the first cycle each source starts **from its newest message**. Historical posts are not imported. New messages are polled every two minutes; the persistent cursor catches up after downtime. Check Saved Messages after the next scheduled digest. For a quick check, temporarily add the current Lisbon time plus a few minutes to `digest_times` (or adjust `digest_cron`), restart, and wait for a new qualifying post.

## Configuration

After editing `config.json`, run `docker compose restart collector`.

| Setting | Meaning |
|---|---|
| `channels` | Numeric IDs of sources you already belong to. Only these histories are collected. |
| `timezone` | IANA zone; default Europe/Lisbon, including daylight saving. |
| `digest_cron` | Numeric five-field cron string, or list of strings; takes precedence over digest_times. |
| `digest_times` | Legacy HH:MM list, supported when digest_cron is absent. |
| `poll_seconds` | Poll interval, minimum 30 seconds. |
| `price_drop_percent` | Resurface an offer when its clear advertised price is at least this much below the last sent price. Default 5. |
| `include_keywords` | Case-insensitive substring matches; empty means include everything. Any match qualifies. |
| `exclude_keywords` | Any match hides a post. |
| `watch_keywords` | Matching qualifying posts are sent on the next poll, outside the digest schedule. |
| `max_price_eur` | Numeric maximum or null. Unclear prices remain included for manual checking. |

Example filters: `"include_keywords": ["ssd", "aspirador"]`, `"exclude_keywords": ["reacondicionado"]`, `"watch_keywords": ["roborock"]`. These are literal matches; no category inference or translation is performed. Filters apply to newly ingested posts; changes do not reprocess already stored entries.

## Duplicate and price behaviour

- Amazon Spain links are merged by ASIN, regardless of affiliate tag or source. Text links, hidden text URLs and inline URL buttons are inspected.
- `amzn.eu` and `amzn.to` links are resolved with bounded, allowlisted HTTPS redirects. Other link shorteners, external coupon landing pages and image-only links are skipped. No Amazon page scraping or checkout verification is performed.
- Each unique product is sent as one compact digest message. A digest is a batch of these messages, not a single oversized post. Up to four original source links are shown.
- Only a single explicit euro amount in a single-product post is treated as a price. Posts with several prices or products are labelled unclear. A sole euro amount could still be a coupon value or shipping charge: these are **advertised, unverified prices**.
- Different ASINs stay separate. The same ASIN can still have different sellers, conditions or coupons: these offers are grouped, not proven equivalent. Original terms remain available in source posts. Improved coupon conditions without a lower parsed price do not trigger another alert.
- The lowest clear price seen is retained. There is no stock/expiry detection, so it may become stale. No repeat alert is sent at the same or a higher price, even weeks later. No Keepa integration is included.
- Edited/deleted posts are not reprocessed. Fresh posts are collected. Removed sources remain in the local history.
- If the server misses a digest slot, pending deals are sent after restart once a slot is due that day. No empty digest is sent. Watchlist entries are not repeated in the following digest.
- Delivery is at-least-once: a crash just after sending may repeat an entry. A source access failure is logged and later sources are still scanned; the failed source resumes from its last saved cursor on the next cycle. Telegram rate-limit waits pause requests instead of continuing to issue calls.

## Operations and privacy

```sh
docker compose stop
docker compose up -d
docker compose logs --tail=100
```

The Docker named volume contains the Telegram account session and deal database. Protect the server and volume backups as account credentials. The default image runs as container root with all capabilities dropped and a read-only application filesystem; it publishes no ports. Restrict shell/Docker access to trusted administrators. Telegram authorisation is account-wide even though this application reads only your configured source histories (the `channels` command lists dialog titles/IDs).

Data stays on your server except for Telegram access, sending to your own Saved Messages, and requests to the allowlisted Amazon retail hosts to resolve short links. Resolution can expose your server IP to those retailers. No analytics or external AI calls. Saved Messages sync through Telegram normally. The database has no automatic retention limit; monitor volume size and protect backups. Do not run multiple instances against the same volume.

To revoke access, terminate this application's session from Telegram Settings → Devices. `docker compose down` keeps data. **`docker compose down -v` permanently deletes the local session and database**; use only if you intend to reset everything.

## Validation and limits

Run the tests without third-party dependencies:

```sh
python3 -m unittest discover -s tests -v
```

The delivered package has unit coverage for product identity, locale price parsing, cross-source merging and meaningful price drops. Live login, channel access, redirect resolution and sending require your server/account and must be verified there. Runtime API reference: https://docs.telethon.dev/en/stable/modules/client.html.


## Original post images

Digest and watchlist messages now reuse images from the selected original post.
Photo albums are kept together (up to ten images); image documents are also
included. Neighbouring posts are excluded by matching the Telegram album ID.
If the digest text exceeds Telegram's caption limit, the images have a compact
caption and the complete digest text is sent as a reply.

Images are fetched from Telegram at delivery time, including for deals already
pending before this update. No database migration, media download folder or
new volume is needed. Photos are not permanently cached: deleted posts, lost
source access, or protected-content restrictions may cause a text-only delivery
with the original link. Link-preview thumbnails and videos are not copied.
Already delivered products are not resent by upgrading. For a multi-product
post, each product receives the original post's images; images are not assigned
to individual products automatically.

After updating the source, rebuild the running service. For the repository's
Compose service: `docker compose up -d --build collector`. If your deployment
renames the service to `telegram-deals`, use that name instead and keep your
existing configuration and data mounts. Deployments using a prebuilt image
must rebuild/publish that image first; a restart alone does not update code.


## Cron digest schedules

Replace the `digest_times` setting in your active configuration with, for example:

```json
"digest_cron": "*/30 9-22 * * *"
```

This sends pending deals every 30 minutes, from 09:00 through 22:59, in your
configured timezone. The fields are **minute hour day-of-month month weekday**.
Supported syntax: numeric values, `*`, comma-separated lists, ranges and positive
steps. Weekdays use 0/7 for Sunday through 6 for Saturday. Names, macros,
seconds, `L`, `W` and `#` extensions are not supported. When both day-of-month
and weekday are restricted, either can match (standard cron OR behaviour).

| Schedule | Expression |
|---|---|
| Every hour | `0 * * * *` |
| Every 15 minutes | `*/15 * * * *` |
| Every two hours at :00 | `0 */2 * * *` |
| Weekdays at 09:00 and 18:00 | `0 9,18 * * 1-5` |
| Daily at 12:30 and 20:30 | `30 12,20 * * *` |

For different minutes at different hours, use a list. Your earlier three times
can be expressed as:

```json
"digest_cron": ["30 12,20 * * *", "0 17 * * *"]
```

Existing configurations with only `digest_times` keep working. If both keys are
present, `digest_cron` wins. Restart after configuration changes. Delivery occurs
on the next completed poll, not at the exact second; schedules faster than the
poll interval are coalesced. On startup/recovery the latest due slot **today**
is processed once; missed slots are not replayed individually. No empty message
is sent. The existing digest-slot state prevents a restart from repeating the
same slot. New schedules can trigger immediately if a slot earlier today is due.
Spring-forward nonexistent times are skipped; repeated autumn wall-clock slots
run once. Configuration is checked at startup. Logs now report stored/pending
product counts and digest delivery counts, including zero.

## Source and duplicate logging

Startup configuration is printed as an aligned block with one channel ID/name
per line. Channel titles are resolved after Telegram authorisation; unavailable
titles are labelled explicitly. Credentials and environment variables are not
printed.

Each collection cycle logs one source summary per product observed in that
cycle, after all configured channels have been scanned. `Duplicate posts found`
is the number of distinct stored source posts minus one, including previous
cycles. Re-reading the same source post does not count again. The summary lists
all contributing channels with their post counts, the selected channel, and the
selection decision. This reflects sources seen so far, not posts that arrive in
future cycles. Lower unambiguous advertised prices replace the selected post;
equal, higher or unclear prices retain it. If no price was clear before, the
first unambiguous price is selected.

Watchlist and scheduled digests are separate delivery passes. A zero watchlist
count followed by a nonzero digest is normal: no pending deals matched watchlist
keywords, but other products were due in the scheduled digest. `Products
collected` is the cumulative unique-product count; `Pending products` counts
products still queued after delivery.

Outgoing post excerpts replace original Amazon/retail-shortener URLs with a
placeholder, followed by the selected product's single canonical Amazon link:
`https://www.amazon.es/dp/ASIN`. Affiliate parameters and shortened Amazon links
are not carried into the outgoing text. Original Telegram source links remain.
This also applies to old pending products, without modifying stored raw posts.


## Per-channel scan diagnostics

Every cycle logs a summary for every configured channel, including quiet ones:
messages scanned, links inspected, posts with recognised Amazon products,
accepted posts, product matches, newly stored products, posts with no recognised
product, keyword/price exclusions, and the cursor before/after scanning. These
counts refer to new posts since that channel's saved cursor, not its full history.
A first-time channel is explicitly logged as initialised without historical import.
A final summary reports successful/failed channel scans.

Unrecognised or unresolved links are counted by hostname only (no full URLs or
query strings). A channel with messages but no recognised products may use an
unsupported link shortener, external landing page, a different Amazon locale,
or image-only promotions. A hostname count is diagnostic, not proof of which
case applies. Only allowlisted retail links are resolved; arbitrary websites
are not followed. Share a representative post/link if a source needs a new
supported format. Product summaries alone show successful matches and do not
indicate which other channels were scanned.


## ASINs embedded in external links

The collector now inspects URL paths, query strings and fragments on any HTTP(S)
host for embedded product IDs, before considering a network request. This
supports publisher links such as `https://boacena.pt/B0CFVYPKGB` and links with
percent-encoded Amazon destinations. Up to three encoding layers are decoded.
Repeated IDs are merged; multiple different IDs in one URL are all collected.

Generic URL tokens must match the common `B0` plus eight alphanumeric characters
and must not be part of a longer alphanumeric ID. Explicit `asin=` parameters
and Amazon-style `/dp/`, `/gp/product/` and `/gp/aw/d/` paths also accept other
ten-character ASIN formats. Matching is case-insensitive. Hostnames and URL
credentials are never searched. This is a format heuristic, not Amazon product
verification; an unrelated ID with the same form could still match.

External sites are not visited just to extract an embedded ID; the existing
network redirect allowlist stays in place. Posts without a recognisable embedded
ID still need a supported direct/short Amazon link. External links containing
recognised ASINs are replaced in the outgoing excerpt, and the selected product
is linked using `https://www.amazon.es/dp/ASIN`.

This applies to newly scanned posts. Previously skipped source messages have
already advanced the cursor and are not automatically replayed. Existing deals,
deduplication history, and configured channel IDs are preserved during upgrade.

## Cupões Tá Fixe landing pages and Amazon short links

The collector can now fetch HTTPS landing pages on `cupoestafixe.com` and
`www.cupoestafixe.com`, including WordPress `?p=...` links. It reads HTML links,
preferring article-content regions and excluding navigation/sidebars. Supported
Amazon short links (`amzn.eu`, `amzn.to`, `link.amazon`, and the `amzlinks.in`
intermediary) are followed through bounded redirects to extract the destination
product ID. Short-link tokens are treated as opaque, even when they happen to
look like ASINs. Outgoing product links remain canonical Amazon Spain links.

No JavaScript is executed. HTTP errors, challenges, oversized pages, missing
links and pages resolving to multiple distinct products are logged and skipped.
HTML is limited to 1 MiB, redirects to eight hops, and candidate links to eight.
Only explicit allowlisted HTTPS hosts are fetched; arbitrary linked websites
are not visited. This adds requests from your server to Cupões Tá Fixe and the
supported Amazon short-link hosts. Live behaviour depends on those sites; the
example page could not be retrieved in the development environment, so tests
cover representative HTML and redirect responses rather than a live page.

Rebuild the image after updating: `resolver.py` is a new application module.
This affects new posts; previously skipped messages are not replayed.


## Private Telegram destination channel

Create a private channel using the same Telegram account used during collector
login, or add that account as an administrator with permission to post. No bot
is needed. Keep the channel private and its invite link unshared if this is a
personal feed.

After upgrading/rebuilding, stop the collector before listing channels so the
interactive command does not share a live SQLite session with the service:

```sh
docker compose stop collector
docker compose run --rm collector channels
```

Use your deployment's service name (`telegram-deals` if renamed). Find the new
channel's `-100...` ID and add it to the active mounted config:

```json
"destination": -1001234567890
```

Use your actual channel ID. Keep it out of the source `channels` array. Numeric
IDs or numeric strings are accepted; invite links and usernames are not used as
destinations. The default/missing setting `"destination": "me"` keeps Saved
Messages. At startup the collector resolves the destination and requires it to
be a channel where the logged-in account is the creator or has post permission.
It never silently falls back to Saved Messages when delivery fails.

Start the service after saving the config. Future digests/watchlist alerts and
any still-pending products use the new destination; already delivered deals are
not reposted. Photo albums and long-caption replies use the same destination.


## Send as a bot so posts arrive as incoming messages

Source collection still uses your existing Telegram account. In bot mode,
filtered posts are published by a separate bot, so the collector is not sending
outgoing posts as your personal account. Unread badges and notifications remain
subject to your Telegram client's settings and whether the channel is open on
one of your devices; the collector does not forcibly mark messages unread.

1. Create a bot with the official https://t.me/BotFather using `/newbot`.
2. Add that bot as an administrator of the private destination channel, granting
   **Post Messages**. The bot does not need access to your source channels.
3. Put its token in your server's `.env` file, not `config.json` or Git:

   ```dotenv
   TG_BOT_TOKEN=your_botfather_token
   ```

   Ensure Compose passes this variable to the container. The repository's
   `env_file: .env` already does this. Custom Compose configurations can use:

   ```yaml
   environment:
     TG_BOT_TOKEN: ${TG_BOT_TOKEN}
   ```

4. In the active mounted configuration, set:

   ```json
   "delivery_mode": "bot",
   "destination": -1001234567890
   ```

   Use your actual destination ID. Bot mode requires a channel ID; Saved
   Messages (`"me"`) is available only in user mode. Keep the destination out
   of the source list. The old/default `"delivery_mode": "user"` keeps existing
   behaviour until you explicitly switch.

5. Update and rebuild the application image (it includes `bot_delivery.py`),
   then recreate the container to pick up environment changes. In a deployment
   with service name `telegram-deals`, run `docker compose up -d --force-recreate
   telegram-deals` after the image has been rebuilt/updated. A restart alone
   does not load new environment variables.

Startup checks the token, destination channel, and the bot's posting rights. It
logs the bot username and destination title, never the token. Your personal
account does not need posting permission in bot mode. If validation or delivery
fails, there is no silent fallback to personal-account posting. Failed products
stay pending; Telegram rate-limit responses are retried with the requested wait.

Photos/albums are downloaded through your existing source-reading account,
then uploaded through the Bot API. Account-specific media references cannot be
reused by the bot. Downloads use temporary files, removed after each delivery,
and are capped at 10 MiB per image. Protected/missing images or rejected photo
formats fall back to the deal text and original link. A long caption is followed
by a reply in the same destination channel. The bot token permits control of the
bot: keep `.env`, Docker access and backups private, and revoke the token through
BotFather if exposed. No token is printed in HTTP failure messages.

Already delivered products are not reposted when switching delivery modes.
Delivery remains at-least-once: a crash after a successful send may repeat an
entry. Live bot delivery requires your token and channel and is not exercised
by the automated tests.
