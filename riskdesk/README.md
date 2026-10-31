# RiskDesk.ai

The dashboard built on the trader-zones skill. Signals only: it never places an order.

Start it on Windows by double-clicking `RiskDesk.bat` in this folder, or from the repo's top folder:

    py riskdesk\dashboard.py

then open http://127.0.0.1:8765.

| File | What it is |
|---|---|
| `dashboard.py` | the local server (standard library only). It uses `../trader-zones/zones.py` for the zones and entries |
| `dashboard.html` | the page: Zones with Indicator Stats, Macro desk, Trades (risk calculators), Journal with the coach, and the floating AI chat box |
| `coach.py` | the Journal coach and the chat box. Asks Claude or Gemini when you press a button or send a message |
| `macro.py` | the Macro desk: prices, economic calendar, headlines, sessions and the AI brief |
| `RiskDesk.bat` | the Windows launcher |
| `RiskDesk_Indicator.pine` | the TradingView indicator (paste it into the Pine Editor on a 5 minute chart) |

Your own settings, saved charts and API keys live in the `trader` folder at the top of the repo, which git leaves
out: `config.json`, `my_zones.json`, `my_levels.json`, `gemini_key.txt` or `anthropic_key.txt`.

## What each tab does

- **Macro**: market sessions, prices (gold, NQ, EURUSD, GBPUSD, dollar, US 10 year, VIX, S&P, oil), the week's USD/EUR/GBP releases, headlines, a plain risk-on/risk-off read, your own bias buttons (kept in this browser), and an AI brief you ask for. Prices come from Yahoo, the calendar from the ForexFactory weekly feed and headlines from Google News, so they can lag your broker.
- **Trades**: a position size calculator (forex, gold, index futures, CFDs) with a contract reference table of value per point and per tick, an exchange rate for accounts in a different currency to the instrument, and optional margin; a risk/reward check; and **Prop firm room**, which takes your prop firm's rules (daily loss, max drawdown fixed or trailing, profit target) and your Journal and shows how much you can still lose today and overall. Trailing drawdown follows closed trades only.
- **Journal**: a total P/L, an edge score, win rate, average R, profit factor, plan followed, a cumulative curve, a month calendar, results by weekday, and results grouped by instrument, setup, emotion, plan or grade. Each trade can carry an optional time, grade (A to D), plan followed or deviated, and how you felt. Results can be grouped by session (Asia, London, New York, Late, on the Dublin clock) or by hour. **Import from a broker CSV** reads a trade history file in the browser (nothing is uploaded), guesses the columns, shows a preview you can correct, skips trades already in the journal and adds the rest. Everything is worked out in the browser from your trades.
- **Chat box** (the button at the bottom right): ask about what is on screen. It sends only the visible zones, entries, levels, your journal totals and the macro numbers, never your keys.

Later ideas: a phone layout with a bottom navigation bar (Dashboard, Journal, Analytics, More).

How the zones, the fib entry and the coach work is written up in `../trader-zones/SKILL.md`.
