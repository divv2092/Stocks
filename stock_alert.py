"""
Daily Stock Alert — Earnings, News & Macro Events
Watchlist: TDOC, NVO, BABA, NOW, SMH, META, GOOGL, AMD, MRNA, CVNA, NKE
Schedule: Mon–Fri at 8 AM ET via GitHub Actions
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
PRICE_ALERT_PCT      = 3.0
EARNINGS_WARN_DAYS   = 3

# ── Portfolio holdings ────────────────────────────────────────────────────────
# Format: "TICKER": (shares, avg_buy_price)
# Negative shares = short position
PORTFOLIO = {
    "META":  (10,    480.00),
    "GOOGL": (5,     170.00),
    "AMD":   (15,    145.00),
    "NOW":   (3,     850.00),
    "NKE":   (250,    53.00),
    "MRNA":  (100,    27.00),
    "TDOC":  (450,     7.00),
    "NVO":   (800,    37.00),
    "BABA":  (50,    122.00),
    "CVNA":  (-385,   81.00),   # short position
    "SMH":   (-100,  576.00),   # short position
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


# ── 1. Price snapshot ─────────────────────────────────────────────────────────
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
                "ticker": ticker,
                "price":  close,
                "chg":    chg,
                "alert":  abs(chg) >= PRICE_ALERT_PCT,
            })
        except Exception as e:
            print(f"[price] {ticker}: {e}")
    return rows


# ── 2. Weekly price summary (Fridays only) ────────────────────────────────────
def get_weekly_summary():
    rows = []
    period_start = TODAY - timedelta(days=TODAY.weekday() + 1)
    for ticker in WATCHLIST:
        try:
            t    = yf.Ticker(ticker)
            hist = t.history(start=period_start.isoformat(), end=TODAY_STR)
            if len(hist) < 2:
                continue
            week_open  = hist["Close"].iloc[0]
            week_close = hist["Close"].iloc[-1]
            week_chg   = (week_close - week_open) / week_open * 100
            rows.append({
                "ticker":     ticker,
                "week_chg":   week_chg,
                "week_close": week_close,
                "week_high":  hist["High"].max(),
                "week_low":   hist["Low"].min(),
            })
        except Exception as e:
            print(f"[weekly] {ticker}: {e}")
    rows.sort(key=lambda x: x["week_chg"], reverse=True)
    return rows


# ── 3. Earnings calendar ──────────────────────────────────────────────────────
def get_earnings():
    results = []
    try:
        data = fh("/calendar/earnings", {"from": TODAY_STR, "to": WEEK_END_STR})
        for item in data.get("earningsCalendar", []):
            if item.get("symbol") not in WATCHLIST:
                continue
            try:
                days_away = (date.fromisoformat(item["date"]) - TODAY).days
            except Exception:
                days_away = 99
            item["days_away"] = days_away
            item["imminent"]  = days_away <= EARNINGS_WARN_DAYS
            ticker = item["symbol"]
            try:
                hist_eps = fh("/stock/earnings", {"symbol": ticker, "limit": 4})
                last = hist_eps[0] if hist_eps else {}
                item["eps_actual"]   = last.get("actual")
                item["eps_estimate"] = last.get("estimate")
                item["eps_surp_pct"] = last.get("surprisePercent")
            except Exception:
                item["eps_actual"] = item["eps_estimate"] = item["eps_surp_pct"] = None
            try:
                bf = fh("/stock/metric", {"symbol": ticker, "metric": "all"})
                item["rev_growth"] = bf.get("metric", {}).get("revenueGrowthTTMYoy")
            except Exception:
                item["rev_growth"] = None
            try:
                rec = fh("/stock/recommendation", {"symbol": ticker})
                r   = rec[0] if rec else {}
                item["analyst_buy"]   = r.get("buy", 0)
                item["analyst_hold"]  = r.get("hold", 0)
                item["analyst_sell"]  = r.get("sell", 0)
                item["analyst_total"] = item["analyst_buy"] + item["analyst_hold"] + item["analyst_sell"]
            except Exception:
                item["analyst_buy"] = item["analyst_hold"] = item["analyst_sell"] = item["analyst_total"] = 0
            results.append(item)
    except Exception as e:
        print(f"[earnings] {e}")
    results.sort(key=lambda x: x.get("days_away", 99))
    return results


# ── 4. Company news ───────────────────────────────────────────────────────────
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


# ── 5. Macro calendar ─────────────────────────────────────────────────────────
def get_macro():
    events = []
    HIGH_IMPACT = {"fed","fomc","cpi","pce","nonfarm","payroll","gdp",
                   "unemployment","retail sales","inflation","ppi","ism","interest rate","jobs","housing"}
    try:
        url = "https://api.nasdaq.com/api/calendar/economicevents"
        r   = requests.get(url, params={"date": TODAY_STR, "datestart": TODAY_STR, "dateend": WEEK_END_STR},
                           headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=10)
        r.raise_for_status()
        for row in r.json().get("data", {}).get("rows", []):
            name = row.get("eventName", "")
            if any(k in name.lower() for k in HIGH_IMPACT):
                events.append({"event": name, "date": row.get("eventDate",""), "actual": row.get("actual","—"), "estimate": row.get("consensus","—")})
        print(f"[macro] {len(events)} events")
    except Exception as e:
        print(f"[macro] failed: {e}")
    return events[:8]


# ── 6. Analyst consensus ──────────────────────────────────────────────────────
def get_analyst_actions():
    actions = []
    for ticker in WATCHLIST:
        try:
            data = fh("/stock/recommendation", {"symbol": ticker})
            if data:
                latest = data[0]
                actions.append({"ticker": ticker, "buy": latest.get("buy",0),
                                 "hold": latest.get("hold",0), "sell": latest.get("sell",0)})
        except Exception as e:
            print(f"[analyst] {ticker}: {e}")
    return actions


# ── 7. Portfolio snapshot ─────────────────────────────────────────────────────
def get_portfolio():
    positions      = []
    total_long_val = 0.0   # sum of abs(value) for allocation bar (long only)
    total_cost     = 0.0
    total_mkt_val  = 0.0   # net market value (longs - shorts)
    total_day_gain = 0.0

    for ticker, (shares, avg_price) in PORTFOLIO.items():
        try:
            t    = yf.Ticker(ticker)
            hist = t.history(period="2d")
            if len(hist) < 1:
                continue

            current_price = hist["Close"].iloc[-1]
            prev_price    = hist["Close"].iloc[-2] if len(hist) >= 2 else current_price
            is_short      = shares < 0

            # For a SHORT: profit when price falls
            # cost_basis = abs(shares) * avg_price (what you sold short at)
            # current_value = shares * current_price  (negative number)
            # gain = cost_basis_proceeds - current_cost_to_close
            #      = abs(shares)*avg_price - abs(shares)*current_price
            cost_basis    = abs(shares) * avg_price
            current_value = shares * current_price          # negative for shorts
            if is_short:
                total_gain     = (avg_price - current_price) * abs(shares)  # profit if price dropped
            else:
                total_gain     = current_value - cost_basis

            total_gain_pct = (total_gain / cost_basis * 100) if cost_basis else 0
            day_gain       = shares * (current_price - prev_price)
            day_gain_pct   = (current_price - prev_price) / prev_price * 100 if prev_price else 0

            positions.append({
                "ticker":         ticker,
                "shares":         shares,
                "is_short":       is_short,
                "avg_price":      avg_price,
                "current_price":  current_price,
                "cost_basis":     cost_basis,
                "current_value":  current_value,
                "market_exposure": abs(shares) * current_price,  # always positive
                "total_gain":     total_gain,
                "total_gain_pct": total_gain_pct,
                "day_gain":       day_gain,
                "day_gain_pct":   day_gain_pct,
            })

            total_long_val += abs(shares) * current_price   # use abs for allocation bar
            total_cost     += cost_basis if not is_short else 0
            total_mkt_val  += current_value
            total_day_gain += day_gain

        except Exception as e:
            print(f"[portfolio] {ticker}: {e}")

    # Allocation % based on absolute market exposure (so shorts show up too)
    for p in positions:
        p["allocation"] = (p["market_exposure"] / total_long_val * 100) if total_long_val else 0

    # Sort: longs by value desc, then shorts
    positions.sort(key=lambda x: (x["is_short"], -x["market_exposure"]))

    total_gain_overall = sum(p["total_gain"] for p in positions)
    summary = {
        "total_value":    total_mkt_val,
        "total_cost":     total_cost,
        "total_gain":     total_gain_overall,
        "total_gain_pct": (total_gain_overall / total_cost * 100) if total_cost else 0,
        "total_day_gain": total_day_gain,
        "total_day_pct":  (total_day_gain / (total_long_val or 1)) * 100,
        "position_count": len(positions),
    }

    print(f"[portfolio] {len(positions)} positions, net mkt val=${total_mkt_val:,.0f}, day P&L=${total_day_gain:,.0f}")
    return positions, summary


# ── 8. Build HTML email ───────────────────────────────────────────────────────
def build_email(prices, earnings, news, macro, analyst, weekly=None, portfolio=None, port_summary=None):
    today_str  = datetime.now().strftime("%A, %B %d, %Y")
    email_type = "📊 Weekly Digest" if IS_FRIDAY else "📈 Daily Alert"

    section = lambda title, icon, content: f"""
    <div style="margin-bottom:32px">
      <h2 style="margin:0 0 12px;font-size:16px;font-weight:700;color:#111827;
                 border-bottom:2px solid #e5e7eb;padding-bottom:8px">{icon} {title}</h2>
      {content}
    </div>"""
    table = lambda header, rows: f"""
    <table style="width:100%;border-collapse:collapse;font-size:14px;color:#374151">
      <thead><tr style="background:#f9fafb;border-bottom:1px solid #e5e7eb">{header}</tr></thead>
      <tbody>{rows}</tbody>
    </table>"""
    th = lambda t, align="left": f'<th style="padding:8px 12px;text-align:{align};font-weight:600;color:#6b7280;font-size:12px;text-transform:uppercase">{t}</th>'

    # ── Portfolio ─────────────────────────────────────────────────────────────
    portfolio    = portfolio or []
    port_summary = port_summary or {}

    if portfolio:
        tv  = port_summary.get("total_value", 0)
        tc  = port_summary.get("total_cost", 0)
        tg  = port_summary.get("total_gain", 0)
        tgp = port_summary.get("total_gain_pct", 0)
        tdg = port_summary.get("total_day_gain", 0)
        tdp = port_summary.get("total_day_pct", 0)

        def card(label, main, sub=None, positive=None):
            if positive is True:
                bg, border, col = "#f0fdf4", "#86efac", "#16a34a"
            elif positive is False:
                bg, border, col = "#fef2f2", "#fca5a5", "#dc2626"
            else:
                bg, border, col = "#f8fafc", "#e2e8f0", "#0f172a"
            sub_html = f'<div style="font-size:12px;color:{col};font-weight:600;margin-top:2px">{sub}</div>' if sub else ""
            return f"""
            <div style="flex:1;min-width:130px;background:{bg};border:1px solid {border};
                        border-radius:10px;padding:14px;text-align:center">
              <div style="font-size:11px;color:#64748b;font-weight:600;text-transform:uppercase;margin-bottom:4px">{label}</div>
              <div style="font-size:20px;font-weight:800;color:{col}">{main}</div>
              {sub_html}
            </div>"""

        gain_pos = tg  >= 0
        day_pos  = tdg >= 0
        summary_cards = f"""
        <div style="display:flex;gap:10px;margin-bottom:20px;flex-wrap:wrap">
          {card("Net Mkt Value", f"${tv:,.0f}")}
          {card("Total Cost (Long)", f"${tc:,.0f}")}
          {card("Total Gain/Loss", f"{'▲' if gain_pos else '▼'} ${abs(tg):,.0f}", f"{'▲' if gain_pos else '▼'} {abs(tgp):.2f}%", gain_pos)}
          {card("Today's P&L", f"{'▲' if day_pos else '▼'} ${abs(tdg):,.0f}", f"{'▲' if day_pos else '▼'} {abs(tdp):.2f}%", day_pos)}
        </div>"""

        # Allocation bar — uses abs(market exposure), labels shorts with (S)
        colors = ["#3b82f6","#8b5cf6","#06b6d4","#10b981","#f59e0b","#ef4444","#ec4899","#6366f1","#14b8a6","#f97316","#84cc16"]
        bar_segs = "".join(
            f'<div style="width:{p["allocation"]:.1f}%;background:{colors[i%len(colors)]};height:100%;display:inline-block;vertical-align:top"></div>'
            for i, p in enumerate(portfolio)
        )
        legend = "".join(
            f'<span style="font-size:11px;color:#374151;white-space:nowrap">'
            f'<span style="display:inline-block;width:8px;height:8px;background:{colors[i%len(colors)]};border-radius:2px;margin-right:3px;vertical-align:middle"></span>'
            f'{p["ticker"]}{"(S)" if p["is_short"] else ""} {p["allocation"]:.1f}%</span>'
            for i, p in enumerate(portfolio)
        )
        alloc_bar = f"""
        <div style="margin-bottom:16px">
          <div style="font-size:11px;color:#6b7280;font-weight:600;margin-bottom:6px;text-transform:uppercase">Allocation by Market Exposure</div>
          <div style="height:10px;border-radius:5px;overflow:hidden;background:#f3f4f6">{bar_segs}</div>
          <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:8px">{legend}</div>
        </div>"""

        # Positions table
        pos_rows = ""
        for p in portfolio:
            tg_pos  = p["total_gain"] >= 0
            day_pos2= p["day_gain"]   >= 0
            row_bg  = "#f8fff8" if tg_pos else "#fff8f8"
            short_badge = ' <span style="background:#7c3aed;color:#fff;font-size:9px;padding:1px 5px;border-radius:3px;font-weight:700">SHORT</span>' if p["is_short"] else ""
            pos_rows += f"""
            <tr style="background:{row_bg};border-bottom:1px solid #f3f4f6">
              <td style="padding:10px 12px;font-weight:700;font-family:monospace;font-size:14px;white-space:nowrap">
                {p["ticker"]}{short_badge}
              </td>
              <td style="padding:10px 12px;text-align:right;color:#6b7280;font-size:12px;white-space:nowrap">
                {abs(p["shares"])} @ ${p["avg_price"]:.2f}
              </td>
              <td style="padding:10px 12px;text-align:right;font-weight:600">${p["current_price"]:.2f}</td>
              <td style="padding:10px 12px;text-align:right;font-weight:700">${p["market_exposure"]:,.0f}</td>
              <td style="padding:10px 12px;text-align:right">
                <span style="color:{"#16a34a" if tg_pos else "#dc2626"};font-weight:700">
                  {"▲" if tg_pos else "▼"} ${abs(p["total_gain"]):,.0f}
                </span>
                <div style="font-size:11px;color:{"#16a34a" if tg_pos else "#dc2626"}">
                  {"▲" if tg_pos else "▼"} {abs(p["total_gain_pct"]):.2f}%
                </div>
              </td>
              <td style="padding:10px 12px;text-align:right">
                <span style="color:{"#16a34a" if day_pos2 else "#dc2626"};font-weight:700">
                  {"▲" if day_pos2 else "▼"} ${abs(p["day_gain"]):,.0f}
                </span>
                <div style="font-size:11px;color:{"#16a34a" if day_pos2 else "#dc2626"}">
                  {"▲" if day_pos2 else "▼"} {abs(p["day_gain_pct"]):.2f}%
                </div>
              </td>
              <td style="padding:10px 12px;text-align:right;color:#6b7280;font-size:12px">{p["allocation"]:.1f}%</td>
            </tr>"""

        port_table = f"""
        <table style="width:100%;border-collapse:collapse;font-size:14px;color:#374151">
          <thead><tr style="background:#f9fafb;border-bottom:1px solid #e5e7eb">
            {th("Ticker")}{th("Position","right")}{th("Price","right")}{th("Exposure","right")}{th("Total G/L","right")}{th("Day G/L","right")}{th("Alloc","right")}
          </tr></thead>
          <tbody>{pos_rows}</tbody>
        </table>"""

        portfolio_section = section("Portfolio", "💼", summary_cards + alloc_bar + port_table)
    else:
        portfolio_section = ""
        print("[portfolio] no positions loaded — check PORTFOLIO dict")

    # ── Price snapshot ────────────────────────────────────────────────────────
    movers      = [r for r in prices if r["alert"]]
    price_rows  = ""
    alert_banner = ""
    if movers:
        chips = " ".join(
            f'<span style="background:{"#fef2f2" if r["chg"]<0 else "#f0fdf4"};border:1px solid {"#fca5a5" if r["chg"]<0 else "#86efac"};border-radius:6px;padding:4px 10px;font-size:13px;font-weight:700;font-family:monospace">{r["ticker"]} {color_pct(r["chg"])}</span>'
            for r in sorted(movers, key=lambda x: abs(x["chg"]), reverse=True)
        )
        alert_banner = f'<div style="background:#fffbeb;border:1px solid #fcd34d;border-radius:8px;padding:12px 16px;margin-bottom:20px"><div style="font-size:12px;font-weight:700;color:#92400e;margin-bottom:8px">⚡ PRICE ALERT — Significant movers today (±{PRICE_ALERT_PCT}%+)</div><div style="display:flex;flex-wrap:wrap;gap:8px">{chips}</div></div>'

    for r in sorted(prices, key=lambda x: abs(x["chg"]), reverse=True):
        bg = "#fef2f2" if r["chg"] < -2 else ("#f0fdf4" if r["chg"] > 2 else "#ffffff")
        price_rows += f"""
        <tr style="background:{bg};{"border-left:3px solid #f59e0b;" if r["alert"] else ""}">
          <td style="padding:8px 12px;font-weight:700;font-family:monospace;font-size:14px">{r["ticker"]}{"&nbsp;<span style='background:#f59e0b;color:#fff;font-size:10px;padding:1px 5px;border-radius:3px'>ALERT</span>" if r["alert"] else ""}</td>
          <td style="padding:8px 12px;text-align:right">${r["price"]:.2f}</td>
          <td style="padding:8px 12px;text-align:right">{color_pct(r["chg"])}</td>
        </tr>"""
    price_section = section("Price Snapshot", "📊", alert_banner + table(th("Ticker") + th("Close","right") + th("Day Chg","right"), price_rows))

    # ── Earnings ──────────────────────────────────────────────────────────────
    earn_rows = ""
    if earnings:
        for e in earnings:
            days_away = e.get("days_away", 99)
            imminent  = e.get("imminent", False)
            badge_text, badge_color = (("TODAY","#dc2626") if days_away==0 else ("TOMORROW","#ea580c") if days_away==1 else (f"IN {days_away} DAYS","#d97706") if imminent else (f"In {days_away}d","#6b7280"))
            badge    = f'<span style="background:{badge_color};color:#fff;font-size:10px;padding:2px 6px;border-radius:3px;font-weight:700;margin-left:6px">{badge_text}</span>'
            est      = e.get("eps_estimate") or e.get("epsEstimate") or "—"
            act      = e.get("eps_actual")   or e.get("epsActual")   or "—"
            surp_pct = e.get("eps_surp_pct")
            def fmt_eps(v):
                try: return f"${float(v):.2f}"
                except: return "—"
            if surp_pct is not None:
                try:
                    sp = float(surp_pct)
                    verdict = ('<span style="background:#16a34a;color:#fff;font-size:10px;font-weight:700;padding:2px 6px;border-radius:3px">BEAT</span>' if sp > 3
                               else '<span style="background:#dc2626;color:#fff;font-size:10px;font-weight:700;padding:2px 6px;border-radius:3px">MISS</span>' if sp < -3
                               else '<span style="background:#6b7280;color:#fff;font-size:10px;font-weight:700;padding:2px 6px;border-radius:3px">IN-LINE</span>')
                    surprise_str = f"({'+' if sp>=0 else ''}{sp:.1f}%) {verdict}"
                except: surprise_str = "—"
            else:
                surprise_str = "<span style='color:#9ca3af;font-size:12px'>Upcoming</span>"
            rev_g = e.get("rev_growth")
            rev_str   = f"{'+' if rev_g and rev_g>=0 else ''}{rev_g:.1f}% YoY" if rev_g is not None else "—"
            rev_color = "#16a34a" if rev_g and rev_g > 0 else "#dc2626"
            ab, ah, as_ = e.get("analyst_buy",0), e.get("analyst_hold",0), e.get("analyst_sell",0)
            at = e.get("analyst_total",0) or 1
            sig = "BUY" if ab/at>0.6 else ("SELL" if as_/at>0.4 else "HOLD")
            sc  = "#16a34a" if sig=="BUY" else ("#dc2626" if sig=="SELL" else "#d97706")
            analyst_bar = f'<div style="display:flex;align-items:center;gap:4px;font-size:11px"><div style="display:flex;height:6px;border-radius:3px;overflow:hidden;width:60px"><div style="width:{int(ab/at*60)}px;background:#16a34a"></div><div style="width:{int(ah/at*60)}px;background:#d97706"></div><div style="width:{int(as_/at*60)}px;background:#dc2626"></div></div><span style="color:{sc};font-weight:700">{sig}</span><span style="color:#9ca3af">({at})</span></div>'
            earn_rows += f"""
            <tr style="background:{"#fff7ed" if imminent else "#ffffff"};border-bottom:1px solid #f3f4f6">
              <td style="padding:10px 12px;font-weight:700;font-family:monospace;vertical-align:top">{e["symbol"]}{badge}<div style="font-size:11px;color:#6b7280;font-weight:400;margin-top:4px">{e.get("date","")} {e.get("hour","")}</div></td>
              <td style="padding:10px 12px;text-align:right;vertical-align:top"><div style="font-weight:600">{fmt_eps(est)}</div><div style="font-size:11px;color:#9ca3af">Est. EPS</div></td>
              <td style="padding:10px 12px;text-align:right;vertical-align:top"><div style="font-weight:600">{fmt_eps(act)}</div><div style="font-size:11px;color:#9ca3af">Act. EPS</div></td>
              <td style="padding:10px 12px;text-align:right;vertical-align:top">{surprise_str}</td>
              <td style="padding:10px 12px;text-align:right;vertical-align:top"><div style="color:{rev_color};font-weight:600">{rev_str}</div><div style="font-size:11px;color:#9ca3af">Rev Growth</div></td>
              <td style="padding:10px 12px;vertical-align:top">{analyst_bar}</td>
            </tr>"""
    else:
        earn_rows = '<tr><td colspan="6" style="padding:12px;color:#6b7280;text-align:center">No earnings this week for watchlist</td></tr>'
    earn_section = section("Earnings Calendar & Consensus", "📅", table(
        th("Ticker") + th("Est. EPS","right") + th("Act. EPS","right") + th("Surprise","right") + th("Rev Growth","right") + th("Analyst Signal"),
        earn_rows))

    # ── Weekly (Fridays) ──────────────────────────────────────────────────────
    weekly_section = ""
    if IS_FRIDAY and weekly:
        best, worst = weekly[0], weekly[-1]
        cards = f"""<div style="display:flex;gap:12px;margin-bottom:16px">
          <div style="flex:1;background:#f0fdf4;border:1px solid #86efac;border-radius:8px;padding:12px;text-align:center"><div style="font-size:11px;color:#166534;font-weight:600;text-transform:uppercase">Best of Week</div><div style="font-size:20px;font-weight:800;font-family:monospace;color:#15803d">{best["ticker"]}</div><div style="color:#16a34a;font-weight:700">{pct(best["week_chg"])}</div></div>
          <div style="flex:1;background:#fef2f2;border:1px solid #fca5a5;border-radius:8px;padding:12px;text-align:center"><div style="font-size:11px;color:#991b1b;font-weight:600;text-transform:uppercase">Worst of Week</div><div style="font-size:20px;font-weight:800;font-family:monospace;color:#dc2626">{worst["ticker"]}</div><div style="color:#dc2626;font-weight:700">{pct(worst["week_chg"])}</div></div>
        </div>"""
        wrows = "".join(f'<tr style="background:{"#fef2f2" if w["week_chg"]<-3 else "#f0fdf4" if w["week_chg"]>3 else "#ffffff"}"><td style="padding:8px 12px;font-weight:700;font-family:monospace">{w["ticker"]}</td><td style="padding:8px 12px;text-align:right">${w["week_close"]:.2f}</td><td style="padding:8px 12px;text-align:right">{color_pct(w["week_chg"])}</td><td style="padding:8px 12px;text-align:right;color:#6b7280">${w["week_high"]:.2f}</td><td style="padding:8px 12px;text-align:right;color:#6b7280">${w["week_low"]:.2f}</td></tr>' for w in weekly)
        weekly_section = section("Week in Review", "🗓️", cards + table(th("Ticker")+th("Close","right")+th("Week Chg","right")+th("High","right")+th("Low","right"), wrows))

    # ── News ──────────────────────────────────────────────────────────────────
    news_items = "".join(f'<tr><td style="padding:8px 12px;font-weight:700;font-family:monospace;white-space:nowrap">{n["ticker"]}</td><td style="padding:8px 12px"><a href="{n["url"]}" style="color:#1d4ed8;text-decoration:none">{n["headline"]}</a><span style="color:#9ca3af;font-size:12px;margin-left:6px">— {n["source"]}</span></td><td style="padding:8px 12px;color:#6b7280;font-size:12px;white-space:nowrap">{datetime.fromtimestamp(n["datetime"]).strftime("%I:%M %p") if n["datetime"] else ""}</td></tr>' for n in news[:15]) or '<tr><td colspan="3" style="padding:12px;color:#6b7280;text-align:center">No news today</td></tr>'
    news_section = section("Top News", "📰", table(th("Ticker")+th("Headline")+th("Time"), news_items))

    # ── Macro ─────────────────────────────────────────────────────────────────
    macro_items = "".join(f'<tr><td style="padding:8px 12px;font-weight:600">{ev.get("event","")}</td><td style="padding:8px 12px;color:#6b7280">{ev.get("date","")}</td><td style="padding:8px 12px;text-align:right">{ev.get("actual","—")}</td><td style="padding:8px 12px;text-align:right;color:#6b7280">{ev.get("estimate","—")}</td></tr>' for ev in macro[:8]) or '<tr><td colspan="4" style="padding:12px;color:#6b7280;text-align:center">No high-impact US macro events this week</td></tr>'
    macro_section = section("Macro Events", "🏛️", table(th("Event")+th("Date")+th("Actual","right")+th("Estimate","right"), macro_items))

    # ── Analyst ───────────────────────────────────────────────────────────────
    analyst_rows = ""
    for a in analyst:
        total = a["buy"] + a["hold"] + a["sell"] or 1
        sig   = "BUY" if a["buy"]/total>0.6 else ("SELL" if a["sell"]/total>0.4 else "HOLD")
        col   = "#16a34a" if sig=="BUY" else ("#dc2626" if sig=="SELL" else "#d97706")
        analyst_rows += f'<tr><td style="padding:8px 12px;font-weight:700;font-family:monospace">{a["ticker"]}</td><td style="padding:8px 12px;text-align:center;color:#16a34a">{a["buy"]}</td><td style="padding:8px 12px;text-align:center;color:#d97706">{a["hold"]}</td><td style="padding:8px 12px;text-align:center;color:#dc2626">{a["sell"]}</td><td style="padding:8px 12px;text-align:center"><span style="background:{col};color:#fff;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:700">{sig}</span></td></tr>'
    analyst_section = section("Analyst Consensus", "🔍", table(th("Ticker")+th("Buy","center")+th("Hold","center")+th("Sell","center")+th("Signal","center"), analyst_rows))

    # ── Assemble ──────────────────────────────────────────────────────────────
    header_color = "linear-gradient(135deg,#1a1a2e 0%,#16213e 50%,#0f3460 100%)" if IS_FRIDAY else "linear-gradient(135deg,#0f172a 0%,#1e3a5f 100%)"
    friday_badge = '<span style="background:#f59e0b;color:#1a1a2e;font-size:11px;font-weight:800;padding:3px 8px;border-radius:4px;margin-left:10px;vertical-align:middle">FRIDAY DIGEST</span>' if IS_FRIDAY else ""

    body = portfolio_section + price_section + earn_section
    if IS_FRIDAY and weekly_section:
        body += weekly_section
    body += news_section + macro_section + analyst_section

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
  <div style="max-width:700px;margin:24px auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1)">
    <div style="background:{header_color};padding:28px 32px">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <div>
          <h1 style="margin:0;color:#fff;font-size:22px;font-weight:800;letter-spacing:-0.5px">{email_type}{friday_badge}</h1>
          <p style="margin:4px 0 0;color:#94a3b8;font-size:13px">{today_str}</p>
        </div>
        <div style="text-align:right">
          <div style="color:#38bdf8;font-size:12px;font-weight:600">WATCHLIST</div>
          <div style="color:#cbd5e1;font-size:11px;margin-top:4px">{" · ".join(WATCHLIST)}</div>
        </div>
      </div>
    </div>
    <div style="padding:28px 32px">{body}</div>
    <div style="background:#f9fafb;padding:16px 32px;border-top:1px solid #e5e7eb;font-size:12px;color:#9ca3af;text-align:center">
      Auto-generated · Data: Finnhub + Yahoo Finance · Not financial advice
    </div>
  </div>
</body>
</html>"""


# ── 9. Send email ─────────────────────────────────────────────────────────────
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

    date_str = datetime.now().strftime("%b %d, %Y")
    tdg      = port_sum.get("total_day_gain", 0)
    port_tag = f" | Port {'+' if tdg>=0 else ''}{tdg:,.0f}" if portfolio else ""

    if IS_FRIDAY:
        subject = f"📊 Weekly Digest{port_tag} — {date_str}"
    elif movers:
        mover_str = ", ".join(f"{r['ticker']} {pct(r['chg'])}" for r in sorted(movers, key=lambda x: abs(x['chg']), reverse=True)[:3])
        subject = f"⚡ Price Alert: {mover_str}{port_tag} — {date_str}"
    else:
        subject = f"📈 Stock Alert{port_tag} — {date_str}"

    html = build_email(prices, earnings, news, macro, analyst, weekly, portfolio, port_sum)
    send_email(html, subject)
