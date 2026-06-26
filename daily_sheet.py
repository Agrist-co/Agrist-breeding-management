"""
ブロイラー飼養管理システム - Excel風日次入力シート（PC専用）
日齢0〜出荷日齢の全行を一覧表示・直接入力
"""

import streamlit as st
import pandas as pd
import math
from datetime import date, timedelta
from supabase import create_client, Client

st.set_page_config(page_title="日次入力シート", layout="wide")

st.markdown("""
<style>
h1 { font-size: 1.1rem !important; margin-bottom: 0.2rem !important; }
.block-container { padding-top: 0.5rem !important; padding-bottom: 0.3rem !important; }
.stSelectbox label, .stNumberInput label { font-size: 0.75rem !important; }
/* ヘッダー情報ボックス */
.header-box {
    background: #f0f4f8;
    border: 1px solid #c8d6e5;
    border-radius: 6px;
    padding: 6px 12px;
    margin-bottom: 6px;
    font-size: 0.82rem;
}
.header-box b { color: #1a5276; }
.header-val { color: #222; font-weight: 600; }
</style>
""", unsafe_allow_html=True)

@st.cache_resource
def get_supabase() -> Client:
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    return create_client(url, key)

supabase = get_supabase()

def fetch(table, order=None):
    q = supabase.table(table).select("*")
    if order:
        q = q.order(order)
    return q.execute().data

# ----------------------------------------------------------
# マスタ取得
# ----------------------------------------------------------
farms        = fetch("farms",        "farm_id")
houses       = fetch("houses",       "house_id")
lot_numbers  = fetch("lot_numbers",  "lot_number_id")
flock_houses = fetch("flock_houses", "flock_house_id")
feed_brands  = fetch("feed_brands",  "feed_brand_id")
workers      = fetch("workers",      "worker_id")
ross308      = fetch("ross308_standard", "day")

farm_map    = {f["farm_id"]:  f["farm_name"]  for f in farms}
farm_opts   = {f["farm_name"]: f["farm_id"]   for f in farms}
house_map   = {h["house_id"]: h["house_name"] for h in houses}
ln_map      = {ln["lot_number_id"]: ln["lot_number"] for ln in lot_numbers}
brand_map   = {b["feed_brand_id"]: b["brand_name"]   for b in feed_brands}
brand_opts  = {b["brand_name"]: b["feed_brand_id"]   for b in feed_brands}
worker_map  = {w["worker_id"]: w["worker_name"]       for w in workers}
worker_opts = {w["worker_name"]: w["worker_id"]       for w in workers}
ross_dict   = {(r["sex"], r["day"]): r for r in ross308}

def get_ross308(age):
    return ross_dict.get(("as_hatched", max(0, min(int(age), 56))), {})

# ----------------------------------------------------------
# 対象選択
# ----------------------------------------------------------
st.title("📋 日次入力シート")

c1, c2, c3, c4 = st.columns(4)
with c1:
    sel_farm    = st.selectbox("農場", list(farm_opts.keys()), key="s_farm")
    sel_farm_id = farm_opts[sel_farm]
with c2:
    farm_lns = [ln for ln in lot_numbers if ln["farm_id"] == sel_farm_id and ln["is_active"]]
    if not farm_lns:
        st.warning("ロット番号を登録してください")
        st.stop()
    sel_ln_id = st.selectbox("ロット番号",
        [ln["lot_number_id"] for ln in farm_lns],
        format_func=lambda x: ln_map[x], key="s_ln")
with c3:
    lot_fhs = [fh for fh in flock_houses if fh["lot_number_id"] == sel_ln_id]
    if not lot_fhs:
        st.warning("鶏舎割当を登録してください")
        st.stop()
    sel_fh_id = st.selectbox("鶏舎",
        [fh["flock_house_id"] for fh in lot_fhs],
        format_func=lambda x: house_map.get(
            next(fh["house_id"] for fh in lot_fhs if fh["flock_house_id"] == x), ""),
        key="s_fh")
with c4:
    # 入雛日：選択した鶏舎に紐づく入雛日を表示（同一鶏舎で複数回転ある場合も対応）
    sel_fh_tmp    = next(fh for fh in lot_fhs if fh["flock_house_id"] == sel_fh_id)
    default_chick = sel_fh_tmp.get("chick_in_date", "")
    # 同一ロット・同一鶏舎の複数入雛日がある場合に備えてリスト化
    chick_dates = sorted(set(
        fh["chick_in_date"] for fh in lot_fhs
        if fh["flock_house_id"] == sel_fh_id
        or (fh["house_id"] == sel_fh_tmp["house_id"] and fh["lot_number_id"] == sel_ln_id)
    ))
    sel_chick_date = st.selectbox("入雛日",
        chick_dates,
        index=chick_dates.index(default_chick) if default_chick in chick_dates else 0,
        key="s_chick_date")
    # 入雛日で絞り込んだflock_houseを確定
    sel_fh_id = next(
        (fh["flock_house_id"] for fh in lot_fhs
         if fh["flock_house_id"] == sel_fh_id
         and fh["chick_in_date"] == sel_chick_date),
        sel_fh_id
    )

sel_fh    = next(fh for fh in lot_fhs if fh["flock_house_id"] == sel_fh_id)
sel_house = next((h for h in houses if h["house_id"] == sel_fh["house_id"]), {})
sel_ln    = next(ln for ln in lot_numbers if ln["lot_number_id"] == sel_ln_id)

# ----------------------------------------------------------
# 上部ヘッダー情報（DB自動取得）
# ----------------------------------------------------------
chick_in_date   = date.fromisoformat(sel_fh["chick_in_date"])
chick_in_count  = sel_fh["chick_in_count"] or 0
spare_count     = sel_fh["spare_count"]    or 0
planned_age     = sel_fh["planned_shipment_age_days"] or 56
today_age       = (date.today() - chick_in_date).days
house_coef      = float(sel_house.get("feed_transfer_coef") or 0)
tank_capacity   = sel_house.get("tank_capacity") or "-"
tank_number     = sel_house.get("tank_number")   or "-"

# 残存羽数計算
all_recs = supabase.table("daily_records") \
    .select("mortality_count,culling_count") \
    .eq("flock_house_id", sel_fh_id).execute().data
total_mort = sum(r["mortality_count"] or 0 for r in all_recs)
total_cull = sum(r["culling_count"]   or 0 for r in all_recs)
remaining  = chick_in_count + spare_count - total_mort - total_cull

st.markdown(f"""
<div class="header-box">
  <span><b>農場</b>: <span class="header-val">{sel_farm}</span></span>&nbsp;&nbsp;
  <span><b>ロット</b>: <span class="header-val">{sel_ln['lot_number']}</span></span>&nbsp;&nbsp;
  <span><b>鶏舎</b>: <span class="header-val">{house_map.get(sel_fh['house_id'],'')}</span></span>&nbsp;&nbsp;
  <span><b>タンクNo</b>: <span class="header-val">{tank_number}</span></span>&nbsp;&nbsp;
  <span><b>タンク容量</b>: <span class="header-val">{tank_capacity} kg</span></span>&nbsp;&nbsp;
  <span><b>搬送係数</b>: <span class="header-val">{house_coef} kg/min</span></span>
  <br>
  <span><b>入雛日</b>: <span class="header-val">{chick_in_date}</span></span>&nbsp;&nbsp;
  <span><b>入雛羽数</b>: <span class="header-val">{chick_in_count:,} 羽</span></span>&nbsp;&nbsp;
  <span><b>スペア</b>: <span class="header-val">{spare_count:,} 羽</span></span>&nbsp;&nbsp;
  <span><b>出荷日齢</b>: <span class="header-val">{planned_age} 日</span></span>&nbsp;&nbsp;
  <span><b>現在日齢</b>: <span class="header-val">{today_age} 日</span></span>&nbsp;&nbsp;
  <span><b>残存羽数</b>: <span class="header-val">{remaining:,} 羽</span></span>
</div>
""", unsafe_allow_html=True)

# ----------------------------------------------------------
# 既存の日次記録を取得
# ----------------------------------------------------------
existing_recs = supabase.table("daily_records") \
    .select("*") \
    .eq("flock_house_id", sel_fh_id) \
    .order("record_date").execute().data

rec_by_date = {r["record_date"]: r for r in existing_recs}

# ----------------------------------------------------------
# 全日齢分のDataFrameを構築（日齢0〜出荷日齢）
# ----------------------------------------------------------
brand_names  = [""] + list(brand_opts.keys())
worker_names = [""] + list(worker_opts.keys())

rows = []
cum_mort = 0
cum_cull = 0

for age in range(0, planned_age + 1):
    rec_date = chick_in_date + timedelta(days=age)
    rec      = rec_by_date.get(str(rec_date), {})
    ross     = get_ross308(age)

    mort     = rec.get("mortality_count") or 0
    cull     = rec.get("culling_count")   or 0
    cum_mort += mort
    cum_cull += cull
    total_loss   = cum_mort + cum_cull
    remain_count = chick_in_count + spare_count - total_loss

    # 採食量計算
    duration = rec.get("feed_duration_min")
    brand_id = rec.get("feed_brand_id")
    brand_nm = brand_map.get(brand_id, "") if brand_id else ""
    brand_obj = next((b for b in feed_brands if b["feed_brand_id"] == brand_id), {}) if brand_id else {}
    ratio    = float(brand_obj.get("transfer_coef_ratio") or 1.0)
    if duration and house_coef > 0:
        intake_kg = round(float(duration) * house_coef * ratio, 2)
        coef_pct  = round(ratio * 100, 1)
    else:
        intake_kg = None
        coef_pct  = None

    # タンク残量（今後計算予定・現時点は直接入力）
    rows.append({
        "日令":        age,
        "月日":        rec_date.strftime("%m/%d"),
        "斃死":        mort if rec else None,
        "淘汰":        cull if rec else None,
        "合計":        total_loss,      # 入雛日からの累計（斃死+淘汰）
        "残羽数":      remain_count,    # 入雛羽数+スペア−累計損耗
        "舎内最高℃":  rec.get("house_temp_max"),
        "舎内最低℃":  rec.get("house_temp_min"),
        "湿度%":       rec.get("house_humidity"),
        "外気最高℃":  rec.get("outside_temp_max"),
        "外気最低℃":  rec.get("outside_temp_min"),
        "平均体重g":   rec.get("avg_body_weight"),
        "標準体重g":   ross.get("weight_g"),
        "採食時間min": duration,
        "採食量kg":    intake_kg,
        "採食係数%":   coef_pct,
        "標準採食g":   ross.get("daily_intake_g"),
        "納品量kg":    rec.get("feed_delivery_qty"),
        "飼料銘柄":    brand_nm,
        "作業日誌":    rec.get("work_log") or "",
        "担当者":      worker_map.get(rec.get("worker_id"), "") if rec.get("worker_id") else "",
        "_date":       str(rec_date),
        "_id":         rec.get("daily_record_id"),
    })

df_all = pd.DataFrame(rows)

# ----------------------------------------------------------
# 表示列と編集列を分ける
# 計算列（合計・残羽数・採食量kg・採食係数%・標準値）は disabled
# ----------------------------------------------------------
display_cols = [
    "日令","月日",
    "斃死","淘汰","合計","残羽数",
    "舎内最高℃","舎内最低℃","湿度%","外気最高℃","外気最低℃",
    "平均体重g","標準体重g",
    "採食時間min","採食量kg","採食係数%","標準採食g",
    "納品量kg","飼料銘柄",
    "作業日誌","担当者",
]

df_disp = df_all[display_cols].copy()

# 今日の行をハイライト（スタイリングは data_editor では非対応のため目印列で代用）
edited = st.data_editor(
    df_disp,
    use_container_width=True,
    hide_index=True,
    num_rows="fixed",
    height=600,
    column_config={
        "日令":       st.column_config.NumberColumn("日令",    disabled=True, width=40),
        "月日":       st.column_config.TextColumn(  "月日",    disabled=True, width=55),
        "斃死":       st.column_config.NumberColumn("斃死",    min_value=0, step=1, width=50),
        "淘汰":       st.column_config.NumberColumn("淘汰",    min_value=0, step=1, width=50),
        "合計":       st.column_config.NumberColumn("合計",    disabled=True, width=50),
        "残羽数":     st.column_config.NumberColumn("残羽数",  disabled=True, width=65),
        "舎内最高℃": st.column_config.NumberColumn("舎内最高℃", step=0.1, width=70),
        "舎内最低℃": st.column_config.NumberColumn("舎内最低℃", step=0.1, width=70),
        "湿度%":      st.column_config.NumberColumn("湿度%",   min_value=0.0, max_value=100.0, step=1.0, width=55),
        "外気最高℃": st.column_config.NumberColumn("外気最高℃", step=0.1, width=70),
        "外気最低℃": st.column_config.NumberColumn("外気最低℃", step=0.1, width=70),
        "平均体重g":  st.column_config.NumberColumn("平均体重g", step=1.0, width=70),
        "標準体重g":  st.column_config.NumberColumn("標準体重g", disabled=True, width=70),
        "採食時間min":st.column_config.NumberColumn("採食時間\nmin", step=1.0, width=65),
        "採食量kg":   st.column_config.NumberColumn("採食量kg", disabled=True, width=65),
        "採食係数%":  st.column_config.NumberColumn("採食係数%", disabled=True, width=65),
        "標準採食g":  st.column_config.NumberColumn("標準採食g", disabled=True, width=65),
        "納品量kg":   st.column_config.NumberColumn("納品量kg", step=100.0, width=65),
        "飼料銘柄":   st.column_config.SelectboxColumn("飼料銘柄", options=brand_names, width=100),
        "作業日誌":   st.column_config.TextColumn(  "作業日誌", width=150),
        "担当者":     st.column_config.SelectboxColumn("担当者", options=worker_names, width=80),
    },
    key=f"sheet_editor_{sel_fh_id}"
)

# ----------------------------------------------------------
# 保存処理
# ----------------------------------------------------------
# 保存結果メッセージを表示（rerun後も保持）
if "sheet_msg" in st.session_state:
    msg_type, msg_text = st.session_state.pop("sheet_msg")
    if msg_type == "success":
        st.success(msg_text)
    else:
        st.error(msg_text)

st.caption(f"合計 {len(df_disp)} 行（日齢0〜{planned_age}日）　入力後「💾 一括保存」を押してください")

if st.button("💾 一括保存", type="primary", key="sheet_save"):
    updated  = 0
    inserted = 0
    skipped  = 0
    errors   = []

    for i, row in edited.iterrows():
        orig     = df_all.iloc[i]
        rec_date = orig["_date"]
        rec_id   = orig["_id"]

        # 斃死・淘汰・環境・採食時間・納品量のどれかが入力されていれば保存対象
        has_data = any([
            pd.notna(row["斃死"])      and row["斃死"]      != 0,
            pd.notna(row["淘汰"])      and row["淘汰"]      != 0,
            pd.notna(row["舎内最高℃"]),
            pd.notna(row["採食時間min"]) and row["採食時間min"] != 0,
            pd.notna(row["納品量kg"])  and row["納品量kg"]  != 0,
            bool(row.get("作業日誌")),
        ])

        if not has_data:
            skipped += 1
            continue

        # 銘柄補正率
        brand_nm = row.get("飼料銘柄") or ""
        brand_id = brand_opts.get(brand_nm) if brand_nm else None
        brand_obj2 = next((b for b in feed_brands if b["feed_brand_id"] == brand_id), {}) if brand_id else {}
        ratio2   = float(brand_obj2.get("transfer_coef_ratio") or 1.0)

        data = {
            "flock_house_id":    sel_fh_id,
            "record_date":       rec_date,
            "mortality_count":   int(row["斃死"]  or 0),
            "culling_count":     int(row["淘汰"]  or 0),
            "house_temp_max":    float(row["舎内最高℃"]) if pd.notna(row["舎内最高℃"]) else None,
            "house_temp_min":    float(row["舎内最低℃"]) if pd.notna(row["舎内最低℃"]) else None,
            "house_humidity":    float(row["湿度%"])      if pd.notna(row["湿度%"])      else None,
            "outside_temp_max":  float(row["外気最高℃"]) if pd.notna(row["外気最高℃"]) else None,
            "outside_temp_min":  float(row["外気最低℃"]) if pd.notna(row["外気最低℃"]) else None,
            "avg_body_weight":   float(row["平均体重g"])  if pd.notna(row["平均体重g"])  and float(row["平均体重g"] or 0) > 0 else None,
            "feed_duration_min": float(row["採食時間min"]) if pd.notna(row["採食時間min"]) and float(row["採食時間min"] or 0) > 0 else None,
            "feed_delivery_qty": float(row["納品量kg"])   if pd.notna(row["納品量kg"])   and float(row["納品量kg"]  or 0) > 0 else None,
            "feed_brand_id":     brand_id,
            "work_log":          str(row["作業日誌"]) if pd.notna(row.get("作業日誌")) and row["作業日誌"] else None,
            "worker_id":         worker_opts.get(row["担当者"]) if pd.notna(row.get("担当者")) and row["担当者"] else None,
        }

        try:
            if rec_id and pd.notna(rec_id):
                supabase.table("daily_records").update(data) \
                    .eq("daily_record_id", int(rec_id)).execute()
                updated += 1
            else:
                supabase.table("daily_records").insert(data).execute()
                inserted += 1
        except Exception as e:
            errors.append(f"日齢{orig['日令']}: {e}")

    if errors:
        st.session_state["sheet_msg"] = ("error", f"エラー: {'; '.join(errors[:3])}")
    else:
        msg = []
        if updated  > 0: msg.append(f"更新 {updated}件")
        if inserted > 0: msg.append(f"新規 {inserted}件")
        if skipped  > 0: msg.append(f"未入力スキップ {skipped}件")
        st.session_state["sheet_msg"] = ("success", f"✅ {' / '.join(msg)}")
    st.rerun()
