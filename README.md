# Telegram deal collector

A personal Docker service that reads selected Telegram channels you already belong to and sends deduplicated Amazon Spain offers to **your Saved Messages**. No bot, AI service or public web port is required.

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

On the first cycle each source starts **from its newest message**. Historical posts are not imported. New messages are polled every two minutes; the persistent cursor catches up after downtime. Check Saved Messages after the next scheduled digest. For a quick check, temporarily add the current Lisbon time plus a few minutes to `digest_times`, restart, and wait for a new qualifying post.

## Configuration

After editing `config.json`, run `docker compose restart collector`.

| Setting | Meaning |
|---|---|
| `channels` | Numeric IDs of sources you already belong to. Only these histories are collected. |
| `timezone` | IANA zone; default Europe/Lisbon, including daylight saving. |
| `digest_times` | Local 24-hour times; defaults 12:30 and 20:30. |
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
- Delivery is at-least-once: a crash just after sending may repeat an entry. A source access failure delays later sources until that source is fixed/removed; logs show the exception type without exposing content or credentials.

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

Run core tests without third-party dependencies:

```sh
python3 -m unittest discover -s tests -v
```

The delivered package has unit coverage for product identity, locale price parsing, cross-source merging and meaningful price drops. Live login, channel access, redirect resolution and sending require your server/account and must be verified there. Runtime API reference: https://docs.telethon.dev/en/stable/modules/client.html.
