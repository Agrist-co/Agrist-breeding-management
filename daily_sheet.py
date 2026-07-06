"""
ブロイラー飼養管理システム - Excel風日次入力シート（PC専用）
日齢0〜出荷日齢の全行を一覧表示・直接入力
"""

import re
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
/* ボタン: フォントを小さく・折り返し防止 */
.stButton > button {
    font-size: 0.75rem !important;
    white-space: nowrap !important;
    padding: 0.25rem 0.5rem !important;
    line-height: 1.4 !important;
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

farm_map    = {f["farm_id"]:  f["farm_name"]  for f in farms}
farm_opts   = {f["farm_name"]: f["farm_id"]   for f in farms}
house_map   = {h["house_id"]: h["house_name"] for h in houses}
tank_no_map = {h["house_id"]: h.get("tank_number", "-") for h in houses}
# flock_house_id → tank_number のマップ
fh_tank_map = {
    fh["flock_house_id"]: tank_no_map.get(fh["house_id"], "-")
    for fh in flock_houses
}
ln_map      = {ln["lot_number_id"]: ln["lot_number"] for ln in lot_numbers}
brand_map   = {b["feed_brand_id"]: b["brand_name"]   for b in feed_brands}
brand_opts  = {b["brand_name"]: b["feed_brand_id"]   for b in feed_brands}
worker_map  = {w["worker_id"]: w["worker_name"]       for w in workers}
worker_opts = {w["worker_name"]: w["worker_id"]       for w in workers}
ross_dict   = {(r["sex"], r["day"]): r                for r in ross308}

def get_ross308(age):
    max_day = max(k[1] for k in ross_dict.keys()) if ross_dict else 56
    return ross_dict.get(("as_hatched", max(0, min(int(age), max_day))), {})

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
    shipping_age = fh.get("planned_shipment_age_days") or 56
    chick_in     = fh["chick_in_count"] or 0
    spare        = fh["spare_count"]    or 0
    total_birds  = chick_in + spare
    active_brs   = [b for b in feed_brands if b.get("is_active") not in (None, False, 0, "false", "0", "")]
    weighted_corr = float(fh.get("feed_correction_factor") or 1.0)

    # 初回投入量: fh設定値 → 0日齢daily_records納品量 → adj_dict[0] の順で取得
    first_qty = float(fh.get("initial_feed_delivery_qty") or 0)
    if first_qty == 0:
        chick_date_tmp = date.fromisoformat(fh["chick_in_date"])
        day0_rec = next(
            (r for r in recs
             if (date.fromisoformat(r["record_date"]) - chick_date_tmp).days == 0),
            None)
        if day0_rec and day0_rec.get("feed_delivery_qty"):
            first_qty = float(day0_rec["feed_delivery_qty"])
    if first_qty == 0 and adj_dict and 0 in adj_dict and (adj_dict[0] or {}).get("delivered"):
        first_qty = float(adj_dict[0]["delivered"])

    # 前期/中期/仕上の銘柄ステージ（発注内容の表示用のみに使用）
    brand_stages = sorted(active_brs, key=lambda b: b.get("age_from_days") or 0)

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

    # adj_dictのactual_tank: ユーザー入力による実測残量補正（Supabaseとは独立）
    adj_tank_map = {
        int(day): float(v["actual_tank"])
        for day, v in (adj_dict or {}).items()
        if v.get("actual_tank") is not None and int(day) > 0  # 0日齢はfirst_qty固定
    }
    # 調整発注辞書（未来日の発注量を上書き）
    adj_delivery_map = {
        int(day): float(v["delivered"])
        for day, v in (adj_dict or {}).items()
        if v.get("delivered") and float(v["delivered"]) > 0
    }

    # 全日齢の標準採食量（環境補正＋加重平均補正込み）
    # 出荷前日18時給餌停止: 出荷前日を0.75掛け、出荷日は行自体を含めない
    days = list(range(0, shipping_age))  # 出荷日(shipping_age)は含まない
    std_feed = []
    for d in days:
        r    = get_ross308(d)
        std  = (r.get("daily_intake_g") or 0) * total_birds / 1000
        env  = get_env_correction(avg_temp, avg_hum, r.get("weight_g") or 1000)
        kg   = std * env  # 純粋標準値（weighted_corrは後でadj_rateとして乗算）
        if d == shipping_age - 1: kg *= 0.75
        std_feed.append(kg)

    df = pd.DataFrame({"day": days, "std_feed_kg": std_feed})
    df["date_obj"] = [chick_date + timedelta(days=d) for d in days]
    df["date_str"] = [d.strftime("%m/%d") for d in df["date_obj"]]

    # 区間補正率
    # act_dict（Supabase）とadj_tank_map（手入力）を統合してタンク残量確定点を作成
    adj_rates   = np.ones(len(df))
    actual_feed = df["std_feed_kg"].values.copy()

    # 日次記録がある日の実績採食を先にactual_feedに反映
    # （補正率計算の分母を「予測残量ベースの消費量」と一致させるため）
    for d, r in rec_by_day.items():
        if d in day_to_idx if False else True:  # day_to_idxはまだ未定義なのでrecで直接
            pass
    # ※day_to_idxは後で定義するため、ここではrec_by_dayをインデックスで直接処理
    for idx_pre, day_val in enumerate(days):
        r_pre = rec_by_day.get(day_val, {})
        if r_pre and house_coef > 0 and r_pre.get("feed_duration_min"):
            bid_pre  = r_pre.get("feed_brand_id")
            bobj_pre = next((b for b in feed_brands if b["feed_brand_id"] == bid_pre), {})                        if bid_pre else get_brand_for_age(day_val, active_brs) or {}
            ratio_pre = float(bobj_pre.get("transfer_coef_ratio") or 1.0)
            actual_feed[idx_pre] = float(r_pre["feed_duration_min"]) * house_coef * ratio_pre

    # 統合タンク残量辞書: 0日齢(first_qty)を起点に、act_dict→adj_tank_mapで上書き
    combined_tank = {}

    # 0日齢: initial_feed_delivery_qtyを起点として登録
    combined_tank[0] = {
        "actual_tank": first_qty,
        "delivered":   0,
    }
    # Supabase確定値
    for d, v in act_dict.items():
        if v.get("actual_tank") is not None:
            combined_tank[d] = {
                "actual_tank": v["actual_tank"],
                "delivered":   v.get("delivered", 0),
            }
    # 手入力実測残量（Supabaseを上書き、0日齢は除外）
    for d, tank_val in adj_tank_map.items():
        if d == 0:
            continue  # 0日齢はfirst_qtyで固定
        combined_tank[d] = {
            "actual_tank": tank_val,
            "delivered":   adj_delivery_map.get(d, 0),
        }

    # day→indexの逆引き辞書
    day_to_idx = {int(df.loc[i, "day"]): i for i in range(len(df))}

    sorted_act  = sorted(combined_tank.keys())
    latest_rate = weighted_corr

    if len(sorted_act) > 1:
        for i in range(len(sorted_act) - 1):
            s, e = sorted_act[i], sorted_act[i+1]
            # s_tank = 実測残量 + s日の納品量（実測後に投入される分）
            # delivered: combined_tank → adj_delivery_map → rec_by_day の順で取得
            s_tank_raw = combined_tank[s]["actual_tank"]
            s_delivery = combined_tank[s].get("delivered", 0)
            if s_delivery == 0 and s in adj_delivery_map:
                s_delivery = adj_delivery_map[s]
            # daily_recordsの実績納品は参照しない
            s_tank     = s_tank_raw + s_delivery
            e_tank     = combined_tank[e]["actual_tank"]

            # 区間内の途中納品量（s+1〜e-1のみ）
            # daily_recordsの実績納品は参照しない（発注予測は独立）
            delivered_between = 0.0
            for dd in range(s + 1, e):
                if dd in combined_tank:
                    delivered_between += combined_tank[dd].get("delivered", 0)
                elif dd in adj_delivery_map:
                    delivered_between += adj_delivery_map[dd]

            consumed = s_tank + delivered_between - e_tank
            # 区間s〜e-1の標準採食量合計（補正率計算の分母は常にstd_feed_kg）
            actual_cons = sum(
                df.loc[day_to_idx[d], "std_feed_kg"]
                for d in range(s, e) if d in day_to_idx
            )
            # 消費量がゼロ以下（残量増加）の場合は補正率計算不能→前の補正率を継続
            if consumed <= 0 or actual_cons <= 0:
                rate = latest_rate  # 前区間の補正率を継続
            else:
                rate = consumed / actual_cons
            latest_rate = rate
            for d in range(s, e):
                if d in day_to_idx:
                    idx = day_to_idx[d]
                    adj_rates[idx]   = rate
                    actual_feed[idx] = df.loc[idx, "std_feed_kg"] * rate

    # 最後の実測残量日以降（last_act含む）: 最新区間補正率を継続適用
    last_act = sorted_act[-1] if sorted_act else 0
    for d in df["day"]:
        if d >= last_act and d in day_to_idx:
            idx = day_to_idx[d]
            actual_feed[idx] = df.loc[idx, "std_feed_kg"] * latest_rate
            adj_rates[idx]   = latest_rate

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

    # 前期・中期の絶対使用量（flock_housesのstarter_qty_kg/grower_qty_kg）
    _starter_total = float(fh.get("starter_qty_kg") or 0)
    _grower_total  = float(fh.get("grower_qty_kg")  or 0)
    # 未設定の場合は標準採食量合計で推定
    if _starter_total == 0:
        _cur_brand_at0 = get_brand_for_age(0, active_brs)
        _cur_age_to    = _cur_brand_at0.get("age_to_days") if _cur_brand_at0 else None
        _starter_total = df.loc[
            df["day"].between(0, _cur_age_to if _cur_age_to is not None else shipping_age - 1),
            "std_feed_kg"
        ].sum() if _cur_brand_at0 else 0.0

    # 前期・中期・仕上の累計発注追跡
    _starter_delivered = [0.0]
    _grower_delivered  = [0.0]

    def get_order_note_for_day(day, qty):
        """前期→中期→仕上の順に残量ベースで銘柄を決定"""
        starter_brand = get_brand_for_age(0, active_brs)
        finish_brand  = next(
            (b for b in sorted(active_brs, key=lambda b: b.get("age_from_days") or 0, reverse=True)
             if b.get("age_to_days") is None), None)
        # 中期銘柄（age_to_daysがあり前期でも仕上でもないもの）
        grower_brand  = next(
            (b for b in sorted(active_brs, key=lambda b: b.get("age_from_days") or 0)
             if b.get("age_to_days") is not None
             and b != starter_brand), None) if _grower_total > 0 else None

        # メーカー名を除去（「全農_前期」→「前期」）
        def _short_name(brand, default):
            if not brand: return default
            n = brand["brand_name"]
            return n.split("_")[-1] if "_" in n else n
        s_name = _short_name(starter_brand, "前期")
        g_name = _short_name(grower_brand,  "中期")
        f_name = _short_name(finish_brand,  "仕上")

        s_remain = max(_starter_total - _starter_delivered[0], 0.0)
        g_remain = max(_grower_total  - _grower_delivered[0],  0.0) if _grower_total > 0 else 0.0

        result_parts = []
        remaining_qty = float(qty)

        # 前期分
        if s_remain > 0 and remaining_qty > 0:
            s_alloc = round(min(s_remain, remaining_qty) / 1000) * 1000
            s_alloc = max(min(s_alloc, int(s_remain), int(remaining_qty)), 0)
            if s_alloc > 0:
                result_parts.append(f"{s_name} {s_alloc:,.0f}kg")
                _starter_delivered[0] += s_alloc
                remaining_qty -= s_alloc

        # 中期分
        if g_remain > 0 and remaining_qty > 0:
            g_alloc = round(min(g_remain, remaining_qty) / 1000) * 1000
            g_alloc = max(min(g_alloc, int(g_remain), int(remaining_qty)), 0)
            if g_alloc > 0:
                result_parts.append(f"{g_name} {g_alloc:,.0f}kg")
                _grower_delivered[0] += g_alloc
                remaining_qty -= g_alloc

        # 仕上分
        if remaining_qty > 0:
            result_parts.append(f"{f_name} {remaining_qty:,.0f}kg")

        return " ＋ ".join(result_parts) if result_parts else f"{f_name} {qty:,.0f}kg"

    # 0日齢: 初回投入は前期
    delivery_kg[0] = first_qty
    pred_tank[0]   = first_qty
    real_tank[0]   = first_qty
    _day0_brand      = get_brand_for_age(0, active_brs)
    _day0_brand_name = _day0_brand["brand_name"] if _day0_brand else "前期"
    _day0_short = _day0_brand_name.split("_")[-1] if _day0_brand_name and "_" in _day0_brand_name else (_day0_brand_name or "前期")
    event_notes[0]   = f"{_day0_short} {first_qty:,.0f}kg" if first_qty > 0 else ""
    _starter_delivered[0] += first_qty  # 初回投入分を前期累計に加算
    day0_feed    = df.loc[0, "act_feed_kg"]
    evening_pred = first_qty - day0_feed


    _debug_fc = []  # デバッグ用
    for d in range(1, len(df)):
        daily_feed   = df.loc[d, "std_feed_kg"] * df.loc[d, "adj_rate"]  # 補正後標準値
        r            = rec_by_day.get(d, {})

        # pred_tank[d] = 前日evening_predを引き継ぐ（朝のタンク残量）
        pred_tank[d] = evening_pred

        # ── 実測残量補正: adj_tank_mapに値があればpred_tank(朝残量)を実測値で上書き ──
        # 実測残量 = 納品直前の朝タンク残量 → pred_tankを補正し、採食後evening_predへ
        if d in adj_tank_map:
            real_tank[d] = adj_tank_map[d]
            pred_tank[d] = adj_tank_map[d]

        # ── 優先1: Supabase実測タンク残量あり（feed_order_details確定値） ──
        if d in act_dict and act_dict[d].get("actual_tank") is not None:
            real_tank[d]   = act_dict[d]["actual_tank"]
            pred_tank[d]   = real_tank[d]
            delivery_kg[d] = act_dict[d].get("delivered", 0)
            event_notes[d] = f"実績: 残{real_tank[d]:.0f}kg 納品{delivery_kg[d]:.0f}kg"
            evening_pred   = real_tank[d] + delivery_kg[d] - daily_feed

        # ── 優先2: 調整発注（確定発注・手動上書き） ──
        elif d in adj_delivery_map:
            adj_qty        = adj_delivery_map[d]
            delivery_kg[d] = adj_qty
            event_notes[d] = get_order_note_for_day(d, adj_qty)
            # 採食は日次記録があれば実績、なければ標準値
            if r and house_coef > 0 and r.get("feed_duration_min"):
                bid   = r.get("feed_brand_id")
                bobj  = next((b for b in feed_brands if b["feed_brand_id"] == bid), {})                         if bid else get_brand_for_age(d, active_brs) or {}
                ratio = float(bobj.get("transfer_coef_ratio") or 1.0)
                ri    = float(r["feed_duration_min"]) * house_coef * ratio
                actual_feed[d] = ri
                evening_pred   = pred_tank[d] + adj_qty - ri
            else:
                evening_pred   = pred_tank[d] + adj_qty - daily_feed

        # ── 優先3: 日次記録あり（採食時間のみ参照・納品量は発注予測に使わない） ──
        elif r and r.get("feed_duration_min"):
            if house_coef > 0:
                bid   = r.get("feed_brand_id")
                bobj  = next((b for b in feed_brands if b["feed_brand_id"] == bid), {})                         if bid else get_brand_for_age(d, active_brs) or {}
                ratio = float(bobj.get("transfer_coef_ratio") or 1.0)
                ri    = float(r["feed_duration_min"]) * house_coef * ratio
                actual_feed[d] = ri  # 実績採食量（表示用のみ）
            # 予測残量は補正後標準値で計算
            evening_pred = pred_tank[d] - daily_feed
            # タンク警戒以下なら発注計算
            if pred_tank[d] <= min_alert:
                _d_idx      = day_to_idx.get(d, d)
                future_need = float(
                    (df.loc[_d_idx:, "std_feed_kg"] * df.loc[_d_idx:, "adj_rate"]).sum()
                )  # 補正後標準値ベース
                cur_tank    = pred_tank[d]
                if future_need - cur_tank > 0:
                    if future_need - (cur_tank + std_qty) <= 0:
                        oq_r = round((future_need - cur_tank) / 1000) * 1000
                        oq_r = max(oq_r, 1000)
                        delivery_kg[d] = oq_r
                        event_notes[d] = f"最終: {get_order_note_for_day(d, oq_r)}"
                    else:
                        delivery_kg[d] = std_qty
                        event_notes[d] = get_order_note_for_day(d, std_qty)
                    evening_pred += delivery_kg[d]

        # ── 優先4: 予測発注計算（タンク残量ベース） ──
        else:
            oq = 0.0
            _d_idx      = day_to_idx.get(d, d)
            future_need = float(
                    (df.loc[_d_idx:, "std_feed_kg"] * df.loc[_d_idx:, "adj_rate"]).sum()
                )  # 補正後標準値ベース
            cur_tank    = pred_tank[d]
            _debug_fc.append(f"d={d} tank={cur_tank:.0f} need={future_need:.0f} alert={min_alert}")
            if pred_tank[d] <= min_alert:
                if future_need - cur_tank <= 0:
                    pass  # 発注不要
                elif future_need - (cur_tank + std_qty) <= 0:
                    oq = round((future_need - cur_tank) / 1000) * 1000
                    oq = max(oq, 1000)
                    event_notes[d] = f"最終: {get_order_note_for_day(d, oq)}"
                else:
                    oq = std_qty
                    event_notes[d] = get_order_note_for_day(d, oq)
                delivery_kg[d] = oq
            evening_pred = pred_tank[d] + delivery_kg[d] - daily_feed

    df["_debug_fc"]       = [_debug_fc] + [None] * (len(df) - 1)
    df["pred_tank"]       = pred_tank
    df["real_tank"]       = real_tank
    df["delivery_kg"]     = delivery_kg
    df["event_notes"]     = event_notes
    df["act_feed_kg"]     = actual_feed
    # 採食累計: 補正後標準値（std_feed_kg × adj_rate）のみで統一
    # 実績採食時間は含めない（発注予測の基準値として一貫性を保つ）
    df["std_act_feed_kg"] = df["std_feed_kg"] * df["adj_rate"]
    df["cum_feed_kg"]     = df["std_act_feed_kg"].cumsum()
    df["cum_delivery_kg"] = df["delivery_kg"].cumsum()  # delivery_kg[0]=first_qty含む
    return df
ross_dict   = {(r["sex"], r["day"]): r for r in ross308}

def get_ross308(age):
    max_day = max(k[1] for k in ross_dict.keys()) if ross_dict else 56
    return ross_dict.get(("as_hatched", max(0, min(int(age), max_day))), {})

# ----------------------------------------------------------
# 対象選択
# ----------------------------------------------------------
st.title("▍ ブロイラー飼養管理 - 入力・発注予測")
tab1, tab2, tab3 = st.tabs(["◈ 記録と予測", "◈ 飼料発注", "◈ 飼育グラフ"])


with tab1:


    c1, c2, c3, c4, c5 = st.columns([2, 2, 2, 2, 1])
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
            if fh.get("chick_in_date") and (
                fh["flock_house_id"] == sel_fh_id
                or (fh["house_id"] == sel_fh_tmp["house_id"] and fh["lot_number_id"] == sel_ln_id)
            )
        ))
        if not chick_dates:
            chick_dates = [default_chick] if default_chick else ["未設定"]
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

    with c5:
        st.markdown("&nbsp;", unsafe_allow_html=True)  # ラベル分の余白
        _do_save = st.button("▶ 保存", type="primary", key="sheet_save", use_container_width=True)

    sel_fh    = next(fh for fh in lot_fhs if fh["flock_house_id"] == sel_fh_id)
    sel_house = next((h for h in houses if h["house_id"] == sel_fh["house_id"]), {})
    sel_ln    = next(ln for ln in lot_numbers if ln["lot_number_id"] == sel_ln_id)

    # 保存メッセージ表示
    if "sheet_msg" in st.session_state:
        msg_type, msg_text = st.session_state.pop("sheet_msg")
        if msg_type == "success":
            st.success(msg_text)
        else:
            st.error(msg_text)

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

    # daily_records を1回だけ取得（日次入力・発注予測・残存羽数で共用）
    existing_recs = supabase.table("daily_records") \
        .select("*") \
        .eq("flock_house_id", sel_fh_id) \
        .order("record_date").execute().data

    rec_by_date = {r["record_date"]: r for r in existing_recs}
    fc_recs     = existing_recs  # 発注予測用に共用

    # 残存羽数計算（取得済みデータから）
    total_mort = sum(r["mortality_count"] or 0 for r in existing_recs)
    total_cull = sum(r["culling_count"]   or 0 for r in existing_recs)
    remaining  = chick_in_count + spare_count - total_mort - total_cull

    st.dataframe(pd.DataFrame([{
        "タンクNo":        tank_number,
        "タンク容量(kg)":  tank_capacity,
        "搬送係数(kg/min)": house_coef,
        "入雛日":          str(chick_in_date),
        "入雛羽数":        f"{chick_in_count:,}",
        "スペア羽数":      f"{spare_count:,}",
        "出荷日齢":        f"{planned_age}日",
    }]), use_container_width=True, hide_index=True)

    # ----------------------------------------------------------
    # 全日齢分のDataFrameを構築（日齢0〜出荷日齢）
    # ----------------------------------------------------------
    # 銘柄名を短縮名（_以降）で管理
    brand_opts_short = {
        (b["brand_name"].split("_")[-1] if "_" in b["brand_name"] else b["brand_name"]): b["feed_brand_id"]
        for b in feed_brands
    }
    brand_names  = [""] + list(brand_opts_short.keys())
    worker_names = [""] + list(worker_opts.keys())

    rows = []
    cum_mort = 0
    cum_cull = 0

    for age in range(0, planned_age):  # 出荷日齢は含まない
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
        if brand_id:
            _brand_full = brand_map.get(brand_id, "")
            brand_nm = _brand_full.split("_")[-1] if "_" in _brand_full else _brand_full
        else:
            brand_nm = ""  # 納品がない日は空白
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
    # ---- 統合DataFrame: 日次入力＋発注予測 ----
    # ---- adj_dict（実測残量・調整発注）のセッション管理 ----
    adj_key = f"adj_dict_{sel_fh_id}"
    if adj_key not in st.session_state:
        st.session_state[adj_key] = {}
    # 日齢0のdeliveredは除去（初回投入はfirst_qtyで固定）
    if 0 in st.session_state.get(adj_key, {}):
        st.session_state[adj_key].pop(0, None)
    adj_dict = st.session_state[adj_key]

    # リセットボタン
    if adj_dict:
        if st.button("🔄 調整をリセット", key="fc_reset"):
            st.session_state[adj_key] = {}
            st.rerun()

    # df_allとdf_fcを日齢で結合
    fc_std_qty   = 4000.0   # 配送単位（kg）
    fc_min_alert = 200.0    # 最低残量アラート（kg）
    fc_lead_time = 0
    df_fc = run_feed_forecast(
        sel_fh, fc_recs, house_coef,
        fc_std_qty, fc_min_alert, fc_lead_time,
        adj_dict=adj_dict)

    # デバッグ
    with st.expander("🔍 発注デバッグ", expanded=False):
        st.write(f"adj_dict: {adj_dict}")
        st.write(f"adj_dictキー型: {[type(k).__name__ for k in adj_dict.keys()]}")
        st.write(f"初回投入量: {float(sel_fh.get('initial_feed_delivery_qty') or 0):.0f}kg")
        st.write(f"feed_correction_factor(DB): {sel_fh.get('feed_correction_factor')}")
        st.write(f"weighted_corr: {float(sel_fh.get('feed_correction_factor') or 1.0)}")
        # feed_order_detailsの実測残量確認
        _fod_debug = supabase.table("feed_order_details") \
            .select("delivery_date,actual_tank_remaining,order_qty") \
            .eq("flock_house_id", sel_fh_id) \
            .not_.is_("actual_tank_remaining", "null") \
            .execute().data
        st.write(f"feed_order_details実測残量: {_fod_debug}")
        # 区間ごとの補正率計算内容
        _dbg_rows = []
        _s0 = 0
        _s0_tank = float(sel_fh.get("initial_feed_delivery_qty") or 0)
        for _d_str, _v in sorted(adj_dict.items(), key=lambda x: int(x[0])):
            _d = int(_d_str)
            _e_tank = _v.get("actual_tank")
            if _e_tank is None: continue
            _std_sum = df_fc.loc[(df_fc["day"] >= _s0) & (df_fc["day"] < _d), "std_feed_kg"].sum()
            _consumed = _s0_tank - float(_e_tank)
            _rate = round(_consumed / _std_sum, 3) if _std_sum > 0 else 0
            _dbg_rows.append({"区間": f"{_s0}→{_d}", "s_tank": _s0_tank,
                              "e_tank": _e_tank, "consumed": round(_consumed,1),
                              "std_sum": round(_std_sum,1), "rate": _rate})
            _s0 = _d
            _s0_tank = float(_e_tank)
        if _dbg_rows:
            st.dataframe(pd.DataFrame(_dbg_rows), hide_index=True)
        st.dataframe(df_fc[["day","date_str","adj_rate","std_feed_kg","pred_tank","delivery_kg"]].round(3), hide_index=True)


    # 調整発注（adj_dictから）
    adj_delivery = {int(day): v.get("delivered") for day, v in adj_dict.items() if v.get("delivered")}

    # 実測残量
    def _get_real(row):
        d = int(row["day"])
        # adj_dictのキーは文字列または整数どちらの場合もある
        _v = adj_dict.get(d) or adj_dict.get(str(d))
        if _v and _v.get("actual_tank") is not None:
            return float(_v["actual_tank"])
        rt = row["real_tank"]
        return float(rt) if not (isinstance(rt, float) and np.isnan(rt)) else None

    # 日齢0（初期投入）の納品量・銘柄をdf_allに反映
    _day0_mask = df_all["_date"] == str(chick_in_date)
    if _day0_mask.any():
        _day0_idx = df_all[_day0_mask].index[0]
        _day0_qty = float(sel_fh.get("initial_feed_delivery_qty") or 0)
        if _day0_qty > 0:
            _day0_bid = next(
                (b["feed_brand_id"] for b in feed_brands
                 if b.get("is_active") and (b.get("age_from_days") or 0) == 0), None)
            _day0_full = brand_map.get(_day0_bid, "") if _day0_bid else ""
            _day0_bnm = _day0_full.split("_")[-1] if "_" in _day0_full else _day0_full
            df_all.at[_day0_idx, "納品量kg"] = _day0_qty
            df_all.at[_day0_idx, "飼料銘柄"] = _day0_bnm

    # 調整発注日の納品情報をdf_fcのevent_notesから取得してdf_allに反映
    # df_fcは前期絶対使用量を考慮した正しい銘柄情報を持っている
    for _adj_day_str, _adj_v in adj_dict.items():
        _adj_del = _adj_v.get("delivered")
        _adj_date = str(chick_in_date + timedelta(days=int(_adj_day_str)))
        _mask = df_all["_date"] == _adj_date
        if not _mask.any():
            continue
        _idx = df_all[_mask].index[0]
        if _adj_del and float(_adj_del) > 0:
            # df_fcのevent_notesから銘柄を取得（前期絶対使用量考慮済み）
            _fc_rows = df_fc[df_fc["day"] == int(_adj_day_str)]
            _note = _fc_rows["event_notes"].values[0] if len(_fc_rows) > 0 else ""
            # event_notesから銘柄名を抽出（例:「前期 4,000kg」→「前期」）
            _bnm = ""
            for _bn in brand_opts_short.keys():
                if _bn in _note:
                    _bnm = _bn
                    break
            df_all.at[_idx, "納品量kg"] = float(_adj_del)
            df_all.at[_idx, "飼料銘柄"] = _bnm
        else:
            # クリア（daily_recordsに実績がない場合のみ）
            if not rec_by_date.get(_adj_date, {}).get("feed_delivery_qty"):
                df_all.at[_idx, "納品量kg"] = None
                df_all.at[_idx, "飼料銘柄"] = ""

    # 統合DataFrameを構築
    today_day_fc = (date.today() - chick_in_date).days
    edit_df = pd.DataFrame({
        # 共通
        "日令":       df_fc["day"].astype(int),
        "月日":       df_fc.apply(
            lambda r: f"◀{r['date_str']}" if r["day"] == today_day_fc else r["date_str"], axis=1),
        # 日次入力列
        "斃死":       df_all["斃死"],
        "淘汰":       df_all["淘汰"],
        "合計":       df_all["合計"],
        "舎内最高℃": df_all["舎内最高℃"],
        "舎内最低℃": df_all["舎内最低℃"],
        "湿度%":      df_all["湿度%"],
        "外気最高℃": df_all["外気最高℃"],
        "外気最低℃": df_all["外気最低℃"],
        "平均体重g":  df_all["平均体重g"],
        "採食時間min":df_all["採食時間min"],
        "納品量kg":   df_all["納品量kg"],
        "飼料銘柄":   df_all["飼料銘柄"],
        "作業日誌":   df_all["作業日誌"],
        # 発注予測列
        "採食kg(予)": df_fc["act_feed_kg"].round(1),
        "標準採食kg": df_fc["std_feed_kg"].round(1),
        "採食累計kg": df_fc["cum_feed_kg"].round(0),
        "補正率":     df_fc["adj_rate"].round(3),
        "予測残量kg": df_fc["pred_tank"].round(0),
        "実測残量kg": df_fc.apply(_get_real, axis=1),
        "予定発注kg": df_fc["delivery_kg"].apply(lambda x: float(x) if x > 0 else None),
        "調整発注kg": df_fc["day"].apply(lambda d: adj_delivery.get(int(d))),
        "発注内容":   df_fc["event_notes"],
    })

    # 合計列を斃死+淘汰の累計で再計算
    _mort_cum = 0
    _cull_cum = 0
    for i in range(len(edit_df)):
        _v_mort = edit_df.at[i, "斃死"]
        _v_cull = edit_df.at[i, "淘汰"]
        _mort_cum += int(_v_mort) if _v_mort is not None and str(_v_mort) not in ("", "nan", "None") else 0
        _cull_cum += int(_v_cull) if _v_cull is not None and str(_v_cull) not in ("", "nan", "None") else 0
        edit_df.at[i, "合計"] = _mort_cum + _cull_cum

    # 統合data_editor
    st.markdown("<div style='margin-top:8px;'></div>", unsafe_allow_html=True)
    edited = st.data_editor(
        edit_df,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        height=12 * 28 + 38,
        column_config={
            # ── 共通 ──
            "日令":       st.column_config.NumberColumn("日\n令",      disabled=True, width=32),
            "月日":       st.column_config.TextColumn(  "月日",         disabled=True, width=44),
            # ── 日次入力 ──
            "斃死":       st.column_config.NumberColumn("斃\n死",      min_value=0, step=1, width=36),
            "淘汰":       st.column_config.NumberColumn("淘\n汰",      min_value=0, step=1, width=36),
            "合計":       st.column_config.NumberColumn("合\n計",      disabled=True, width=36),
            "舎内最高℃": st.column_config.NumberColumn("舎内\n最高℃", step=0.1, width=55),
            "舎内最低℃": st.column_config.NumberColumn("舎内\n最低℃", step=0.1, width=55),
            "湿度%":      st.column_config.NumberColumn("湿\n度%",     min_value=0.0, max_value=100.0, step=1.0, width=40),
            "外気最高℃": st.column_config.NumberColumn("外気\n最高℃", step=0.1, width=55),
            "外気最低℃": st.column_config.NumberColumn("外気\n最低℃", step=0.1, width=55),
            "平均体重g":  st.column_config.NumberColumn("平均\n体重g", step=1.0, width=55),
            "採食時間min":st.column_config.NumberColumn("採食\nmin",   step=0.1, format="%.1f", width=50),
            "納品量kg":   st.column_config.NumberColumn("納品\nkg",    step=100.0, width=55),
            "飼料銘柄":   st.column_config.SelectboxColumn("飼料\n銘柄", options=brand_names, width=80, disabled=True),
            "作業日誌":   st.column_config.TextColumn(  "作業日誌",     width=120),
            # ── 発注予測 ──
            "採食kg(予)": st.column_config.NumberColumn("採食\nkg(予)", disabled=True, width=55),
            "標準採食kg": st.column_config.NumberColumn("標準\n採食kg", disabled=True, width=55),
            "採食累計kg": st.column_config.NumberColumn("累計\nkg",    disabled=True, width=55),
            "補正率":     st.column_config.NumberColumn("補\n正率",    disabled=True, width=42),
            "予測残量kg": st.column_config.NumberColumn("予測\n残量kg", disabled=True, width=60),
            "実測残量kg": st.column_config.NumberColumn("実測\n残量kg", step=100.0, width=60),
            "予定発注kg": st.column_config.NumberColumn("予定\n発注kg", disabled=True, width=60),
            "調整発注kg": st.column_config.NumberColumn("調整\n発注kg", step=1000.0, width=60),
            "発注内容":   st.column_config.TextColumn(  "発注内容",     disabled=True, width=150),
        },
        key=f"unified_editor_{sel_fh_id}"
    )

    # ---- Step5: 実測残量・調整発注の変更を即時検知→再計算 ----
    # セルの現在値のみで判断（adj_dictの引き継ぎはしない）
    new_adj = {}
    for i, row in edited.iterrows():
        day   = int(row["日令"])
        entry = {}
        # 実測残量: セルに値があれば保存、空欄なら削除（元の予測に戻す）
        new_real = row.get("実測残量kg")
        if new_real is not None and pd.notna(new_real) and day > 0:
            entry["actual_tank"] = float(new_real)
        # 調整発注: 日齢0は除外、セルに値があれば保存
        if day > 0:
            new_del = row.get("調整発注kg")
            if new_del is not None and pd.notna(new_del) and float(new_del) > 0:
                entry["delivered"] = float(new_del)
        if entry:
            new_adj[str(day)] = entry  # キーを文字列に統一

    if new_adj != adj_dict:
        st.session_state[adj_key] = new_adj
        # 調整発注が変更された日の納品量・銘柄をdaily_recordsに即時反映
        for _day_str, _v in new_adj.items():
            _day = int(_day_str)
            _new_del = _v.get("delivered")
            _old_del = adj_dict.get(_day_str, {}).get("delivered") if isinstance(_day_str, str) else adj_dict.get(_day, {}).get("delivered")
            # 調整発注が変更・削除された場合
            if _new_del != _old_del:
                _del_date = str(chick_in_date + timedelta(days=_day))
                _exists = supabase.table("daily_records") \
                    .select("daily_record_id") \
                    .eq("flock_house_id", sel_fh_id) \
                    .eq("record_date", _del_date).execute().data
                if _new_del and _new_del > 0:
                    # 発注内容から銘柄を取得
                    _fod = supabase.table("feed_order_details") \
                        .select("event_notes,feed_brand_id") \
                        .eq("flock_house_id", sel_fh_id) \
                        .eq("delivery_date", _del_date) \
                        .order("detail_id", desc=True).limit(1).execute().data
                    _notes   = re.sub(r"^\[.*?\]\s*", "", re.sub(r"^(最終|納品): ", "", _fod[0].get("event_notes") or "")) if _fod else ""
                    _brand   = _fod[0].get("feed_brand_id") if _fod else None
                    _upd = {"feed_delivery_qty": float(_new_del), "feed_brand_id": _brand, "feed_order_notes": _notes}
                else:
                    # クリア
                    _upd = {"feed_delivery_qty": None, "feed_brand_id": None, "feed_order_notes": None}
                if _exists:
                    supabase.table("daily_records").update(_upd) \
                        .eq("daily_record_id", _exists[0]["daily_record_id"]).execute()
                elif _new_del and _new_del > 0:
                    supabase.table("daily_records").insert({
                        **_upd, "flock_house_id": sel_fh_id, "record_date": _del_date
                    }).execute()
        # 削除された調整発注をクリア
        for _day in list(adj_dict.keys()):
            _day_int = int(_day)
            if _day_int not in [int(k) for k in new_adj.keys()]:
                _old_del = adj_dict[_day].get("delivered")
                if _old_del:
                    _del_date = str(chick_in_date + timedelta(days=_day_int))
                    _exists = supabase.table("daily_records") \
                        .select("daily_record_id") \
                        .eq("flock_house_id", sel_fh_id) \
                        .eq("record_date", _del_date).execute().data
                    if _exists:
                        supabase.table("daily_records").update({
                            "feed_delivery_qty": None, "feed_brand_id": None, "feed_order_notes": None
                        }).eq("daily_record_id", _exists[0]["daily_record_id"]).execute()
        st.rerun()

    # ---- 保存処理 ----
    _do_fc_save = _do_save  # 一括保存と連動
    if _do_save:
        updated  = 0
        inserted = 0
        skipped  = 0
        errors   = []
        has_data_debug = []

        for i, row in edited.iterrows():
            orig = df_all.iloc[i]
            rec_date = chick_in_date + timedelta(days=int(row["日令"]))
            rec_id   = orig.get("_id")
            brand_id = next((bid for bname, bid in brand_opts_short.items()
                             if bname == row.get("飼料銘柄")), None) if row.get("飼料銘柄") else None

            has_data = any([
                pd.notna(row["斃死"])        and row["斃死"]        != 0,
                pd.notna(row["淘汰"])        and row["淘汰"]        != 0,
                pd.notna(row["舎内最高℃"]),
                pd.notna(row["採食時間min"]) and row["採食時間min"] != 0,
                pd.notna(row.get("納品量kg")) and row.get("納品量kg") != 0,
                bool(row.get("作業日誌")),
            ])
            # 既存レコードがある場合は納品量クリアのために必ず処理（skipしない）
            has_existing = rec_id and pd.notna(rec_id)
            if not has_data and not has_existing:
                skipped += 1
                continue

            # 納品量入力時にfeed_order_detailsから発注内容をコピー
            _delivery_qty = float(row["納品量kg"]) if pd.notna(row.get("納品量kg")) and row.get("納品量kg") else None
            _order_notes  = None
            _brand_id_del = None
            if _delivery_qty and _delivery_qty > 0:
                _fod = supabase.table("feed_order_details") \
                    .select("event_notes,feed_brand_id") \
                    .eq("flock_house_id", sel_fh_id) \
                    .eq("delivery_date", str(rec_date)) \
                    .order("detail_id", desc=True).limit(1).execute().data
                if _fod:
                    _raw_notes   = _fod[0].get("event_notes") or ""
                    _order_notes = re.sub(r"^\[.*?\]\s*", "", re.sub(r"^(最終|納品): ", "", _raw_notes))
                    _brand_id_del = _fod[0].get("feed_brand_id")
            # 納品量が空欄の場合はNULLでクリア（前日移動に対応）
            _delivery_qty_save = _delivery_qty if _delivery_qty and _delivery_qty > 0 else None
            _brand_save  = _brand_id_del if _delivery_qty_save else None
            _notes_save  = _order_notes  if _delivery_qty_save else None

            data_update = {
                "mortality_count":   int(row["斃死"])  if pd.notna(row["斃死"])  else 0,
                "culling_count":     int(row["淘汰"])  if pd.notna(row["淘汰"])  else 0,
                "house_temp_max":    float(row["舎内最高℃"]) if pd.notna(row["舎内最高℃"]) else None,
                "house_temp_min":    float(row["舎内最低℃"]) if pd.notna(row["舎内最低℃"]) else None,
                "house_humidity":    float(row["湿度%"])      if pd.notna(row["湿度%"])      else None,
                "outside_temp_max":  float(row["外気最高℃"]) if pd.notna(row["外気最高℃"]) else None,
                "outside_temp_min":  float(row["外気最低℃"]) if pd.notna(row["外気最低℃"]) else None,
                "avg_body_weight":   float(row["平均体重g"])  if pd.notna(row["平均体重g"])  and float(row["平均体重g"] or 0) > 0 else None,
                "feed_duration_min": float(row["採食時間min"]) if pd.notna(row["採食時間min"]) and float(row["採食時間min"] or 0) > 0 else None,
                "work_log":          str(row["作業日誌"]) if pd.notna(row.get("作業日誌")) and row["作業日誌"] else None,
                "feed_delivery_qty": _delivery_qty_save,
                "feed_brand_id":     _brand_save,
                "feed_order_notes":  _notes_save,
            }
            data_insert = {**data_update, "flock_house_id": sel_fh_id, "record_date": rec_date}

            try:
                if rec_id and pd.notna(rec_id):
                    supabase.table("daily_records").update(data_update) \
                        .eq("daily_record_id", int(rec_id)).execute()
                    updated += 1
                else:
                    supabase.table("daily_records").insert(data_insert).execute()
                    inserted += 1
            except Exception as e:
                errors.append(str(e))


        # メッセージ
        msg = []
        if errors:
            st.session_state["sheet_msg"] = ("error", f"エラー: {'; '.join(errors[:3])}")
        else:
            if updated  > 0: msg.append(f"更新 {updated}件")
            if inserted > 0: msg.append(f"新規 {inserted}件")
            if skipped  > 0: msg.append(f"未入力スキップ {skipped}件")
            st.session_state["sheet_save_msg"] = msg

        # order_plan: df_fcから発注予定行を抽出
        order_plan = df_fc[df_fc["delivery_kg"] > 0].copy()
        if not order_plan.empty:
            order_plan["納品予定日_dt"] = order_plan["date_obj"]
            order_plan["納品予定日"]    = order_plan["date_str"]
            order_plan["日齢"]          = order_plan["day"].astype(int)
            order_plan["発注量kg"]      = order_plan["delivery_kg"].round(0)
            order_plan["発注種別"]      = order_plan["event_notes"]
            order_plan["タンク残量kg"]  = order_plan["pred_tank"].round(0)

        # 発注予測の保存（_do_fc_save連動）
        if _do_fc_save and not order_plan.empty:
            try:
                # adj_dictの実測残量をfeed_order_detailsに保存
                for _day_str, _v in new_adj.items():
                    _act_tank = _v.get("actual_tank")
                    if _act_tank is None:
                        continue
                    _act_date = str(chick_in_date + timedelta(days=int(_day_str)))
                    _exist = supabase.table("feed_order_details") \
                        .select("detail_id") \
                        .eq("flock_house_id", sel_fh_id) \
                        .eq("delivery_date", _act_date) \
                        .limit(1).execute().data
                    if _exist:
                        supabase.table("feed_order_details").update({
                            "actual_tank_remaining": float(_act_tank),
                        }).eq("detail_id", _exist[0]["detail_id"]).execute()
                    else:
                        supabase.table("feed_order_details").insert({
                            "flock_house_id":        sel_fh_id,
                            "delivery_date":         _act_date,
                            "actual_tank_remaining": float(_act_tank),
                            "order_qty":             0,
                            "status":                "実測",
                        }).execute()

                # 今日以降の予定配送を削除（actual_tank_remainingが入力済みは保持）
                _del_targets = supabase.table("feed_order_details") \
                    .select("detail_id,actual_tank_remaining") \
                    .eq("flock_house_id", sel_fh_id) \
                    .gte("delivery_date", str(date.today())) \
                    .execute().data
                _del_ids = [r["detail_id"] for r in _del_targets
                            if r.get("actual_tank_remaining") is None]
                if _del_ids:
                    supabase.table("feed_order_details").delete().in_("detail_id", _del_ids).execute()

                # 予定配送を再登録
                for _, r in order_plan.iterrows():
                    dt = r["納品予定日_dt"]
                    if hasattr(dt, "date"): dt = dt.date()
                    supabase.table("feed_order_details").insert({
                        "order_id":       None,
                        "flock_house_id": sel_fh_id,
                        "feed_brand_id":  None,
                        "order_qty":      float(r["発注量kg"]),
                        "tank_remaining": float(r["タンク残量kg"]),
                        "delivery_date":  str(dt),
                        "day_age":        int(r["日齢"]),
                        "event_notes":    str(r["発注種別"]),
                        "pred_tank":      float(r["タンク残量kg"]),
                        "status":         "予定",
                    }).execute()

                _sheet_msg = st.session_state.pop("sheet_save_msg", [])
                _fc_msg    = f"予定配送 {len(order_plan)}件保存"
                _all_msg   = _sheet_msg + [_fc_msg]
                st.session_state["sheet_msg"] = ("success", f"✅ {' / '.join(_all_msg)}")
                st.rerun()
            except Exception as e:
                st.error(f"保存エラー: {e}")

with tab2:
    st.markdown("### ◆ 発注")

    # ---- 農場をチェックボックスで複数選択 ----
    st.markdown("**対象農場を選択**")
    _farm_cols = st.columns(min(len(farms), 6))
    _sel_farm_ids = []
    for i, f in enumerate(farms):
        if _farm_cols[i % len(_farm_cols)].checkbox(
                f["farm_name"], value=True, key=f"o_farm_chk_tab3_{f['farm_id']}"):
            _sel_farm_ids.append(f["farm_id"])

    if not _sel_farm_ids:
        st.warning("農場を1つ以上選択してください")
        st.stop()

    o_farm_ln_ids = {ln["lot_number_id"] for ln in lot_numbers if ln["farm_id"] in _sel_farm_ids}
    o_farm_fhs    = [fh for fh in flock_houses
                     if fh["lot_number_id"] in o_farm_ln_ids and fh.get("status") == "育成中"]

    if not o_farm_fhs:
        st.info("育成中の鶏舎がありません")
    else:
        import pandas as pd
        o_fh_ids  = [fh["flock_house_id"] for fh in o_farm_fhs]
        o_details = []
        for fh_id in o_fh_ids:
            rows = supabase.table("feed_order_details") \
                .select("*") \
                .eq("flock_house_id", fh_id) \
                .in_("status", ["予定", "発注済"]) \
                .order("delivery_date").execute().data
            # order_qty>0かつ、同一日付で最新のレコードのみ保持
            _seen_dates = {}
            for r in rows:
                if r.get("delivery_date") and float(r.get("order_qty") or 0) > 0:
                    _dt = r["delivery_date"]
                    # 同一日付は最新(detail_id大)を優先
                    if _dt not in _seen_dates or r["detail_id"] > _seen_dates[_dt]["detail_id"]:
                        _seen_dates[_dt] = r
            rows = list(_seen_dates.values())
            for row in rows:
                fh_obj = next((f for f in o_farm_fhs if f["flock_house_id"] == fh_id), {})
                ln_obj = next((ln for ln in lot_numbers if ln["lot_number_id"] == fh_obj.get("lot_number_id")), {})
                row["house_name"] = house_map.get(fh_obj.get("house_id"), "")
                row["lot_number"] = ln_obj.get("lot_number", "")
                o_details.append(row)

        if o_details:
            _all_dates = [date.fromisoformat(r["delivery_date"]) for r in o_details if r.get("delivery_date")]
            _min_date  = min(_all_dates) if _all_dates else date.today()
            _max_date  = max(_all_dates) if _all_dates else date.today() + timedelta(days=14)
        else:
            _min_date = date.today()
            _max_date = date.today() + timedelta(days=14)

        _farm_change_key = "o_farm_prev"
        _sel_ids_str = str(sorted(_sel_farm_ids))
        if st.session_state.get(_farm_change_key) != _sel_ids_str:
            st.session_state["o_from_val"] = _min_date
            st.session_state["o_to_val"]   = _max_date
            st.session_state[_farm_change_key] = _sel_ids_str
            st.session_state.pop("o_order_id", None)
            st.session_state.pop("o_order_text", None)

        oc1, oc2, oc3, oc4 = st.columns([2, 2, 2, 1])
        with oc1:
            o_from = st.date_input("納品範囲（開始）",
                value=st.session_state.get("o_from_val", _min_date), key="o_from_t3")
            st.session_state["o_from_val"] = o_from
        with oc2:
            o_to   = st.date_input("納品範囲（終了）",
                value=st.session_state.get("o_to_val", _max_date), key="o_to_t3")
            st.session_state["o_to_val"] = o_to
        with oc3:
            o_order_date = st.date_input("発注日", value=date.today(), key="o_order_date_t3")
        with oc4:
            st.markdown("&nbsp;", unsafe_allow_html=True)
            _do_order_save_top = st.button("▶ 発注確定登録", type="primary",
                key="o_save_top_t3", use_container_width=True)

        if not o_details:
            st.info("予定配送が登録されていません。タブ1の発注予測でシミュレーションを実行してください。")
        else:
            df_od = pd.DataFrame(o_details)
            df_od["delivery_date_dt"] = pd.to_datetime(df_od["delivery_date"]).dt.date
            df_sel = df_od[
                (df_od["delivery_date_dt"] >= o_from) &
                (df_od["delivery_date_dt"] <= o_to)
            ].copy()

            if df_sel.empty:
                st.info(f"指定期間（{o_from}〜{o_to}）に予定配送がありません")
            else:
                o_total = df_sel["order_qty"].sum()
                ob_info, ob_btn = st.columns([3, 1])
                ob_info.markdown(f"**対象: {len(df_sel)}件　合計: {o_total:,.0f} kg**")

                disp_sel = df_sel[["delivery_date","house_name","order_qty","event_notes","status"]].copy()
                # event_notesの[確定]タグとメーカー名を除去
                def _clean_notes(x):
                    if not x: return ""
                    x = re.sub(r"^\[.*?\]\s*", "", str(x))  # [確定]等を除去
                    x = re.sub(r"[^ ＋,0-9kgA-Za-z仕上前期中期最終]+_", "", x)  # メーカー名を除去
                    return x.strip()
                disp_sel["event_notes"] = disp_sel["event_notes"].apply(_clean_notes)
                disp_sel.columns = ["納品予定日","鶏舎","発注量kg","発注内容","状況"]
                disp_sel = disp_sel.sort_values("納品予定日").reset_index(drop=True)
                st.dataframe(disp_sel, use_container_width=True, hide_index=True)

                obc1, obc2 = st.columns([1, 3])

                with obc1:
                    if _do_order_save_top:
                        try:
                            res = supabase.table("feed_orders").insert({
                                "farm_id":         _sel_farm_ids[0],
                                "order_date":      str(o_order_date),
                                "delivery_date":   str(o_from),
                                "lead_time_days":  0,
                                "total_order_qty": float(o_total),
                                "status":          "発注済",
                            }).execute()
                            o_order_id = res.data[0]["order_id"]
                            for detail_id in df_sel["detail_id"].tolist():
                                supabase.table("feed_order_details").update({
                                    "order_id": o_order_id,
                                    "status":   "発注済",
                                }).eq("detail_id", detail_id).execute()

                            _o_farm_names = ", ".join(f["farm_name"] for f in farms if f["farm_id"] in _sel_farm_ids)
                            o_lines = [f"【飼料発注】{_o_farm_names}", f"発注日: {o_order_date}", "",
                                       f"{'納品予定日':<12}{'タンクNo':<10}{'発注量(kg)':<12}{'発注内容'}", "-" * 60]
                            for _, dr in df_sel.sort_values("delivery_date").iterrows():
                                tank_no = fh_tank_map.get(dr.get("flock_house_id"), "-")
                                o_lines.append(
                                    f"{dr['delivery_date']:<12}{str(tank_no):<10}"
                                    f"{dr['order_qty']:>8,.0f}kg    {dr['event_notes'] or ''}")

                            # daily_recordsに反映
                            for _, dr in df_sel.iterrows():
                                try:
                                    _del_date = str(dr["delivery_date"])
                                    _fh_id    = int(dr["flock_house_id"])
                                    _qty      = float(dr["order_qty"])
                                    _notes    = re.sub(r"^\[.*?\]\s*|^最終:\s*|^納品:\s*", "", str(dr.get("event_notes") or ""))
                                    _brand_id = next(
                                        (b["feed_brand_id"] for b in feed_brands
                                         if b.get("brand_name") and b["brand_name"] in _notes), None)
                                    _exists = supabase.table("daily_records") \
                                        .select("daily_record_id") \
                                        .eq("flock_house_id", _fh_id) \
                                        .eq("record_date", _del_date).execute().data
                                    if _exists:
                                        supabase.table("daily_records").update({
                                            "feed_delivery_qty": _qty,
                                            "feed_brand_id":     _brand_id,
                                        }).eq("daily_record_id", _exists[0]["daily_record_id"]).execute()
                                    else:
                                        supabase.table("daily_records").insert({
                                            "flock_house_id":    _fh_id,
                                            "record_date":       _del_date,
                                            "feed_delivery_qty": _qty,
                                            "feed_brand_id":     _brand_id,
                                        }).execute()
                                except Exception:
                                    pass

                            o_order_text_new = "\n".join(o_lines)
                            st.session_state["o_order_id"]   = o_order_id
                            st.session_state["o_order_text"] = o_order_text_new
                            st.rerun()
                        except Exception as e:
                            st.error(f"登録エラー: {e}")

                with obc2:
                    if st.session_state.get("o_order_text"):
                        st.markdown("#### ▸ 発注書プレビュー")
                        o_body_text = st.text_area("発注書（編集可）",
                            value=st.session_state.get("o_order_text", ""),
                            height=250, key="o_body_t3")
                    else:
                        o_body_text = ""
                    if st.session_state.get("o_order_id"):
                        st.markdown("#### ▸ 送信")
                        send_method = st.radio("送信方法",
                            ["📧 メール", "🖨️ 印刷"],
                            horizontal=True, key="o_send_method_t3")

                        if send_method == "📧 メール":
                            o_email_settings = supabase.table("email_settings").select("*").execute().data
                            o_farm_es = [es for es in o_email_settings
                                         if es.get("farm_id") in _sel_farm_ids or es.get("farm_id") is None]
                            if not o_farm_es:
                                st.warning("メール設定がありません")
                            else:
                                o_setting_opts = {es["setting_name"]: es for es in o_farm_es}
                                o_es      = o_setting_opts[st.selectbox("送信先設定",
                                    list(o_setting_opts.keys()), key="o_email_sel_t3")]
                                o_to_addr = st.text_input("宛先", value=o_es.get("to_address",""), key="o_to_addr_t3")
                                o_cc_addr = st.text_input("CC",  value=o_es.get("cc_address",""),  key="o_cc_t3")
                                o_subject = st.text_input("件名",
                                    value=f"【飼料発注】{o_order_date}", key="o_subject_t3")
                                if st.button("▶ メール送信", key="o_send_email_t3", type="primary"):
                                    try:
                                        import smtplib
                                        from email.mime.text import MIMEText
                                        from email.mime.multipart import MIMEMultipart
                                        smtp_host = st.secrets.get("smtp",{}).get("host","")
                                        smtp_port = int(st.secrets.get("smtp",{}).get("port",587))
                                        smtp_user = st.secrets.get("smtp",{}).get("user","")
                                        smtp_pass = st.secrets.get("smtp",{}).get("password","")
                                        if not smtp_host:
                                            st.error("SMTP設定がありません")
                                        else:
                                            msg = MIMEMultipart()
                                            msg["From"]    = smtp_user
                                            msg["To"]      = o_to_addr
                                            if o_cc_addr: msg["Cc"] = o_cc_addr
                                            msg["Subject"] = o_subject
                                            msg.attach(MIMEText(o_body_text, "plain", "utf-8"))
                                            rcpts = [a.strip() for a in o_to_addr.split(",")]
                                            if o_cc_addr:
                                                rcpts += [a.strip() for a in o_cc_addr.split(",")]
                                            with smtplib.SMTP(smtp_host, smtp_port) as sv:
                                                sv.starttls()
                                                sv.login(smtp_user, smtp_pass)
                                                sv.sendmail(smtp_user, rcpts, msg.as_string())
                                            st.success(f"✅ 送信完了 → {o_to_addr}")
                                    except Exception as e:
                                        st.error(f"送信エラー: {e}")

                        else:  # 印刷
                            _rows_html = ""
                            for _, r in df_sel.sort_values("delivery_date").iterrows():
                                _tank_no = fh_tank_map.get(r.get("flock_house_id"), "-")
                                _rows_html += f"""<tr>
                                    <td>{r['delivery_date']}</td>
                                    <td class="tank">{_tank_no}</td>
                                    <td style="text-align:right">{r['order_qty']:,.0f}</td>
                                    <td class="notes">{r['event_notes'] or ''}</td>
                                </tr>"""
                            _o_farm_names2 = ", ".join(f["farm_name"] for f in farms if f["farm_id"] in _sel_farm_ids)
                            _print_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>発注書</title>
<style>
  body {{ font-family: 'Source Sans Pro','Noto Sans JP',sans-serif; font-size: 12pt; padding: 20mm; }}
  h2 {{ font-size: 14pt; margin-bottom: 4px; }}
  p {{ margin: 2px 0; font-size: 11pt; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
  th {{ background: #f0f0f0; border: 1px solid #999; padding: 6px 8px; font-size: 11pt; text-align: left; white-space: nowrap; }}
  th.tank {{ width: 60px; }}
  td {{ border: 1px solid #999; padding: 5px 8px; white-space: nowrap; overflow: hidden; font-size: 11pt; }}
  td.tank {{ width: 60px; }}
  td.notes {{ font-size: 11pt; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 250px; }}
  tr {{ height: 24px; max-height: 24px; }}
  .footer {{ margin-top: 20px; font-size: 11pt; }}
  @media print {{ button {{ display: none; }} }}
</style>
</head><body>
<h2>【飼料発注】{_o_farm_names2}</h2>
<p>発注日: {o_order_date}</p>
<table>
  <thead><tr>
    <th>納品予定日</th><th class="tank">タンクNo</th><th style="text-align:right">発注量(kg)</th><th>発注内容</th>
  </tr></thead>
  <tbody>{_rows_html}</tbody>
</table>
<div class="footer"><p>よろしくお願いいたします。</p><p>{_o_farm_names2}</p></div>
<br>
<button onclick="window.print()" style="padding:8px 20px;font-size:12pt;cursor:pointer;">▶ 印刷</button>
</body></html>"""
                            import urllib.parse
                            _encoded = urllib.parse.quote(_print_html, safe='')
                            st.components.v1.html(f"""
<button onclick="
  var w=window.open('','_blank');
  w.document.open('text/html','replace');
  w.document.write(decodeURIComponent('{_encoded}'));
  w.document.close();
  setTimeout(function(){{w.focus();}},300);
" style="background:#1f77b4;color:white;border:none;padding:10px 24px;border-radius:4px;cursor:pointer;font-size:14px;">
▶ 印刷プレビュー
</button>""", height=55)


with tab3:
    st.markdown("### ◆ 推移グラフ")

    gc1, gc2, gc3 = st.columns(3)
    with gc1:
        g_farm    = st.selectbox("農場", list(farm_opts.keys()), key="g_farm")
        g_farm_id = farm_opts[g_farm]
    with gc2:
        # 農場内の全鶏舎（flock_housesから直接）
        g_farm_ln_ids = {ln["lot_number_id"] for ln in lot_numbers if ln["farm_id"] == g_farm_id}
        g_all_fhs = [fh for fh in flock_houses if fh["lot_number_id"] in g_farm_ln_ids]
        if not g_all_fhs:
            st.info("鶏舎がありません")
            st.stop()
        g_house_opts = {}
        for fh in g_all_fhs:
            hn = house_map.get(fh["house_id"], str(fh["house_id"]))
            if hn not in g_house_opts:
                g_house_opts[hn] = fh["house_id"]
        g_house_name = st.selectbox("鶏舎", list(g_house_opts.keys()), key="g_house")
        g_house_id   = g_house_opts[g_house_name]
    with gc3:
        # 選択した鶏舎の入雛日一覧
        g_fhs = [fh for fh in g_all_fhs if fh["house_id"] == g_house_id]
        if not g_fhs:
            st.info("データがありません")
            st.stop()
        g_fh_id = st.selectbox("入雛日",
            [fh["flock_house_id"] for fh in g_fhs],
            format_func=lambda x: next(
                (fh["chick_in_date"] for fh in g_fhs if fh["flock_house_id"] == x), ""),
            key="g_fh")

    g_recs = supabase.table("daily_records") \
        .select("*").eq("flock_house_id", g_fh_id) \
        .order("record_date").execute().data

    if g_recs:
        g_fh_obj   = next(fh for fh in g_fhs if fh["flock_house_id"] == g_fh_id)
        g_chick_dt = date.fromisoformat(g_fh_obj["chick_in_date"])
        df_g       = pd.DataFrame(g_recs)
        df_g["日齢"] = df_g["record_date"].apply(
            lambda d: (date.fromisoformat(d) - g_chick_dt).days)
        df_g["標準採食量_g"] = df_g["日齢"].apply(lambda a: get_ross308(a).get("daily_intake_g"))
        df_g["標準体重_g"]   = df_g["日齢"].apply(lambda a: get_ross308(a).get("weight_g"))

        g_item = st.selectbox("グラフ項目",
            ["体重（実績 vs 標準）","採食量（実績 vs 標準）","斃死+淘汰率","温度・湿度"],
            key="g_item")

        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.font_manager as fm
        import os, urllib.request

        # 日本語フォント設定
        font_path = "/tmp/NotoSansJP.ttf"
        if not os.path.exists(font_path):
            try:
                urllib.request.urlretrieve(
                    "https://moji.or.jp/wp-content/ipafont/IPAexfont/ipaexg00401.zip",
                    "/tmp/ipaexg.zip")
                import zipfile
                with zipfile.ZipFile("/tmp/ipaexg.zip") as z:
                    for name in z.namelist():
                        if name.endswith(".ttf"):
                            with z.open(name) as src, open(font_path, "wb") as dst:
                                dst.write(src.read())
                            break
            except:
                font_path = None

        if font_path and os.path.exists(font_path):
            try:
                fm.fontManager.addfont(font_path)
                prop = fm.FontProperties(fname=font_path)
                plt.rcParams["font.family"] = prop.get_name()
            except:
                plt.rcParams["font.family"] = "DejaVu Sans"
        else:
            plt.rcParams["font.family"] = "DejaVu Sans"

        x_max   = int(g_fh_obj.get("planned_shipment_age_days") or 56)
        x_ticks = list(range(0, x_max + 1, 7))
        std_ages = list(range(0, min(x_max + 1, 57)))
        std_bw   = [get_ross308(a).get("weight_g")       for a in std_ages]
        std_fi   = [get_ross308(a).get("daily_intake_g") for a in std_ages]
        chick_in_total = g_fh_obj["chick_in_count"] or 0

        fig, ax = plt.subplots(figsize=(10, 4))

        if g_item == "体重（実績 vs 標準）":
            w = df_g[df_g["avg_body_weight"].notna()]
            ax.plot(w["日齢"], w["avg_body_weight"], "o-",
                    label="Actual (g)", color="steelblue", linewidth=1.5)
            ax.plot(std_ages, std_bw, "--",
                    label="Ross308 Standard (g)", color="orange", alpha=0.8)
            ax.set_ylabel("Body Weight (g)")
            ax.set_title("Body Weight - Actual vs Ross308")

        elif g_item == "採食量（実績 vs 標準）":
            _g_h     = next((h for h in houses if h["house_id"] == g_fh_obj["house_id"]), {})
            _g_hcoef = float(_g_h.get("feed_transfer_coef") or 0)
            _g_ratio_map = {b["feed_brand_id"]: float(b.get("transfer_coef_ratio") or 1.0)
                            for b in feed_brands}
            cum_loss = 0
            rem_list = []
            spare    = g_fh_obj["spare_count"] or 0
            for _, row in df_g.iterrows():
                cum_loss += (row["mortality_count"] or 0) + (row["culling_count"] or 0)
                rem_list.append(max(chick_in_total + spare - cum_loss, 1))
            df_g["残存羽数"] = rem_list
            def per_bird(r):
                if pd.isna(r.get("feed_duration_min")) or _g_hcoef == 0:
                    return None
                ratio = _g_ratio_map.get(r.get("feed_brand_id"), 1.0)
                return round(float(r["feed_duration_min"]) * _g_hcoef * ratio * 1000 / r["残存羽数"], 2)
            df_g["実績採食量_g/羽"] = df_g.apply(per_bird, axis=1)
            ax.bar(df_g["日齢"], df_g["実績採食量_g/羽"].fillna(0.0),
                   label="Actual (g/bird)", color="steelblue", alpha=0.7)
            ax.plot(std_ages, std_fi, "--",
                    label="Ross308 Standard (g/bird)", color="orange", linewidth=1.5)
            ax.set_ylabel("Feed Intake per Bird (g)")
            ax.set_title("Feed Intake per Bird - Actual vs Ross308")

        elif g_item == "斃死+淘汰率":
            df_g["斃死淘汰率%"] = (
                (df_g["mortality_count"].fillna(0) + df_g["culling_count"].fillna(0))
                / chick_in_total * 100
            ).round(3)
            ax.bar(df_g["日齢"], df_g["斃死淘汰率%"], color="tomato", alpha=0.8,
                   label="Mort+Cull Rate (%)")
            ax.set_ylabel("Mortality+Culling Rate (%)")
            ax.set_title("Mortality + Culling Rate (%)")

        elif g_item == "温度・湿度":
            ax2 = ax.twinx()
            ax.plot(df_g["日齢"], df_g["house_temp_max"], "r-",
                    label="House Max (C)", linewidth=1.5)
            ax.plot(df_g["日齢"], df_g["house_temp_min"], "b-",
                    label="House Min (C)", linewidth=1.5)
            # Ross308推奨温度（体重ベース・RH60%）
            if comfort_temp:
                comfort_upper = []
                for age in std_ages:
                    bw = get_ross308(age).get("weight_g") or 0
                    closest = min(comfort_temp,
                        key=lambda r: abs((r["body_weight_g"] or 0) - bw))
                    comfort_upper.append(closest.get("rh_60pct_temp_c"))
                ax.plot(std_ages, comfort_upper, "r--", alpha=0.5,
                        label="Ross308 Comfort Temp (RH60%)", linewidth=1.2)
            ax2.plot(df_g["日齢"], df_g["house_humidity"], "g-",
                     label="Humidity (%)", linewidth=1.5, alpha=0.8)
            ax2.set_ylabel("Humidity (%)", color="green")
            ax2.tick_params(axis="y", labelcolor="green")
            ax2.set_ylim(0, 100)
            ax.set_ylabel("Temperature (C)")
            ax.set_title("Temperature & Humidity")
            lines1, labels1 = ax.get_legend_handles_labels()
            lines2, labels2 = ax2.get_legend_handles_labels()
            ax.legend(lines1 + lines2, labels1 + labels2, loc="upper right", fontsize=8)

        ax.set_xlabel("Age (days)")
        ax.set_xlim(0, x_max)
        ax.set_xticks(x_ticks)
        ax.grid(True, alpha=0.3)
        if g_item != "温度・湿度":
            ax.legend()
        st.pyplot(fig)
        plt.close()
    else:
        st.info("記録がありません")
