# RiskDesk.ai

The dashboard built on the trader-zones skill. Signals only: it never places an order.

Start it on Windows by double-clicking `RiskDesk.bat` in this folder, or from the repo's top folder:

    py riskdesk\dashboard.py

then open http://127.0.0.1:8765.

| File | What it is |
|---|---|
| `dashboard.py` | the local server (standard library only). It uses `../trader-zones/zones.py` for the zones and entries |
| `dashboard.html` | the page: Zones with Indicator Stats, Trades (risk calculators), Journal with the coach |
| `coach.py` | the Journal coach. Asks Claude or Gemini to review the journal when you press the button |
| `RiskDesk.bat` | the Windows launcher |
| `RiskDesk_Indicator.pine` | the TradingView indicator (paste it into the Pine Editor on a 5 minute chart) |

Your own settings, saved charts and API keys live in the `trader` folder at the top of the repo, which git leaves
out: `config.json`, `my_zones.json`, `my_levels.json`, `gemini_key.txt` or `anthropic_key.txt`.

How the zones, the fib entry and the coach work is written up in `../trader-zones/SKILL.md`.
