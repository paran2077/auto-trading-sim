"""
自動売買ボット - 毎日1回実行される
移動平均クロス戦略（MA5 / MA20）で売買判断
"""

import sys, io
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import yfinance as yf
import pandas as pd
import sqlite3
from datetime import datetime, date
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "trading.db")

STOCKS = {
    "7203.T": "トヨタ",
    "6758.T": "ソニー",
    "7974.T": "任天堂",
    "AAPL":   "Apple",
    "NVDA":   "Nvidia",
    "TSLA":   "Tesla",
}

INITIAL_CASH = 100_000  # 仮想元金（円）
MAX_POSITION_RATIO = 0.2  # 1銘柄に使う資金の最大割合


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS portfolio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            shares REAL NOT NULL,
            avg_price REAL NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS cash (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            amount REAL NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            name TEXT NOT NULL,
            action TEXT NOT NULL,
            shares REAL NOT NULL,
            price REAL NOT NULL,
            total REAL NOT NULL,
            reason TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS daily_summary (
            date TEXT PRIMARY KEY,
            total_value REAL NOT NULL,
            cash REAL NOT NULL,
            stock_value REAL NOT NULL
        )
    """)
    # 初回のみ現金を設定
    c.execute("INSERT OR IGNORE INTO cash (id, amount) VALUES (1, ?)", (INITIAL_CASH,))
    conn.commit()
    conn.close()


def get_cash(conn):
    return conn.execute("SELECT amount FROM cash WHERE id=1").fetchone()[0]


def set_cash(conn, amount):
    conn.execute("UPDATE cash SET amount=? WHERE id=1", (amount,))


def get_position(conn, ticker):
    row = conn.execute(
        "SELECT shares, avg_price FROM portfolio WHERE ticker=?", (ticker,)
    ).fetchone()
    return row if row else (0, 0)


def set_position(conn, ticker, shares, avg_price):
    if shares <= 0:
        conn.execute("DELETE FROM portfolio WHERE ticker=?", (ticker,))
    else:
        conn.execute(
            "INSERT OR REPLACE INTO portfolio (ticker, shares, avg_price) VALUES (?,?,?)",
            (ticker, shares, avg_price)
        )


def get_price_history(ticker, period="60d"):
    try:
        df = yf.download(ticker, period=period, auto_adjust=True, progress=False)
        if df.empty:
            return None
        return df["Close"].squeeze()
    except Exception as e:
        print(f"  価格取得エラー {ticker}: {e}")
        return None


def calc_signal(prices):
    """MA5がMA20を上抜け→BUY、下抜け→SELL、それ以外→HOLD"""
    if len(prices) < 21:
        return "HOLD", None, None
    ma5  = prices.rolling(5).mean()
    ma20 = prices.rolling(20).mean()
    # 直近2日のクロス判定
    prev_diff = ma5.iloc[-2] - ma20.iloc[-2]
    curr_diff = ma5.iloc[-1] - ma20.iloc[-1]
    if prev_diff < 0 and curr_diff >= 0:
        return "BUY",  round(float(ma5.iloc[-1]), 2), round(float(ma20.iloc[-1]), 2)
    if prev_diff > 0 and curr_diff <= 0:
        return "SELL", round(float(ma5.iloc[-1]), 2), round(float(ma20.iloc[-1]), 2)
    return "HOLD", round(float(ma5.iloc[-1]), 2), round(float(ma20.iloc[-1]), 2)


def run_trading():
    init_db()
    conn = sqlite3.connect(DB_PATH)
    today = date.today().isoformat()

    print(f"\n=== 自動売買実行: {today} ===")

    cash = get_cash(conn)
    print(f"現在の現金残高: ¥{cash:,.0f}")

    for ticker, name in STOCKS.items():
        print(f"\n[{name} / {ticker}]")
        prices = get_price_history(ticker)
        if prices is None or len(prices) < 2:
            print("  データ取得失敗、スキップ")
            continue

        current_price = float(prices.iloc[-1])
        signal, ma5, ma20 = calc_signal(prices)
        shares_held, avg_price = get_position(conn, ticker)

        print(f"  現在値: {current_price:.2f}  MA5: {ma5}  MA20: {ma20}  シグナル: {signal}")
        print(f"  保有株数: {shares_held:.4f}  平均取得価格: {avg_price:.2f}")

        if signal == "BUY" and shares_held == 0:
            budget = cash * MAX_POSITION_RATIO
            if budget < current_price:
                print("  資金不足のためスキップ")
                continue
            shares_to_buy = budget / current_price
            cost = shares_to_buy * current_price
            cash -= cost
            set_cash(conn, cash)
            set_position(conn, ticker, shares_to_buy, current_price)
            conn.execute(
                "INSERT INTO trades (date,ticker,name,action,shares,price,total,reason) VALUES (?,?,?,?,?,?,?,?)",
                (today, ticker, name, "BUY", shares_to_buy, current_price, cost,
                 f"ゴールデンクロス MA5={ma5} MA20={ma20}")
            )
            print(f"  → 買い注文: {shares_to_buy:.4f}株 @ {current_price:.2f} (合計: ¥{cost:,.0f})")

        elif signal == "SELL" and shares_held > 0:
            proceeds = shares_held * current_price
            cash += proceeds
            set_cash(conn, cash)
            set_position(conn, ticker, 0, 0)
            profit = proceeds - (shares_held * avg_price)
            conn.execute(
                "INSERT INTO trades (date,ticker,name,action,shares,price,total,reason) VALUES (?,?,?,?,?,?,?,?)",
                (today, ticker, name, "SELL", shares_held, current_price, proceeds,
                 f"デッドクロス MA5={ma5} MA20={ma20} 損益: ¥{profit:,.0f}")
            )
            print(f"  → 売り注文: {shares_held:.4f}株 @ {current_price:.2f} (合計: ¥{proceeds:,.0f}, 損益: ¥{profit:,.0f})")
        else:
            print("  → 様子見（ホールド）")

    # 総資産を記録
    stock_value = 0.0
    positions = conn.execute("SELECT ticker, shares FROM portfolio").fetchall()
    for t, s in positions:
        prices = get_price_history(t, period="5d")
        if prices is not None and len(prices) > 0:
            stock_value += s * float(prices.iloc[-1])

    total_value = cash + stock_value
    conn.execute(
        "INSERT OR REPLACE INTO daily_summary (date, total_value, cash, stock_value) VALUES (?,?,?,?)",
        (today, total_value, cash, stock_value)
    )
    conn.commit()
    conn.close()

    print(f"\n=== 本日の結果 ===")
    print(f"現金: ¥{cash:,.0f}")
    print(f"株式評価額: ¥{stock_value:,.0f}")
    print(f"総資産: ¥{total_value:,.0f}")
    print(f"損益: ¥{total_value - INITIAL_CASH:+,.0f}")


if __name__ == "__main__":
    run_trading()
