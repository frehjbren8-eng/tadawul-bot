import os
import asyncio
import logging
import requests
from datetime import datetime
from telegram import Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ── Config ──────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
ALPHA_VANTAGE_KEY = os.environ["ALPHA_VANTAGE_KEY"]
CHAT_ID          = os.environ["CHAT_ID"]   # your telegram chat/group id

# ── Logging ──────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

# ── Saudi Stocks ──────────────────────────────────────
STOCKS = [
    {"ticker": "2222.SAU", "symbol": "2222", "name": "أرامكو السعودية",    "sector": "الطاقة"},
    {"ticker": "1120.SAU", "symbol": "1120", "name": "مصرف الراجحي",       "sector": "البنوك"},
    {"ticker": "1180.SAU", "symbol": "1180", "name": "بنك الجزيرة",        "sector": "البنوك"},
    {"ticker": "1010.SAU", "symbol": "1010", "name": "الرياض بنك",         "sector": "البنوك"},
    {"ticker": "2010.SAU", "symbol": "2010", "name": "سابك",               "sector": "البتروكيماويات"},
    {"ticker": "7010.SAU", "symbol": "7010", "name": "الاتصالات السعودية", "sector": "الاتصالات"},
    {"ticker": "7020.SAU", "symbol": "7020", "name": "موبايلي",            "sector": "الاتصالات"},
    {"ticker": "7030.SAU", "symbol": "7030", "name": "زين السعودية",       "sector": "الاتصالات"},
    {"ticker": "4001.SAU", "symbol": "4001", "name": "هايبر بنده",         "sector": "التجزئة"},
    {"ticker": "4190.SAU", "symbol": "4190", "name": "جرير",               "sector": "التجزئة"},
    {"ticker": "1050.SAU", "symbol": "1050", "name": "بنك البلاد",         "sector": "البنوك"},
    {"ticker": "1060.SAU", "symbol": "1060", "name": "بنك الإنماء",        "sector": "البنوك"},
    {"ticker": "2350.SAU", "symbol": "2350", "name": "كيان",               "sector": "البتروكيماويات"},
    {"ticker": "2090.SAU", "symbol": "2090", "name": "نماء للكيماويات",    "sector": "البتروكيماويات"},
    {"ticker": "1211.SAU", "symbol": "1211", "name": "معدنية",             "sector": "الطاقة"},
]

MA_PERIOD  = 50   # يمكن تغييره: 20, 50, 100, 200
MIN_BREAK  = 1.0  # نسبة الاختراق الأدنى (%)


# ── Alpha Vantage fetch ───────────────────────────────
def fetch_closes(ticker: str):
    url = (
        "https://www.alphavantage.co/query"
        f"?function=TIME_SERIES_DAILY_ADJUSTED"
        f"&symbol={ticker}&outputsize=full&apikey={ALPHA_VANTAGE_KEY}"
    )
    r = requests.get(url, timeout=15)
    data = r.json()

    if "Note" in data or "Information" in data:
        raise RuntimeError("RATE_LIMIT")

    ts = data.get("Time Series (Daily)")
    if not ts:
        return None

    sorted_dates = sorted(ts.keys(), reverse=True)   # newest first
    closes = [float(ts[d]["5. adjusted close"]) for d in sorted_dates]
    return closes


def calc_ma(closes, period):
    if len(closes) < period:
        return None
    return sum(closes[:period]) / period


# ── Core scan ────────────────────────────────────────
def scan_breakouts(ma_period=MA_PERIOD, min_break=MIN_BREAK):
    results = []
    rate_limited = False

    for stock in STOCKS:
        if rate_limited:
            break
        try:
            closes = fetch_closes(stock["ticker"])
            if not closes:
                continue
            ma = calc_ma(closes, ma_period)
            if not ma:
                continue

            price      = closes[0]
            prev       = closes[1] if len(closes) > 1 else price
            change_pct = ((price - prev) / prev) * 100
            break_pct  = ((price - ma) / ma) * 100

            if break_pct >= min_break:
                results.append({
                    **stock,
                    "price":      price,
                    "change":     change_pct,
                    "ma":         ma,
                    "break_pct":  break_pct,
                    "signal":     "🔥 قوية" if break_pct >= 3 else "⚡ متوسطة",
                })

            # Respect 5 calls/min free tier
            import time; time.sleep(1.4)

        except RuntimeError as e:
            if "RATE_LIMIT" in str(e):
                rate_limited = True
                log.warning("Rate limit hit during scan")
            continue
        except Exception as e:
            log.warning(f"Error fetching {stock['ticker']}: {e}")
            continue

    results.sort(key=lambda x: x["break_pct"], reverse=True)
    return results, rate_limited


# ── Message builder ───────────────────────────────────
def build_message(results, rate_limited, ma_period):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"📈 *تقرير اختراق المقاومة — تداول*",
        f"🕐 {now}  |  MA {ma_period} يوم",
        "─" * 30,
    ]

    if not results:
        lines.append("✅ لا توجد اختراقات حالياً بالمعايير المحددة.")
    else:
        lines.append(f"⚡ *{len(results)} سهم اخترق المقاومة:*\n")
        for s in results:
            chg_icon = "🟢" if s["change"] >= 0 else "🔴"
            lines.append(
                f"{chg_icon} *{s['name']}* ({s['symbol']})\n"
                f"   السعر: `{s['price']:.2f}` ر.س  |  التغيير: `{s['change']:+.2f}%`\n"
                f"   MA{ma_period}: `{s['ma']:.2f}`  |  الاختراق: `+{s['break_pct']:.2f}%`\n"
                f"   الإشارة: {s['signal']}  |  القطاع: {s['sector']}\n"
            )

    if rate_limited:
        lines.append("\n⚠️ _تم إيقاف الفحص مبكراً بسبب حد الطلبات المجاني._")

    lines.append("─" * 30)
    lines.append("_البيانات من Alpha Vantage_")
    return "\n".join(lines)


# ── Telegram handlers ─────────────────────────────────
async def cmd_start(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 أهلاً! أنا بوت كاشف اختراق المقاومة للسوق السعودي.\n\n"
        "الأوامر المتاحة:\n"
        "/scan — مسح فوري الآن\n"
        "/status — حالة البوت\n\n"
        "📅 يرسل تقرير تلقائي كل يوم الساعة 9 صباحاً."
    )


async def cmd_scan(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 جاري المسح... قد يأخذ بضع دقائق ⏳")
    try:
        results, rate_limited = await asyncio.get_event_loop().run_in_executor(
            None, scan_breakouts
        )
        msg = build_message(results, rate_limited, MA_PERIOD)
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        log.error(f"Scan error: {e}")
        await update.message.reply_text(f"❌ حدث خطأ: {e}")


async def cmd_status(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"✅ البوت يعمل بشكل طبيعي\n"
        f"📊 MA Period: {MA_PERIOD} يوم\n"
        f"📉 Min Breakout: {MIN_BREAK}%\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )


# ── Scheduled daily job ───────────────────────────────
async def daily_scan(bot: Bot):
    log.info("Running scheduled daily scan...")
    try:
        results, rate_limited = await asyncio.get_event_loop().run_in_executor(
            None, scan_breakouts
        )
        msg = build_message(results, rate_limited, MA_PERIOD)
        await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
        log.info(f"Daily scan sent: {len(results)} breakouts found")
    except Exception as e:
        log.error(f"Daily scan error: {e}")


# ── Main ──────────────────────────────────────────────
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("scan",   cmd_scan))
    app.add_handler(CommandHandler("status", cmd_status))

    # Scheduler: every day at 09:00 Riyadh time (UTC+3 = 06:00 UTC)
    scheduler = AsyncIOScheduler(timezone="Asia/Riyadh")
    scheduler.add_job(
        daily_scan,
        trigger="cron",
        hour=9, minute=0,
        args=[app.bot]
    )
    scheduler.start()

    log.info("🤖 Bot started. Waiting for messages...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
