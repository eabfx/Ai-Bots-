# RiskDesk.ai

A personal trading dashboard: macro desk, journal, risk calculators, prop firm tracker and an AI assistant. Signals only: it never places an order. The Zones finder is a separate project in `../zones-app`.

Start it on Windows by double-clicking `RiskDesk.bat` in this folder, or from the repo's top folder:

    py riskdesk\dashboard.py

then open http://127.0.0.1:8765.

| File | What it is |
|---|---|
| `dashboard.py` | the local server (standard library only) |
| `dashboard.html` | the page: Home, Macro desk, Journal with the coach, Trades (risk calculators, prop firm room) and the AI assistant |
| `coach.py` | the Journal coach and the chat box. Asks Claude or Gemini when you press a button or send a message |
| `macro.py` | the Macro desk: prices, economic calendar, headlines, sessions and the AI brief |
| `RiskDesk.bat` | the Windows launcher |

Your own settings, saved charts and API keys live in the `trader` folder at the top of the repo, which git leaves
out: `config.json`, `gemini_key.txt` or `anthropic_key.txt`.

## What each page does

- **Home**: the page it opens on. Sessions (open or closed, with a countdown), your numbers (today, last 7 days, 30 day win rate, prop room), market cards for gold, NQ, EURUSD and GBPUSD with a small chart, your bias buttons and the AI read, capital flow (the day's mood and how far each market moved), a price ticker, currency strength (USD, EUR, GBP, JPY, CAD, AUD, CHF, NZD), the news feed, the next 24 hours of releases and the assistant. On a wide screen the pages are in a sidebar on the left.
- **Macro**: market sessions, prices (gold, NQ, EURUSD, GBPUSD, dollar, US 10 year, VIX, S&P, oil), the week's USD/EUR/GBP releases, headlines, a plain risk-on/risk-off read, your own bias buttons (kept in this browser), and an AI brief you ask for. Prices come from Yahoo, the calendar from the ForexFactory weekly feed and headlines from Google News, so they can lag your broker.
- **Trades**: a position size calculator (forex, gold, index futures, CFDs) with a contract reference table of value per point and per tick, an exchange rate for accounts in a different currency to the instrument, and optional margin; a risk/reward check; and **Prop firm room**, which takes your prop firm's rules (daily loss, max drawdown fixed or trailing, profit target) and your Journal and shows how much you can still lose today and overall. Trailing drawdown follows closed trades only.
- **Journal**: a total P/L, an edge score, win rate, average R, profit factor, plan followed, a cumulative curve, a month calendar, results by weekday, and results grouped by instrument, setup, emotion, plan or grade. Each trade can carry an optional time, grade (A to D), plan followed or deviated, and how you felt. Results can be grouped by session (Asia, London, New York, Late, on the Dublin clock) or by hour. **Import from a broker CSV** reads a trade history file in the browser (nothing is uploaded), guesses the columns, shows a preview you can correct, skips trades already in the journal and adds the rest. Everything is worked out in the browser from your trades.
- **Assistant**: on Home it has its own panel, on the other pages it is the button at the bottom right. Ask about what is on screen. It sends only your journal totals, the macro numbers and the calendar, never your keys.

On a narrow screen (a phone, or a small window) the pages move to a bar along the bottom and buttons and fields get bigger. The dashboard still runs on your PC and listens only on that computer, so a phone cannot reach it yet.

The Zones app (zones, entries, Indicator Stats, the TradingView indicator) is described in `../zones-app/README.md`.
