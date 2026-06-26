"""
Supabase接続テスト（v3: lot_numbers対応版）
"""

import streamlit as st
import pandas as pd

st.title("🔌 Supabase接続テスト")

try:
    from supabase import create_client, Client
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    supabase: Client = create_client(url, key)
    st.success("✅ Supabaseクライアント初期化OK")
except ImportError:
    st.error("❌ supabase-py がインストールされていません")
    st.stop()
except KeyError as e:
    st.error(f"❌ secrets.tomlの設定が見つかりません: {e}")
    st.stop()
except Exception as e:
    st.error(f"❌ 接続エラー: {e}")
    st.stop()

st.subheader("📊 テーブル確認")

tests = [
    ("ross308_standard",        "Ross308標準データ"),
    ("ross308_carcass_yield",   "枝肉歩留りデータ"),
    ("ross_comfort_temp",       "快適温度データ"),
    ("feed_correction_factors", "暑熱補正係数"),
    ("farms",                   "農場マスタ"),
    ("houses",                  "鶏舎マスタ"),
    ("feed_brands",             "飼料銘柄マスタ"),
    ("workers",                 "担当者マスタ"),
    ("lot_numbers",             "ロット番号マスタ"),
    ("flock_houses",            "鶏舎割当"),
    ("daily_records",           "日次記録"),
]

for table, label in tests:
    try:
        res = supabase.table(table).select("*", count="exact").limit(1).execute()
        count = res.count if res.count is not None else len(res.data)
        st.write(f"✅ `{table}`（{label}）: **{count}件**")
    except Exception as e:
        st.write(f"❌ `{table}`（{label}）: エラー → {e}")

st.subheader("📈 Ross308 As-Hatched（1〜7日齢）")
try:
    res = supabase.table("ross308_standard") \
        .select("day, weight_g, daily_intake_g, fcr") \
        .eq("sex", "as_hatched") \
        .lte("day", 7) \
        .order("day") \
        .execute()
    df = pd.DataFrame(res.data)
    df.columns = ["日齢", "体重(g)", "採食量(g/羽)", "FCR"]
    st.dataframe(df, use_container_width=True)
except Exception as e:
    st.error(f"データ取得エラー: {e}")
