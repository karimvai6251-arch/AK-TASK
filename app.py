import os, sqlite3
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from functools import wraps

app=Flask(__name__)
app.secret_key=os.getenv("SECRET_KEY","change-this-secret-key")
DB="app.db"
ADMIN_PASSWORD=os.getenv("ADMIN_PASSWORD","123456")

def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init_db():
    c=db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,telegram_id TEXT UNIQUE,name TEXT,username TEXT,balance REAL DEFAULT 0,ref_code TEXT UNIQUE,invited_by TEXT,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,description TEXT,reward REAL DEFAULT 0,link TEXT,active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS task_done(id INTEGER PRIMARY KEY AUTOINCREMENT,telegram_id TEXT,task_id INTEGER,UNIQUE(telegram_id,task_id));
    CREATE TABLE IF NOT EXISTS withdrawals(id INTEGER PRIMARY KEY AUTOINCREMENT,telegram_id TEXT,amount REAL,method TEXT,account TEXT,status TEXT DEFAULT 'pending',created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
    """); c.commit(); c.close()

def admin_required(f):
    @wraps(f)
    def w(*a,**k):
        if not session.get("admin"): return redirect(url_for("admin_login"))
        return f(*a,**k)
    return w

@app.route("/")
def home(): return render_template("index.html")

@app.post("/api/login")
def login():
    d=request.json or {}; tg=str(d.get("telegram_id","")).strip()
    if not tg:return jsonify(ok=False,error="Telegram ID missing"),400
    c=db(); u=c.execute("SELECT * FROM users WHERE telegram_id=?",(tg,)).fetchone()
    if not u:
        code="AK"+tg[-8:]
        c.execute("INSERT INTO users(telegram_id,name,username,ref_code,invited_by) VALUES(?,?,?,?,?)",(tg,d.get("name","User"),d.get("username",""),code,d.get("ref") or None)); c.commit()
    c.close(); return jsonify(ok=True)

@app.get("/api/data")
def data():
    tg=request.args.get("telegram_id",""); c=db()
    u=c.execute("SELECT * FROM users WHERE telegram_id=?",(tg,)).fetchone()
    ts=c.execute("SELECT * FROM tasks WHERE active=1 ORDER BY id DESC").fetchall()
    done={x["task_id"] for x in c.execute("SELECT task_id FROM task_done WHERE telegram_id=?",(tg,)).fetchall()}
    today=c.execute("""SELECT COALESCE(SUM(t.reward),0) s FROM task_done d JOIN tasks t ON t.id=d.task_id WHERE d.telegram_id=? AND date('now','localtime')=date('now','localtime')""",(tg,)).fetchone()["s"]
    c.close()
    return jsonify(user=dict(u) if u else None,tasks=[dict(t,done=t["id"] in done) for t in ts],today_income=today)

@app.post("/api/task")
def complete_task():
    d=request.json or {}; tg=str(d.get("telegram_id","")); tid=int(d.get("task_id",0)); c=db()
    t=c.execute("SELECT * FROM tasks WHERE id=? AND active=1",(tid,)).fetchone()
    u=c.execute("SELECT * FROM users WHERE telegram_id=?",(tg,)).fetchone()
    if not t or not u:c.close(); return jsonify(ok=False,error="Invalid task"),400
    try:
        c.execute("INSERT INTO task_done(telegram_id,task_id) VALUES(?,?)",(tg,tid))
        c.execute("UPDATE users SET balance=balance+? WHERE telegram_id=?",(t["reward"],tg)); c.commit()
    except sqlite3.IntegrityError:c.close(); return jsonify(ok=False,error="Task already completed")
    c.close(); return jsonify(ok=True,reward=t["reward"])

@app.post("/api/withdraw")
def withdraw():
    d=request.json or {}; tg=str(d.get("telegram_id","")); amount=float(d.get("amount",0))
    c=db(); u=c.execute("SELECT * FROM users WHERE telegram_id=?",(tg,)).fetchone()
    if not u or amount<=0 or amount>u["balance"]:c.close(); return jsonify(ok=False,error="Insufficient balance or invalid amount"),400
    c.execute("INSERT INTO withdrawals(telegram_id,amount,method,account) VALUES(?,?,?,?)",(tg,amount,d.get("method",""),d.get("account","")))
    c.execute("UPDATE users SET balance=balance-? WHERE telegram_id=?",(amount,tg)); c.commit(); c.close(); return jsonify(ok=True)

@app.route("/admin/login",methods=["GET","POST"])
def admin_login():
    if request.method=="POST" and request.form.get("password")==ADMIN_PASSWORD:
        session["admin"]=True; return redirect("/admin")
    return render_template("admin_login.html")

@app.route("/admin/logout")
def logout(): session.clear(); return redirect("/admin/login")

@app.get("/admin")
@admin_required
def admin():
    c=db(); users=c.execute("SELECT * FROM users ORDER BY id DESC").fetchall(); tasks=c.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall(); ws=c.execute("SELECT * FROM withdrawals ORDER BY id DESC").fetchall(); c.close()
    return render_template("admin.html",users=users,tasks=tasks,withdrawals=ws)

@app.post("/admin/task")
@admin_required
def add_task():
    c=db(); c.execute("INSERT INTO tasks(title,description,reward,link) VALUES(?,?,?,?)",(request.form["title"],request.form.get("description",""),float(request.form["reward"]),request.form.get("link",""))); c.commit(); c.close(); return redirect("/admin")

@app.get("/admin/withdraw/<int:wid>/<action>")
@admin_required
def waction(wid,action):
    c=db(); w=c.execute("SELECT * FROM withdrawals WHERE id=?",(wid,)).fetchone()
    if w and w["status"]=="pending" and action in ("approve","reject"):
        if action=="reject": c.execute("UPDATE users SET balance=balance+? WHERE telegram_id=?",(w["amount"],w["telegram_id"]))
        c.execute("UPDATE withdrawals SET status=? WHERE id=?",(action,wid)); c.commit()
    c.close(); return redirect("/admin")

if __name__=="__main__":
    init_db(); app.run(host="0.0.0.0",port=int(os.getenv("PORT",5000)),debug=True)
