import streamlit as st
import feedparser
import re
import time
import urllib.request
import json
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import os
from supabase import create_client, Client

# ==========================================
# 1. 初期設定とスタイル (モバイル特化UI)
# ==========================================
st.set_page_config(page_title="Market Signal JP", layout="centered", page_icon="🗼")

# スマホの縦長画面でカード型に見せるためのカスタムCSS
st.markdown("""
    <style>
    /* メインコンテナの余白調整（スマホ向けに広く使う） */
    .block-container {
        padding-top: 2rem;
        padding-bottom: 2rem;
        max-width: 600px; /* モバイルファーストな幅設定 */
    }
    .terminal-text {
        font-family: 'Courier New', Courier, monospace;
        color: #00FF00;
        background-color: #111;
        padding: 15px;
        border-radius: 8px;
        font-size: 0.85rem;
        white-space: pre-wrap;
    }
    .metric-card {
        background-color: #1E1E1E;
        padding: 15px;
        margin-bottom: 10px;
        border-radius: 12px;
        border-left: 5px solid #00FF00;
        box-shadow: 0 4px 6px rgba(0,0,0,0.3);
    }
    .metric-card h3 {
        margin-top: 0;
        margin-bottom: 5px;
        font-size: 1.2rem;
    }
    .metric-card p {
        margin: 0;
        color: #CCC;
        font-size: 0.9rem;
    }
    .badge {
        display: inline-block;
        background-color: #333;
        color: #00FF00;
        padding: 2px 8px;
        border-radius: 10px;
        font-size: 0.75rem;
        margin-right: 5px;
        margin-top: 5px;
    }
    .disclaimer {
        font-size: 0.75rem;
        color: #888;
        border-top: 1px solid #333;
        padding-top: 15px;
        margin-top: 30px;
    }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 2. データベース接続 (Supabase)
# ==========================================
# st.secretsから認証情報を取得。設定されていない場合はローカルセッションで代用する親切設計
USE_SUPABASE = False
try:
    url: str = st.secrets["SUPABASE_URL"]
    key: str = st.secrets["SUPABASE_KEY"]
    supabase: Client = create_client(url, key)
    USE_SUPABASE = True
except Exception:
    if "local_db" not in st.session_state:
        st.session_state.local_db = []

def save_prediction(player_name, tickers):
    date_str = datetime.today().strftime('%Y-%m-%d')
    record = {
        "player_name": player_name,
        "prediction_date": date_str,
        "tickers": json.dumps(tickers) # リストをJSON文字列で保存
    }
    if USE_SUPABASE:
        try:
            supabase.table("predictions").insert(record).execute()
        except Exception as e:
            st.error(f"DB保存エラー: {e}")
    else:
        st.session_state.local_db.append(record)

def get_past_predictions():
    if USE_SUPABASE:
        try:
            response = supabase.table("predictions").select("*").order("created_at", desc=True).execute()
            return response.data
        except Exception as e:
            st.error(f"DB読込エラー: {e}")
            return []
    else:
        return st.session_state.local_db

# ==========================================
# 3. 日本株プールと日本語NLPロジック
# ==========================================
# .Tは日本のYahoo Financeのティッカーシンボル
STOCK_POOL = {
    "7203.T": {"name": "トヨタ自動車", "keywords": ["トヨタ", "自動車", "車", "EV", "電気自動車", "ハイブリッド", "モビリティ", "円安"]},
    "7974.T": {"name": "任天堂", "keywords": ["任天堂", "ゲーム", "スイッチ", "マリオ", "ポケモン", "エンタメ", "コンソール", "USJ"]},
    "6758.T": {"name": "ソニーG", "keywords": ["ソニー", "プレイステーション", "PS5", "映画", "音楽", "イメージセンサー", "半導体"]},
    "9984.T": {"name": "ソフトバンクG", "keywords": ["ソフトバンク", "投資", "AI", "アーム", "Arm", "ビジョンファンド", "孫正義"]},
    "8035.T": {"name": "東京エレクトロン", "keywords": ["エレクトロン", "半導体", "製造装置", "チップ", "TSMC", "AI半導体"]},
    "8306.T": {"name": "三菱UFJ", "keywords": ["三菱UFJ", "銀行", "金融", "金利", "利上げ", "日銀", "メガバンク"]},
    "9983.T": {"name": "ファーストリテイリング", "keywords": ["ファストリ", "ユニクロ", "アパレル", "服", "小売り", "インバウンド", "GU"]},
    "8058.T": {"name": "三菱商事", "keywords": ["三菱商事", "商社", "資源", "エネルギー", "バフェット", "総合商社"]},
    "4502.T": {"name": "武田薬品", "keywords": ["武田薬品", "薬", "医薬品", "ワクチン", "ヘルスケア", "バイオ", "製薬"]},
    "4661.T": {"name": "オリエンタルランド", "keywords": ["オリエンタルランド", "ディズニー", "テーマパーク", "観光", "インバウンド", "レジャー"]}
}

DEFAULT_FEEDS = [
    "https://news.yahoo.co.jp/rss/topics/business.xml", # Yahoo!ニュース 経済
    "https://assets.wor.jp/rss/rdf/nikkei/business.rdf", # 日経新聞風RSS (非公式まとめ等)
    "", "", ""
]

def fetch_text_from_rss(url):
    """RSSフィードからタイトルと要約を取得"""
    if not url: return ""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        response = urllib.request.urlopen(req, timeout=5)
        feed = feedparser.parse(response.read())
        text_data = []
        for entry in feed.entries[:10]: # 軽量化のため10件
            title = entry.get('title', '')
            summary = re.sub(r'<[^>]+>', '', entry.get('summary', ''))
            text_data.append(f"{title} {summary}")
        return " ".join(text_data)
    except Exception:
        return ""

def analyze_japanese_text(combined_text):
    """形態素解析を使わず、in演算子（部分一致）で高速カウント"""
    scores = {}
    for ticker, info in STOCK_POOL.items():
        score = 0
        matched_words = []
        for kw in info["keywords"]:
            count = combined_text.count(kw)
            if count > 0:
                score += count
                matched_words.append(kw)
        
        if score > 0:
            scores[ticker] = {
                "name": info["name"],
                "score": score,
                "matched": matched_words
            }
            
    # スコア順にソートし、上位5件を抽出
    sorted_tickers = sorted(scores.items(), key=lambda x: x[1]['score'], reverse=True)[:5]
    return sorted_tickers

def check_performance(tickers, start_date_str):
    """指定日(予測日)から現在までのパフォーマンスをyfinanceで計算"""
    try:
        # yfinance用に日付をパース
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_date = datetime.today()
        
        # もし予測日が今日なら、データがまだ無いので前日と比較（擬似的に見せる）
        if (end_date - start_date).days < 1:
            start_date = end_date - timedelta(days=2)
            
        total_perf = 0.0
        results = []
        
        for ticker in tickers:
            hist = yf.Ticker(ticker).history(start=start_date.strftime('%Y-%m-%d'))
            if len(hist) >= 1:
                open_price = hist['Open'].iloc[0]
                close_price = hist['Close'].iloc[-1]
                pct_change = ((close_price - open_price) / open_price) * 100
            else:
                pct_change = 0.0
            
            results.append({"ticker": ticker, "perf": pct_change})
            total_perf += pct_change
            
        avg_perf = total_perf / len(tickers) if tickers else 0.0
        return results, avg_perf
    except Exception:
        return [], 0.0

# ==========================================
# 4. メインUI (タブ構成)
# ==========================================
st.title("🗼 Market Signal JP")
if not USE_SUPABASE:
    st.warning("⚠️ DB未設定: 現在のデータは一時保存です。ブラウザを閉じると消去されます。")

tab1, tab2 = st.tabs(["📡 今日の予測ミッション", "🏆 過去の答え合わせ"])

# -------------------------------
# TAB 1: 予測実行画面
# -------------------------------
with tab1:
    st.markdown("**ミッション**: 最新のニュースソースを入力し、AIに日本株ポートフォリオを構築させよ。")
    
    # 縦積みレイアウト（スマホ特化）
    player_name = st.text_input("👤 エージェント名", "Player 1")
    
    st.markdown("🔗 **情報源 (RSS / YouTube RSS)**")
    feed_urls = []
    for i in range(3): # モバイル向けにデフォルト入力枠を3つに削減（任意で5まで増やせます）
        default_val = DEFAULT_FEEDS[i] if i < len(DEFAULT_FEEDS) else ""
        feed_urls.append(st.text_input(f"Source {i+1}", value=default_val, key=f"url_{i}", placeholder="https://..."))

    if st.button("🚀 データを解析して予測する", type="primary", use_container_width=True):
        terminal_output = st.empty()
        
        terminal_text = "> [SYS] システムを起動中...\n"
        terminal_output.markdown(f'<div class="terminal-text">{terminal_text}</div>', unsafe_allow_html=True)
        time.sleep(0.5)
        
        combined_text = ""
        for idx, url in enumerate(feed_urls):
            if url:
                terminal_text += f"> [NET] 情報源{idx+1}からテキスト抽出中...\n"
                terminal_output.markdown(f'<div class="terminal-text">{terminal_text}</div>', unsafe_allow_html=True)
                combined_text += fetch_text_from_rss(url) + " "
                
        terminal_text += "> [NLP] 日本語キーワード・マッチング実行中...\n"
        terminal_output.markdown(f'<div class="terminal-text">{terminal_text}</div>', unsafe_allow_html=True)
        time.sleep(1)
        
        top_tickers = analyze_japanese_text(combined_text)
        
        terminal_text += "> [SYS] 抽出完了。ポートフォリオを保存します。\n"
        terminal_output.markdown(f'<div class="terminal-text">{terminal_text}</div>', unsafe_allow_html=True)
        
        if top_tickers:
            # DBに保存するティッカーリストの作成
            saved_tickers = [t[0] for t in top_tickers]
            save_prediction(player_name, saved_tickers)
            
            st.success("✅ 今日の予測データをクラウドに保存しました！")
            st.markdown("### 抽出された仮想ポートフォリオ")
            
            # モバイル向けカードUI（縦積み）
            for ticker, data in top_tickers:
                badges = "".join([f"<span class='badge'>{kw}</span>" for kw in data['matched']])
                st.markdown(f"""
                <div class="metric-card">
                    <h3>{data['name']} ({ticker.replace('.T', '')})</h3>
                    <p>スコア: <b>{data['score']}</b></p>
                    <div style="margin-top:5px;">{badges}</div>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.error("関連キーワードが見つかりませんでした。別のURLを試してください。")

# -------------------------------
# TAB 2: 答え合わせ画面
# -------------------------------
with tab2:
    st.markdown("**結果発表**: 過去に構築したポートフォリオの、予測日から今日までの実際の株価変動（%）を確認します。")
    
    if st.button("🔄 過去のデータを読み込んで答え合わせ", use_container_width=True):
        records = get_past_predictions()
        
        if not records:
            st.info("過去の予測データがありません。")
        else:
            for rec in records:
                p_name = rec.get("player_name", "Unknown")
                p_date = rec.get("prediction_date", "")
                
                # JSON文字列をリストに復元
                tickers_list = rec.get("tickers", "[]")
                if isinstance(tickers_list, str):
                    tickers_list = json.loads(tickers_list)
                
                # yfinanceで計算
                with st.spinner(f"{p_date} のデータを計算中..."):
                    results, avg_perf = check_performance(tickers_list, p_date)
                
                # スコアによって色とアイコンを変える
                color = "#00FF00" if avg_perf >= 0 else "#FF4444"
                icon = "📈" if avg_perf >= 0 else "📉"
                
                # モバイル向けカードUIで結果表示
                st.markdown(f"""
                <div class="metric-card" style="border-left-color: {color};">
                    <h3>{icon} {p_name} の予測</h3>
                    <p>予測日: {p_date}</p>
                    <h2 style="color:{color}; margin: 5px 0;">{avg_perf:+.2f}%</h2>
                    <p style="font-size:0.8rem;">銘柄: {", ".join([t.replace('.T', '') for t in tickers_list])}</p>
                </div>
                """, unsafe_allow_html=True)

st.markdown("""
<div class="disclaimer">
<b>【免責事項・注意事項】</b><br>
本アプリはプログラミング学習用のシミュレーションゲームです。表示される銘柄やパフォーマンスは過去データを用いたゲーム上の演出であり、実際の投資助言や売買を推奨するものではありません。金融商品取引法における投資助言業務には該当しません。
</div>
""", unsafe_allow_html=True)
