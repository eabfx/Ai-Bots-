# Zones

A separate personal project from RiskDesk.ai: supply and demand zones, entries and the TradingView indicator. Signals only, it never places an order.

Start it on Windows by double-clicking `ZonesApp.bat` in this folder, or from the repo's top folder:

    py zones-app\zones_app.py

then open http://127.0.0.1:8766 (RiskDesk.ai uses 8765, so both can run together).

| File | What it is |
|---|---|
| `zones_app.py` | the local server (standard library only). It uses `../trader-zones/zones.py` for the zones and entries |
| `zones.html` | the page: one tab per market, entries, a zone ladder, My zones, My levels and Indicator Stats |
| `ZonesApp.bat` | the Windows launcher |
| `RiskDesk_Indicator.pine` | the TradingView indicator (paste it into the Pine Editor on a 5 minute chart) |

Your own zones and settings live in the `trader` folder at the top of the repo, which git leaves out: `config.json`, `my_zones.json`, `my_levels.json`.

How the zones, the fib entry and the indicator work is written up in `../trader-zones/SKILL.md`.
