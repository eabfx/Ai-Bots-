#!/usr/bin/env python3
"""A local dashboard for trader-zones: your markets, their zones and the entries found, in a browser.

    py trader-zones\\dashboard.py

opens http://127.0.0.1:8765 on your own computer. Nothing leaves your machine except the chart requests to
Yahoo that zones.py already makes. It shows signals only. It never places an order.

Standard library only, like zones.py. The page is one self-contained file with no outside scripts or fonts.
"""

import argparse
import contextlib
import io
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zones as z    # noqa: E402

PORT = 8765
ORDER = ["XAUUSD", "NQ", "MGC", "GBPUSD", "EURUSD"]


def dublin_text(ts):
    return z.to_dublin(ts).strftime("%a %d %b %H:%M")


def zone_row(zn):
    return {"tf": zn["tf"], "kind": zn["kind"], "low": zn["low"], "high": zn["high"], "status": zn["status"],
            "t": zn["t"], "when": dublin_text(zn["t"]),
            "touched": dublin_text(zn["touched_t"]) if zn.get("touched_t") else None}


def signal_row(r):
    return {"side": r["side"], "trigger": r["trigger"], "zone": dict(r["zone"], when=dublin_text(r["zone"]["t"])),
            "swept": dublin_text(r["sweep_t"]), "gap": list(r["gap"]), "entry_t": r["entry_t"],
            "entry_when": dublin_text(r["entry_t"]), "entry": r["entry"], "stop": r["stop"], "target": r["target"],
            "rr": r["rr"],
            "also": [dict(o, when=dublin_text(o["t"])) for o in r.get("also", [])]}


def mine_path(config_path):
    """The user's own zones live next to the config file, in my_zones.json."""
    return os.path.join(os.path.dirname(os.path.abspath(config_path)), "my_zones.json")


def load_mine(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except ValueError as e:
        raise z.ZoneError("%s is not valid JSON: %s" % (path, e))
    if not isinstance(doc, dict):
        raise z.ZoneError("%s must hold an object with one list of zones per market" % path)
    return doc


def mine_list(doc, sym):
    """The saved zones for one market; an entry that is not an object stays in place (as {}) so numbering holds."""
    items = doc.get(sym)
    return [m if isinstance(m, dict) else {} for m in items] if isinstance(items, list) else []


def save_mine(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


MAX_MINE = 100


def add_mine(path, sym, kind, low, high, when, note=""):
    sym = z.resolve(sym)
    z.manual_zone((kind, low, high, when), [], flag="the zone")            # raises a plain error when it is wrong
    doc = load_mine(path)
    items = mine_list(doc, sym)
    entry = {"kind": kind.lower(), "low": float(low), "high": float(high), "when": when.strip(),
             "note": str(note or "")[:80]}
    if any(all(m.get(k) == entry[k] for k in ("kind", "low", "high", "when")) for m in items):
        raise z.ZoneError("that zone is already in your list")
    if len(items) >= MAX_MINE:
        raise z.ZoneError("you have %d zones saved for %s already: remove some first" % (MAX_MINE, sym))
    doc[sym] = items + [entry]
    save_mine(path, doc)
    return doc[sym]


def remove_mine(path, sym, index):
    sym = z.resolve(sym)
    doc = load_mine(path)
    items = mine_list(doc, sym)
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(items):
        raise z.ZoneError("there is no saved zone number %s for %s" % (index, sym))
    doc[sym] = items[:index] + items[index + 1:]
    save_mine(path, doc)
    return doc[sym]


def snapshot(sym, cfg, now, spot=None, offline=None, shift=None, mine=None):
    """Everything the page shows for one market, as plain JSON-ready data. Errors raise z.ZoneError.
    `spot` is the price on the user's chart right now: the gap to the data is measured and applied.
    `shift` is a gap measured earlier, reused as it is (a price typed an hour ago would give a wrong gap now).
    Spot wins when both are given. Spot forex pairs take neither.
    `mine` is the user's own saved zones for this market: they are checked for entries next to the tool's own."""
    sym = z.resolve(sym)
    notes = []
    shifted = False
    used = 0.0
    saved = z.SHIFT
    try:
        z.SHIFT = 0.0
        if spot is not None:
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                z.SHIFT = z.spot_shift(sym, spot, now, offline, cfg)
            shifted = z.SHIFT != 0.0
            if buf.getvalue().strip():
                notes.append(buf.getvalue().strip())
        elif shift is not None and not z.MARKETS[sym].get("spot"):
            z.SHIFT = shift
            shifted = shift != 0.0
            notes.append("Prices shifted by %s, the gap measured when you entered your price. It drifts through the "
                         "day: type a fresh price to measure it again." % z.px(sym, shift))
        used = z.SHIFT
        specs = []
        for i, m in enumerate(mine or []):
            spec = (m.get("kind"), m.get("low"), m.get("high"), m.get("when"))
            try:
                z.manual_zone(spec, [], flag="the zone")
                specs.append(spec)
            except z.ZoneError as e:
                notes.append("Your saved zone number %d was skipped: %s" % (i + 1, e))
        found = z.analyse(sym, cfg, now, offline, extra_specs=specs)
    finally:
        z.SHIFT = saved
    m1 = found["m1"]
    if z.flat_share(m1) > 0.9:
        notes.append(z.FLAT_NOTE % sym)
    if sym == "XAUUSD" and not shifted:
        notes.append("These are COMEX gold futures prices, about 11 to 16 points above spot gold. Type the price your "
                     "spot chart shows now into the box above and press Refresh to line everything up with it.")
    stages = []
    if not found["results"]:
        for zn, best, trace in found["staged"]:
            stages.append({"head": z.stage_line(sym, zn, best).strip(),
                           "lines": [ln.strip() for ln in z.trace_lines(sym, zn, trace)]})
    market = z.MARKETS[sym]
    return {"symbol": sym, "yahoo": market["yahoo"], "market_note": market["note"], "spot_market": bool(market.get("spot")),
            "digits": market["digits"], "shifted": shifted, "shift_value": used,
            "as_of": dublin_text(now), "now_t": now, "price": m1[-1]["c"] if m1 else None,
            "price_when": dublin_text(m1[-1]["t"] + 60) if m1 else None,
            "days": cfg["days"], "per_day": cfg["max_per_day"], "notes": notes,
            "mine": [dict(m, index=i) for i, m in enumerate(mine or [])],
            "zones": [zone_row(zn) for zn in found["zones"]],
            "signals": [signal_row(r) for r in found["results"]],
            "stages": stages, "furthest": z.STAGES[found["best"]]}


class Handler(BaseHTTPRequestHandler):
    config_path = "trader/config.json"
    offline = None
    mine_file = "trader/my_zones.json"

    def log_message(self, fmt, *args):        # keep the console quiet
        pass

    def send(self, code, body, kind):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, code, doc):
        self.send(code, json.dumps(doc), "application/json; charset=utf-8")

    def host_ok(self):
        """Only answer requests addressed to this computer by name or number, which blocks other websites
        from reaching the dashboard through the browser (DNS rebinding)."""
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in ("127.0.0.1:%d" % port, "localhost:%d" % port)

    def do_POST(self):
        if not self.host_ok():
            return self.send_json(403, {"error": "this dashboard only answers requests from this computer"})
        if self.headers.get("X-Requested-With") != "zones-dashboard":
            return self.send_json(403, {"error": "missing X-Requested-With header"})
        try:
            size = int(self.headers.get("Content-Length") or 0)
            if not 0 < size <= 20000:
                raise z.ZoneError("the request body is empty or too large")
            try:
                body = json.loads(self.rfile.read(size).decode("utf-8"))
            except ValueError:
                raise z.ZoneError("the request body is not valid JSON")
            if not isinstance(body, dict):
                raise z.ZoneError("the request body must be a JSON object")
            path = urlparse(self.path).path
            if path == "/api/mine/add":
                items = add_mine(self.mine_file, str(body.get("symbol", "")), body.get("kind"), body.get("low"),
                                 body.get("high"), body.get("when"), body.get("note", ""))
            elif path == "/api/mine/remove":
                items = remove_mine(self.mine_file, str(body.get("symbol", "")), body.get("index"))
            else:
                return self.send_json(404, {"error": "not found"})
            self.send_json(200, {"mine": [dict(m, index=i) for i, m in enumerate(items)]})
        except z.ZoneError as e:
            self.send_json(400, {"error": str(e)})
        except Exception as e:
            self.send_json(500, {"error": "%s: %s" % (type(e).__name__, e)})

    def do_GET(self):
        if not self.host_ok():
            return self.send_json(403, {"error": "this dashboard only answers requests from this computer"})
        url = urlparse(self.path)
        if url.path == "/":
            self.send(200, PAGE, "text/html; charset=utf-8")
        elif url.path == "/api/markets":
            self.send_json(200, {"markets": [{"symbol": s, "spot": bool(z.MARKETS[s].get("spot")),
                                              "note": z.MARKETS[s]["note"]} for s in ORDER]})
        elif url.path == "/api/snapshot":
            self.snapshot(parse_qs(url.query))
        else:
            self.send_json(404, {"error": "not found"})

    def snapshot(self, q):
        try:
            sym = (q.get("symbol") or [""])[0]
            spot_text = (q.get("spot") or [""])[0].strip()
            try:
                spot = float(spot_text) if spot_text else None
            except ValueError:
                raise z.ZoneError("your price must be a number like 4021.5")
            shift_text = (q.get("shift") or [""])[0].strip()
            try:
                shift = float(shift_text) if shift_text else None
            except ValueError:
                raise z.ZoneError("shift must be a number")
            cfg = z.read_config(self.config_path)
            days = (q.get("days") or [""])[0]
            if days:
                if not days.isdigit() or not 1 <= int(days) <= 14:
                    raise z.ZoneError("days must be a whole number from 1 to 14")
                cfg["days"] = int(days)
            per_day = (q.get("per_day") or [""])[0]
            if per_day:
                if not per_day.isdigit() or not 1 <= int(per_day) <= 20:
                    raise z.ZoneError("per_day must be a whole number from 1 to 20")
                cfg["max_per_day"] = int(per_day)
            now = z.parse_now((q.get("now") or [""])[0] or None)
            mine = mine_list(load_mine(self.mine_file), z.resolve(sym))
            self.send_json(200, snapshot(sym, cfg, now, spot, self.offline, shift, mine))
        except z.ZoneError as e:
            self.send_json(400, {"error": str(e)})
        except Exception as e:                      # a network or data problem: say so, keep serving
            self.send_json(500, {"error": "%s: %s" % (type(e).__name__, e)})


class DashboardServer(HTTPServer):
    # On Windows, address reuse lets a second dashboard bind a port that is already in use and run beside the
    # first without any error, so it is turned off there. Elsewhere it only helps a quick restart.
    allow_reuse_address = os.name != "nt"


def make_server(port=PORT, config_path="trader/config.json", offline=None):
    handler = type("Bound", (Handler,), {"config_path": config_path, "offline": offline,
                                         "mine_file": mine_path(config_path)})
    return DashboardServer(("127.0.0.1", port), handler)


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zones dashboard</title>
<style>
:root{--bg:#f4f3ef;--panel:#fff;--ink:#1d2329;--mute:#68717a;--line:#dedbd3;--demand:#1f8a5b;--supply:#c23b3b;
--accent:#2457c5;--warn:#9a6a00;--warnbg:#fff4d6}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#14181c;--panel:#1d2329;--ink:#e8ebee;
--mute:#98a2ac;--line:#2f373f;--demand:#38b57f;--supply:#e26a6a;--accent:#7aa2f7;--warn:#e5b84c;--warnbg:#3a3118}}
:root[data-theme="dark"]{--bg:#14181c;--panel:#1d2329;--ink:#e8ebee;--mute:#98a2ac;--line:#2f373f;--demand:#38b57f;
--supply:#e26a6a;--accent:#7aa2f7;--warn:#e5b84c;--warnbg:#3a3118}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:16px}
header{display:flex;flex-wrap:wrap;gap:12px;align-items:baseline;justify-content:space-between;margin-bottom:8px}
h1{font-size:20px;margin:0}
.safe{color:var(--mute);font-size:13px}
.tabs{display:flex;flex-wrap:wrap;gap:6px;margin:12px 0}
.tab{border:1px solid var(--line);background:var(--panel);color:var(--ink);padding:8px 14px;border-radius:8px;cursor:pointer;font:inherit}
.tab[aria-selected="true"]{border-color:var(--accent);box-shadow:inset 0 0 0 1px var(--accent)}
.bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:12px}
.bar label{color:var(--mute);font-size:13px;display:flex;gap:6px;align-items:center}
input,select,button{font:inherit;color:var(--ink);background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:7px 10px}
input[type=number]{width:120px}
button.go{background:var(--accent);border-color:var(--accent);color:#fff;cursor:pointer}
button:disabled{opacity:.6;cursor:wait}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:14px}
.card h2{font-size:15px;margin:0 0 10px}
.price{font-size:28px;font-weight:600;font-variant-numeric:tabular-nums}
.mute{color:var(--mute)}
.note{background:var(--warnbg);color:var(--warn);border-radius:8px;padding:8px 10px;margin:8px 0;font-size:13px}
.err{background:#fde7e7;color:#8c1d1d;border-radius:8px;padding:10px 12px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);font-size:14px;white-space:nowrap}
th{color:var(--mute);font-weight:500;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.scroll{overflow-x:auto}
.tag{display:inline-block;border-radius:6px;padding:1px 8px;font-size:12px;font-weight:600;color:#fff}
.tag.long,.tag.demand{background:var(--demand)}
.tag.short,.tag.supply{background:var(--supply)}
.sig{border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin-bottom:10px}
.sig .row{display:flex;flex-wrap:wrap;gap:6px 22px;margin-top:6px}
.sig .row div span{display:block;color:var(--mute);font-size:12px}
.sig .row div b{font-variant-numeric:tabular-nums}
.stage{margin:0 0 10px}
.stage .head{font-weight:600;font-size:14px}
.stage .ln{color:var(--mute);font-size:13px;margin-left:12px}
svg text{fill:var(--mute);font-size:11px}
.empty{color:var(--mute)}
.form{display:flex;flex-wrap:wrap;gap:8px;align-items:flex-end;margin-top:10px}
.form label{display:flex;flex-direction:column;gap:3px;color:var(--mute);font-size:12px}
.form input[type=text]{width:170px}
button.small{padding:3px 10px;font-size:13px;cursor:pointer}
details summary{cursor:pointer;margin-top:4px}
details div{margin-left:16px;font-size:13px}
@media (max-width:560px){.price{font-size:24px}}
</style>
</head>
<body>
<div class="wrap">
<header>
<h1>Zones dashboard</h1>
<div class="safe">Signals only. Nothing is ever placed from here. Set your stop and size before any order. Times are Dublin.</div>
</header>
<div class="tabs" id="tabs" role="tablist"></div>
<div class="bar">
<label id="spotLabel">Your chart price now <input type="number" step="any" id="spot" placeholder="e.g. 4021.5"></label>
<label>Days <select id="days"><option>1</option><option selected>2</option><option>3</option><option>5</option><option>7</option></select></label>
<label>Entries <select id="window"><option value="24" selected>last 24 hours</option><option value="72">last 3 days</option><option value="0">all</option></select></label>
<label>Zones per day <select id="perday"><option selected>3</option><option>6</option><option>10</option><option>20</option></select></label>
<button class="go" id="refresh">Refresh</button>
<label><input type="checkbox" id="auto" style="padding:0"> Refresh every 5 minutes</label>
</div>
<div id="out"><div class="card empty">Loading...</div></div>
</div>
<script>
(function(){
var markets=[], current=null, timer=null, busy=false;
var $=function(id){return document.getElementById(id)};
function store(k,v){try{if(v===undefined){return localStorage.getItem(k)}localStorage.setItem(k,v)}catch(e){return null}}
function el(tag,cls,text){var n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n}
function fmt(x,d){return x===null||x===undefined?"-":Number(x).toFixed(d)}
function age(sec){
  if(sec<0)sec=0;var m=Math.round(sec/60);
  if(m<60)return m+" min ago";
  var h=Math.floor(m/60);if(h<48)return h+"h "+(m%60)+"m ago";
  return Math.floor(h/24)+" days ago";
}
function tag(kind){return el("span","tag "+kind,kind.toUpperCase())}

function tabs(){
  var box=$("tabs");box.textContent="";
  markets.forEach(function(m){
    var b=el("button","tab",m.symbol);b.setAttribute("role","tab");
    b.setAttribute("aria-selected",String(m.symbol===current));
    b.onclick=function(){current=m.symbol;store("zd.market",current);tabs();spotBox();load()};
    box.appendChild(b);
  });
}
function spotBox(){
  var m=markets.filter(function(x){return x.symbol===current})[0];
  var show=m&&!m.spot;
  $("spotLabel").style.display=show?"":"none";
  $("spot").value=show?(store("zd.spot."+current)||""):"";
}
function table(head,rows){
  var wrap=el("div","scroll"),t=el("table"),tr=el("tr");
  head.forEach(function(h){tr.appendChild(el("th",null,h))});t.appendChild(tr);
  rows.forEach(function(r){var row=el("tr");r.forEach(function(c){var td=el("td");
    if(c&&c.nodeType){td.appendChild(c)}else{td.textContent=c}row.appendChild(td)});t.appendChild(row)});
  wrap.appendChild(t);return wrap;
}
function ladder(d){
  var zs=d.zones;if(!zs.length)return null;
  var lanes=["60m","30m","15m"],W=720,H=300,L=64,top=14,bot=14;
  if(zs.some(function(x){return x.tf==="mine"}))lanes.push("mine");
  var lo=Math.min.apply(null,zs.map(function(x){return x.low})),hi=Math.max.apply(null,zs.map(function(x){return x.high}));
  if(d.price!==null){lo=Math.min(lo,d.price);hi=Math.max(hi,d.price)}
  var pad=(hi-lo)*0.05||1;lo-=pad;hi+=pad;
  function y(p){return top+(hi-p)/(hi-lo)*(H-top-bot)}
  var ns="http://www.w3.org/2000/svg",s=document.createElementNS(ns,"svg");
  s.setAttribute("viewBox","0 0 "+W+" "+H);s.setAttribute("width","100%");s.setAttribute("role","img");
  s.setAttribute("aria-label","Zones by timeframe against price");
  function add(tagName,attrs,text){var n=document.createElementNS(ns,tagName);
    for(var k in attrs){n.setAttribute(k,attrs[k])}if(text!==undefined)n.textContent=text;s.appendChild(n);return n}
  for(var i=0;i<=4;i++){var p=lo+(hi-lo)*i/4;
    add("line",{x1:L,x2:W,y1:y(p),y2:y(p),stroke:"var(--line)","stroke-width":1});
    add("text",{x:L-6,y:y(p)+4,"text-anchor":"end"},fmt(p,d.digits>3?4:2))}
  var lw=(W-L)/lanes.length;
  lanes.forEach(function(name,i){add("text",{x:L+lw*i+lw/2,y:11,"text-anchor":"middle"},name);
    var inl=zs.filter(function(x){return x.tf===name});
    inl.forEach(function(x,j){
      var w=Math.max(10,(lw-12)/Math.max(inl.length,1)),xx=L+lw*i+6+j*((lw-12-w)/Math.max(inl.length-1,1));
      if(inl.length===1)xx=L+lw*i+lw/2-w/2;
      var r=add("rect",{x:xx,y:y(x.high),width:w,height:Math.max(2,y(x.low)-y(x.high)),rx:2,
        fill:x.kind==="demand"?"var(--demand)":"var(--supply)","fill-opacity":x.status==="fresh"?0.7:0.3,
        stroke:x.kind==="demand"?"var(--demand)":"var(--supply)"});
      var tt=document.createElementNS(ns,"title");
      tt.textContent=x.kind+" "+x.tf+" "+x.when+"  "+fmt(x.low,d.digits)+" to "+fmt(x.high,d.digits)+"  "+x.status;
      r.appendChild(tt)})});
  if(d.price!==null){add("line",{x1:L,x2:W,y1:y(d.price),y2:y(d.price),stroke:"var(--accent)","stroke-width":1.5,"stroke-dasharray":"5 4"});
    add("text",{x:W-2,y:y(d.price)-4,"text-anchor":"end"},"price "+fmt(d.price,d.digits))}
  return s;
}
var lastData=null;
function render(d){
  lastData=d;
  var out=$("out");out.textContent="";
  var c=el("div","card");
  var top=el("div");top.style.cssText="display:flex;flex-wrap:wrap;gap:6px 24px;align-items:baseline";
  top.appendChild(el("div","price",fmt(d.price,d.digits)));
  var meta=el("div","mute",d.symbol+" ("+d.yahoo+"), "+d.market_note+". Last bar "+(d.price_when||"-")+", checked "+d.as_of+" Dublin.");
  top.appendChild(meta);c.appendChild(top);
  d.notes.forEach(function(n){c.appendChild(el("div","note",n))});
  out.appendChild(c);

  var hrs=parseInt($("window").value,10)||0;
  var shown=d.signals.slice().reverse().filter(function(r){return hrs===0||d.now_t-r.entry_t<=hrs*3600});
  var sc=el("div","card");sc.appendChild(el("h2",null,"Entries found ("+shown.length+
    (shown.length<d.signals.length?" of "+d.signals.length+", change Entries above to see older ones":"")+")"));
  if(!d.signals.length){
    sc.appendChild(el("div","empty","No entry. Furthest any zone got: "+d.furthest+"."));
    d.stages.forEach(function(s){var b=el("div","stage");b.appendChild(el("div","head",s.head));
      s.lines.forEach(function(l){b.appendChild(el("div","ln",l))});sc.appendChild(b)});
  }
  if(d.signals.length&&!shown.length){
    sc.appendChild(el("div","empty","No entry in this window. The most recent was "+
      d.signals[d.signals.length-1].entry_when+" ("+age(d.now_t-d.signals[d.signals.length-1].entry_t)+")."));
  }
  shown.forEach(function(r){
    var b=el("div","sig"),h=el("div");h.appendChild(tag(r.side));
    h.appendChild(el("span",null," "+r.zone.tf+" "+r.zone.kind+" zone "+fmt(r.zone.low,d.digits)+" to "+fmt(r.zone.high,d.digits)+
      ", swept "+r.swept+", "+r.trigger+" "+fmt(r.gap[0],d.digits)+" to "+fmt(r.gap[1],d.digits)+" in the OTE"));
    b.appendChild(h);
    var row=el("div","row");
    [["Entry time",r.entry_when+" ("+age(d.now_t-r.entry_t)+")"],["Entry",fmt(r.entry,d.digits)],["Stop",fmt(r.stop,d.digits)],
     ["Target",r.target===null?"none":fmt(r.target,d.digits)],["Reward to risk",r.rr===null?"-":r.rr.toFixed(1)]].forEach(function(p){
      var x=el("div");x.appendChild(el("span",null,p[0]));x.appendChild(el("b",null,p[1]));row.appendChild(x)});
    b.appendChild(row);
    if(r.also.length){
      var dt=el("details","mute"),sm=el("summary",null,"Same trade also seen from "+r.also.length+" other zone"+(r.also.length>1?"s":""));
      dt.appendChild(sm);
      r.also.forEach(function(o){dt.appendChild(el("div",null,"The "+o.tf+" "+o.kind+" zone "+
        fmt(o.low,d.digits)+" to "+fmt(o.high,d.digits)))});
      b.appendChild(dt)}
    sc.appendChild(b)});
  out.appendChild(sc);

  var zc=el("div","card");zc.appendChild(el("h2",null,"Zones (last "+d.days+" day"+(d.days>1?"s":"")+", up to "+d.per_day+" a day)"));
  var lad=ladder(d);if(lad)zc.appendChild(lad);
  if(!d.zones.length){zc.appendChild(el("div","empty","No zones. The rules are strict: a small-bodied, long-wicked candle, then a strong move away."))}
  else{zc.appendChild(table(["Side","Frame","Zone candle (Dublin)","Low","High","Status"],d.zones.slice().reverse().map(function(x){
    return [tag(x.kind),x.tf,x.when,fmt(x.low,d.digits),fmt(x.high,d.digits),x.status+(x.touched?", back in "+x.touched:"")]})))}
  out.appendChild(zc);
  out.appendChild(mineCard(d));
}
function post(url,body){
  return fetch(url,{method:"POST",headers:{"Content-Type":"application/json","X-Requested-With":"zones-dashboard"},
    body:JSON.stringify(body)}).then(function(r){return r.json().then(function(j){return{ok:r.ok,j:j}})});
}
function mineCard(d){
  var c=el("div","card");c.appendChild(el("h2",null,"My zones ("+d.mine.length+")"));
  c.appendChild(el("div","mute","Boxes you drew yourself. They are saved on this computer and checked for entries on every "+
    "refresh, next to the tool's own zones. Type prices as your chart shows them"+
    (d.symbol==="XAUUSD"?" (enter your chart price at the top first, so gold lines up)":"")+"."));
  if(d.mine.length){
    c.appendChild(table(["Side","Low","High","Candle opened (Dublin)","Note",""],d.mine.map(function(m){
      var rm=el("button","small","Remove");
      rm.onclick=function(){post("/api/mine/remove",{symbol:d.symbol,index:m.index}).then(function(x){
        if(x.ok){load()}else{msg.textContent=x.j.error||"Could not remove it"}})};
      return [m.kind?tag(m.kind):"?",m.low===undefined?"-":fmt(m.low,d.digits),m.high===undefined?"-":fmt(m.high,d.digits),
        m.when||"-",m.note||"",rm]})));
  }
  var f=el("div","form"),side=el("select"),low=el("input"),high=el("input"),when=el("input"),note=el("input"),
      add=el("button","go","Add zone"),msg=el("div","err");
  msg.style.display="none";
  ["demand","supply"].forEach(function(k){var o=el("option",null,k);o.value=k;side.appendChild(o)});
  low.type=high.type="number";low.step=high.step="any";when.type=note.type="text";
  when.placeholder="2026-10-28 02:00";note.placeholder="optional";note.maxLength=80;
  function lab(text,input){var l=el("label",null,text);l.appendChild(input);f.appendChild(l)}
  lab("Side",side);lab("Low",low);lab("High",high);lab("Candle opened (Dublin time)",when);lab("Note",note);
  f.appendChild(add);c.appendChild(f);c.appendChild(msg);
  add.onclick=function(){
    msg.style.display="none";
    post("/api/mine/add",{symbol:d.symbol,kind:side.value,low:low.value,high:high.value,when:when.value.trim(),note:note.value})
      .then(function(x){if(x.ok){load()}else{msg.textContent=x.j.error||"Could not add it";msg.style.display=""}})
      .catch(function(e){msg.textContent="Could not reach the dashboard server: "+e;msg.style.display=""});
  };
  return c;
}
function showError(msg){var out=$("out");out.textContent="";out.appendChild(el("div","err",msg))}
function load(){
  if(busy||!current)return;busy=true;
  var btn=$("refresh");btn.disabled=true;btn.textContent="Loading...";
  var m=markets.filter(function(x){return x.symbol===current})[0];
  var typed=(!m||m.spot)?"":$("spot").value.trim(), sym=current, query="";
  if(m&&!m.spot)store("zd.spot."+sym,typed);
  store("zd.days",$("days").value);store("zd.perday",$("perday").value);
  var kept=null;try{kept=JSON.parse(store("zd.gap."+sym)||"null")}catch(e){kept=null}
  if(typed&&kept&&kept.text===typed){query="&shift="+encodeURIComponent(kept.v)}     // same price as before: reuse its gap
  else if(typed){query="&spot="+encodeURIComponent(typed)}                           // a new price: measure the gap now
  else{store("zd.gap."+sym,"")}
  var url="/api/snapshot?symbol="+encodeURIComponent(sym)+"&days="+encodeURIComponent($("days").value)+
    "&per_day="+encodeURIComponent($("perday").value)+query;
  fetch(url).then(function(r){return r.json().then(function(j){return{ok:r.ok,j:j}})}).then(function(x){
    if(x.ok){
      if(typed&&query.indexOf("&spot=")===0)store("zd.gap."+sym,JSON.stringify({text:typed,v:x.j.shift_value}));
      render(x.j)
    }else{showError(x.j.error||"Something went wrong")}
  }).catch(function(e){showError("Could not reach the dashboard server: "+e)}).then(function(){
    busy=false;btn.disabled=false;btn.textContent="Refresh"});
}
function auto(){clearInterval(timer);if($("auto").checked)timer=setInterval(load,300000)}
$("refresh").onclick=load;$("auto").onchange=auto;
$("window").onchange=function(){store("zd.window",$("window").value);if(lastData)render(lastData)};
$("spot").onkeydown=function(e){if(e.key==="Enter")load()};
var d0=store("zd.days");if(d0)$("days").value=d0;
var p0=store("zd.perday");if(p0)$("perday").value=p0;
var w0=store("zd.window");if(w0)$("window").value=w0;
fetch("/api/markets").then(function(r){return r.json()}).then(function(j){
  markets=j.markets;var saved=store("zd.market");
  current=markets.some(function(m){return m.symbol===saved})?saved:markets[0].symbol;
  tabs();spotBox();load();
}).catch(function(e){showError("Could not reach the dashboard server: "+e)});
})();
</script>
</body>
</html>
"""


def main(argv=None):
    p = argparse.ArgumentParser(prog="dashboard.py", description="Local dashboard for trader-zones (signals only)")
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--offline", help="folder with saved Yahoo charts, to try the dashboard without the internet")
    p.add_argument("--no-browser", action="store_true", help="do not open the browser")
    args = p.parse_args(argv)
    try:
        server = make_server(args.port, args.config, args.offline)
    except OSError as e:
        print("error: cannot use port %d (%s). Try --port 8766." % (args.port, e), file=sys.stderr)
        return 2
    url = "http://127.0.0.1:%d" % args.port
    print("Dashboard running at %s  (press Ctrl+C to stop). Signals only, nothing is placed." % url)
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
