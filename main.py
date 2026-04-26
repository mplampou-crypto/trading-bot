import os, asyncio, ccxt, pandas as pd, ta, numpy as np
from sklearn.ensemble import RandomForestClassifier

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)

# ===== ENV =====
TOKEN = os.getenv("TELEGRAM_TOKEN")
OKX_API_KEY = os.getenv("OKX_API_KEY")
OKX_SECRET = os.getenv("OKX_SECRET")
OKX_PASSWORD = os.getenv("OKX_PASSWORD")

SYMBOL = "AAVE/USDT"
ADMIN_ID = 123456789   # ΒΑΛΕ ΤΟ ΔΙΚΟ ΣΟΥ TELEGRAM ID
GROUP_LINK = "https://t.me/yourgroup"

# ===== USERS =====
users = {}
pending = {}

# ===== EXCHANGE =====
exchange = ccxt.okx({
    "apiKey": OKX_API_KEY,
    "secret": OKX_SECRET,
    "password": OKX_PASSWORD,
})

# ===== ML MODEL =====
model = RandomForestClassifier(n_estimators=50)
trained = False

def get_data():
    ohlcv = exchange.fetch_ohlcv(SYMBOL, "15m", limit=150)
    df = pd.DataFrame(ohlcv, columns=["t","o","h","l","c","v"])
    df["rsi"] = ta.momentum.RSIIndicator(df["c"]).rsi()
    df["ema20"] = ta.trend.ema_indicator(df["c"], 20)
    df["ema50"] = ta.trend.ema_indicator(df["c"], 50)
    return df.dropna()

def build_features(df):
    df["target"] = (df["c"].shift(-3) > df["c"]).astype(int)
    df = df.dropna()
    X = df[["rsi","ema20","ema50","v"]]
    y = df["target"]
    return X, y

def train_model():
    global trained
    df = get_data()
    X, y = build_features(df)
    model.fit(X, y)
    trained = True
    print("ML trained")

def ml_score(df):
    if not trained:
        train_model()
    last = df.iloc[-1]
    features = np.array([[
        last["rsi"], last["ema20"], last["ema50"], last["v"]
    ]])
    prob = model.predict_proba(features)[0][1]
    return prob * 100

# ===== LEVERAGE =====
def leverage(score):
    if score > 80:
        return 10
    elif score > 70:
        return 5
    else:
        return 2

# ===== SIGNAL =====
def signal(df):
    last = df.iloc[-1]
    high = df["h"].rolling(20).max().iloc[-1]
    low = df["l"].rolling(20).min().iloc[-1]
    if last["c"] > high:
        return "LONG", last["c"]
    if last["c"] < low:
        return "SHORT", last["c"]
    return None, None

# ===== START =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("💳 25€ / 1 μήνα", callback_data="sub1")],
        [InlineKeyboardButton("💳 100€ / 6 μήνες", callback_data="sub6")]
    ]
    await update.message.reply_text(
        "👋 Καλώς ήρθες!\n\nΔιάλεξε συνδρομή:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# ===== SELECT SUB =====
async def choose_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    pending[uid] = q.data
    await q.edit_message_text(
        "📩 Στείλε proof πληρωμής (screenshot ή tx hash)"
    )

# ===== HANDLE PROOF =====
async def proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in pending:
        return
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"🧾 User {uid} sent payment proof\n/approve {uid}\n/reject {uid}"
    )
    await update.message.reply_text("⏳ Περιμένεις approval")

# ===== ADMIN =====
async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    uid = int(context.args[0])
    users[uid] = True
    kb = [[InlineKeyboardButton("🚀 Μπες στην ομάδα", url=GROUP_LINK)]]
    await context.bot.send_message(
        chat_id=uid,
        text="✅ Approved!",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    uid = int(context.args[0])
    await context.bot.send_message(
        chat_id=uid,
        text="❌ Payment rejected"
    )

# ===== SIGNAL LOOP =====
async def loop(app):
    while True:
        try:
            df = get_data()
            sig, price = signal(df)

            if not sig:
                await asyncio.sleep(60)
                continue

            score = ml_score(df)

            if score < 60:
                await asyncio.sleep(60)
                continue

            lev = leverage(score)
            tp = price * (1.02 if sig == "LONG" else 0.98)
            sl = price * (0.99 if sig == "LONG" else 1.01)

            msg = (
                f"📊 SIGNAL\n\n"
                f"{sig}\n"
                f"Entry: {price:.2f}\n"
                f"TP: {tp:.2f}\n"
                f"SL: {sl:.2f}\n\n"
                f"🧠 Score: {score:.1f}\n"
                f"⚡ Leverage: {lev}x"
            )

            for uid in list(users.keys()):
                try:
                    await app.bot.send_message(uid, msg)
                except Exception as e:
                    print(f"Failed to send to {uid}: {e}")

        except Exception as e:
            print(f"Loop error: {e}")

        await asyncio.sleep(60)

# ===== MAIN =====
async def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(choose_sub, pattern="sub"))
    app.add_handler(MessageHandler(filters.TEXT, proof))
    app.add_handler(CommandHandler("approve", approve))
    app.add_handler(CommandHandler("reject", reject))

    async with app:
        await app.start()
        await app.updater.start_polling()
        print("RUNNING...")
        await loop(app)

if __name__ == "__main__":
    asyncio.run(main())