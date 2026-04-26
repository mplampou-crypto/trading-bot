import os
import ccxt
import pandas as pd
import ta
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# ================= ENV VARIABLES =================
TOKEN = os.getenv("TELEGRAM_TOKEN")
OKX_API_KEY = os.getenv("OKX_API_KEY")
OKX_SECRET = os.getenv("OKX_SECRET")
OKX_PASSWORD = os.getenv("OKX_PASSWORD")

SYMBOL = "AAVE/USDT"

# 👉 ΒΑΛΕ ΤΟ TELEGRAM ID ΣΟΥ
CHAT_ID = 123456789

# ================= EXCHANGE =================
exchange = ccxt.okx({
    "apiKey": OKX_API_KEY,
    "secret": OKX_SECRET,
    "password": OKX_PASSWORD,
})

# ================= DATA =================
def get_data():
    ohlcv = exchange.fetch_ohlcv(SYMBOL, timeframe="15m", limit=100)
    df = pd.DataFrame(ohlcv, columns=["t","o","h","l","c","v"])

    df["rsi"] = ta.momentum.RSIIndicator(df["c"]).rsi()
    return df

# ================= SIGNAL =================
def generate_signal():
    df = get_data()
    last = df.iloc[-1]

    high = df["h"].rolling(20).max().iloc[-1]
    low = df["l"].rolling(20).min().iloc[-1]

    if last["c"] > high:
        return "LONG", last["c"]

    if last["c"] < low:
        return "SHORT", last["c"]

    return None, None

# ================= SIGNAL LOOP =================
async def send_signal(app):
    while True:
        try:
            signal, price = generate_signal()

            if signal:
                tp = price * 1.02 if signal == "LONG" else price * 0.98
                sl = price * 0.99 if signal == "LONG" else price * 1.01

                keyboard = [[
                    InlineKeyboardButton("✅ Confirm", callback_data=f"{signal}|{price}|{tp}|{sl}"),
                    InlineKeyboardButton("❌ Cancel", callback_data="cancel")
                ]]

                msg = f"""
📊 SIGNAL

Direction: {signal}
Entry: {price:.2f}
TP: {tp:.2f}
SL: {sl:.2f}
"""

                await app.bot.send_message(
                    chat_id=CHAT_ID,
                    text=msg,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )

            await asyncio.sleep(60)

        except Exception as e:
            print("ERROR:", e)
            await asyncio.sleep(10)

# ================= BUTTON HANDLER =================
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "cancel":
        await q.edit_message_text("❌ Trade canceled")
        return

    signal, price, tp, sl = q.data.split("|")

    price = float(price)
    tp = float(tp)
    sl = float(sl)

    side = "buy" if signal == "LONG" else "sell"

    try:
        exchange.create_market_order(
            SYMBOL,
            side,
            0.01  # μικρό ποσό για αρχή
        )

        await q.edit_message_text("✅ TRADE EXECUTED")

    except Exception as e:
        await q.edit_message_text(f"❌ ERROR: {e}")

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Bot is running")

# ================= MAIN =================
async def main():
    if not TOKEN:
        print("❌ TELEGRAM TOKEN NOT FOUND")
        return

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))

    asyncio.create_task(send_signal(app))

    print("🚀 BOT RUNNING...")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())