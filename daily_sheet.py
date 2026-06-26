"""
ブロイラー飼養管理システム - Excel風日次入力シート（PC専用）
日齢0〜出荷日齢の全行を一覧表示・直接入力
"""

import streamlit as st
import pandas as pd
import numpy as np
import math
from datetime import date, timedelta
from supabase import create_client, Client

st.set_page_config(page_title="日次入力シート", layout="wide")

st.markdown("""
<style>
h1 { font-size: 1.1rem !important; margin-bottom: 0.2rem !important; }
.block-container { padding-top: 0.5rem !important; padding-bottom: 0.3rem !important; }
.stSelectbox label, .stNumberInput label { font-size: 0.75rem !important; }

/* ヘッダー情報ボックス（少し大きく） */
.header-box {
    background: #f0f4f8;
    border: 1px solid #c8d6e5;
    border-radius: 6px;
    padding: 8px 14px;
    margin-bottom: 6px;
    font-size: 0.95rem;
    line-height: 2.0;
}
.header-box b { color: #1a5276; font-size: 0.88rem; }
.header-val { color: #111; font-weight: 700; font-size: 0.98rem; }

/* data_editorの列ヘッダー（表題）を小さく */
div[data-testid="stDataFrameResizable"] th,
div[data-testid="stDataFrameResizable"] [data-testid="column-header-cell"] {
    font-size: 0.68rem !important;
    padding: 2px 4px !important;
    line-height: 1.2 !important;
}
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
comfort_temp = fetch("ross_comfort_temp", "body_weight_g")
ross308      = fetch("ross308_standard", "day")

farm_map    = {f["farm_id"]:  f["farm_name"]  for f in farms}
farm_opts   = {f["farm_name"]: f["farm_id"]   for f in farms}
house_map   = {h["house_id"]: h["house_name"] for h in houses}
ln_map      = {ln["lot_number_id"]: ln["lot_number"] for ln in lot_numbers}
brand_map   = {b["feed_brand_id"]: b["brand_name"]   for b in feed_brands}
brand_opts  = {b["brand_name"]: b["feed_brand_id"]   for b in feed_brands}
worker_map  = {w["worker_id"]: w["worker_name"]       for w in workers}
worker_opts = {w["worker_name"]: w["worker_id"]       for w in workers}
ross_dict   = {(r["sex"], r["day"]): r                for r in ross308}

def get_ross308(age):
    return ross_dict.get(("as_hatched", max(0, min(int(age), 56))), {})

def get_brand_for_age(age_days, active_brands):
    matched = [b for b in active_brands
               if (b.get("age_from_days") or 0) <= age_days
               and (b.get("age_to_days") is None or age_days <= b.get("age_to_days"))]
    return max(matched, key=lambda b: b.get("age_from_days") or 0) if matched else None

def get_env_correction(house_temp_avg, humidity, body_weight_g):
    if not comfort_temp or house_temp_avg is None:
        return 1.0
    closest = min(comfort_temp, key=lambda r: abs((r["body_weight_g"] or 0) - body_weight_g))
    rh = humidity or 60.0
    if rh <= 40:   upper = closest["rh_40pct_temp_c"]
    elif rh <= 50: upper = closest["rh_40pct_temp_c"] + (closest["rh_50pct_temp_c"] - closest["rh_40pct_temp_c"]) * (rh - 40) / 10
    elif rh <= 60: upper = closest["rh_50pct_temp_c"] + (closest["rh_60pct_temp_c"] - closest["rh_50pct_temp_c"]) * (rh - 50) / 10
    else:          upper = closest["rh_60pct_temp_c"] + (closest["rh_70pct_temp_c"] - closest["rh_60pct_temp_c"]) * (min(rh, 70) - 60) / 10
    excess = house_temp_avg - upper
    if excess <= 0:   return 1.00
    elif excess <= 3: return 0.95
    elif excess <= 6: return 0.88
    else:             return 0.78

def run_feed_forecast(fh, recs, house_coef, std_qty, min_alert, lead_time, adj_dict=None):
    """出荷日齢までのタンク残量・発注予測を実行"""
    chick_date   = date.fromisoformat(fh["chick_in_date"])
    shipping_age = min(fh.get("planned_shipment_age_days") or 56, 56)
    chick_in     = fh["chick_in_count"] or 0
    spare        = fh["spare_count"]    or 0
    total_birds  = chick_in + spare
    first_qty    = float(fh.get("initial_feed_delivery_qty") or 0)
    active_brs   = [b for b in feed_brands if b.get("is_active")]
    weighted_corr = float(fh.get("feed_correction_factor") or 1.0)

    # 前期/中期/仕上の総量をbrand_stagesから計算
    brand_stages = sorted(active_brs, key=lambda b: b.get("age_from_days") or 0)
    pre_limit, mid_limit = 0.0, 0.0
    if len(brand_stages) >= 1:
        b0 = brand_stages[0]
        for d in range(int(b0.get("age_from_days") or 0), min((b0.get("age_to_days") or shipping_age) + 1, shipping_age + 1)):
            r = get_ross308(d)
            pre_limit += (r.get("daily_intake_g") or 0) * total_birds / 1000
    if len(brand_stages) >= 2:
        b1 = brand_stages[1]
        for d in range(int(b1.get("age_from_days") or 0), min((b1.get("age_to_days") or shipping_age) + 1, shipping_age + 1)):
            r = get_ross308(d)
            mid_limit += (r.get("daily_intake_g") or 0) * total_birds / 1000

    # 日次記録をday→recordに変換
    rec_by_day = {}
    for r in recs:
        d = (date.fromisoformat(r["record_date"]) - chick_date).days
        rec_by_day[d] = r

    # 環境データ（直近3日平均）
    recent = recs[-3:] if recs else []
    avg_temp, avg_hum = None, 60.0
    if recent:
        temps = [((r.get("house_temp_max") or 0) + (r.get("house_temp_min") or 0)) / 2
                 for r in recent if r.get("house_temp_max")]
        if temps: avg_temp = sum(temps) / len(temps)
        hums = [r.get("house_humidity") or 60 for r in recent]
        avg_hum = sum(hums) / len(hums)

    # 実績確定日（実測タンク残量がある発注明細）
    order_details = supabase.table("feed_order_details")         .select("*, feed_orders(delivery_date)")         .eq("flock_house_id", fh["flock_house_id"])         .not_.is_("actual_tank_remaining", "null")         .execute().data
    act_dict = {}
    for od in order_details:
        dd = (od.get("feed_orders") or {}).get("delivery_date")
        if dd:
            day = (date.fromisoformat(dd) - chick_date).days
            act_dict[day] = {
                "actual_tank": float(od["actual_tank_remaining"]),
                "delivered":   float(od["order_qty"] or 0),
            }

    # adj_dict（ユーザー調整値）をact_dictにマージ
    if adj_dict:
        for day, adj in adj_dict.items():
            if day not in act_dict:
                act_dict[day] = {}
            if adj.get("actual_tank") is not None:
                act_dict[day]["actual_tank"] = adj["actual_tank"]
            if adj.get("delivered") is not None:
                act_dict[day]["delivered"] = adj["delivered"]

    # 全日齢の標準採食量（環境補正＋加重平均補正込み）
    days = list(range(0, shipping_age + 1))
    std_feed = []
    for d in days:
        r   = get_ross308(d)
        std = (r.get("daily_intake_g") or 0) * total_birds / 1000
        env = get_env_correction(avg_temp, avg_hum, r.get("weight_g") or 1000)
        kg  = std * env * weighted_corr
        if d == 45: kg *= 0.75  # 給餌停止
        std_feed.append(kg)

    df = pd.DataFrame({"day": days, "std_feed_kg": std_feed})
    df["date_obj"] = [chick_date + timedelta(days=d) for d in days]
    df["date_str"] = [d.strftime("%m/%d") for d in df["date_obj"]]

    # 区間補正率
    adj_rates   = np.ones(len(df))
    actual_feed = df["std_feed_kg"].values.copy()  # 書き込み可能なコピー
    sorted_act  = sorted(act_dict.keys())
    latest_rate = weighted_corr

    if len(sorted_act) > 1:
        for i in range(len(sorted_act) - 1):
            s, e = sorted_act[i], sorted_act[i+1]
            s_tank = act_dict[s]["actual_tank"]
            e_tank = act_dict[e]["actual_tank"]
            consumed = s_tank + sum(act_dict[d].get("delivered", 0) for d in range(s+1, e+1) if d in act_dict) - e_tank
            std_cons = df.loc[s:e-1, "std_feed_kg"].sum()
            rate = consumed / std_cons if std_cons > 0 else 1.0
            latest_rate = rate
            for d in range(s, e):
                adj_rates[d] = rate
                actual_feed[d] = df.loc[d, "std_feed_kg"] * rate

    last_act  = sorted_act[-1] if sorted_act else 0
    today_day = (date.today() - chick_date).days
    for d in range(last_act, len(df)):
        actual_feed[d] = df.loc[d, "std_feed_kg"] * latest_rate
        adj_rates[d]   = latest_rate

    df["adj_rate"]    = adj_rates
    df["act_feed_kg"] = actual_feed

    # ----------------------------------------------------------
    # タンク残量シミュレーション
    # 発注ルール:
    #   タンク残量 ≦ min_alert(200kg) になったら配送単位(std_qty)で発注
    #   繰り返し条件: 予測採食量累計 - 予測配送量累計 > 配送単位
    #     → 再度配送単位で発注
    #   終了条件: 予測採食量累計 - 予測配送量累計 ≦ 配送単位
    #     → 最終発注量 = 予測採食量累計 - 予測配送量累計（端数）
    # ----------------------------------------------------------
    pred_tank   = np.zeros(len(df))
    real_tank   = np.full(len(df), np.nan)
    delivery_kg = np.zeros(len(df))
    event_notes = [""] * len(df)
    allocated_pre, allocated_mid = first_qty, 0.0

    # 累計管理
    cum_feed_kg     = 0.0   # 予測採食量累計
    cum_delivery_kg = first_qty  # 予測配送量累計（初回納入量を含む）

    pred_tank[0] = first_qty
    real_tank[0] = first_qty
    event_notes[0] = f"初回: {first_qty:.0f}kg"
    cum_feed_kg  += df.loc[0, "act_feed_kg"]
    evening_pred  = first_qty - df.loc[0, "act_feed_kg"]

    def get_order_note(qty, allocated_pre, allocated_mid, pre_limit, mid_limit):
        """発注種別メモを生成（前期/中期/仕上の混載判定）"""
        rem_pre = max(pre_limit - allocated_pre, 0) if pre_limit > 0 else 0
        rem_mid = max(mid_limit - allocated_mid, 0) if mid_limit > 0 else 0
        if rem_pre > 0:
            if rem_pre >= qty:
                return f"前期: {qty:.0f}kg", qty, 0
            else:
                mix = qty - rem_pre
                label = f"前期{rem_pre:.0f}/中期{mix:.0f}kg" if mid_limit > 0 else f"前期{rem_pre:.0f}/仕上{mix:.0f}kg"
                return label, rem_pre, mix if mid_limit > 0 else 0
        elif rem_mid > 0:
            if rem_mid >= qty:
                return f"中期: {qty:.0f}kg", 0, qty
            else:
                mix = qty - rem_mid
                return f"中期{rem_mid:.0f}/仕上{mix:.0f}kg", 0, rem_mid
        else:
            return f"仕上: {qty:.0f}kg", 0, 0

    for d in range(1, len(df)):
        pred_tank[d]  = evening_pred
        daily_feed    = df.loc[d, "act_feed_kg"]

        if d in act_dict and act_dict[d].get("actual_tank") is not None:
            # ---- 実績確定日 ----
            real_tank[d]    = act_dict[d]["actual_tank"]
            pred_tank[d]    = real_tank[d]
            delivery_kg[d]  = act_dict[d].get("delivered", 0)
            cum_delivery_kg += delivery_kg[d]
            event_notes[d]  = f"実績: 残{real_tank[d]:.0f}kg 納品{delivery_kg[d]:.0f}kg"
            if pre_limit > 0 and allocated_pre < pre_limit:
                allocated_pre += delivery_kg[d]
            elif mid_limit > 0 and allocated_mid < mid_limit:
                allocated_mid += delivery_kg[d]
            cum_feed_kg  += daily_feed
            evening_pred  = real_tank[d] + delivery_kg[d] - daily_feed

        elif d > today_day:
            # ---- 未来日：発注判定 ----
            cum_feed_kg += daily_feed

            if pred_tank[d] <= min_alert:
                # 残量不足 → 発注
                # 繰り返し発注判定
                remaining_need = cum_feed_kg - cum_delivery_kg

                if remaining_need <= 0:
                    # 既に配送量が足りている
                    pass
                elif remaining_need <= std_qty:
                    # 端数発注（最終発注）
                    order_qty = round(remaining_need, 0)
                    delivery_kg[d] += order_qty
                    cum_delivery_kg += order_qty
                    note, add_pre, add_mid = get_order_note(
                        order_qty, allocated_pre, allocated_mid, pre_limit, mid_limit)
                    allocated_pre += add_pre
                    allocated_mid += add_mid
                    event_notes[d] = f"最終発注({note})"
                else:
                    # 配送単位で発注（必要に応じて複数回）
                    total_order = 0.0
                    notes_list  = []
                    while cum_feed_kg - (cum_delivery_kg + total_order) > std_qty:
                        total_order += std_qty
                        note, add_pre, add_mid = get_order_note(
                            std_qty, allocated_pre, allocated_mid, pre_limit, mid_limit)
                        allocated_pre += add_pre
                        allocated_mid += add_mid
                        notes_list.append(note)
                    # 最後の端数
                    final_need = cum_feed_kg - (cum_delivery_kg + total_order)
                    if final_need > 0:
                        total_order += final_need
                        note, add_pre, add_mid = get_order_note(
                            final_need, allocated_pre, allocated_mid, pre_limit, mid_limit)
                        allocated_pre += add_pre
                        allocated_mid += add_mid
                        notes_list.append(f"端数{note}")
                    delivery_kg[d] += total_order
                    cum_delivery_kg += total_order
                    event_notes[d]   = " + ".join(notes_list) if notes_list else f"発注: {total_order:.0f}kg"

            evening_pred = pred_tank[d] + delivery_kg[d] - daily_feed

        else:
            # ---- 実績日（実測なし）----
            r = rec_by_day.get(d, {})
            if r and house_coef > 0 and r.get("feed_duration_min"):
                brand_id   = r.get("feed_brand_id")
                brand_obj  = next((b for b in feed_brands if b["feed_brand_id"] == brand_id), {}) \
                             if brand_id else get_brand_for_age(d, active_brs) or {}
                ratio      = float(brand_obj.get("transfer_coef_ratio") or 1.0)
                real_intake = float(r["feed_duration_min"]) * house_coef * ratio
                delivery_today = float(r.get("feed_delivery_qty") or 0)
                actual_feed[d]  = real_intake
                cum_feed_kg    += real_intake
                cum_delivery_kg += delivery_today
                evening_pred    = pred_tank[d] + delivery_today - real_intake
            else:
                cum_feed_kg += daily_feed
                evening_pred = pred_tank[d] - daily_feed

    df["pred_tank"]   = pred_tank
    df["real_tank"]   = real_tank
    df["delivery_kg"] = delivery_kg
    df["event_notes"] = event_notes
    df["act_feed_kg"] = actual_feed
    # 累計列を追加（確認用）
    df["cum_feed_kg"]     = df["act_feed_kg"].cumsum()
    df["cum_delivery_kg"] = df["delivery_kg"].cumsum() + first_qty
    return df
ross_dict   = {(r["sex"], r["day"]): r for r in ross308}

def get_ross308(age):
    return ross_dict.get(("as_hatched", max(0, min(int(age), 56))), {})

# ----------------------------------------------------------
# 対象選択
# ----------------------------------------------------------
st.title("📋 ブロイラー飼養管理 - 入力・発注予測")
sheet_tab, forecast_tab = st.tabs(["📝 日次入力シート", "🚛 発注予測"])

with sheet_tab:
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
# タブ1: 日次入力シート
# ----------------------------------------------------------
with sheet_tab:
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

# ----------------------------------------------------------
# タブ2: 発注予測（直接編集→自動再計算）
# ----------------------------------------------------------
with forecast_tab:
    st.markdown("#### ⚙️ 発注パラメータ")
    fp1, fp2, fp3 = st.columns(3)
    with fp1:
        fc_std_qty   = st.number_input("基本発注量（kg）",
            min_value=0.0, value=5000.0, step=500.0, key="fc_std_qty")
    with fp2:
        fc_min_alert = st.number_input("最低残量アラート（kg）",
            min_value=0.0, value=500.0, step=100.0, key="fc_min_alert")
    with fp3:
        fc_lead_time = st.number_input("リードタイム（日）",
            min_value=1, max_value=14, value=3, step=1, key="fc_lead")

    st.caption("💡 表の「実測残量kg」「調整発注kg」を直接入力すると自動再計算されます")

    try:
        fc_recs = supabase.table("daily_records")             .select("*")             .eq("flock_house_id", sel_fh_id)             .order("record_date").execute().data

        today_day_fc = (date.today() - chick_in_date).days

        # ---- Step1: adj_dictを読み込む（セッション保持） ----
        adj_key = f"adj_dict_{sel_fh_id}"
        if adj_key not in st.session_state:
            st.session_state[adj_key] = {}
        adj_dict = st.session_state[adj_key]

        # ---- Step2: 発注予測を実行（adj_dictを反映） ----
        df_fc = run_feed_forecast(
            sel_fh, fc_recs, house_coef,
            fc_std_qty, fc_min_alert, fc_lead_time,
            adj_dict=adj_dict)

        # ---- Step3: サマリ ----
        tank_now = float(df_fc[df_fc["day"] == today_day_fc]["pred_tank"].iloc[0])             if today_day_fc in df_fc["day"].values else 0.0
        next_orders = df_fc[(df_fc["day"] > today_day_fc) & (df_fc["delivery_kg"] > 0)]

        sm1, sm2, sm3, sm4 = st.columns(4)
        sm1.metric("現在日齢",    f"{today_day_fc} 日")
        sm2.metric("タンク残量",  f"{tank_now:,.0f} kg")
        sm3.metric("次回発注予定",
            next_orders["date_str"].iloc[0] if not next_orders.empty else "なし")
        sm4.metric("次回発注種別",
            next_orders["event_notes"].iloc[0] if not next_orders.empty else "-")

        # ---- Step4: 編集可能なシミュレーション表 ----
        st.markdown("#### 📊 タンク残量シミュレーション（直接編集→自動再計算）")

        # 編集用DataFrameを構築
        # 実測残量・調整発注量は編集可能、それ以外は読み取り専用
        edit_df = pd.DataFrame({
            "日齢":         df_fc["day"].astype(int),
            "月日":         df_fc["date_str"],
            "今日":         df_fc["day"].apply(lambda d: "◀" if d == today_day_fc else ""),
            "採食kg":       df_fc["act_feed_kg"].round(1),
            "採食累計kg":   df_fc["cum_feed_kg"].round(0),
            "補正率":       df_fc["adj_rate"].round(3),
            "予測残量kg":   df_fc["pred_tank"].round(0),
            "実測残量kg":   df_fc["real_tank"].apply(
                lambda x: float(x) if not (isinstance(x, float) and np.isnan(x)) else None),
            "調整発注kg":   df_fc["delivery_kg"].apply(lambda x: float(x) if x > 0 else None),
            "配送累計kg":   df_fc["cum_delivery_kg"].round(0),
            "発注種別":     df_fc["event_notes"],
        })

        edited_fc = st.data_editor(
            edit_df,
            use_container_width=True,
            hide_index=True,
            num_rows="fixed",
            height=500,
            column_config={
                "日齢":       st.column_config.NumberColumn("日齢",    disabled=True, width=45),
                "月日":       st.column_config.TextColumn(  "月日",    disabled=True, width=55),
                "今日":       st.column_config.TextColumn(  "",        disabled=True, width=30),
                "採食kg":       st.column_config.NumberColumn("採食kg",     disabled=True, width=60),
                "採食累計kg":   st.column_config.NumberColumn("採食累計kg", disabled=True, width=75),
                "補正率":       st.column_config.NumberColumn("補正率",     disabled=True, width=55),
                "予測残量kg":   st.column_config.NumberColumn("予測残量kg", disabled=True, width=80),
                "実測残量kg": st.column_config.NumberColumn("実測残量kg",
                    min_value=0.0, step=10.0, width=85,
                    help="納品直前の実測タンク残量を入力→自動再計算"),
                "調整発注kg":   st.column_config.NumberColumn("調整発注kg",
                    min_value=0.0, step=500.0, width=85,
                    help="発注量を手動で変更→自動再計算"),
                "配送累計kg":   st.column_config.NumberColumn("配送累計kg", disabled=True, width=75),
                "発注種別":     st.column_config.TextColumn("発注種別",   disabled=True, width=180),
            },
            key=f"fc_editor_{sel_fh_id}"
        )

        # ---- Step5: 変更を検知してadj_dictを更新→自動再計算 ----
        new_adj = {}
        for i, row in edited_fc.iterrows():
            day     = int(row["日齢"])
            changed = False
            entry   = {}
            # 実測残量の変更を検知
            orig_real = edit_df.at[i, "実測残量kg"]
            new_real  = row["実測残量kg"]
            if pd.notna(new_real) and new_real != orig_real:
                entry["actual_tank"] = float(new_real)
                changed = True
            elif pd.notna(orig_real):
                entry["actual_tank"] = float(orig_real)
            # 調整発注量の変更を検知
            orig_del = edit_df.at[i, "調整発注kg"]
            new_del  = row["調整発注kg"]
            if pd.notna(new_del) and new_del != orig_del:
                entry["delivered"] = float(new_del)
                changed = True
            elif pd.notna(orig_del):
                entry["delivered"] = float(orig_del)
            if entry:
                new_adj[day] = entry

        # adj_dictが変わったらsession_stateに保存（→次のレンダリングで自動再計算）
        if new_adj != adj_dict:
            st.session_state[adj_key] = new_adj
            st.rerun()

        # ---- Step6: 調整内容のリセット ----
        if adj_dict:
            adj_count = len([v for v in adj_dict.values()
                             if v.get("actual_tank") or v.get("delivered")])
            st.caption(f"✏️ 調整中: {adj_count}件の変更あり")
            if st.button("🔄 調整をリセット", key="fc_reset"):
                st.session_state[adj_key] = {}
                st.rerun()

        # ---- Step7: 発注計画一覧 ----
        order_plan = df_fc[df_fc["delivery_kg"] > 0].copy()
        if not order_plan.empty:
            st.markdown("#### 📋 発注計画一覧")
            active_brs = [b for b in feed_brands if b.get("is_active")]
            order_plan["納品予定日"] = order_plan["date_str"]
            order_plan["日齢"]       = order_plan["day"]
            order_plan["発注量kg"]   = order_plan["delivery_kg"].apply(lambda x: f"{x:,.0f}")
            order_plan["発注種別"]   = order_plan["event_notes"]
            order_plan["タンク残量"] = order_plan["pred_tank"].apply(lambda x: f"{x:,.0f}")
            order_plan["使用銘柄"]   = order_plan["日齢"].apply(
                lambda d: (get_brand_for_age(d, active_brs) or {}).get("brand_name", "-"))
            st.dataframe(
                order_plan[["納品予定日","日齢","発注量kg","発注種別","タンク残量","使用銘柄"]],
                use_container_width=True, hide_index=True)

            # ---- Step8: 調整内容をDBに保存 ----
            if adj_dict and st.button("💾 調整内容を保存", key="fc_save", type="primary"):
                try:
                    for day, adj in adj_dict.items():
                        rec_date = str(chick_in_date + timedelta(days=day))
                        # feed_order_detailsに調整発注を保存
                        if adj.get("delivered") and adj["delivered"] > 0:
                            # 既存発注記録があるか確認
                            existing_order = supabase.table("feed_orders")                                 .select("order_id")                                 .eq("farm_id", sel_fh["lot_number_id"])                                 .execute().data
                            # 調整発注をfeed_ordersに記録
                            res = supabase.table("feed_orders").insert({
                                "farm_id":         next(
                                    (ln["farm_id"] for ln in lot_numbers
                                     if ln["lot_number_id"] == sel_fh["lot_number_id"]), None),
                                "order_date":      str(date.today()),
                                "delivery_date":   rec_date,
                                "lead_time_days":  fc_lead_time,
                                "total_order_qty": adj["delivered"],
                                "status":          "発注済",
                                "remarks":         "シミュレーション調整発注",
                            }).execute()
                            order_id = res.data[0]["order_id"]
                            supabase.table("feed_order_details").insert({
                                "order_id":              order_id,
                                "flock_house_id":        sel_fh_id,
                                "order_qty":             adj["delivered"],
                                "actual_tank_remaining": adj.get("actual_tank"),
                                "calc_tank_remaining":   float(
                                    df_fc[df_fc["day"] == day]["pred_tank"].iloc[0])
                                    if day in df_fc["day"].values else None,
                            }).execute()
                    st.success("✅ 調整内容を保存しました")
                    st.session_state[adj_key] = {}
                    st.rerun()
                except Exception as e:
                    st.error(f"保存エラー: {e}")
        else:
            st.info("出荷日齢まで発注不要です")

    except Exception as e:
        st.error(f"発注予測エラー: {e}")
        import traceback
        st.code(traceback.format_exc())
