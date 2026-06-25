"""
Supabase接続テスト
既存app.pyとは別ファイルとして配置して動作確認に使う
"""

import streamlit as st

st.title("🔌 Supabase接続テスト")

# ----------------------------------------------------------
# 1. supabase-py クライアントの初期化
# ----------------------------------------------------------
try:
    from supabase import create_client, Client

    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    supabase: Client = create_client(url, key)
    st.success("✅ Supabaseクライアント初期化OK")

except ImportError:
    st.error("❌ supabase-py がインストールされていません。requirements.txtに追加してください。")
    st.code("supabase==2.3.4", language="text")
    st.stop()

except KeyError as e:
    st.error(f"❌ secrets.tomlの設定が見つかりません: {e}")
    st.stop()

except Exception as e:
    st.error(f"❌ 接続エラー: {e}")
    st.stop()

# ----------------------------------------------------------
# 2. マスタデータ取得テスト
# ----------------------------------------------------------
st.subheader("📊 マスタデータ確認")

tests = [
    ("ross308_standard",       "Ross308標準データ"),
    ("ross308_carcass_yield",  "枝肉歩留りデータ"),
    ("ross_comfort_temp",      "快適温度データ"),
    ("feed_correction_factors","暑熱補正係数"),
    ("farms",                  "農場マスタ"),
    ("houses",                 "鶏舎マスタ"),
    ("feed_brands",            "飼料銘柄マスタ"),
    ("workers",                "担当者マスタ"),
    ("flocks",                 "飼養ロット"),
    ("daily_records",          "日次記録"),
]

for table, label in tests:
    try:
        res = supabase.table(table).select("*", count="exact").limit(1).execute()
        count = res.count if res.count is not None else len(res.data)
        st.write(f"✅ `{table}`（{label}）: **{count}件**")
    except Exception as e:
        st.write(f"❌ `{table}`（{label}）: エラー → {e}")

# ----------------------------------------------------------
# 3. Ross308データ簡易表示
# ----------------------------------------------------------
st.subheader("📈 Ross308 As-Hatched（1〜7日齢）")

try:
    res = supabase.table("ross308_standard") \
        .select("day, weight_g, daily_intake_g, fcr") \
        .eq("sex", "as_hatched") \
        .lte("day", 7) \
        .order("day") \
        .execute()

    import pandas as pd
    df = pd.DataFrame(res.data)
    df.columns = ["日齢", "体重(g)", "採食量(g/羽)", "FCR"]
    st.dataframe(df, use_container_width=True)

except Exception as e:
    st.error(f"データ取得エラー: {e}")
