"""
Daily Stock Alert — Earnings, News & Macro Events
Watchlist: TDOC, NVO, BABA, NOW, SMH, META, GOOGL, AMD, MRNA, CVNA, NKE
Schedule: Mon–Fri at 8 AM ET via GitHub Actions

Improvements:
  - Price alerts: only highlights tickers with ±3%+ moves (daily email still sends, movers flagged)
  - Earnings countdown: badge shows "In X days" for upcoming earnings within 3 days
  - Weekly Friday digest: Friday email includes full week summary (best/worst movers, week % change)
"""

import os
import smtplib
import requests
import yfinance as yf
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import date, timedelta, datetime


# ── Config ────────────────────────────────────────────────────────────────────
WATCHLIST            = ["TDOC", "NVO", "BABA", "NOW", "SMH", "META", "GOOGL", "AMD", "MRNA", "CVNA", "NKE"]
PRICE_ALERT_PCT      = 3.0          # flag tickers that move ±3% or more
EARNINGS_WARN_DAYS   = 3            # highlight earnings within this many days

# ── Portfolio holdings ────────────────────────────────────────────────────────
# Format: "TICKER": (shares, avg_buy_price)
# Update these whenever you buy/sell. Tickers here don't need to be in WATCHLIST.
PORTFOLIO = {
    "META":  (10,  480.00),
    "GOOGL": (5,   170.00),
    "AMD":   (15,  145.00),
    "NOW":   (3,   850.00),
    "NKE":   (250,   53.00),
    "MRNA":  (100,   27.00),
    "TDOC":  (450,   7.00),
    "NVO":   (800,   37.00),
    "BABA":  (50,   122.00),
    "CVNA":  (-385,  81.00),
    "SMH":   (-100,   576.00),
}

FINNHUB_KEY  = os.environ["FINNHUB_KEY"]
GMAIL_USER   = os.environ["GMAIL_USER"]
GMAIL_PASS   = os.environ["GMAIL_APP_PASSWORD"]
ALERT_TO     = [e.strip() for e in os.environ["ALERT_TO_EMAIL"].split(",")]

FINNHUB_BASE = "https://finnhub.io/api/v1"
TODAY        = date.today()
TODAY_STR    = TODAY.isoformat()
WEEK_END_STR = (TODAY + timedelta(days=7)).isoformat()
IS_FRIDAY    = TODAY.weekday() == 4


# ── Helpers ───────────────────────────────────────────────────────────────────
def fh(endpoint, params):
    params["token"] = FINNHUB_KEY
    r = requests.get(f"{FINNHUB_BASE}{endpoint}", params=params, timeout=10)
    r.raise_for_status()
    return r.json()

def pct(val):
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.2f}%"

def color_pct(val):
    c = "#16a34a" if val >= 0 else "#dc2626"
    return f'<span style="color:{c};font-weight:600">{pct(val)}</span>'


# ── 1. Price snapshot (with ±3% alert flagging) ───────────────────────────────
def get_price_snapshot():
    rows = []
    for ticker in WATCHLIST:
        try:
            t    = yf.Ticker(ticker)
            hist = t.history(period="2d")
            if len(hist) < 2:
                continue
            prev  = hist["Close"].iloc[-2]
            close = hist["Close"].iloc[-1]
            chg   = (close - prev) / prev * 100
            rows.append({
                "ticker":  ticker,
                "price":   close,
                "chg":     chg,
                "alert":   abs(chg) >= PRICE_ALERT_PCT,   # ← NEW: flag big movers
            })
        except Exception as e:
            print(f"[price] {ticker}: {e}")
    return rows


# ── 2. Weekly price summary (Fridays only) ────────────────────────────────────
def get_weekly_summary():
    """Fetch Mon–Fri % change for each ticker. Called only on Fridays."""
    rows = []
    # Go back enough to cover Mon of this week
    period_start = TODAY - timedelta(days=TODAY.weekday() + 1)  # last Sunday
    for ticker in WATCHLIST:
        try:
            t    = yf.Ticker(ticker)
            hist = t.history(start=period_start.isoformat(), end=TODAY_STR)
            if len(hist) < 2:
                continue
            week_open  = hist["Close"].iloc[0]
            week_close = hist["Close"].iloc[-1]
            week_chg   = (week_close - week_open) / week_open * 100
            week_high  = hist["High"].max()
            week_low   = hist["Low"].min()
            rows.append({
                "ticker":     ticker,
                "week_chg":   week_chg,
                "week_close": week_close,
                "week_high":  week_high,
                "week_low":   week_low,
            })
        except Exception as e:
            print(f"[weekly] {ticker}: {e}")
    rows.sort(key=lambda x: x["week_chg"], reverse=True)
    return rows


# ── 3. Earnings calendar (countdown + full consensus) ────────────────────────
def get_earnings():
    results = []
    try:
        data = fh("/calendar/earnings", {"from": TODAY_STR, "to": WEEK_END_STR})
        cal  = data.get("earningsCalendar", [])
        for item in cal:
            if item.get("symbol") not in WATCHLIST:
                continue

            # Days until earnings
            try:
                earn_date = date.fromisoformat(item["date"])
                days_away = (earn_date - TODAY).days
            except Exception:
                days_away = 99
            item["days_away"] = days_away
            item["imminent"]  = days_away <= EARNINGS_WARN_DAYS

            ticker = item["symbol"]

            # ── Historical EPS surprise (last reported quarter) ───────────────
            try:
                hist_eps = fh("/stock/earnings", {"symbol": ticker, "limit": 4})
                if hist_eps:
                    last = hist_eps[0]
                    item["eps_actual"]   = last.get("actual")
                    item["eps_estimate"] = last.get("estimate")
                    item["eps_surprise"] = last.get("surprise")
                    item["eps_surp_pct"] = last.get("surprisePercent")
                else:
                    item["eps_actual"]   = item.get("epsActual")
                    item["eps_estimate"] = item.get("epsEstimate")
                    item["eps_surprise"] = None
                    item["eps_surp_pct"] = None
            except Exception:
                item["eps_actual"]   = item.get("epsActual")
                item["eps_estimate"] = item.get("epsEstimate")
                item["eps_surprise"] = None
                item["eps_surp_pct"] = None

            # ── Revenue growth from basic metrics ────────────────────────────
            try:
                bf = fh("/stock/metric", {"symbol": ticker, "metric": "all"})
                metric = bf.get("metric", {})
                item["rev_growth"] = metric.get("revenueGrowthTTMYoy")
                item["pe_ratio"]   = metric.get("peBasicExclExtraTTM")
            except Exception:
                item["rev_growth"] = None
                item["pe_ratio"]   = None

            # ── Analyst buy/hold/sell counts ─────────────────────────────────
            try:
                rec = fh("/stock/recommendation", {"symbol": ticker})
                if rec:
                    r = rec[0]
                    item["analyst_buy"]   = r.get("buy", 0)
                    item["analyst_hold"]  = r.get("hold", 0)
                    item["analyst_sell"]  = r.get("sell", 0)
                    item["analyst_total"] = item["analyst_buy"] + item["analyst_hold"] + item["analyst_sell"]
                else:
                    item["analyst_buy"] = item["analyst_hold"] = item["analyst_sell"] = item["analyst_total"] = 0
            except Exception:
                item["analyst_buy"] = item["analyst_hold"] = item["analyst_sell"] = item["analyst_total"] = 0

            results.append(item)

    except Exception as e:
        print(f"[earnings] {e}")

    results.sort(key=lambda x: x.get("days_away", 99))
    return results


# ── 4. Company news (today) ───────────────────────────────────────────────────
def get_news():
    all_news = []
    for ticker in WATCHLIST:
        try:
            items = fh("/company-news", {"symbol": ticker, "from": TODAY_STR, "to": TODAY_STR})
            for n in items[:3]:
                all_news.append({
                    "ticker":   ticker,
                    "headline": n.get("headline", ""),
                    "source":   n.get("source", ""),
                    "url":      n.get("url", "#"),
                    "datetime": n.get("datetime", 0),
                })
        except Exception as e:
            print(f"[news] {ticker}: {e}")
    all_news.sort(key=lambda x: x["datetime"], reverse=True)
    return all_news


# ── 5. Macro calendar (free — Nasdaq API, Yahoo fallback) ────────────────────
def get_macro():
    events = []
    HIGH_IMPACT = {
        "fed", "fomc", "cpi", "pce", "nonfarm", "payroll", "gdp",
        "unemployment", "retail sales", "inflation", "ppi", "ism",
        "interest rate", "jobs", "housing"
    }
    try:
        url     = "https://api.nasdaq.com/api/calendar/economicevents"
        params  = {"date": TODAY_STR, "datestart": TODAY_STR, "dateend": WEEK_END_STR}
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        r = requests.get(url, params=params, headers=headers, timeout=10)
        r.raise_for_status()
        for row in r.json().get("data", {}).get("rows", []):
            name = row.get("eventName", "")
            if any(k in name.lower() for k in HIGH_IMPACT):
                events.append({
                    "event":    name,
                    "date":     row.get("eventDate", ""),
                    "actual":   row.get("actual", "—"),
                    "estimate": row.get("consensus", "—"),
                })
        print(f"[macro] fetched {len(events)} events from Nasdaq")
    except Exception as e:
        print(f"[macro] Nasdaq failed: {e} — trying Yahoo fallback")
        try:
            from html.parser import HTMLParser
            url     = "https://finance.yahoo.com/calendar/economic"
            headers = {"User-Agent": "Mozilla/5.0"}
            r = requests.get(url, headers=headers, timeout=10)

            class TableParser(HTMLParser):
                def __init__(self):
                    super().__init__(); self.in_td=False; self.cells=[]; self.current=[]
                def handle_starttag(self, tag, attrs):
                    if tag=="td": self.in_td=True
                    if tag=="tr":
                        if self.current: self.cells.append(self.current)
                        self.current=[]
                def handle_endtag(self, tag):
                    if tag=="td": self.in_td=False
                def handle_data(self, data):
                    if self.in_td: self.current.append(data.strip())

            parser = TableParser(); parser.feed(r.text)
            for row in parser.cells:
                if len(row) >= 2:
                    name = row[1] if len(row) > 1 else row[0]
                    if any(k in name.lower() for k in HIGH_IMPACT):
                        events.append({"event": name, "date": row[0], "actual": row[3] if len(row)>3 else "—", "estimate": row[2] if len(row)>2 else "—"})
            print(f"[macro] fallback fetched {len(events)} events")
        except Exception as e2:
            print(f"[macro] fallback also failed: {e2}")
    return events[:8]


# ── 6. Analyst consensus ──────────────────────────────────────────────────────
def get_analyst_actions():
    actions = []
    for ticker in WATCHLIST:
        try:
            data = fh("/stock/recommendation", {"symbol": ticker})
            if data:
                latest = data[0]
                actions.append({
                    "ticker": ticker,
                    "buy":    latest.get("buy", 0),
                    "hold":   latest.get("hold", 0),
                    "sell":   latest.get("sell", 0),
                    "period": latest.get("period", ""),
                })
        except Exception as e:
            print(f"[analyst] {ticker}: {e}")
    return actions


# ── 7. Portfolio snapshot ─────────────────────────────────────────────────────
def get_portfolio():
    """
    Fetches current prices for each holding, calculates:
      - Current value, cost basis, total gain/loss $, total gain/loss %
      - Daily P&L for each position
      - Portfolio allocation % per position
      - Overall portfolio summary (total value, total gain, day change)
    """
    positions = []
    total_value    = 0.0
    total_cost     = 0.0
    total_day_gain = 0.0

    for ticker, (shares, avg_price) in PORTFOLIO.items():
        try:
            t    = yf.Ticker(ticker)
            hist = t.history(period="2d")
            if len(hist) < 1:
                continue

            current_price = hist["Close"].iloc[-1]
            prev_price    = hist["Close"].iloc[-2] if len(hist) >= 2 else current_price

            cost_basis    = shares * avg_price
            current_value = shares * current_price
            total_gain    = current_value - cost_basis
            total_gain_pct= (total_gain / cost_basis) * 100 if cost_basis else 0
            day_gain      = shares * (current_price - prev_price)
            day_gain_pct  = (current_price - prev_price) / prev_price * 100 if prev_price else 0

            positions.append({
                "ticker":        ticker,
                "shares":        shares,
                "avg_price":     avg_price,
                "current_price": current_price,
                "cost_basis":    cost_basis,
                "current_value": current_value,
                "total_gain":    total_gain,
                "total_gain_pct":total_gain_pct,
                "day_gain":      day_gain,
                "day_gain_pct":  day_gain_pct,
            })

            total_value    += current_value
            total_cost     += cost_basis
            total_day_gain += day_gain

        except Exception as e:
            print(f"[portfolio] {ticker}: {e}")

    # Add allocation % now that we know total_value
    for p in positions:
        p["allocation"] = (p["current_value"] / total_value * 100) if total_value else 0

    # Sort by current value descending
    positions.sort(key=lambda x: x["current_value"], reverse=True)

    summary = {
        "total_value":     total_value,
        "total_cost":      total_cost,
        "total_gain":      total_value - total_cost,
        "total_gain_pct":  ((total_value - total_cost) / total_cost * 100) if total_cost else 0,
        "total_day_gain":  total_day_gain,
        "total_day_pct":   (total_day_gain / (total_value - total_day_gain) * 100) if total_value else 0,
    }

    print(f"[portfolio] {len(positions)} positions, total=${total_value:,.0f}")
    return positions, summary


# ── 8. Build HTML email ───────────────────────────────────────────────────────
def build_email(prices, earnings, news, macro, analyst, weekly=None, portfolio=None, port_summary=None):
    today_str = datetime.now().strftime("%A, %B %d, %Y")
    email_type = "📊 Weekly Digest" if IS_FRIDAY else "📈 Daily Alert"

    # helpers
    section = lambda title, icon, content: f"""
    <div style="margin-bottom:32px">
      <h2 style="margin:0 0 12px;font-size:16px;font-weight:700;color:#111827;
                 border-bottom:2px solid #e5e7eb;padding-bottom:8px">{icon} {title}</h2>
      {content}
    </div>"""
    table   = lambda header, rows: f"""
    <table style="width:100%;border-collapse:collapse;font-size:14px;color:#374151">
      <thead><tr style="background:#f9fafb;border-bottom:1px solid #e5e7eb">{header}</tr></thead>
      <tbody>{rows}</tbody>
    </table>"""
    th = lambda t, align="left": f'<th style="padding:8px 12px;text-align:{align};font-weight:600;color:#6b7280;font-size:12px;text-transform:uppercase">{t}</th>'

    # ── Price rows (alert badge for ±3%+) ──────────────────────────────────────
    movers     = [r for r in prices if r["alert"]]
    non_movers = [r for r in prices if not r["alert"]]
    price_rows = ""
    alert_banner = ""

    if movers:
        mover_chips = " ".join(
            f'<span style="background:{"#fef2f2" if r["chg"]<0 else "#f0fdf4"};'
            f'border:1px solid {"#fca5a5" if r["chg"]<0 else "#86efac"};'
            f'border-radius:6px;padding:4px 10px;font-size:13px;font-weight:700;font-family:monospace">'
            f'{r["ticker"]} {color_pct(r["chg"])}</span>'
            for r in sorted(movers, key=lambda x: abs(x["chg"]), reverse=True)
        )
        alert_banner = f"""
        <div style="background:#fffbeb;border:1px solid #fcd34d;border-radius:8px;
                    padding:12px 16px;margin-bottom:20px">
          <div style="font-size:12px;font-weight:700;color:#92400e;margin-bottom:8px">
            ⚡ PRICE ALERT — Significant movers today (±{PRICE_ALERT_PCT}%+)
          </div>
          <div style="display:flex;flex-wrap:wrap;gap:8px">{mover_chips}</div>
        </div>"""

    for r in sorted(prices, key=lambda x: abs(x["chg"]), reverse=True):
        alert_style = "border-left:3px solid #f59e0b;" if r["alert"] else ""
        bg = "#fef2f2" if r["chg"] < -2 else ("#f0fdf4" if r["chg"] > 2 else "#ffffff")
        price_rows += f"""
        <tr style="background:{bg};{alert_style}">
          <td style="padding:8px 12px;font-weight:700;font-family:monospace;font-size:14px">
            {r['ticker']}
            {"&nbsp;<span style='background:#f59e0b;color:#fff;font-size:10px;padding:1px 5px;border-radius:3px;font-family:sans-serif'>ALERT</span>" if r['alert'] else ""}
          </td>
          <td style="padding:8px 12px;text-align:right">${r['price']:.2f}</td>
          <td style="padding:8px 12px;text-align:right">{color_pct(r['chg'])}</td>
        </tr>"""

    price_section = section("Price Snapshot", "📊", alert_banner + table(
        th("Ticker") + th("Close", "right") + th("Day Chg", "right"), price_rows))

    # ── Earnings rows (countdown + consensus) ────────────────────────────────
    earn_rows = ""
    if earnings:
        for e in earnings:
            days_away = e.get("days_away", 99)
            imminent  = e.get("imminent", False)
            if days_away == 0:
                badge_text, badge_color = "TODAY", "#dc2626"
            elif days_away == 1:
                badge_text, badge_color = "TOMORROW", "#ea580c"
            elif imminent:
                badge_text, badge_color = f"IN {days_away} DAYS", "#d97706"
            else:
                badge_text, badge_color = f"In {days_away}d", "#6b7280"

            badge    = f'<span style="background:{badge_color};color:#fff;font-size:10px;padding:2px 6px;border-radius:3px;font-weight:700;margin-left:6px">{badge_text}</span>'
            row_bg   = "#fff7ed" if imminent else "#ffffff"

            # EPS values
            est      = e.get("eps_estimate") or e.get("epsEstimate") or "—"
            act      = e.get("eps_actual")   or e.get("epsActual")   or "—"
            surp_pct = e.get("eps_surp_pct")

            # Beat / Miss / In-line badge
            if surp_pct is not None:
                try:
                    sp = float(surp_pct)
                    if sp > 3:
                        verdict = '<span style="background:#16a34a;color:#fff;font-size:10px;font-weight:700;padding:2px 6px;border-radius:3px">BEAT</span>'
                    elif sp < -3:
                        verdict = '<span style="background:#dc2626;color:#fff;font-size:10px;font-weight:700;padding:2px 6px;border-radius:3px">MISS</span>'
                    else:
                        verdict = '<span style="background:#6b7280;color:#fff;font-size:10px;font-weight:700;padding:2px 6px;border-radius:3px">IN-LINE</span>'
                    surprise_str = f"({'+' if sp>=0 else ''}{sp:.1f}%) {verdict}"
                except Exception:
                    surprise_str = "—"
            else:
                surprise_str = "<span style='color:#9ca3af;font-size:12px'>Upcoming</span>"

            # Format EPS nicely
            def fmt_eps(v):
                try: return f"${float(v):.2f}"
                except: return str(v) if v and v != "—" else "—"

            # Revenue growth
            rev_g = e.get("rev_growth")
            rev_str = f"{'+' if rev_g and rev_g>=0 else ''}{rev_g:.1f}% YoY" if rev_g is not None else "—"
            rev_color = "#16a34a" if rev_g and rev_g > 0 else "#dc2626"

            # Analyst consensus mini bar
            ab = e.get("analyst_buy", 0)
            ah = e.get("analyst_hold", 0)
            as_ = e.get("analyst_sell", 0)
            at = e.get("analyst_total", 0) or 1
            buy_w  = int(ab / at * 60)
            hold_w = int(ah / at * 60)
            sell_w = int(as_ / at * 60)
            consensus_signal = "BUY" if ab/at > 0.6 else ("SELL" if as_/at > 0.4 else "HOLD")
            sig_color = "#16a34a" if consensus_signal=="BUY" else ("#dc2626" if consensus_signal=="SELL" else "#d97706")
            analyst_bar = f"""
              <div style="display:flex;align-items:center;gap:4px;font-size:11px">
                <div style="display:flex;height:6px;border-radius:3px;overflow:hidden;width:60px">
                  <div style="width:{buy_w}px;background:#16a34a"></div>
                  <div style="width:{hold_w}px;background:#d97706"></div>
                  <div style="width:{sell_w}px;background:#dc2626"></div>
                </div>
                <span style="color:{sig_color};font-weight:700">{consensus_signal}</span>
                <span style="color:#9ca3af">({at})</span>
              </div>""" if at > 0 else "—"

            # PE ratio
            pe = e.get("pe_ratio")
            pe_str = f"{pe:.1f}x" if pe else "—"

            earn_rows += f"""
            <tr style="background:{row_bg};border-bottom:1px solid #f3f4f6">
              <td style="padding:10px 12px;font-weight:700;font-family:monospace;vertical-align:top">
                {e['symbol']}{badge}
                <div style="font-size:11px;color:#6b7280;font-weight:400;margin-top:4px">{e.get('date','')} {e.get('hour','')}</div>
              </td>
              <td style="padding:10px 12px;text-align:right;vertical-align:top">
                <div style="font-weight:600">{fmt_eps(est)}</div>
                <div style="font-size:11px;color:#9ca3af">Est. EPS</div>
              </td>
              <td style="padding:10px 12px;text-align:right;vertical-align:top">
                <div style="font-weight:600">{fmt_eps(act)}</div>
                <div style="font-size:11px;color:#9ca3af">Act. EPS</div>
              </td>
              <td style="padding:10px 12px;text-align:right;vertical-align:top">
                {surprise_str}
              </td>
              <td style="padding:10px 12px;text-align:right;vertical-align:top">
                <div style="color:{rev_color};font-weight:600">{rev_str}</div>
                <div style="font-size:11px;color:#9ca3af">Rev Growth</div>
              </td>
              <td style="padding:10px 12px;vertical-align:top">
                {analyst_bar}
              </td>
            </tr>"""
    else:
        earn_rows = '<tr><td colspan="6" style="padding:12px;color:#6b7280;text-align:center">No earnings this week for watchlist</td></tr>'

    earn_section = section("Earnings Calendar & Consensus", "📅", table(
        th("Ticker") + th("Est. EPS", "right") + th("Act. EPS", "right") + th("Surprise", "right") + th("Rev Growth", "right") + th("Analyst Signal"),
        earn_rows))

    # ── Weekly summary section (Fridays only) ─────────────────────────────────
    weekly_section = ""
    if IS_FRIDAY and weekly:
        best  = weekly[0]
        worst = weekly[-1]
        summary_cards = f"""
        <div style="display:flex;gap:12px;margin-bottom:16px">
          <div style="flex:1;background:#f0fdf4;border:1px solid #86efac;border-radius:8px;padding:12px;text-align:center">
            <div style="font-size:11px;color:#166534;font-weight:600;text-transform:uppercase">Best of Week</div>
            <div style="font-size:20px;font-weight:800;font-family:monospace;color:#15803d">{best['ticker']}</div>
            <div style="color:#16a34a;font-weight:700">{pct(best['week_chg'])}</div>
          </div>
          <div style="flex:1;background:#fef2f2;border:1px solid #fca5a5;border-radius:8px;padding:12px;text-align:center">
            <div style="font-size:11px;color:#991b1b;font-weight:600;text-transform:uppercase">Worst of Week</div>
            <div style="font-size:20px;font-weight:800;font-family:monospace;color:#dc2626">{worst['ticker']}</div>
            <div style="color:#dc2626;font-weight:700">{pct(worst['week_chg'])}</div>
          </div>
        </div>"""

        weekly_rows = ""
        for w in weekly:
            bg = "#fef2f2" if w["week_chg"] < -3 else ("#f0fdf4" if w["week_chg"] > 3 else "#ffffff")
            weekly_rows += f"""
            <tr style="background:{bg}">
              <td style="padding:8px 12px;font-weight:700;font-family:monospace">{w['ticker']}</td>
              <td style="padding:8px 12px;text-align:right">${w['week_close']:.2f}</td>
              <td style="padding:8px 12px;text-align:right">{color_pct(w['week_chg'])}</td>
              <td style="padding:8px 12px;text-align:right;color:#6b7280">${w['week_high']:.2f}</td>
              <td style="padding:8px 12px;text-align:right;color:#6b7280">${w['week_low']:.2f}</td>
            </tr>"""

        weekly_section = section("📆 Week in Review", "🗓️", summary_cards + table(
            th("Ticker") + th("Close", "right") + th("Week Chg", "right") + th("High", "right") + th("Low", "right"),
            weekly_rows))

    # ── News ──────────────────────────────────────────────────────────────────
    news_items = ""
    if news:
        for n in news[:15]:
            ts = datetime.fromtimestamp(n["datetime"]).strftime("%I:%M %p") if n["datetime"] else ""
            news_items += f"""
            <tr>
              <td style="padding:8px 12px;font-weight:700;font-family:monospace;white-space:nowrap">{n['ticker']}</td>
              <td style="padding:8px 12px">
                <a href="{n['url']}" style="color:#1d4ed8;text-decoration:none">{n['headline']}</a>
                <span style="color:#9ca3af;font-size:12px;margin-left:6px">— {n['source']}</span>
              </td>
              <td style="padding:8px 12px;color:#6b7280;font-size:12px;white-space:nowrap">{ts}</td>
            </tr>"""
    else:
        news_items = '<tr><td colspan="3" style="padding:12px;color:#6b7280;text-align:center">No news today</td></tr>'
    news_section = section("Top News", "📰", table(th("Ticker") + th("Headline") + th("Time"), news_items))

    # ── Macro ─────────────────────────────────────────────────────────────────
    macro_items = ""
    if macro:
        for ev in macro[:8]:
            macro_items += f"""
            <tr>
              <td style="padding:8px 12px;font-weight:600">{ev.get('event','')}</td>
              <td style="padding:8px 12px;color:#6b7280">{ev.get('date','')}</td>
              <td style="padding:8px 12px;text-align:right">{ev.get('actual','—')}</td>
              <td style="padding:8px 12px;text-align:right;color:#6b7280">{ev.get('estimate','—')}</td>
            </tr>"""
    else:
        macro_items = '<tr><td colspan="4" style="padding:12px;color:#6b7280;text-align:center">No high-impact US macro events this week</td></tr>'
    macro_section = section("Macro Events (High Impact US)", "🏛️", table(
        th("Event") + th("Date") + th("Actual", "right") + th("Estimate", "right"), macro_items))

    # ── Analyst ───────────────────────────────────────────────────────────────
    analyst_rows = ""
    for a in analyst:
        total     = a["buy"] + a["hold"] + a["sell"] or 1
        buy_pct   = a["buy"]  / total * 100
        sell_pct  = a["sell"] / total * 100
        consensus = "BUY" if buy_pct > 60 else ("SELL" if sell_pct > 40 else "HOLD")
        cons_color = "#16a34a" if consensus == "BUY" else ("#dc2626" if consensus == "SELL" else "#d97706")
        analyst_rows += f"""
        <tr>
          <td style="padding:8px 12px;font-weight:700;font-family:monospace">{a['ticker']}</td>
          <td style="padding:8px 12px;text-align:center;color:#16a34a">{a['buy']}</td>
          <td style="padding:8px 12px;text-align:center;color:#d97706">{a['hold']}</td>
          <td style="padding:8px 12px;text-align:center;color:#dc2626">{a['sell']}</td>
          <td style="padding:8px 12px;text-align:center">
            <span style="background:{cons_color};color:#fff;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:700">{consensus}</span>
          </td>
        </tr>"""
    analyst_section = section("Analyst Consensus", "🔍", table(
        th("Ticker") + th("Buy","center") + th("Hold","center") + th("Sell","center") + th("Signal","center"),
        analyst_rows))

    # ── Portfolio section ─────────────────────────────────────────────────────
    portfolio     = portfolio or []
    port_summary  = port_summary or {}
    if portfolio and port_summary:
        tv   = port_summary.get("total_value", 0)
        tc   = port_summary.get("total_cost", 0)
        tg   = port_summary.get("total_gain", 0)
        tgp  = port_summary.get("total_gain_pct", 0)
        tdg  = port_summary.get("total_day_gain", 0)
        tdp  = port_summary.get("total_day_pct", 0)

        gain_color    = "#16a34a" if tg  >= 0 else "#dc2626"
        day_color     = "#16a34a" if tdg >= 0 else "#dc2626"
        gain_arrow    = "▲" if tg  >= 0 else "▼"
        day_arrow     = "▲" if tdg >= 0 else "▼"

        # Summary cards row
        summary_cards = f"""
        <div style="display:flex;gap:10px;margin-bottom:20px;flex-wrap:wrap">
          <div style="flex:1;min-width:130px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:14px;text-align:center">
            <div style="font-size:11px;color:#64748b;font-weight:600;text-transform:uppercase;margin-bottom:4px">Total Value</div>
            <div style="font-size:22px;font-weight:800;color:#0f172a">${tv:,.0f}</div>
          </div>
          <div style="flex:1;min-width:130px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:14px;text-align:center">
            <div style="font-size:11px;color:#64748b;font-weight:600;text-transform:uppercase;margin-bottom:4px">Total Cost</div>
            <div style="font-size:22px;font-weight:800;color:#0f172a">${tc:,.0f}</div>
          </div>
          <div style="flex:1;min-width:130px;background:{"#f0fdf4" if tg>=0 else "#fef2f2"};border:1px solid {"#86efac" if tg>=0 else "#fca5a5"};border-radius:10px;padding:14px;text-align:center">
            <div style="font-size:11px;color:#64748b;font-weight:600;text-transform:uppercase;margin-bottom:4px">Total Gain</div>
            <div style="font-size:20px;font-weight:800;color:{gain_color}">{gain_arrow} ${abs(tg):,.0f}</div>
            <div style="font-size:12px;color:{gain_color};font-weight:600">{gain_arrow} {abs(tgp):.2f}%</div>
          </div>
          <div style="flex:1;min-width:130px;background:{"#f0fdf4" if tdg>=0 else "#fef2f2"};border:1px solid {"#86efac" if tdg>=0 else "#fca5a5"};border-radius:10px;padding:14px;text-align:center">
            <div style="font-size:11px;color:#64748b;font-weight:600;text-transform:uppercase;margin-bottom:4px">Today's P&L</div>
            <div style="font-size:20px;font-weight:800;color:{day_color}">{day_arrow} ${abs(tdg):,.0f}</div>
            <div style="font-size:12px;color:{day_color};font-weight:600">{day_arrow} {abs(tdp):.2f}%</div>
          </div>
        </div>"""

        # Allocation bar (horizontal stacked bar)
        bar_segments = ""
        colors = ["#3b82f6","#8b5cf6","#06b6d4","#10b981","#f59e0b","#ef4444","#ec4899","#6366f1","#14b8a6","#f97316","#84cc16"]
        for i, p in enumerate(portfolio):
            c = colors[i % len(colors)]
            bar_segments += f'<div style="width:{p["allocation"]:.1f}%;background:{c};height:100%;display:inline-block;vertical-align:top" title="{p["ticker"]} {p["allocation"]:.1f}%"></div>'

        alloc_bar = f"""
        <div style="margin-bottom:8px">
          <div style="font-size:11px;color:#6b7280;font-weight:600;margin-bottom:6px;text-transform:uppercase">Allocation</div>
          <div style="height:10px;border-radius:5px;overflow:hidden;background:#f3f4f6;width:100%">
            {bar_segments}
          </div>
          <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:8px">"""
        for i, p in enumerate(portfolio):
            c = colors[i % len(colors)]
            alloc_bar += f'<span style="font-size:11px;color:#374151"><span style="display:inline-block;width:8px;height:8px;background:{c};border-radius:2px;margin-right:3px;vertical-align:middle"></span>{p["ticker"]} {p["allocation"]:.1f}%</span>'
        alloc_bar += "</div></div>"

        # Positions table
        pos_rows = ""
        for p in portfolio:
            tg_color  = "#16a34a" if p["total_gain"] >= 0 else "#dc2626"
            day_color2= "#16a34a" if p["day_gain"]   >= 0 else "#dc2626"
            tg_arrow  = "▲" if p["total_gain"] >= 0 else "▼"
            day_arrow2= "▲" if p["day_gain"]   >= 0 else "▼"
            row_bg    = "#fafffe" if p["total_gain"] >= 0 else "#fffafa"

            pos_rows += f"""
            <tr style="background:{row_bg};border-bottom:1px solid #f3f4f6">
              <td style="padding:10px 12px;font-weight:700;font-family:monospace;font-size:14px">{p["ticker"]}</td>
              <td style="padding:10px 12px;text-align:right;color:#6b7280">{p["shares"]} @ ${p["avg_price"]:.2f}</td>
              <td style="padding:10px 12px;text-align:right;font-weight:600">${p["current_price"]:.2f}</td>
              <td style="padding:10px 12px;text-align:right;font-weight:700">${p["current_value"]:,.0f}</td>
              <td style="padding:10px 12px;text-align:right">
                <span style="color:{tg_color};font-weight:700">{tg_arrow} ${abs(p["total_gain"]):,.0f}</span>
                <div style="font-size:11px;color:{tg_color}">{tg_arrow} {abs(p["total_gain_pct"]):.2f}%</div>
              </td>
              <td style="padding:10px 12px;text-align:right">
                <span style="color:{day_color2};font-weight:700">{day_arrow2} ${abs(p["day_gain"]):,.0f}</span>
                <div style="font-size:11px;color:{day_color2}">{day_arrow2} {abs(p["day_gain_pct"]):.2f}%</div>
              </td>
              <td style="padding:10px 12px;text-align:right;color:#6b7280;font-size:12px">{p["allocation"]:.1f}%</td>
            </tr>"""

        port_table = f"""
        <table style="width:100%;border-collapse:collapse;font-size:14px;color:#374151">
          <thead><tr style="background:#f9fafb;border-bottom:1px solid #e5e7eb">
            <th style="padding:8px 12px;text-align:left;font-weight:600;color:#6b7280;font-size:12px;text-transform:uppercase">Ticker</th>
            <th style="padding:8px 12px;text-align:right;font-weight:600;color:#6b7280;font-size:12px;text-transform:uppercase">Position</th>
            <th style="padding:8px 12px;text-align:right;font-weight:600;color:#6b7280;font-size:12px;text-transform:uppercase">Price</th>
            <th style="padding:8px 12px;text-align:right;font-weight:600;color:#6b7280;font-size:12px;text-transform:uppercase">Value</th>
            <th style="padding:8px 12px;text-align:right;font-weight:600;color:#6b7280;font-size:12px;text-transform:uppercase">Total G/L</th>
            <th style="padding:8px 12px;text-align:right;font-weight:600;color:#6b7280;font-size:12px;text-transform:uppercase">Day G/L</th>
            <th style="padding:8px 12px;text-align:right;font-weight:600;color:#6b7280;font-size:12px;text-transform:uppercase">Alloc</th>
          </tr></thead>
          <tbody>{pos_rows}</tbody>
        </table>"""

        portfolio_section = section("💼 Portfolio", "💼", summary_cards + alloc_bar + port_table)
    else:
        portfolio_section = ""

    # ── Assemble ──────────────────────────────────────────────────────────────
    header_color = "linear-gradient(135deg,#1a1a2e 0%,#16213e 50%,#0f3460 100%)" if IS_FRIDAY else "linear-gradient(135deg,#0f172a 0%,#1e3a5f 100%)"
    friday_badge = '<span style="background:#f59e0b;color:#1a1a2e;font-size:11px;font-weight:800;padding:3px 8px;border-radius:4px;margin-left:10px;vertical-align:middle">FRIDAY DIGEST</span>' if IS_FRIDAY else ""

    body_sections = portfolio_section + price_section + earn_section
    if IS_FRIDAY and weekly_section:
        body_sections += weekly_section
    body_sections += news_section + macro_section + analyst_section

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
  <div style="max-width:680px;margin:24px auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1)">
    <div style="background:{header_color};padding:28px 32px">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <div>
          <h1 style="margin:0;color:#fff;font-size:22px;font-weight:800;letter-spacing:-0.5px">
            {email_type}{friday_badge}
          </h1>
          <p style="margin:4px 0 0;color:#94a3b8;font-size:13px">{today_str}</p>
        </div>
        <div style="text-align:right">
          <div style="color:#38bdf8;font-size:12px;font-weight:600">WATCHLIST</div>
          <div style="color:#cbd5e1;font-size:11px;margin-top:4px">{" · ".join(WATCHLIST)}</div>
        </div>
      </div>
    </div>
    <div style="padding:28px 32px">{body_sections}</div>
    <div style="background:#f9fafb;padding:16px 32px;border-top:1px solid #e5e7eb;font-size:12px;color:#9ca3af;text-align:center">
      Auto-generated · Data: Finnhub + Yahoo Finance · Not financial advice
    </div>
  </div>
</body>
</html>"""


# ── 8. Send email ─────────────────────────────────────────────────────────────
def send_email(html_body, subject):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = ", ".join(ALERT_TO)
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_PASS)
        server.sendmail(GMAIL_USER, ALERT_TO, msg.as_string())
    print(f"✅ Email sent to {', '.join(ALERT_TO)}")


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"⏳ Fetching data... (Friday={IS_FRIDAY})")
    prices              = get_price_snapshot()
    earnings            = get_earnings()
    news                = get_news()
    macro               = get_macro()
    analyst             = get_analyst_actions()
    portfolio, port_sum = get_portfolio()
    weekly              = get_weekly_summary() if IS_FRIDAY else []

    movers = [r for r in prices if r["alert"]]
    print(f"  prices={len(prices)} movers={len(movers)} earnings={len(earnings)} news={len(news)} macro={len(macro)} analyst={len(analyst)} portfolio={len(portfolio)} weekly={len(weekly)}")

    # Subject line: include portfolio day P&L if available
    date_str = datetime.now().strftime("%b %d, %Y")
    port_day = port_sum.get("total_day_gain", 0)
    port_tag = f" | Port {'+' if port_day>=0 else ''}{port_day:,.0f}" if port_sum else ""
    if IS_FRIDAY:
        subject = f"📊 Weekly Digest{port_tag} — {date_str}"
    elif movers:
        mover_str = ", ".join(f"{r['ticker']} {pct(r['chg'])}" for r in sorted(movers, key=lambda x: abs(x['chg']), reverse=True)[:3])
        subject = f"⚡ Price Alert: {mover_str}{port_tag} — {date_str}"
    else:
        subject = f"📈 Stock Alert{port_tag} — {date_str}"

    html = build_email(prices, earnings, news, macro, analyst, weekly, portfolio, port_sum)
    send_email(html, subject)
