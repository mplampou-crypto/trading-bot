import ccxt, pandas as pd, ta, time, datetime
from telegram import *
from telegram.ext import *
from config import *
from db import conn, cur

# ===== OKX =====
exchange = ccxt.okx({
    "apiKey": OKX_API_KEY,
    "secret": OKX_SECRET,
    "password": OKX_PASSWORD
})

# ===== DATA =====
def get_data():
    df = pd.DataFrame(
        exchange.fetch_ohlcv(SYMBOL, '15m', limit=200),
        columns=["t","o","h","l","c","v"]
    )

    df["rsi"] = ta.momentum.RSIIndicator(df["c"]).rsi()
    df["ema20"] = ta.trend.ema_indicator(df["c"], 20)
    df["ema50"] = ta.trend.ema_indicator(df["c"], 50)
    df["vol_ma"] = df["v"].rolling(20).mean()

    return df

# ===== STRATEGY =====
def generate_signal():
    df = get_data()
    last = df.iloc[-1]

    high = df["h"].rolling(30).max().iloc[-1]
    low = df["l"].rolling(30).min().iloc[-1]

    if last["c"] > high:
        return "LONG", df

    if last["c"] < low:
        return "SHORT", df

    return None, df

# ===== AI FILTER =====
def ai_filter(df, direction):
    last = df.iloc[-1]
    score = 50

    if last["v"] > last["vol_ma"]:
        score += 15

    if 40 < last["rsi"] < 60:
        score += 10

    if direction == "LONG" and last["ema20"] > last["ema50"]:
        score += 10

    if direction == "SHORT" and last["ema20"] < last["ema50"]:
        score += 10

    return score

# ===== RISK =====
def position_size(balance, entry, sl):
    risk = balance * RISK_PER_TRADE
    return round(risk / abs(entry - sl), 3)

def trades_today():
    return cur.execute("SELECT COUNT(*) FROM trades").fetchone()[0]

# ===== TELEGRAM =====
async def start(update, context):
    await update.message.reply_text("Send 12-digit code to activate")

async def handle(update, context):
    uid = update.effective_user.id
    txt = update.message.text

    if txt.isdigit() and len(txt) == 12:
        cur.execute("INSERT OR REPLACE INTO users VALUES (?,1)", (uid,))
        conn.commit()
        await update.message.reply_text("✅ Activated")

# ===== SIGNAL LOOP =====
async def signal_loop(app):
    while True:
        try:
            direction, df = generate_signal()

            if not direction:
                time.sleep(60)
                continue

            score = ai_filter(df, direction)

            if score < 70:
                time.sleep(60)
                continue

            entry = df["c"].iloc[-1]
            tp = entry * (1.02 if direction == "LONG" else 0.98)
            sl = entry * (0.99 if direction == "LONG" else 1.01)

            msg = f"""
📊 SIGNAL

Direction: {direction}
Entry: {entry:.2f}
TP: {tp:.2f}
SL: {sl:.2f}

AI Score: {score}
"""

            keyboard = [[
                InlineKeyboardButton("✅ Confirm",
                    callback_data=f"{entry}|{tp}|{sl}|{direction}"),
                InlineKeyboardButton("❌ Cancel", callback_data="cancel")
            ]]

            users = cur.execute("SELECT user_id FROM users WHERE approved=1").fetchall()

            for u in users:
                await app.bot.send_message(
                    u[0], msg,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )

        except Exception as e:
            print("ERROR:", e)

        time.sleep(60)

# ===== EXECUTION =====
async def buttons(update, context):
    q = update.callback_query
    await q.answer()

    if q.data == "cancel":
        await q.edit_message_text("❌ Canceled")
        return

    entry, tp, sl, direction = q.data.split("|")
    entry, tp, sl = float(entry), float(tp), float(sl)

    balance = 1000
    size = position_size(balance, entry, sl)

    side = "buy" if direction == "LONG" else "sell"

    try:
        exchange.create_market_order(SYMBOL, side, size)

        exchange.create_order(
            SYMBOL,
            "limit",
            "sell" if direction == "LONG" else "buy",
            size,
            tp
        )

        cur.execute("""
        INSERT INTO trades (entry,tp,sl,direction,status,pnl)
        VALUES (?,?,?,?,?,?)
        """, (entry, tp, sl, direction, "open", 0))
        conn.commit()

        await q.edit_message_text("✅ Trade opened")

    except Exception as e:
        await q.edit_message_text(f"Error: {e}")

# ===== TRACK TRADES =====
def track_trades():
    while True:
        trades = cur.execute("SELECT * FROM trades WHERE status='open'").fetchall()
        price = exchange.fetch_ticker(SYMBOL)["last"]

        for t in trades:
            id, entry, tp, sl, direction, _, _ = t

            if direction == "LONG":
                if price >= tp:
                    pnl = tp - entry
                    cur.execute("UPDATE trades SET status='win', pnl=? WHERE id=?", (pnl, id))
                elif price <= sl:
                    pnl = sl - entry
                    cur.execute("UPDATE trades SET status='loss', pnl=? WHERE id=?", (pnl, id))

            if direction == "SHORT":
                if price <= tp:
                    pnl = entry - tp
                    cur.execute("UPDATE trades SET status='win', pnl=? WHERE id=?", (pnl, id))
                elif price >= sl:
                    pnl = entry - sl
                    cur.execute("UPDATE trades SET status='loss', pnl=? WHERE id=?", (pnl, id))

        conn.commit()
        time.sleep(20)

# ===== MAIN =====
async def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT, handle))
    app.add_handler(CallbackQueryHandler(buttons))

    app.create_task(signal_loop(app))

    import threading
    threading.Thread(target=track_trades).start()

    print("🚀 BOT RUNNING...")
    await app.run_polling()

import asyncio
asyncio.run(main())