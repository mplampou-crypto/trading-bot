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
TOKEN = os.getenv("TELEGRAM_TOKEN")
OKX_API_KEY = os.getenv("OKX_API_KEY")
OKX_SECRET = os.getenv("OKX_SECRET")
OKX_PASSWORD = os.getenv("OKX_PASSWORD")

SYMBOL = "AAVE/USDT"
ADMIN_ID = 123456789   # ΒΑΛΕ ΤΟ ΔΙΚΟ ΣΟΥ TELEGRAM ID
GROUP_LINK = "https://t.me/yourgroup"

# ===== DATABASE =====
def init_db():
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     INTEGER PRIMARY KEY,
            username    TEXT,
            plan        TEXT,
            approved    INTEGER DEFAULT 0,
            expiry      TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS pending (
            user_id     INTEGER PRIMARY KEY,
            username    TEXT,
            plan        TEXT,
            paysafe     TEXT
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
    return row  # (plan, paysafe) or None

def db_get_approved_users():
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("SELECT user_id, expiry FROM users WHERE approved=1")
    rows = c.fetchall()
    conn.close()
    return rows  # list of (user_id, expiry)

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
    df = df.copy()
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
    features = np.array([[last["rsi"], last["ema20"], last["ema50"], last["v"]]])
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
    low  = df["l"].rolling(20).min().iloc[-1]
    if last["c"] > high:
        return "LONG", last["c"]
    if last["c"] < low:
        return "SHORT", last["c"]
    return None, None

# ===== USER STATE (για να ξέρουμε τι περιμένουμε από κάθε user) =====
# "await_paysafe" = περιμένει paysafe code
user_state = {}

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

    # Αποθήκευσε επιλογή στο state
    user_state[uid] = {"step": "await_paysafe", "plan": q.data}

    plan_text = "25€ / 1 μήνα" if q.data == "sub1" else "100€ / 6 μήνες"

    await q.edit_message_text(
        f"✅ Επέλεξες: {plan_text}\n\n"
        f"📩 Στείλε τον 12ψήφιο κωδικό PaySafe:\n"
        f"(π.χ. 123456789012)"
    )

# ===== HANDLE MESSAGES =====
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()

    state = user_state.get(uid, {})

    # ===== ΑΝΑΜΕΝΟΥΜΕ PAYSAFE =====
    if state.get("step") == "await_paysafe":

        # Έλεγχος: μόνο αριθμοί και ακριβώς 12 ψηφία
        if not text.isdigit() or len(text) != 12:
            await update.message.reply_text(
                "❌ Μη έγκυρος κωδικός!\n\n"
                "Ο κωδικός PaySafe πρέπει να είναι ακριβώς 12 αριθμοί.\n"
                "Δοκίμασε ξανά:"
            )
            return

        plan = state["plan"]
        username = update.effective_user.username or str(uid)

        # Αποθήκευσε στη βάση
        db_add_pending(uid, username, plan, text)
        user_state.pop(uid, None)

        plan_text = "25€ / 1 μήνα" if plan == "sub1" else "100€ / 6 μήνες"

        # Μήνυμα στον admin
        kb_admin = [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"approve_{uid}"),
                InlineKeyboardButton("❌ Reject",  callback_data=f"reject_{uid}")
            ]
        ]
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
        return

    # Αν δεν είναι σε κάποιο step, αγνόησε
    return

# ===== ADMIN APPROVE/REJECT BUTTONS =====
async def admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.from_user.id != ADMIN_ID:
        return

    data = q.data  # "approve_12345" ή "reject_12345"
    action, uid_str = data.split("_", 1)
    uid = int(uid_str)

    pending = db_get_pending(uid)

    if not pending:
        await q.edit_message_text("⚠️ Ο χρήστης δεν βρέθηκε στα pending.")
        return

    plan, paysafe = pending

    if action == "approve":
        expiry = db_approve(uid, plan)
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

# ===== ADMIN COMMANDS (manual fallback) =====
async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    uid = int(context.args[0])
    pending = db_get_pending(uid)
    if not pending:
        await update.message.reply_text("⚠️ User δεν βρέθηκε.")
        return
    plan, _ = pending
    expiry = db_approve(uid, plan)
    kb = [[InlineKeyboardButton("🚀 Μπες στην ομάδα", url=GROUP_LINK)]]
    await context.bot.send_message(chat_id=uid, text=f"✅ Εγκρίθηκες! Λήξη: {expiry}", reply_markup=InlineKeyboardMarkup(kb))
    await update.message.reply_text(f"✅ Approved {uid}")

async def reject_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    uid = int(context.args[0])
    db_reject(uid)
    await context.bot.send_message(chat_id=uid, text="❌ Απορρίφθηκες.")
    await update.message.reply_text(f"❌ Rejected {uid}")

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

            approved = db_get_approved_users()
            now = datetime.now()

            for uid, expiry in approved:
                try:
                    # Έλεγχος αν έχει λήξει η συνδρομή
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

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("approve", approve_cmd))
    app.add_handler(CommandHandler("reject", reject_cmd))
    app.add_handler(CallbackQueryHandler(choose_sub, pattern="^sub"))
    app.add_handler(CallbackQueryHandler(admin_action, pattern="^approve_|^reject_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    async with app:
        await app.start()
        await app.updater.start_polling()
        print("RUNNING...")
        await loop(app)

if __name__ == "__main__":
    asyncio.run(main())