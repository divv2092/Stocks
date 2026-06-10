# 📈 Daily Stock Alert

A GitHub Actions–powered script that emails you a daily digest of **earnings, news, macro events, and analyst signals** for your watchlist — every weekday at 8 AM ET.

**Watchlist:** TDOC · NVO · BABA · NOW · SMH · META · GOOGL · AMD · MRNA · CVNA · NKE

---

## What You Get in Each Email

| Section | Details |
|---|---|
| 📊 Price Snapshot | Previous close, daily % change (color-coded) |
| 📅 Earnings This Week | Upcoming earnings for your tickers, EPS estimate vs actual |
| 📰 Top News | Up to 3 headlines per ticker from today |
| 🏛️ Macro Events | High-impact US economic events this week (Fed, CPI, jobs, etc.) |
| 🔍 Analyst Consensus | Buy / Hold / Sell counts + overall signal |

---

## Setup (One Time)

### Step 1 — Get a free Finnhub API key
1. Go to [finnhub.io](https://finnhub.io) → Sign up (free)
2. Copy your API key from the dashboard

### Step 2 — Create a Gmail App Password
Gmail requires an App Password (not your regular password) for SMTP access.

1. Go to your Google Account → **Security**
2. Enable **2-Step Verification** (required)
3. Go to **App Passwords** → Select app: Mail → Select device: Other → type "StockAlert"
4. Copy the 16-character password generated

### Step 3 — Add GitHub Secrets
In your GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**

Add these 4 secrets:

| Secret Name | Value |
|---|---|
| `FINNHUB_KEY` | Your Finnhub API key |
| `GMAIL_USER` | Your Gmail address (e.g. `you@gmail.com`) |
| `GMAIL_APP_PASSWORD` | The 16-char App Password from Step 2 |
| `ALERT_TO_EMAIL` | Email address to receive alerts |

### Step 4 — Push to GitHub
```bash
git init
git add .
git commit -m "Initial stock alert setup"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/stock-alert.git
git push -u origin main
```

The workflow runs automatically at **8 AM ET Monday–Friday**.

---

## Manual Test Run
Go to your repo → **Actions** tab → **Daily Stock Alert** → **Run workflow** → click the green button.

---

## Customization

**Change watchlist** — Edit `WATCHLIST` in `stock_alert.py`:
```python
WATCHLIST = ["TDOC", "NVO", "BABA", ...]
```

**Change time** — Edit the cron in `.github/workflows/stock_alert.yml`:
```yaml
- cron: '0 12 * * 1-5'   # 12:00 UTC = 8:00 AM ET
- cron: '0 13 * * 1-5'   # 13:00 UTC = 9:00 AM ET
- cron: '0 21 * * 1-5'   # 21:00 UTC = 5:00 PM ET (after-hours)
```

**Price move threshold** — Add alerts only for big movers:
```python
PRICE_MOVE_THRESHOLD = 0.03   # alert only if ±3% or more
```

---

## File Structure
```
stock-alert/
├── stock_alert.py               ← Main script
├── requirements.txt             ← Python dependencies
├── .github/
│   └── workflows/
│       └── stock_alert.yml      ← GitHub Actions schedule
└── README.md
```

---

## Free Tier Limits
| Service | Free Tier |
|---|---|
| Finnhub | 60 API calls/minute — plenty for daily use |
| GitHub Actions | 2,000 minutes/month — script takes ~30 sec |
| Gmail SMTP | Unlimited sends |

---

*Data sources: Finnhub (earnings, news, macro, analyst) + Yahoo Finance (price data). Not financial advice.*
