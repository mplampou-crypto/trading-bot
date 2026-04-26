import ccxt
import pandas as pd
import ta
import time
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

from config import *

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

# ================= TELEGRAM =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot is running...")

# ================= SEND SIGNAL =================
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
Entry: {price}
TP: {tp}
SL: {sl}
"""

                await app.bot.send_message(
                    chat_id=app.bot_data["chat_id"],
                    text=msg,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )

            await asyncio.sleep(60)

        except Exception as e:
            print("error:", e)
            await asyncio.sleep(10)

# ================= BUTTONS =================
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "cancel":
        await q.edit_message_text("❌ Canceled")
        return

    signal, price, tp, sl = q.data.split("|")

    price = float(price)
    tp = float(tp)
    sl = float(sl)

    side = "buy" if signal == "LONG" else "sell"

    try:
        order = exchange.create_market_order(
            SYMBOL,
            side,
            0.01
        )

        await q.edit_message_text("✅ TRADE EXECUTED")

    except Exception as e:
        await q.edit_message_text(f"ERROR: {e}")

# ================= MAIN =================
async def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))

    # chat id (βάλε το δικό σου telegram id εδώ)
    app.bot_data["chat_id"] = 123456789

    asyncio.create_task(send_signal(app))

    print("BOT RUNNING")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())