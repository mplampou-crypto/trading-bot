from flask import Flask, render_template
import sqlite3

app = Flask(__name__)

@app.route("/")
def home():
    conn = sqlite3.connect("bot.db")
    cur = conn.cursor()

    trades = cur.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 20").fetchall()

    pnl = sum(t[6] for t in trades)
    wins = sum(1 for t in trades if t[5]=="win")
    losses = sum(1 for t in trades if t[5]=="loss")

    winrate = (wins/(wins+losses))*100 if wins+losses else 0

    return render_template("index.html",
        trades=trades,pnl=pnl,winrate=winrate)

app.run(host="0.0.0.0",port=5000)