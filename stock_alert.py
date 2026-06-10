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
WATCHLIST = ["TDOC", "NVO", "BABA", "NOW", "SMH", "META", "GOOGL", "AMD", "MRNA", "CVNA", "NKE"]

FINNHUB_KEY  = os.environ["FINNHUB_KEY"]
GMAIL_USER   = os.environ["GMAIL_USER"]
GMAIL_PASS   = os.environ["GMAIL_APP_PASSWORD"]
ALERT_TO     = os.environ["ALERT_TO_EMAIL"]

FINNHUB_BASE = "https://finnhub.io/api/v1"
TODAY        = date.today().isoformat()
WEEK_END     = (date.today() + timedelta(days=7)).isoformat()


# ── Helpers ───────────────────────────────────────────────────────────────────
def fh(endpoint, params):
    """Call Finnhub REST API."""
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
            rows.append({"ticker": ticker, "price": close, "chg": chg})
        except Exception as e:
            print(f"[price] {ticker}: {e}")
    return rows


# ── 2. Earnings calendar (this week) ─────────────────────────────────────────
def get_earnings():
    results = []
    try:
        data = fh("/calendar/earnings", {"from": TODAY, "to": WEEK_END})
        cal  = data.get("earningsCalendar", [])
        for item in cal:
            if item.get("symbol") in WATCHLIST:
                results.append(item)
    except Exception as e:
        print(f"[earnings] {e}")
    return results


# ── 3. Company news (today) ───────────────────────────────────────────────────
def get_news():
    all_news = []
    for ticker in WATCHLIST:
        try:
            items = fh("/company-news", {"symbol": ticker, "from": TODAY, "to": TODAY})
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
    # Sort newest first
    all_news.sort(key=lambda x: x["datetime"], reverse=True)
    return all_news


# ── 4. Macro / economic calendar ─────────────────────────────────────────────
def get_macro():
    events = []
    try:
        data = fh("/calendar/economic", {"from": TODAY, "to": WEEK_END})
        for ev in data.get("economicCalendar", []):
            # Filter high-impact US events
            if ev.get("country", "").upper() == "US" and ev.get("impact", "").lower() == "high":
                events.append(ev)
    except Exception as e:
        print(f"[macro] {e}")
    return events


# ── 5. Analyst upgrades / downgrades (today) ──────────────────────────────────
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


# ── 6. Build HTML email ───────────────────────────────────────────────────────
def build_email(prices, earnings, news, macro, analyst):
    today_str = datetime.now().strftime("%A, %B %d, %Y")

    # ── Price table rows
    price_rows = ""
    for r in sorted(prices, key=lambda x: abs(x["chg"]), reverse=True):
        bg = "#fef2f2" if r["chg"] < -2 else ("#f0fdf4" if r["chg"] > 2 else "#ffffff")
        price_rows += f"""
        <tr style="background:{bg}">
          <td style="padding:8px 12px;font-weight:700;font-family:monospace;font-size:14px">{r['ticker']}</td>
          <td style="padding:8px 12px;text-align:right">${r['price']:.2f}</td>
          <td style="padding:8px 12px;text-align:right">{color_pct(r['chg'])}</td>
        </tr>"""

    # ── Earnings rows
    earn_rows = ""
    if earnings:
        for e in earnings:
            est   = e.get("epsEstimate", "N/A")
            act   = e.get("epsActual",   "—")
            earn_rows += f"""
            <tr>
              <td style="padding:8px 12px;font-weight:700;font-family:monospace">{e['symbol']}</td>
              <td style="padding:8px 12px">{e.get('date','')}</td>
              <td style="padding:8px 12px">{e.get('hour','')}</td>
              <td style="padding:8px 12px;text-align:right">{est}</td>
              <td style="padding:8px 12px;text-align:right">{act}</td>
            </tr>"""
    else:
        earn_rows = '<tr><td colspan="5" style="padding:12px;color:#6b7280;text-align:center">No earnings this week for watchlist</td></tr>'

    # ── News items
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

    # ── Macro events
    macro_items = ""
    if macro:
        for ev in macro[:8]:
            ev_date = ev.get("time", ev.get("date", ""))
            macro_items += f"""
            <tr>
              <td style="padding:8px 12px;font-weight:600">{ev.get('event','')}</td>
              <td style="padding:8px 12px;color:#6b7280">{ev_date}</td>
              <td style="padding:8px 12px;text-align:right">{ev.get('actual','—')}</td>
              <td style="padding:8px 12px;text-align:right;color:#6b7280">{ev.get('estimate','—')}</td>
            </tr>"""
    else:
        macro_items = '<tr><td colspan="4" style="padding:12px;color:#6b7280;text-align:center">No high-impact US macro events this week</td></tr>'

    # ── Analyst consensus rows
    analyst_rows = ""
    for a in analyst:
        total = a["buy"] + a["hold"] + a["sell"] or 1
        buy_pct  = a["buy"]  / total * 100
        sell_pct = a["sell"] / total * 100
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

    section = lambda title, icon, content: f"""
    <div style="margin-bottom:32px">
      <h2 style="margin:0 0 12px;font-size:16px;font-weight:700;color:#111827;
                 border-bottom:2px solid #e5e7eb;padding-bottom:8px">
        {icon} {title}
      </h2>
      {content}
    </div>"""

    table = lambda header, rows: f"""
    <table style="width:100%;border-collapse:collapse;font-size:14px;color:#374151">
      <thead><tr style="background:#f9fafb;border-bottom:1px solid #e5e7eb">{header}</tr></thead>
      <tbody>{rows}</tbody>
    </table>"""

    th = lambda t, align="left": f'<th style="padding:8px 12px;text-align:{align};font-weight:600;color:#6b7280;font-size:12px;text-transform:uppercase">{t}</th>'

    price_section = section("Price Snapshot", "📊",
        table(
            th("Ticker") + th("Close", "right") + th("Day Chg", "right"),
            price_rows
        ))

    earn_section = section("Earnings This Week", "📅",
        table(
            th("Ticker") + th("Date") + th("Time") + th("Est. EPS", "right") + th("Act. EPS", "right"),
            earn_rows
        ))

    news_section = section("Top News", "📰",
        table(th("Ticker") + th("Headline") + th("Time"), news_items))

    macro_section = section("Macro Events (High Impact US)", "🏛️",
        table(th("Event") + th("Date") + th("Actual", "right") + th("Estimate", "right"), macro_items))

    analyst_section = section("Analyst Consensus", "🔍",
        table(
            th("Ticker") + th("Buy", "center") + th("Hold", "center") + th("Sell", "center") + th("Signal", "center"),
            analyst_rows
        ))

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">

  <div style="max-width:680px;margin:24px auto;background:#ffffff;border-radius:12px;
              overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1)">

    <!-- Header -->
    <div style="background:linear-gradient(135deg,#0f172a 0%,#1e3a5f 100%);padding:28px 32px">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <div>
          <h1 style="margin:0;color:#ffffff;font-size:22px;font-weight:800;letter-spacing:-0.5px">
            📈 Stock Alert Digest
          </h1>
          <p style="margin:4px 0 0;color:#94a3b8;font-size:13px">{today_str}</p>
        </div>
        <div style="text-align:right">
          <div style="color:#38bdf8;font-size:12px;font-weight:600">WATCHLIST</div>
          <div style="color:#cbd5e1;font-size:11px;margin-top:4px">
            {" · ".join(WATCHLIST)}
          </div>
        </div>
      </div>
    </div>

    <!-- Body -->
    <div style="padding:28px 32px">
      {price_section}
      {earn_section}
      {news_section}
      {macro_section}
      {analyst_section}
    </div>

    <!-- Footer -->
    <div style="background:#f9fafb;padding:16px 32px;border-top:1px solid #e5e7eb;
                font-size:12px;color:#9ca3af;text-align:center">
      Auto-generated · Data: Finnhub + Yahoo Finance · Not financial advice
    </div>
  </div>

</body>
</html>"""
    return html


# ── 7. Send email ─────────────────────────────────────────────────────────────
def send_email(html_body):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📈 Stock Alert — {datetime.now().strftime('%b %d, %Y')}"
    msg["From"]    = GMAIL_USER
    msg["To"]      = ALERT_TO
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_PASS)
        server.sendmail(GMAIL_USER, ALERT_TO, msg.as_string())
    print(f"✅ Email sent to {ALERT_TO}")


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("⏳ Fetching data...")
    prices   = get_price_snapshot()
    earnings = get_earnings()
    news     = get_news()
    macro    = get_macro()
    analyst  = get_analyst_actions()

    print(f"  prices={len(prices)} earnings={len(earnings)} news={len(news)} macro={len(macro)} analyst={len(analyst)}")

    html = build_email(prices, earnings, news, macro, analyst)
    send_email(html)
