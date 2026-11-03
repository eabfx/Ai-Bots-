# Kelly Terminal

A personal trading dashboard: macro desk, journal, backtest log, risk calculators, prop firm tracker, alerts and an AI assistant. Signals only: it never places an order. The Zones finder is a separate project in `../zones-app`.

**Daily backup:** while Kelly Terminal runs it saves a zip of your whole trader folder (journal, screenshots, notes, settings) to `trader/backups` once a day and keeps the newest 14. It never changes your originals. The status bar shows the time of the last copy. For a second copy somewhere else (a OneDrive or USB folder), add `"backup_copy_to": "D:/Backups"` to `trader/config.json`. Restore by unzipping a backup over the trader folder while Kelly Terminal is closed.

**Open it like an app (Windows):** run `powershell -ExecutionPolicy Bypass -File riskdesk\install-app.ps1` once. You get a Kelly Terminal icon on the Desktop and in the Start menu that opens it in its own window (no browser tabs), starting the server quietly if needed. The server also starts at sign-in so prices and the hourly AI read keep going. Remove with `-Remove`.

Start it on Windows by double-clicking `RiskDesk.bat` in this folder, or from the repo's top folder:

    py riskdesk\dashboard.py

then open http://127.0.0.1:8765.

| File | What it is |
|---|---|
| `dashboard.py` | the local server (standard library only) |
| `store.py` | saves the journal and its screenshots as files in `trader\journal` |
| `notion_import.py` | reads a Notion export of your Trade Journey database |
| `dashboard.html` | the page: Home, Macro, Journal, Backtest, Trades, Alerts, Connections and the AI assistant |
| `coach.py` | the Journal coach and the chat box. Asks Claude or Gemini when you press a button or send a message |
| `macro.py` | the Macro desk: prices, economic calendar, headlines, sessions and the AI brief |
| `RiskDesk.bat` | the Windows launcher (shows a console) |
| `install-app.ps1`, `RiskDeskApp.ps1`, `RiskDeskApp.vbs` | the app icon: Desktop and Start menu shortcuts, quiet start, own window |

Your own settings, saved charts and API keys live in the `trader` folder at the top of the repo, which git leaves
out: `config.json`, `gemini_key.txt` or `anthropic_key.txt`.

## What each page does

- **Home**: the page it opens on. Sessions (open or closed, with a countdown), your numbers (today, last 7 days, 30 day win rate, prop room), market cards for gold, NQ, EURUSD and GBPUSD with a small chart, your bias buttons and the AI read, capital flow (the day's mood and how far each market moved), a price ticker, currency strength (USD, EUR, GBP, JPY, CAD, AUD, CHF, NZD), the news feed, the next 24 hours of releases and the assistant. On a wide screen the pages are in a sidebar on the left.
- **Macro**: market sessions, prices (gold, NQ, EURUSD, GBPUSD, dollar, US 10 year, VIX, S&P, oil), the week's USD/EUR/GBP releases, headlines, a plain risk-on/risk-off read, your own bias buttons (kept in this browser), and an AI brief you ask for. Prices come from Yahoo, the calendar from the ForexFactory weekly feed and headlines from Google News, so they can lag your broker.
- **Trades**: a position size calculator (forex, gold, index futures, CFDs) with a contract reference table of value per point and per tick, an exchange rate for accounts in a different currency to the instrument, and optional margin; a risk/reward check; and **Prop firm room**, which takes your prop firm's rules (daily loss, max drawdown fixed or trailing, profit target) and your Journal and shows how much you can still lose today and overall. Trailing drawdown follows closed trades only.
- **Journal**: your live trades. Saved as a file on this PC (`trader\journal\trades.json`, with each screenshot as an image in `trader\journal\images`), so clearing the browser loses nothing. Back up the `journal` folder now and then (the previous save is kept as `trades.prev.json`). Trades can carry a zone, bias, account, a screenshot (choose a file or paste with Ctrl+V), and a result in R with the risk as a percent of the account; when every trade in a view is in percent, the totals show %. Click a screenshot to see it large.
- **Backtest**: the same page over your backtest trades only. They never count in Home, the prop firm room, the alerts, the coach or the assistant.
- **Alerts**: price levels, a warning before high-impact news, 15 minutes before London and New York open, a daily loss limit, a losing streak, and a warning when you log a live trade with the checklist not fully ticked. They show on screen, as a desktop notification if you allow it, and with a beep, and they are checked every minute, only while Kelly Terminal is open in a browser tab.
- **Connections**: what is connected (AI key, prices, calendar and news, journal storage) and the two imports. **Import from Notion** reads an export of your Trade Journey database (Markdown & CSV) that you put in `trader\import`, shows a preview, then adds the trades and copies each screenshot. Rows marked No Trade or Invalid are skipped; Backtest rows go to Backtest and Live Account rows to the Journal; trades already there are skipped. **Import from a broker CSV** is the same idea for a broker history file.
- **Journal details**: a total P/L, an edge score, win rate, average R, profit factor, plan followed, a cumulative curve, a month calendar, results by weekday, and results grouped by instrument, setup, emotion, plan or grade. Each trade can carry an optional time, grade (A to D), plan followed or deviated, and how you felt. Results can be grouped by session (Asia, London, New York, Late, on the Dublin clock) or by hour. **Import from a broker CSV** reads a trade history file in the browser (nothing is uploaded), guesses the columns, shows a preview you can correct, skips trades already in the journal and adds the rest. Everything is worked out in the browser from your trades.
- **Assistant**: on Home it has its own panel, on the other pages it is the button at the bottom right. Ask about what is on screen. It sends only your journal totals, the macro numbers and the calendar, never your keys.

On a narrow screen (a phone, or a small window) the pages move to a bar along the bottom and buttons and fields get bigger. The dashboard runs on your PC and listens only on that computer. To reach it from your phone without putting it on the internet, use Tailscale (a free private network between your own devices):

1. Install Tailscale on the PC and on the phone and sign in to the same account. In the Tailscale admin page turn on MagicDNS and HTTPS certificates.
2. On the PC, in PowerShell: `tailscale serve --bg 8765`. It prints an address like `https://my-pc.tail1234.ts.net`.
3. Add that name (without https://) to `trader\config.json`: `"allowed_hosts": ["my-pc.tail1234.ts.net"]`, then restart Kelly Terminal. Any other name is still refused.
4. Open the address on your phone. Only devices signed in to your Tailscale account can reach it, and your data stays on the PC, which has to be on. Alerts only fire while the page is open in a browser tab.

The Zones app (zones, entries, Indicator Stats, the TradingView indicator) is described in `../zones-app/README.md`.

## Notion import: no-trade days, tests and screenshots

Every row of the Trade Journey table is imported. Rows that are not a counted result (no-trade days, INVALID, trades marked
only TEST, wins with no risk percent) are stored with a tag and shown in "Kept but not counted" on the Journal or Backtest page.
They never reach the win rate, P/L, charts, alerts or the coach.

If the trades were copied without screenshots, export the database from Notion (Markdown & CSV), put the .zip in
`trader\import`, then press Preview and Import again: trades already there are not duplicated, and each one that is
missing its screenshot gets it. Exporting only reads your Notion; it changes nothing there.

## Backup price source (Twelve Data)

Prices come from Yahoo Finance. If Yahoo does not answer for gold or one of the currency pairs, Kelly Terminal can ask
Twelve Data instead, and the Macro page says which prices came from the backup. It is off until you give it a key:

1. Make a free account at twelvedata.com and copy your API key.
2. Save the key as the only text in `trader\twelvedata_key.txt` (the `trader` folder is never committed to git),
   or set the `TWELVEDATA_API_KEY` variable.
3. Restart Kelly Terminal.

The free plan covers gold and the currency pairs, not the indices (Nasdaq, VIX, S&P, the dollar index, yields and
oil), so those stay Yahoo only. The backup is asked only when Yahoo has failed, so it uses very few of the free requests.
It gives the last price and the change since the previous close, with no small chart.

## Live prices

While Home or Macro is open, the page asks for fresh prices every 2 seconds, and Kelly Terminal asks Yahoo for each market's last price about every 3 seconds (`live.py`). Every number that changes is rewritten in place and flashes green for up or red for down, and the small dot beside "Live, updated" shows the feed is running. Nothing is invented: a price that did not move stays still, and a market whose answer failed keeps its last real price. It only asks Yahoo while a page is open and slows itself down if Yahoo asks it to. Yahoo's free feed is not tick by tick like a paid terminal, so expect a change every few seconds, not every millisecond. It stops when you switch to another tab or the window is hidden.

## Data accuracy

What each price really is (measured against the feeds on 3 November 2026):

- **Yahoo's free CME futures run about 10 minutes late**: gold (GC), Nasdaq (NQ), crude oil and the dollar index. Their exchange clock keeps a steady ten minute gap behind the real time. Forex pairs from Yahoo are live to within about a minute.
- Kelly Terminal measures that gap itself and tags the price ("10m late", "closed", "15m old") on Home, the Quote monitor and the Markets cards. The Data quality panel under Connections lists every feed with its state, age, read count, speed and how many stray ticks were held back.
- **Gold spot** (XAU/USD) is read live from Twelve Data (every 30 seconds) when a key is set and shown beside the delayed gold futures price. Futures normally sit a few dollars above spot.
- **Second opinion**: every 5 minutes EUR/USD and GBP/USD are compared with Twelve Data. More than 2.5 pips apart is flagged ("check").
- **Bad ticks**: a reading that jumps further than the market plausibly moves in a few seconds is held back until the next reading agrees. A normal reopening gap after a quiet spell is shown at once.
- Twelve Data credits are counted and capped per day (780 of the free plan's 800, change it with `TWELVEDATA_DAILY_CREDITS`); a rate limit makes it back off.

What the free feeds cannot do is tick-accurate futures. For real-time NQ and MGC the options are a paid feed from the exchange or a data vendor (Twelve Data's paid plans, Polygon, Databento) or your broker's own price API.

## Running it online with Google sign-in

This is optional. The copy on your PC does not change: sign-in only switches on when `config.json` has an `"auth"`
section, and Kelly Terminal refuses to start if that section is there but anything it needs is missing.

What you need: a small Ubuntu server (for example Hetzner Cloud), a free name that points at it (for example
DuckDNS, which gives `something.duckdns.org`), and a Google OAuth client of type "Web application".

1. Create the server with Ubuntu and note its IP address. In DuckDNS, make a name and point it at that address.
2. In Google Cloud Console, create the OAuth client. Add this as its authorised redirect address, with your own name:
   `https://something.duckdns.org/auth/callback`. Put your Google address on the consent screen's test users.
3. On the server, as root: `git clone https://github.com/eabfx/Ai-Bots-.git /opt/riskdesk`, then
   `DOMAIN=something.duckdns.org EMAIL=you@gmail.com bash /opt/riskdesk/riskdesk/deploy/setup-server.sh`.
   It asks for the Google client id and secret without showing them. It installs the https front door (Caddy),
   starts Kelly Terminal as a service, closes every port except ssh and the web, and backs the journal up nightly.
4. On your PC, copy the journal over once: `.\riskdesk\deploy\copy-journal-to-server.ps1 -Server <the server IP>`.
5. Open `https://something.duckdns.org` and sign in. `/auth/logout` signs you out.

Get newer versions with `bash /opt/riskdesk/riskdesk/deploy/update-server.sh`. Only the addresses listed under
`auth.emails` can get in; every page and every data route needs the sign-in cookie, and it is signed so it cannot be
forged. The Google secret, your AI keys and the journal live in `/opt/riskdesk/trader`, which is never in git.
The journal on the server and the one on your PC are separate copies: they do not sync.

### Install it as an app, and updates

Open the https address in Chrome, Edge or Safari and use **Install app** (on a phone: Share, then **Add to Home Screen**).
Kelly Terminal then opens in its own window with its own icon. It works the same on your PC at http://127.0.0.1:8765.
The app keeps no copy of your data on the device: your journal stays on the server (or PC) it came from.

The server checks GitHub every hour. When there is a newer version it installs it and restarts Kelly Terminal, and if the new
version does not come back healthy it goes back to the one that was running. Reload the page to get the new version.

## Import from TradeZella

Connections page, "Import from TradeZella". It reads `backtest.csv` and `demo.csv` from `trader\tradezella` (copied from TradeZella through its Claude connection, so they are not in git). Backtest trades become a percent of the practice balance with the R TradeZella worked out, and go to the Backtest page as "TradeZella Backtest". Demo-account trades are kept as "TradeZella Demo" but never counted. Preview first; Import skips anything already in the journal.

## Notion live link and Second Brain

Connections page, "Notion live link". It reads your Notion, never writes to it: every call goes through one gate that only allows reading a page, its blocks and a database, and querying a database; anything else raises before it is sent.

Set up (about two minutes): in Notion open notion.so/profile/integrations, New integration, give it only the **Read content** capability, copy its secret into `trader\notion_key.txt` (nothing else in the file), then open the Trade Journal page, the ... menu, Connections, add the integration, and restart Kelly Terminal. The page called "Thoughts + Emotions" is never read by the automatic refresh, even though the integration can reach it. The one exception is the locked section below, and only when you ask for it.

- **Journal rows:** the Trade Journal 2026 database is read every 5 minutes and merged into the journal by Trade No. and date. A row you add or fill in later in Notion becomes or updates the trade; notes and screenshots you added on the dashboard are kept.
- **Second Brain tab:** your own editable, Notion-style workspace (see below). The live link keeps `trader\notion\hub.json` as a read-only snapshot of the Notion pages, which is only used for the first copy.
- Copies are kept in `trader\notion` (not in git), so the tab still shows the last copy if Notion is unreachable. The database and page ids can be changed in config.json under "notion": {"journal_db": "...", "hub_page": "..."}.


## Second Brain (editable)

The Second Brain button (bottom left, white and black) opens a Notion-style workspace that is stored on your PC in `trader\brain\brain.json` (not in git; a `brain.bak.json` copy is refreshed every 30 minutes). The first time it opens it copies your Notion Trade Journal: the pages from `trader\notion\hub.json`, with each checklist page turned into a real empty page you can write in, and the **Trade Journal 2026** table from the Notion export in `trader\import-done` (or `trader\import`), with its screenshots.

- **Pages:** click anywhere to write. Type `/` (or press the + beside a block) for text, headings, bulleted/numbered lists, to-dos, toggles, quotes, callouts, dividers, code, tables, 2 or 3 columns, sub-pages, databases and images. Markdown shortcuts work (`# `, `- `, `1. `, `[] `, `> `, `---`). Select text for bold, italic, underline, strikethrough, code, links and colours. Drag the dotted handle to move a block, or click it to delete, duplicate, turn into another type or colour it. Tab / Shift+Tab indent lists. Tables have + / - rows and columns and header toggles.
- **Databases:** property chips for select and multi-select (create, rename, recolour, delete options), date, number, checkbox, URL and screenshots; Filter, Sort, Search, show or hide and reorder properties, add, rename, retype and delete properties, add, duplicate and delete rows (with Undo), and OPEN a row as a page with its own notes and images.
- **Nothing here is ever sent to Notion.** The editor has no Notion code at all; edits live only in your copy, and saving is automatic (the status at the top right says Saving / Saved). An old tab cannot overwrite newer work.

### Thoughts + Emotions (password locked)

Second Brain has a lock button, **Thoughts + Emotions**. The first time you open it you choose a PIN or password (at least 4 characters, so a 4-digit PIN works; a longer one is much harder to guess). Kelly Terminal then copies that page from Notion once, read only (nothing in Notion is changed), and keeps it in `trader\brain\vault.json`, encrypted with a key made from your password (PBKDF2-SHA256, 600,000 rounds, and an authenticated stream cipher built from HMAC-SHA256). The password is never stored. Wrong guesses are slowed down: after 5 wrong tries it waits 2 minutes.

- It locks again when you leave Second Brain, press Lock, or after 20 minutes of no use. The key only ever lives in memory.
- **If you forget the password, the pages cannot be recovered.** Keep the password somewhere safe.
- Pictures on that Notion page are not copied (they would sit outside the lock). Text, tables, lists and sub-pages are.
- "Copy from Notion (read only)" inside the lock never replaces what you wrote: a second copy arrives as a new sub-page.
- You can write and edit in it exactly like the rest of Second Brain, and change the password from inside.


## Look and keys

Kelly Terminal uses a terminal look: flat black, square panels with small title bars, dense mono numbers, a top bar and a status bar (Dublin time, sessions, when prices and the AI read were last updated). The Geist and Geist Mono fonts are bundled in `assets/fonts`, so nothing is loaded from the internet.

- `1` to `8` jump to Home, Macro, Journal, Backtest, Trades, Alerts, Connections and Second Brain (when you are not typing in a box).
- `` ` `` or `/` opens the command box: type a tab name, `brief` (write the market read now), `refresh` or `theme`.

## Funding

The **Funding** page totals every prop-firm account you hold, split into forex / CFD and futures and into three stages (in evaluation, passed but not activated, funded and active), with a progress bar to your funding goal, your pay-outs, the rules sheet per firm and a live-status card for each account. Most of it is copied read-only from your Notion page (the two funding breakdown tables, the pay-outs list, the goal line, the rules cheat sheet and the Account Scaling System databases), refreshed with the Notion link. Nothing is ever written to Notion. You can add accounts and pay-outs, change a stage or hide a Notion row on the page; those changes live in `funding.json` in the trader folder and are never overwritten by the next Notion copy. If Notion's typed totals disagree with its rows, the page adds up the rows and says so. **Connect accounts** shows what is linked today (Notion, live and read only) and what a real broker feed needs: prop firms have no public account API, so a live feed means a read-only key from you (for example a MetaApi investor password for MT4/MT5 accounts) placed in the trader folder.

## Markets and Settings

**Markets** is a board of live charts you choose. Pick a layout (1, 2x1, 2x2, 3x2, 3x3, 4x3), a timeframe (1m, 5m, 15m, 1h, 1d) and line or candles, for every chart at once or one chart at a time. Type any Yahoo symbol to add a chart (`GC=F`, `NQ=F`, `EURUSD=X`, `^GSPC`, `BTC-USD`, `NVDA`), click a symbol to change it, and use the arrows to reorder. Prices refresh every 20 seconds while the page is open. Forex is live; CME futures (`=F`) run about ten minutes behind on the free Yahoo feed. Your layout is saved in `markets.json` in the data folder.

**Settings** holds the preferences (theme, replay the intro, reset Home, reset charts) and the Connections content that used to be its own page. The `connections` command still works.

## Phone alerts

Alerts normally fire only while the page is open. Turn on **Phone alerts** on the Alerts page and the server also watches your price levels, high-impact news and the London and New York opens once a minute and sends them through [ntfy](https://ntfy.sh) (free app for iPhone and Android). The page shows a private topic name (`kelly-` and 16 random characters, kept in `push_topic.txt` in the trader folder): subscribe the ntfy app to it. Turning the switch off deletes the file. Only run this on one machine (the server), or you will get each alert twice.

## Other page features

- **Customise** on Home hides, shows and reorders the panels and switches to a compact density (saved in the browser).
- **Report** on Journal, Backtest and Forward Test opens a clean one-page report of the period to print or save as a PDF.
- Journal pages also show streaks, the deepest drop, the spread of results in R, and an **AI review** of the week on request.
- If the server restarts for an update, the page now retries quietly instead of showing "Unexpected end of JSON input".
