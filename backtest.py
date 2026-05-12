"""
バックテスト - MA5/MA20クロス戦略を過去2年で検証
楽天証券 超割コース の取引コストを含む
"""

import yfinance as yf
import pandas as pd
import os
import sys

if hasattr(sys.stdout, 'buffer'):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

STOCKS = {
    "7203.T": ("トヨタ",  "JP"),
    "6758.T": ("ソニー",  "JP"),
    "7974.T": ("任天堂",  "JP"),
    "AAPL":   ("Apple",  "US"),
    "NVDA":   ("Nvidia", "US"),
    "TSLA":   ("Tesla",  "US"),
}

INITIAL_CASH    = 100_000
MAX_POS_RATIO   = 0.20
BACKTEST_PERIOD = "2y"
OUTPUT_CSV      = os.path.join(os.path.dirname(__file__), "backtest_results.csv")
SUMMARY_CSV     = os.path.join(os.path.dirname(__file__), "backtest_summary.csv")

# 楽天証券 超割コース 日本株手数料テーブル（税込）
_JP_FEE = [
    (50_000,      55),
    (100_000,     99),
    (200_000,    115),
    (500_000,    275),
    (1_000_000,  535),
    (1_500_000,  640),
    (2_000_000, 1_013),
]


def calc_rakuten_fee(amount: float, market: str) -> float:
    """楽天証券手数料を計算する。
    日本株: 超割コース（1注文ごと）
    米国株: 約定代金の0.495%（最低¥33≈$0.22、最高¥3,300≈$22 / ¥150換算）
    """
    if market == "JP":
        for threshold, fee in _JP_FEE:
            if amount <= threshold:
                return float(fee)
        return 1_070.0 + (amount - 2_000_000) * 0.00022
    else:
        return max(33.0, min(amount * 0.00495, 3_300.0))


def calc_signal(prices: pd.Series):
    if len(prices) < 21:
        return "HOLD", None, None
    ma5  = prices.rolling(5).mean()
    ma20 = prices.rolling(20).mean()
    prev_diff = float(ma5.iloc[-2]) - float(ma20.iloc[-2])
    curr_diff = float(ma5.iloc[-1]) - float(ma20.iloc[-1])
    if prev_diff < 0 and curr_diff >= 0:
        return "BUY",  round(float(ma5.iloc[-1]), 2), round(float(ma20.iloc[-1]), 2)
    if prev_diff > 0 and curr_diff <= 0:
        return "SELL", round(float(ma5.iloc[-1]), 2), round(float(ma20.iloc[-1]), 2)
    return "HOLD", round(float(ma5.iloc[-1]), 2), round(float(ma20.iloc[-1]), 2)


def run_backtest():
    print(f"=== バックテスト開始 (期間: {BACKTEST_PERIOD}) ===\n")

    # 価格データ取得
    price_data = {}
    for ticker, (name, market) in STOCKS.items():
        df = yf.download(ticker, period=BACKTEST_PERIOD, auto_adjust=True, progress=False)
        if df.empty:
            print(f"  {name}: データ取得失敗")
            continue
        price_data[ticker] = df["Close"].squeeze()
        print(f"  {name} ({ticker}): {len(df)}日分取得")

    if not price_data:
        print("データが取得できませんでした。")
        return

    # 全取引日を統合してソート
    all_dates = sorted(set().union(*[s.index for s in price_data.values()]))
    print(f"\n対象期間: {all_dates[0].date()} 〜 {all_dates[-1].date()} ({len(all_dates)}営業日)\n")

    cash       = float(INITIAL_CASH)
    positions  = {}   # ticker -> {"shares": float, "avg_price": float}
    trades     = []
    daily_vals = []
    total_fees = 0.0

    for dt in all_dates:
        day_str = str(dt.date())

        for ticker, (name, market) in STOCKS.items():
            if ticker not in price_data:
                continue
            series = price_data[ticker]
            hist   = series[series.index <= dt]
            if len(hist) < 21:
                continue

            signal, ma5, ma20 = calc_signal(hist)
            current_price = float(hist.iloc[-1])
            pos           = positions.get(ticker)
            shares_held   = pos["shares"]    if pos else 0.0
            avg_price_pos = pos["avg_price"] if pos else 0.0

            if signal == "BUY" and shares_held == 0:
                budget = cash * MAX_POS_RATIO
                if budget < current_price:
                    continue
                shares_to_buy = budget / current_price
                cost          = shares_to_buy * current_price
                fee           = calc_rakuten_fee(cost, market)
                if cash < cost + fee:
                    continue
                cash      -= cost + fee
                total_fees += fee
                positions[ticker] = {"shares": shares_to_buy, "avg_price": current_price}
                trades.append({
                    "date": day_str, "ticker": ticker, "name": name,
                    "action": "BUY", "shares": shares_to_buy,
                    "price": current_price, "total": cost,
                    "fee": round(fee, 2), "profit": None,
                    "ma5": ma5, "ma20": ma20,
                })

            elif signal == "SELL" and shares_held > 0:
                proceeds = shares_held * current_price
                fee      = calc_rakuten_fee(proceeds, market)
                profit   = proceeds - (shares_held * avg_price_pos) - fee
                cash     += proceeds - fee
                total_fees += fee
                del positions[ticker]
                trades.append({
                    "date": day_str, "ticker": ticker, "name": name,
                    "action": "SELL", "shares": shares_held,
                    "price": current_price, "total": proceeds,
                    "fee": round(fee, 2), "profit": round(profit, 2),
                    "ma5": ma5, "ma20": ma20,
                })

        # 日次評価額
        stock_val = 0.0
        for t, pos in positions.items():
            ser = price_data.get(t)
            if ser is not None:
                h = ser[ser.index <= dt]
                if len(h) > 0:
                    stock_val += pos["shares"] * float(h.iloc[-1])
        daily_vals.append({
            "date": day_str,
            "total_value": round(cash + stock_val, 2),
            "cash": round(cash, 2),
            "stock_value": round(stock_val, 2),
        })

    # 未決済ポジションの評価（参考）
    open_stock_val = 0.0
    for t, pos in positions.items():
        ser = price_data.get(t)
        if ser is not None and len(ser) > 0:
            open_stock_val += pos["shares"] * float(ser.iloc[-1])

    final_total   = cash + open_stock_val
    total_return  = (final_total - INITIAL_CASH) / INITIAL_CASH * 100

    sell_trades   = [t for t in trades if t["action"] == "SELL"]
    win_trades    = [t for t in sell_trades if t["profit"] and t["profit"] > 0]
    win_rate      = len(win_trades) / len(sell_trades) * 100 if sell_trades else 0.0
    total_profit  = sum(t["profit"] for t in sell_trades if t["profit"])

    # 最大ドローダウン
    peak, max_dd = float(INITIAL_CASH), 0.0
    for d in daily_vals:
        v = d["total_value"]
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # ── 結果表示 ──────────────────────────────────────────
    sep = "─" * 42
    print(sep)
    print(f"  バックテスト結果サマリー")
    print(sep)
    print(f"  初期資金          : ¥{INITIAL_CASH:>10,.0f}")
    print(f"  最終資産(未決含む): ¥{final_total:>10,.0f}")
    print(f"  総リターン        :  {total_return:>+9.2f}%")
    print(f"  実現損益合計      : ¥{total_profit:>+10,.0f}")
    print(f"  総取引手数料      : ¥{total_fees:>10,.0f}")
    print(f"  総取引回数        :  {len(trades):>9}回")
    print(f"  決済回数          :  {len(sell_trades):>9}回")
    print(f"  勝率              :  {win_rate:>9.1f}%")
    print(f"  最大ドローダウン  :  {max_dd:>9.2f}%")
    print(sep)

    print("\n  銘柄別サマリー")
    print(f"  {'銘柄':<10} {'取引':>4}  {'損益':>12}  {'手数料':>8}  {'勝/負'}")
    stock_summaries = []
    for ticker, (name, market) in STOCKS.items():
        st_trades = [t for t in trades     if t["ticker"] == ticker]
        st_sells  = [t for t in st_trades  if t["action"] == "SELL"]
        st_wins   = [t for t in st_sells   if t["profit"] and t["profit"] > 0]
        st_profit = sum(t["profit"] for t in st_sells if t["profit"])
        st_fees   = sum(t["fee"]    for t in st_trades)
        st_wr     = len(st_wins) / len(st_sells) * 100 if st_sells else 0
        win_loss  = f"{len(st_wins)}勝{len(st_sells)-len(st_wins)}敗"
        print(f"  {name:<10} {len(st_trades):>4}回  ¥{st_profit:>+10,.0f}  ¥{st_fees:>6,.0f}  {win_loss}")
        stock_summaries.append({
            "ticker": ticker, "name": name, "market": market,
            "trades": len(st_trades), "sells": len(st_sells),
            "wins": len(st_wins), "profit": round(st_profit, 2),
            "fees": round(st_fees, 2), "win_rate": round(st_wr, 1),
        })
    print(sep)

    # CSV保存
    if trades:
        pd.DataFrame(trades).to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
        print(f"\n取引履歴 → {OUTPUT_CSV}")

    summary = {
        "period": BACKTEST_PERIOD,
        "start_date": str(all_dates[0].date()),
        "end_date":   str(all_dates[-1].date()),
        "initial_cash":   INITIAL_CASH,
        "final_total":    round(final_total, 2),
        "total_return":   round(total_return, 2),
        "total_profit":   round(total_profit, 2),
        "total_fees":     round(total_fees, 2),
        "total_trades":   len(trades),
        "sell_trades":    len(sell_trades),
        "win_trades":     len(win_trades),
        "win_rate":       round(win_rate, 1),
        "max_drawdown":   round(max_dd, 2),
    }
    pd.DataFrame([summary]).to_csv(SUMMARY_CSV, index=False, encoding="utf-8-sig")
    print(f"サマリー      → {SUMMARY_CSV}")

    return trades, daily_vals, summary


if __name__ == "__main__":
    run_backtest()
