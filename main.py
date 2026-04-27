import os, asyncio, ccxt, pandas as pd, ta, numpy as np, sqlite3
from sklearn.ensemble import RandomForestClassifier
from datetime import datetime, timedelta

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)

# ===== ENV =====
TELEGRAM_TOKEN = os.getenv("8695476159:AAFlzUmjHI8WNAK95Cr3G-bdTIMPZmdpe-c")
if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN is missing!")
OKX_API_KEY = os.getenv("73a643a4-0b70-489a-93de-1757e6216145")
OKX_SECRET = os.getenv(" E47E8A6C808A701BBEEDF53EDF8CD6EB")
OKX_PASSWORD = os.getenv("Mysecurepass123#")

SYMBOL = "AAVE/USDT"
ADMIN_ID =  5313104598
GROUP_LINK = "https://t.me/yourgroup"

# ===== DATABASE =====
def init_db():
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id  INTEGER PRIMARY KEY,
            username TEXT,
            plan     TEXT,
            approved INTEGER DEFAULT 0,
            expiry   TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS pending (
            user_id  INTEGER PRIMARY KEY,
            username TEXT,
            plan     TEXT,
            paysafe  TEXT
        )
    """)
    conn.commit()
    conn.close()

def db_approve(user_id, plan):
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    months = 1 if plan == "sub1" else 6
    expiry = (datetime.now() + timedelta(days=30 * months)).strftime("%Y-%m-%d")
    c.execute("""
        INSERT INTO users (user_id, plan, approved, expiry)
        VALUES (?, ?, 1, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            plan=excluded.plan, approved=1, expiry=excluded.expiry
    """, (user_id, plan, expiry))
    c.execute("DELETE FROM pending WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()
    return expiry

def db_reject(user_id):
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("DELETE FROM pending WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def db_add_pending(user_id, username, plan, paysafe):
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("""
        INSERT INTO pending (user_id, username, plan, paysafe)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            plan=excluded.plan, paysafe=excluded.paysafe
    """, (user_id, username, plan, paysafe))
    conn.commit()
    conn.close()

def db_get_pending(user_id):
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("SELECT plan, paysafe FROM pending WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row

def db_get_approved_users():
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("SELECT user_id, expiry FROM users WHERE approved=1")
    rows = c.fetchall()
    conn.close()
    return rows

def db_is_approved(user_id):
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("SELECT approved, expiry FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return False
    approved, expiry = row
    if not approved:
        return False
    if datetime.strptime(expiry, "%Y-%m-%d") < datetime.now():
        return False
    return True

# ===== EXCHANGE =====
exchange = ccxt.okx({
    "apiKey": OKX_API_KEY,
    "secret": OKX_SECRET,
    "password": OKX_PASSWORD,
})

# ===== ML MODEL =====
model = RandomForestClassifier(n_estimators=100, random_state=42)
trained = False

def get_data():
    ohlcv = exchange.fetch_ohlcv(SYMBOL, "15m", limit=300)
    df = pd.DataFrame(ohlcv, columns=["t","o","h","l","c","v"])

    # Trend
    df["ema20"]  = ta.trend.ema_indicator(df["c"], 20)
    df["ema50"]  = ta.trend.ema_indicator(df["c"], 50)
    df["ema200"] = ta.trend.ema_indicator(df["c"], 200)

    # Momentum
    df["rsi"] = ta.momentum.RSIIndicator(df["c"]).rsi()
    macd = ta.trend.MACD(df["c"])
    df["macd"]        = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_diff"]   = macd.macd_diff()

    # Volatility
    bb = ta.volatility.BollingerBands(df["c"])
    df["bb_upper"] = bb.bollinger_hband()
    df["bb_lower"] = bb.bollinger_lband()
    df["bb_width"] = bb.bollinger_wband()
    df["atr"]      = ta.volatility.AverageTrueRange(df["h"], df["l"], df["c"]).average_true_range()

    # Volume
    df["volume_ma"] = df["v"].rolling(20).mean()
    df["volume_ratio"] = df["v"] / df["volume_ma"]

    return df.dropna()

def build_features(df):
    df = df.copy()
    df["target"] = (df["c"].shift(-3) > df["c"]).astype(int)
    df = df.dropna()
    features = [
        "rsi", "ema20", "ema50", "ema200",
        "macd", "macd_signal", "macd_diff",
        "bb_upper", "bb_lower", "bb_width",
        "atr", "volume_ratio", "v"
    ]
    return df[features], df["target"]

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
    features = [[
        last["rsi"], last["ema20"], last["ema50"], last["ema200"],
        last["macd"], last["macd_signal"], last["macd_diff"],
        last["bb_upper"], last["bb_lower"], last["bb_width"],
        last["atr"], last["volume_ratio"], last["v"]
    ]]
    prob = model.predict_proba(features)[0][1]
    return prob * 100

# ===== DYNAMIC TP/SL =====
def calc_tp_sl(sig, price, atr):
    """
    TP/SL βασισμένο στο ATR (Average True Range)
    αντί για σταθερό ποσοστό.
    LONG:  TP = price + 2*ATR  |  SL = price - 1*ATR
    SHORT: TP = price - 2*ATR  |  SL = price + 1*ATR
    Risk/Reward παραμένει 1:2
    """
    if sig == "LONG":
        tp = price + (2 * atr)
        sl = price - (1 * atr)
    else:
        tp = price - (2 * atr)
        sl = price + (1 * atr)
    return tp, sl

# ===== DYNAMIC LEVERAGE =====
def leverage(score, atr, price):
    """
    Leverage ανάλογα με ML score ΚΑΙ volatility (ATR%).
    Υψηλό volatility = χαμηλότερο leverage για προστασία.
    """
    atr_pct = (atr / price) * 100  # ATR ως % της τιμής

    if score > 80:
        base_lev = 10
    elif score > 70:
        base_lev = 5
    else:
        base_lev = 2

    # Μείωση leverage αν το ATR > 2% (πολύ volatile)
    if atr_pct > 2:
        base_lev = max(2, base_lev - 3)
    elif atr_pct > 1:
        base_lev = max(2, base_lev - 1)

    return base_lev

# ===== SIGNAL =====
def signal(df):
    last  = df.iloc[-1]
    high  = df["h"].rolling(20).max().iloc[-1]
    low   = df["l"].rolling(20).min().iloc[-1]

    # Επιπλέον φίλτρο: volume πρέπει να είναι πάνω από τον μέσο όρο
    if last["volume_ratio"] < 1.2:
        return None, None, None

    # Επιπλέον φίλτρο: MACD επιβεβαίωση
    if last["c"] > high and last["macd_diff"] > 0:
        return "LONG", last["c"], last["atr"]

    if last["c"] < low and last["macd_diff"] < 0:
        return "SHORT", last["c"], last["atr"]

    return None, None, None

# ===== USER STATE =====
user_state = {}

# ===== START =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("💳 25€ / 1 μήνα",   callback_data="sub1")],
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
    user_state[uid] = {"step": "await_paysafe", "plan": q.data}
    plan_text = "25€ / 1 μήνα" if q.data == "sub1" else "100€ / 6 μήνες"
    await q.edit_message_text(
        f"✅ Επέλεξες: {plan_text}\n\n"
        f"📩 Στείλε τον 12ψήφιο κωδικό PaySafe:\n"
        f"(π.χ. 123456789012)"
    )

# ===== HANDLE MESSAGES =====
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    text = update.message.text.strip()
    state = user_state.get(uid, {})

    if state.get("step") == "await_paysafe":
        if not text.isdigit() or len(text) != 12:
            await update.message.reply_text(
                "❌ Μη έγκυρος κωδικός!\n\n"
                "Ο κωδικός PaySafe πρέπει να είναι ακριβώς 12 αριθμοί.\n"
                "Δοκίμασε ξανά:"
            )
            return

        plan     = state["plan"]
        username = update.effective_user.username or str(uid)
        db_add_pending(uid, username, plan, text)
        user_state.pop(uid, None)

        plan_text = "25€ / 1 μήνα" if plan == "sub1" else "100€ / 6 μήνες"

        kb_admin = [[
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_{uid}"),
            InlineKeyboardButton("❌ Reject",  callback_data=f"reject_{uid}")
        ]]
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"🧾 Νέα αίτηση πληρωμής!\n\n"
                f"👤 User: @{username} ({uid})\n"
                f"📦 Πλάνο: {plan_text}\n"
                f"💳 PaySafe: {text}"
            ),
            reply_markup=InlineKeyboardMarkup(kb_admin)
        )
        await update.message.reply_text(
            "⏳ Ο κωδικός στάλθηκε!\nΠερίμενε έγκριση από τον admin."
        )

# ===== ADMIN BUTTONS =====
async def admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.from_user.id != ADMIN_ID:
        return

    action, uid_str = q.data.split("_", 1)
    uid     = int(uid_str)
    pending = db_get_pending(uid)

    if not pending:
        await q.edit_message_text("⚠️ Ο χρήστης δεν βρέθηκε στα pending.")
        return

    plan, paysafe = pending

    if action == "approve":
        expiry    = db_approve(uid, plan)
        plan_text = "25€ / 1 μήνα" if plan == "sub1" else "100€ / 6 μήνες"
        kb = [[InlineKeyboardButton("🚀 Μπες στην ομάδα", url=GROUP_LINK)]]
        await context.bot.send_message(
            chat_id=uid,
            text=(
                f"✅ Εγκρίθηκες!\n\n"
                f"📦 Πλάνο: {plan_text}\n"
                f"📅 Λήξη: {expiry}"
            ),
            reply_markup=InlineKeyboardMarkup(kb)
        )
        await q.edit_message_text(f"✅ Approved user {uid} — λήξη {expiry}")

    elif action == "reject":
        db_reject(uid)
        await context.bot.send_message(
            chat_id=uid,
            text="❌ Ο κωδικός PaySafe απορρίφθηκε.\nΕπικοινώνησε με τον admin."
        )
        await q.edit_message_text(f"❌ Rejected user {uid}")

# ===== ADMIN COMMANDS =====
async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    uid     = int(context.args[0])
    pending = db_get_pending(uid)
    if not pending:
        await update.message.reply_text("⚠️ User δεν βρέθηκε.")
        return
    plan, _ = pending
    expiry  = db_approve(uid, plan)
    kb = [[InlineKeyboardButton("🚀 Μπες στην ομάδα", url=GROUP_LINK)]]
    await context.bot.send_message(
        chat_id=uid,
        text=f"✅ Εγκρίθηκες! Λήξη: {expiry}",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    await update.message.reply_text(f"✅ Approved {uid}")

async def reject_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    uid = int(context.args[0])
    db_reject(uid)
    await context.bot.send_message(chat_id=uid, text="❌ Απορρίφθηκες.")
    await update.message.reply_text(f"❌ Rejected {uid}")

# ===== STATUS =====
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        df  = get_data()
        sig, price, atr = signal(df)
        score    = ml_score(df)
        approved = db_get_approved_users()
        last     = df.iloc[-1]
        atr_pct  = (last["atr"] / last["c"]) * 100

        await update.message.reply_text(
            f"🤖 Bot Status\n\n"
            f"📊 Symbol: {SYMBOL}\n"
            f"💰 Τιμή: {last['c']:.2f}\n"
            f"📈 Signal: {sig if sig else 'Κανένα αυτή τη στιγμή'}\n"
            f"🧠 ML Score: {score:.1f}\n"
            f"📉 ATR: {last['atr']:.2f} ({atr_pct:.2f}%)\n"
            f"📊 Volume Ratio: {last['volume_ratio']:.2f}x\n"
            f"👥 Approved users: {len(approved)}\n"
            f"✅ Bot: Online"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

# ===== TEST SIGNAL =====
async def test_signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        df  = get_data()
        sig, price, atr = signal(df)
        score = ml_score(df)

        if not sig:
            last    = df.iloc[-1]
            atr_pct = (last["atr"] / last["c"]) * 100
            await update.message.reply_text(
                f"📭 Δεν υπάρχει signal αυτή τη στιγμή\n\n"
                f"💰 Τιμή: {last['c']:.2f}\n"
                f"🧠 ML Score: {score:.1f}\n"
                f"📉 ATR: {last['atr']:.2f} ({atr_pct:.2f}%)\n"
                f"📊 Volume Ratio: {last['volume_ratio']:.2f}x\n"
                f"📈 MACD diff: {last['macd_diff']:.4f}"
            )
            return

        lev     = leverage(score, atr, price)
        tp, sl  = calc_tp_sl(sig, price, atr)
        atr_pct = (atr / price) * 100

        await update.message.reply_text(
            f"📊 TEST SIGNAL\n\n"
            f"{sig}\n"
            f"Entry: {price:.2f}\n"
            f"TP: {tp:.2f}\n"
            f"SL: {sl:.2f}\n\n"
            f"🧠 Score: {score:.1f}\n"
            f"⚡ Leverage: {lev}x\n"
            f"📉 ATR: {atr:.2f} ({atr_pct:.2f}%)"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

# ===== SIGNAL LOOP =====
async def loop(app):
    while True:
        try:
            df  = get_data()
            sig, price, atr = signal(df)

            if not sig:
                await asyncio.sleep(60)
                continue

            score = ml_score(df)

            if score < 60:
                await asyncio.sleep(60)
                continue

            lev    = leverage(score, atr, price)
            tp, sl = calc_tp_sl(sig, price, atr)
            atr_pct = (atr / price) * 100

            msg = (
                f"📊 SIGNAL\n\n"
                f"{sig}\n"
                f"Entry: {price:.2f}\n"
                f"TP: {tp:.2f}\n"
                f"SL: {sl:.2f}\n\n"
                f"🧠 Score: {score:.1f}\n"
                f"⚡ Leverage: {lev}x\n"
                f"📉 ATR: {atr:.2f} ({atr_pct:.2f}%)"
            )

            approved = db_get_approved_users()
            now      = datetime.now()

            for uid, expiry in approved:
                try:
                    if datetime.strptime(expiry, "%Y-%m-%d") < now:
                        continue
                    await app.bot.send_message(uid, msg)
                except Exception as e:
                    print(f"Failed to send to {uid}: {e}")

        except Exception as e:
            print(f"Loop error: {e}")

        await asyncio.sleep(60)

# ===== MAIN =====
async def main():
    init_db()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start",      start))
    app.add_handler(CommandHandler("approve",    approve_cmd))
    app.add_handler(CommandHandler("reject",     reject_cmd))
    app.add_handler(CommandHandler("status",     status))
    app.add_handler(CommandHandler("testsignal", test_signal))
    app.add_handler(CallbackQueryHandler(choose_sub,   pattern="^sub"))
    app.add_handler(CallbackQueryHandler(admin_action, pattern="^approve_|^reject_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    async with app:
        await app.start()
        await app.updater.start_polling()
        print("RUNNING...")
        await loop(app)

if __name__ == "__main__":
    asyncio.run(main())