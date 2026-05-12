"""
株式自動売買 ダッシュボード
起動: streamlit run dashboard.py
"""

import streamlit as st
import sqlite3
import pandas as pd
import plotly.graph_objects as go
import yfinance as yf
from datetime import date
import os

DB_PATH    = os.path.join(os.path.dirname(__file__), "trading.db")
BT_CSV     = os.path.join(os.path.dirname(__file__), "backtest_results.csv")
BT_SUM_CSV = os.path.join(os.path.dirname(__file__), "backtest_summary.csv")
INITIAL_CASH = 100_000

st.set_page_config(page_title="自動売買ダッシュボード", page_icon="📈", layout="wide")
st.title("📈 自動売買シミュレーター（楽天証券モデル）")

if not os.path.exists(DB_PATH):
    st.warning("まだデータがありません。trading_bot.py を先に実行してください。")
    st.stop()

conn = sqlite3.connect(DB_PATH)

tab_live, tab_bt = st.tabs(["現在の状況", "バックテスト（楽天証券）"])

# ══════════════════════════════════════════════════════════════
# TAB 1 : 現在の状況
# ══════════════════════════════════════════════════════════════
with tab_live:
    # 現金残高
    cash_row = conn.execute("SELECT amount FROM cash WHERE id=1").fetchone()
    cash = cash_row[0] if cash_row else INITIAL_CASH

    # 保有ポジション
    positions = conn.execute("SELECT ticker, shares, avg_price FROM portfolio").fetchall()
    stock_value  = 0.0
    position_data = []
    for ticker, shares, avg_price in positions:
        try:
            df = yf.download(ticker, period="5d", auto_adjust=True, progress=False)
            current_price = float(df["Close"].squeeze().iloc[-1]) if not df.empty else avg_price
        except Exception:
            current_price = avg_price
        value  = shares * current_price
        pl     = value - shares * avg_price
        pl_pct = (pl / (shares * avg_price) * 100) if avg_price > 0 else 0
        stock_value += value
        position_data.append({
            "銘柄": ticker, "株数": round(shares, 4),
            "取得価格": round(avg_price, 2), "現在値": round(current_price, 2),
            "評価額": round(value, 0), "損益": round(pl, 0), "損益率": round(pl_pct, 2),
        })

    total_value  = cash + stock_value
    total_pl     = total_value - INITIAL_CASH
    total_pl_pct = total_pl / INITIAL_CASH * 100

    # KPI カード
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("総資産",     f"¥{total_value:,.0f}", f"{total_pl:+,.0f} ({total_pl_pct:+.2f}%)")
    c2.metric("現金",       f"¥{cash:,.0f}")
    c3.metric("株式評価額", f"¥{stock_value:,.0f}")
    c4.metric("元金",       f"¥{INITIAL_CASH:,.0f}")

    st.divider()

    # 資産推移グラフ
    summary_df = pd.read_sql("SELECT * FROM daily_summary ORDER BY date", conn)
    if not summary_df.empty:
        st.subheader("資産推移")
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=summary_df["date"], y=summary_df["total_value"],
            name="総資産", line=dict(color="#00b4d8", width=2),
            fill="tozeroy", fillcolor="rgba(0,180,216,0.1)"
        ))
        fig.add_hline(y=INITIAL_CASH, line_dash="dash", line_color="gray",
                      annotation_text="元金 ¥100,000")
        fig.update_layout(height=300, margin=dict(l=0, r=0, t=10, b=0),
                          yaxis_tickprefix="¥", yaxis_tickformat=",")
        st.plotly_chart(fig, use_container_width=True)
        st.divider()

    # 保有ポジション
    st.subheader("保有ポジション")
    if position_data:
        df_pos = pd.DataFrame(position_data)
        def _color_pl(val):
            return "color: #e63946" if val < 0 else "color: #2dc653" if val > 0 else ""
        st.dataframe(
            df_pos.style.applymap(_color_pl, subset=["損益", "損益率"]).format({
                "取得価格": "{:.2f}", "現在値": "{:.2f}",
                "評価額": "¥{:,.0f}", "損益": "¥{:+,.0f}", "損益率": "{:+.2f}%"
            }),
            use_container_width=True, hide_index=True
        )
    else:
        st.info("現在保有しているポジションはありません。")

    st.divider()

    # 取引履歴
    st.subheader("取引履歴")
    trades_df = pd.read_sql(
        "SELECT date, name, action, shares, price, total, fee, reason FROM trades ORDER BY id DESC LIMIT 50",
        conn
    )
    if not trades_df.empty:
        trades_df.columns = ["日付", "銘柄", "売買", "株数", "価格", "合計金額", "手数料", "理由"]
        trades_df["株数"]   = trades_df["株数"].round(4)
        trades_df["価格"]   = trades_df["価格"].round(2)
        trades_df["合計金額"] = trades_df["合計金額"].round(0)
        trades_df["手数料"]  = trades_df["手数料"].fillna(0).round(0)

        def _color_action(val):
            if val == "BUY":  return "color: #2dc653; font-weight: bold"
            if val == "SELL": return "color: #e63946; font-weight: bold"
            return ""

        st.dataframe(
            trades_df.style.applymap(_color_action, subset=["売買"]).format(
                {"合計金額": "¥{:,.0f}", "手数料": "¥{:,.0f}"}
            ),
            use_container_width=True, hide_index=True
        )
    else:
        st.info("まだ取引履歴がありません。")

# ══════════════════════════════════════════════════════════════
# TAB 2 : バックテスト
# ══════════════════════════════════════════════════════════════
with tab_bt:
    st.subheader("バックテスト結果（楽天証券 超割コース 手数料込み）")

    if not os.path.exists(BT_SUM_CSV):
        st.info("バックテストをまだ実行していません。ターミナルで以下を実行してください：\n\n```\npython backtest.py\n```")
    else:
        # サマリー KPI
        summary = pd.read_csv(BT_SUM_CSV)
        s = summary.iloc[0]

        st.caption(f"検証期間: {s['start_date']} 〜 {s['end_date']}  |  戦略: MA5/MA20クロス")

        k1, k2, k3, k4, k5 = st.columns(5)
        ret_color = "normal" if float(s["total_return"]) >= 0 else "inverse"
        k1.metric("総リターン",        f"{float(s['total_return']):+.2f}%")
        k2.metric("最終資産",          f"¥{float(s['final_total']):,.0f}",
                                       f"{float(s['final_total']) - float(s['initial_cash']):+,.0f}")
        k3.metric("勝率",              f"{float(s['win_rate']):.1f}%",
                                       f"{int(s['win_trades'])}勝 / {int(s['sell_trades'])}決済")
        k4.metric("最大ドローダウン",  f"{float(s['max_drawdown']):.2f}%")
        k5.metric("総手数料",          f"¥{float(s['total_fees']):,.0f}")

        st.divider()

        # 取引履歴テーブル
        if os.path.exists(BT_CSV):
            bt_df = pd.read_csv(BT_CSV)

            # 資産推移グラフを再構築
            st.subheader("銘柄別 損益サマリー")
            sell_df = bt_df[bt_df["action"] == "SELL"].copy()
            if not sell_df.empty:
                grp = sell_df.groupby("name").agg(
                    取引回数=("profit", "count"),
                    実現損益=("profit", "sum"),
                    手数料合計=("fee", "sum"),
                ).reset_index().rename(columns={"name": "銘柄"})
                grp["実現損益"]  = grp["実現損益"].round(0)
                grp["手数料合計"] = grp["手数料合計"].round(0)

                def _color_profit(val):
                    return "color: #e63946" if val < 0 else "color: #2dc653" if val > 0 else ""

                st.dataframe(
                    grp.style.applymap(_color_profit, subset=["実現損益"]).format(
                        {"実現損益": "¥{:+,.0f}", "手数料合計": "¥{:,.0f}"}
                    ),
                    use_container_width=True, hide_index=True
                )

            st.divider()

            # 全取引一覧
            st.subheader("全取引一覧")
            show_df = bt_df[["date","name","action","shares","price","total","fee","profit"]].copy()
            show_df.columns = ["日付","銘柄","売買","株数","価格","合計金額","手数料","損益"]
            show_df["株数"]   = show_df["株数"].round(4)
            show_df["価格"]   = show_df["価格"].round(2)

            def _ca(val):
                if val == "BUY":  return "color: #2dc653; font-weight: bold"
                if val == "SELL": return "color: #e63946; font-weight: bold"
                return ""
            def _cp(val):
                if pd.isna(val): return ""
                return "color: #e63946" if val < 0 else "color: #2dc653" if val > 0 else ""

            st.dataframe(
                show_df.style
                    .applymap(_ca, subset=["売買"])
                    .applymap(_cp, subset=["損益"])
                    .format({"合計金額": "¥{:,.0f}", "手数料": "¥{:,.0f}",
                             "損益": lambda v: f"¥{v:+,.0f}" if pd.notna(v) else "-"}),
                use_container_width=True, hide_index=True
            )

conn.close()
st.caption(f"最終更新: {date.today().isoformat()} | データ: Yahoo Finance (yfinance) | 手数料: 楽天証券 超割コース")
