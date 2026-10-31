---
name: trader-zones
description: >-
  Zone Finder. Finds ICT-style supply and demand zones on the 1-hour, 30-minute
  and 15-minute charts of five markets (XAUUSD, GBPUSD, EURUSD, NQ and MGC), then
  looks for the 5-minute and 3-minute entry that follows a sweep of a zone: a
  fair value gap (FVG) or inverse FVG (IFVG) inside the 62% to 79% retracement,
  then a 3-minute change of character (CHoCH). Zones are one candle, high to
  low: a small body, long wicks, then a strong move away. Asia, London and New
  York windows only. Free Yahoo data. Use when the user says "find my zones",
  "supply and demand", "demand zone on gold", "any setups on GBPUSD", "FVG
  entry", "IFVG", "sweep and CHoCH", "check NQ", "check MGC", or at the start of
  a London or New York session.
argument-hint: "[markets | zones SYMBOL | setup SYMBOL] [--tf 30m] [--days 2] [--now \"YYYY-MM-DD HH:MM\"]"
---

# trader-zones

The Zone Finder is the forex, gold and futures sibling of the Chart Scanner. It
reads free charts and lists the supply and demand zones that match a written
rule, and the entries that matched the rule after a zone was swept. A zone is a
price area where a rule fired on past bars, not a prediction. Most people who
trade lose money; this tool does not change that. It only draws the same zones
the same way every time, so you can check them against your own marks.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/zones.py" markets                      # the five markets and their Yahoo tickers
python3 "${CLAUDE_SKILL_DIR}/zones.py" zones GBPUSD                 # last 2 days, 60m + 30m + 15m, max 3 a day
python3 "${CLAUDE_SKILL_DIR}/zones.py" zones XAUUSD --tf 30m --days 2 --max-per-day 3
python3 "${CLAUDE_SKILL_DIR}/zones.py" setup NQ                     # sweep + FVG/IFVG in the OTE + M3 CHoCH
python3 "${CLAUDE_SKILL_DIR}/zones.py" setup MGC --now "2026-10-26 08:15" --offline DIR
python3 "${CLAUDE_SKILL_DIR}/zones.py" setup EURUSD --write         # also writes trader/zones.json
python3 "${CLAUDE_SKILL_DIR}/zones.py" explain GBPUSD --at "2026-10-27 02:00" --tf 30m   # why that candle is or is not a zone
```

`bars SYMBOL --tf 3m --last 40` prints the latest candles in Dublin time with the swing highs and
lows the rules use marked, so the user can set the tool's view beside their own chart.

`explain` takes the New York time of one candle and prints every number the rule
looked at: body against ATR, wick share, each following bar's displacement test,
the move away, and the session window. Use it when a zone the user marks by hand
is missing from `zones`. It also says which candle the tool picked instead and why.

`--save DIR` on `zones`, `setup` or `explain` keeps the Yahoo charts that run downloaded
(`DIR/GBPUSD_1m.json` and so on). Replay them later with `--offline DIR --now "..."`, or send them
along when a result needs checking against the user's own chart.

A chart export works in place of a saved Yahoo chart: put `SYMBOL_interval.csv` (for example
`XAUUSD_30m.csv`, `XAUUSD_1m.csv`) in the `--offline` folder. It needs a header with time, open, high, low and
close, with time as Unix seconds or an ISO date and time. This is how the user's own feed (OANDA spot gold, say)
can replace Yahoo's futures candles when calibrating.

`setup SYMBOL --zone KIND LOW HIGH WHEN` uses a box the user drew by hand instead of the detected zones
(`--zone demand 29841.25 29881.25 "2026-10-28 02:00"`, the time being the zone candle's open in Dublin time;
repeat the option for more boxes). It checks the entry logic against the user's own zone.

`--spot PRICE` (on `zones`, `setup`, `explain` and `bars`) takes the gap between the futures price and the
user's own spot chart off every price. Give the price the chart shows now; run it again on another day,
because the gap moves a little. It fixes the price level only: futures and spot candles still differ in shape.
It does nothing for GBPUSD and EURUSD, which are already spot (shifting them moved a zone about 3 pips off
the user's own box).

## Dashboard

`py trader-zones\dashboard.py` opens a page on the user's own computer (http://127.0.0.1:8765, reachable
only from that computer). One tab per market shows the last price, the entries found with entry, stop,
target and reward to risk, a ladder of the zones by timeframe against price, and a table of the zones. All
times are Dublin. The "your chart price now" box does what `--spot` does and is hidden for GBPUSD and
EURUSD, and the price is only trusted for 3 hours (the gap drifts): after that it is cleared and the page asks for a
fresh one. The Zone rule setting switches between "both" (the page's default: the standard zones plus the user's way),
"candle before the move" (the user's way alone: `zone_candle` "last", `move_atr` 1.3, `base_body_atr` 0.7) and
"standard", which is still what the command line uses. "Both" exists because neither rule alone found every box the
user drew by hand. Refresh re-runs the tool; it can
also refresh every 5 minutes.
The dashboard is called RiskDesk.ai. On Windows, double-click `trader-zones\RiskDesk.bat` to start it. The page is `trader-zones/dashboard.html`, laid out like the user's RiskDesk site: black and white, a dark or
light theme (button top right), and three sections. **Zones** has a Frames filter (1 hour, 30 min, 15 min), the
Show zones setting and a Visits column that lists every time price came back to a zone (the session it opened
in, how deep, and whether it held). **Trades** has RiskDesk's position size and risk/reward calculators (the
"Size this trade" button on an entry fills them in) and **Journal** keeps trades in the browser in RiskDesk's
own format, so a RiskDesk backup restores there. Nothing is sent anywhere. `zones --touches` prints the same
visit list on the command line. `--port` changes the port,
`--no-browser` does not open the browser, `--offline DIR` runs from saved charts. It shows signals only
and never places an order.

**My zones.** TradingView does not let a program read the boxes a user draws (and exporting chart data needs
a paid plan), so the dashboard keeps its own list. The My zones card at the bottom of each market takes a
side, a low and high price as the user's chart shows them, and the Dublin time the candle opened. The zone is
saved in `trader/my_zones.json` and from then on every refresh checks it for entries, next to the tool's own
zones (it shows in its own `mine` column on the ladder). When the user sends a screenshot of their boxes,
read each box's prices off the price axis and its start off the time axis, and add it for them (a second copy
of the file is fine to edit by hand: `{"EURUSD": [{"kind": "demand", "low": 1.11325, "high": 1.11404,
"when": "2026-10-14 18:30", "note": ""}]}`). For gold, the user's chart price has to be entered at the top first
so the data lines up with their spot prices. A saved zone with a typo is skipped with a note and the rest
still run.

Exit code 0 when something was found, 1 when nothing was, 2 on an error (the
reason is one plain line).

## The markets

| symbol | Yahoo ticker | what it really is |
| --- | --- | --- |
| XAUUSD | GC=F | COMEX gold futures, standing in for spot gold |
| GBPUSD | GBPUSD=X | spot forex |
| EURUSD | EURUSD=X | spot forex |
| NQ | NQ=F | Nasdaq 100 futures (NAS100, US100 and NDX are accepted) |
| MGC | MGC=F | micro gold futures |

Yahoo's forex 1-minute bars (GBPUSD, EURUSD) carry one price per minute, with no highs or
lows, so the 3-minute candles built from them have approximate wicks and swings, and `setup`
says so. The gold and Nasdaq futures bars are real candles. Expect forex entries to differ from a
chart on another feed by a pip or two.

Gold futures and spot gold are close but not the same price: the futures carry a
premium and roll to a new contract. Treat a gold zone as an area, not an exact
tick, and check it against your own chart. Yahoo's forex and futures bars can lag
and have gaps.

## The zone

One candle, high to low, drawn by a fixed rule so it is the same every run:

1. Displacement: a candle whose body is at least 1.5 x ATR(14) and 60 % of its
   own range, and that closes beyond the last confirmed swing high (demand) or
   swing low (supply).
2. Zone candle: a candle up to 5 back from it with a body under 0.4 x ATR, wicks
   making up more than half its range, and a move away from its extreme of at
   least 2 x ATR. When several candles in a row qualify, the most extreme one is
   the zone: the highest high for supply, the lowest low for demand.
2b. Slow move: no single big candle is needed. A small-bodied, long-wicked candle
   followed by a close 2 x ATR beyond it within 10 bars, with price never trading
   beyond its extreme by more than 0.1 x ATR before then, is a zone too. What came
   before the candle does not matter, so a pause inside a leg counts. One move
   gets one zone.
2c. Which candle. By default the zone is the most extreme candle of the pause before the move.
   `zone_candle` "last" in the config's `zones` block takes the nearest candle with the zone shape,
   the one right before the move ("the candle before the big move, wick to wick"). It usually also
   needs a lower `move_atr` (1.3 instead of 2.0), because that candle's near edge is further from the
   move. On the user's saved charts this reproduces the EURUSD 15m box from Tue 27 08:45 but changes
   zones on every market, including gold ones that match under the default, so it is an option only.
3. Fresh until price trades back into it, then touched.
   **Respected.** The user's rule is that a zone has to be respected at some point. Once price trades
   back into a zone (from the bar after the move that made it), it is judged: *respected* when price then
   moves away from it by `respect_atr` x ATR (default 1.0, measured with the ATR where price came back; the
   bar that first comes back cannot count as its own bounce), *failed* when a bar closes through the far
   edge before that happens (a wick through does not fail it), otherwise *being tested*. A zone that held
   once stays respected. A fresh zone is untested, and a hand-drawn zone has no verdict. `zones` prints the
   verdict on each line and `--hide-failed` leaves out the failed ones; the dashboard has a "Show zones"
   setting (not failed, all, respected only) that never hides the user's own boxes. The verdict is shown and
   filtered only: it does not change which entries are found.
4. Only zones from the last 2 days, at most 3 per day, and only candles that
   opened in an Asia, London or New York window (Dublin time).

## The entry (long shown, short is the mirror image)

1. Price touches a demand zone.
2. On 5-minute bars it sweeps the latest swing low (wick below, close back
   above), with the wick reaching into the zone.
3. After the sweep a 5-minute FVG forms, or a bearish FVG is closed through (an
   IFVG), and its midpoint sits in the 62 % to 79 % retracement of the leg from
   the sweep low to the highest high. The leg is measured when price comes back
   into the gap, or as it stands now if price has not come back yet, so a gap
   that formed in the middle of the leg still counts once the leg is finished.
4. Price trades back into that gap without breaking the sweep low.
5. On 3-minute bars, once price is back in the gap, a close above the last swing high made
   after the sweep (the CHoCH) is the trigger, inside a session window. Set
   `choch_needs_trend` to true to also require that swing to be a lower high than the one
   before it (the stricter, older rule).
6. The stop sits under the sweep low (above the sweep high for a short; set `stop_at` to
   `leg_low` to put it under the low the leg started from instead) and the target is 3
   times the risk (`target_r`; set it to 0 to aim at the nearest opposing zone or swing
   instead). The entry is dropped when the reward to risk is under 2 (`min_rr`). One trade
   seen through several sweeps or zones is listed once, with the other zones named under it.

When nothing triggered, `setup` prints one line per touched zone with how far it got,
for example "swept, no FVG or IFVG with its midpoint in the OTE", so a quiet day is
explained zone by zone. Under each swept zone it also lists the sweep (when, which swing it
took) and each FVG or IFVG after it, with the gap's midpoint against the 62% to 79% band,
so the output can be set beside the user's own 5-minute chart.
Under each gap in the band that price has come back to, one more line shows the 3-minute check: the
last swing after the sweep (the CHoCH level), whether it is a lower high (higher low for a short, shown only with `choch_needs_trend`), the
latest 3-minute close and its time, or that the sweep extreme was taken out first. Set it beside the
3-minute chart to see why a CHoCH did or did not count.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/config.json` | optional `zones` object that overrides any default, for example `{"zones": {"min_rr": 3, "sessions": {"ny": [13, 17]}}}` |
| `trader/zones.json` | written with `--write`: the zones or the setups, with times as Unix seconds |

Session hours are Dublin time (Asia 00:00 to 08:00, London 08:00 to 16:00, New York 13:00 to 21:00), with Irish daylight saving. Set `session_tz` to `ny` to read them as New York time instead, for example the short kill zones. The 3-minute
bars are built from 1-minute bars, and Yahoo keeps about a week of 1-minute
history, so `setup` cannot look further back than that.

## Steps

1. At the start of a session: `zones.py zones SYMBOL` for each market. Say how
   many zones were found and how many are fresh.
2. During a session: `zones.py setup SYMBOL`. Report any entry with its zone,
   sweep time, gap, entry, stop, target and reward to risk, or the stage the
   best zone reached.
3. The user decides what to do. Never pick for them.

## Rules

- Say what was read: the market, the Yahoo ticker, the time of the data.
- Say "matched a rule", never "a buy", "a winner" or "a good trade".
- Never print a P&L, a return or a profit figure.
- A signal is stale once price has moved on. Re-run before acting on it.
- Say plainly that gold is read from futures.

## What it never does

It never places, sizes or checks an order, never says which market to trade, and
never promises that a zone or an entry works.

## Safety

- Education and tooling, not financial advice. A signal is a rules check, not a recommendation.
- This skill cannot place an order. The pack's only order path is trader-broker, and only when
  trader-risk passed that exact order, the user typed an explicit yes for that one order in the
  chat, and the user clicked Allow once on Claude's own card.
- Paper trading by default. Going live is the user's own decision, in their own config.
- Futures and forex are leveraged: a small move can lose more than the account holds. Set the stop
  and the size before any order.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.
