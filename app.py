import os
import json
import numpy as np
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta, date
from PIL import Image, ImageDraw, ImageFont
import matplotlib.pyplot as plt

# ページのレイアウト設定（画面を広く使う）
st.set_page_config(layout="wide", page_title="鶏舎飼料管理システム")

# ==============================================================================
# 🎯 日本語フォントのセットアップ（Windows / Mac / Linux 自動判定）
# ==============================================================================
def find_japanese_font():
    candidates = [
        # Windows標準フォント
        "C:/Windows/Fonts/meiryo.ttc",
        "C:/Windows/Fonts/YuGothM.ttc",
        "C:/Windows/Fonts/YuGothR.ttc",
        "C:/Windows/Fonts/msgothic.ttc",
        # Mac標準フォント
        "/System/Library/Fonts/ヒラギノ角ゴシック W4.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        # Linux（Streamlit Cloud等）
        "/usr/share/fonts/truetype/vlgothic/VL-Gothic-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    # Linux環境で見つからない場合はインストールを試みる（Streamlit Cloud等）
    if os.name != "nt":
        try:
            os.system("apt-get -y install fonts-vlgothic > /dev/null 2>&1")
        except:
            pass
        if os.path.exists("/usr/share/fonts/truetype/vlgothic/VL-Gothic-Regular.ttf"):
            return "/usr/share/fonts/truetype/vlgothic/VL-Gothic-Regular.ttf"
    return None

FONT_PATH = find_japanese_font()

# ==============================================================================
# ☁️ GitHub連携によるデータ永続化（Streamlit Cloud対応）
# ==============================================================================
# st.secrets に GITHUB_TOKEN / GITHUB_REPO / GITHUB_BRANCH が設定されていれば
# データの保存先をGitHubリポジトリに切り替える（Streamlit Cloud再起動でも消えない）。
# ローカル実行時（secretsが無い場合）は、従来通りローカルフォルダに保存する。
USE_GITHUB_STORAGE = False
_gh_repo = None
_gh_init_error = None
try:
    if "GITHUB_TOKEN" in st.secrets and "GITHUB_REPO" in st.secrets:
        from github import Github, GithubException
        _gh_branch = st.secrets.get("GITHUB_BRANCH", "main")
        _gh_client = Github(st.secrets["GITHUB_TOKEN"])
        _gh_repo = _gh_client.get_repo(st.secrets["GITHUB_REPO"])
        # 接続確認のため軽い呼び出しを行い、失敗すればここで例外が出る
        _ = _gh_repo.full_name
        USE_GITHUB_STORAGE = True
    else:
        _gh_init_error = "st.secrets に GITHUB_TOKEN または GITHUB_REPO が見つかりません。"
except Exception as _e:
    USE_GITHUB_STORAGE = False
    _gh_repo = None
    _gh_init_error = f"{type(_e).__name__}: {_e}"

GH_DATA_PREFIX = "鶏舎飼料管理データ"  # GitHubリポジトリ内の保存先フォルダ名

def gh_path_join(*parts):
    return "/".join([p.strip("/") for p in parts if p])

def gh_read_json(path):
    """GitHub上のJSONファイルを読み込む。存在しなければNoneを返す。"""
    try:
        content_file = _gh_repo.get_contents(path, ref=_gh_branch)
        return json.loads(content_file.decoded_content.decode("utf-8"))
    except Exception:
        return None

def gh_write_json(path, data_obj, commit_message):
    """GitHub上にJSONファイルを書き込む（新規作成 or 更新を自動判定）。"""
    content_str = json.dumps(data_obj, ensure_ascii=False, indent=4)
    try:
        existing = _gh_repo.get_contents(path, ref=_gh_branch)
        _gh_repo.update_file(path, commit_message, content_str, existing.sha, branch=_gh_branch)
    except GithubException:
        _gh_repo.create_file(path, commit_message, content_str, branch=_gh_branch)

def gh_list_tree():
    """GitHub上の保存データ構造をスキャンしてローカル版と同じ形式の辞書を返す。"""
    data_tree = {}
    try:
        contents = _gh_repo.get_contents(GH_DATA_PREFIX, ref=_gh_branch)
    except Exception:
        return data_tree
    farms = [c for c in contents if c.type == "dir"]
    for farm_c in farms:
        farm = farm_c.name
        data_tree[farm] = {}
        try:
            houses = [c for c in _gh_repo.get_contents(farm_c.path, ref=_gh_branch) if c.type == "dir"]
        except Exception:
            houses = []
        for house_c in houses:
            house = house_c.name
            data_tree[farm][house] = {}
            try:
                tanks = [c for c in _gh_repo.get_contents(house_c.path, ref=_gh_branch) if c.type == "dir"]
            except Exception:
                tanks = []
            for tank_c in tanks:
                tank = tank_c.name
                data_tree[farm][house][tank] = []
                try:
                    files = _gh_repo.get_contents(tank_c.path, ref=_gh_branch)
                except Exception:
                    files = []
                for f in files:
                    if f.name.endswith(".json") and f.name != "latest_session.json":
                        data_tree[farm][house][tank].append(f.name.replace(".json", ""))
                data_tree[farm][house][tank].sort()
    return data_tree

def gh_collect_farm_records(farm):
    """GitHub上の指定農場配下にある全保存JSONファイルを読み込んで一覧で返す。"""
    results = []
    try:
        houses = [c for c in _gh_repo.get_contents(gh_path_join(GH_DATA_PREFIX, farm), ref=_gh_branch) if c.type == "dir"]
    except Exception:
        return results
    for house_c in houses:
        try:
            tanks = [c for c in _gh_repo.get_contents(house_c.path, ref=_gh_branch) if c.type == "dir"]
        except Exception:
            tanks = []
        for tank_c in tanks:
            try:
                files = _gh_repo.get_contents(tank_c.path, ref=_gh_branch)
            except Exception:
                files = []
            for f in sorted(files, key=lambda x: x.name):
                if f.name.endswith(".json") and f.name != "latest_session.json":
                    try:
                        results.append(json.loads(f.decoded_content.decode("utf-8")))
                    except Exception:
                        pass
    return results

# --- 📁 ディレクトリ管理（Streamlit汎用） ---
BASE_DIR = './鶏舎飼料管理データ/'
if not os.path.exists(BASE_DIR):
    os.makedirs(BASE_DIR)
LATEST_SESSION_FILE = os.path.join(BASE_DIR, "latest_session.json")

# ==============================================================================
# Ross 308 標準指標データ
# ==============================================================================
ROSS308_STD = [
    (0, 44, 0.0, 0), (1, 62, 0.196, 12), (2, 81, 0.352, 16), (3, 102, 0.476, 20),
    (4, 125, 0.577, 24), (5, 151, 0.658, 27), (6, 181, 0.724, 31), (7, 213, 0.780, 35),
    (8, 249, 0.826, 39), (9, 288, 0.865, 44), (10, 330, 0.900, 48), (11, 376, 0.930, 52),
    (12, 425, 0.957, 57), (13, 477, 0.982, 62), (14, 533, 1.005, 67), (15, 592, 1.026, 72),
    (16, 655, 1.047, 77), (17, 720, 1.066, 83), (18, 789, 1.086, 88), (19, 860, 1.105, 94),
    (20, 935, 1.123, 100), (21, 1012, 1.142, 105), (22, 1092, 1.160, 111), (23, 1174, 1.178, 117),
    (24, 1258, 1.196, 122), (25, 1345, 1.214, 128), (26, 1434, 1.233, 134), (27, 1524, 1.251, 139),
    (28, 1616, 1.269, 145), (29, 1710, 1.288, 150), (30, 1805, 1.306, 156), (31, 1901, 1.325, 161),
    (32, 1999, 1.343, 166), (33, 2097, 1.362, 171), (34, 2196, 1.381, 176), (35, 2296, 1.399, 180),
    (36, 2396, 1.418, 185), (37, 2496, 1.437, 189), (38, 2597, 1.456, 193), (39, 2697, 1.474, 197),
    (40, 2798, 1.493, 201), (41, 2898, 1.512, 204), (42, 2998, 1.531, 207), (43, 3097, 1.550, 211),
    (44, 3197, 1.569, 213), (45, 3295, 1.587, 216), (46, 3393, 1.606, 219), (47, 3490, 1.625, 221),
    (48, 3586, 1.644, 223), (49, 3681, 1.663, 225), (50, 3776, 1.681, 227), (51, 3869, 1.700, 229),
    (52, 3961, 1.719, 230), (53, 4052, 1.738, 231), (54, 4142, 1.756, 233), (55, 4230, 1.775, 233),
    (56, 4318, 1.793, 234)
]

# ==============================================================================
# 🔄 状態管理 (Streamlit Session State) の初期化
# ==============================================================================
if "current_records" not in st.session_state:
    st.session_state.current_records = {0: {"delivered": 5000, "actual_tank": 5000, "type": "確定"}}
if "current_adjustments" not in st.session_state:
    st.session_state.current_adjustments = {}

# 最初の一回だけ、前回セッションがあれば自動復元
if "initialized" not in st.session_state:
    loaded = None
    if USE_GITHUB_STORAGE:
        loaded = gh_read_json(gh_path_join(GH_DATA_PREFIX, "latest_session.json"))
    elif os.path.exists(LATEST_SESSION_FILE):
        try:
            with open(LATEST_SESSION_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
        except:
            loaded = None
    if loaded:
        try:
            st.session_state.current_records = {int(k): v for k, v in loaded["records"].items()}
            st.session_state.current_adjustments = {int(k): v for k, v in loaded.get("adjustments", {}).items()}
        except:
            pass
    st.session_state.initialized = True

# --- 📁 ディレクトリデータスキャン ---
def scan_directory():
    if USE_GITHUB_STORAGE:
        return gh_list_tree()
    data_tree = {}
    if not os.path.exists(BASE_DIR): return data_tree
    for farm in sorted(os.listdir(BASE_DIR)):
        farm_path = os.path.join(BASE_DIR, farm)
        if os.path.isdir(farm_path):
            data_tree[farm] = {}
            for house in sorted(os.listdir(farm_path)):
                house_path = os.path.join(farm_path, house)
                if os.path.isdir(house_path):
                    data_tree[farm][house] = {}
                    for tank in sorted(os.listdir(house_path)):
                        tank_path = os.path.join(house_path, tank)
                        if os.path.isdir(tank_path):
                            data_tree[farm][house][tank] = []
                            for f in sorted(os.listdir(tank_path)):
                                if f.endswith('.json') and f != "latest_session.json":
                                    data_tree[farm][house][tank].append(f.replace('.json', ''))
    return data_tree

# --- 🔄 計算コアロジック（Jupyter版から完全移植） ---
def calculate_table_core(param_dict, rec_dict, adj_dict):
    birds = param_dict["birds"]
    shipping_age = min(param_dict["shipping_age"], 56)
    tank_cap = param_dict["tank_cap"]
    min_alert = param_dict["min_alert"]
    std_qty = param_dict["std_qty"]
    pre_limit = param_dict["pre_limit"]
    mid_limit = param_dict["mid_limit"]
    first_qty = param_dict["first_qty"]
    
    start_date_val = param_dict["start_date"]
    if isinstance(start_date_val, str):
        start_date_val = datetime.strptime(start_date_val, "%Y-%m-%d").date()

    df = pd.DataFrame(ROSS308_STD, columns=["day", "weight", "fcr", "std_intake_g"])
    df = df[df["day"] <= shipping_age].reset_index(drop=True)

    df["date_obj"] = [start_date_val + timedelta(days=int(d)) for d in df["day"]]
    df["date"] = [d.strftime("%m/%d") for d in df["date_obj"]]
    df["std_feed_kg"] = (df["std_intake_g"] * birds) / 1000.0
    if 45 in df["day"].values: df.loc[df["day"] == 45, "std_feed_kg"] *= 0.75

    act_dict = {d: v for d, v in rec_dict.items() if v.get("type") == "確定"}
    adj_rates = np.ones(len(df))
    actual_feed = df["std_feed_kg"].copy().values
    sorted_act_days = sorted(act_dict.keys())
    latest_rate = 1.0

    if len(sorted_act_days) > 1:
        for i in range(len(sorted_act_days) - 1):
            start_day = sorted_act_days[i]
            end_day = sorted_act_days[i + 1]
            prev_start_total = act_dict[start_day]["actual_tank"] + (act_dict[start_day]["delivered"] if start_day > 0 else 0)
            if start_day == 0: prev_start_total = first_qty
                
            current_morning_tank = act_dict[end_day]["actual_tank"]
            actual_consumed = prev_start_total - current_morning_tank
            std_consumed = df.loc[start_day:end_day-1, "std_feed_kg"].sum()
            rate_period = actual_consumed / std_consumed if std_consumed > 0 else 1.0
            latest_rate = rate_period
            
            for d in range(start_day, end_day):
                adj_rates[d] = rate_period
                actual_feed[d] = df.loc[d, "std_feed_kg"] * rate_period

    last_act_day = sorted_act_days[-1] if sorted_act_days else 0
    for d in range(0, len(df)):
        if d >= last_act_day and last_act_day > 0:
            actual_feed[d] = df.loc[d, "std_feed_kg"] * latest_rate
            adj_rates[d] = latest_rate

    df["adj_rate"] = adj_rates
    df["act_feed_kg"] = actual_feed
    df["act_intake_g"] = (df["act_feed_kg"] * 1000.0) / birds

    pred_tank_morning = np.zeros(len(df))
    real_tank_morning = np.zeros(len(df))
    delivery_plan = np.zeros(len(df))
    event_notes = [""] * len(df)

    pred_tank_morning[0] = first_qty
    real_tank_morning[0] = first_qty
    event_notes[0] = f"【初回】前期: {first_qty:.0f}kg"
    allocated_pre = first_qty
    allocated_mid = 0.0
    evening_pred_tank = first_qty - df.loc[0, "act_feed_kg"]

    for d in range(1, len(df)):
        if d <= last_act_day and last_act_day > 0:
            if d in act_dict:
                delivery_plan[d] = act_dict[d]["delivered"]
                real_tank_morning[d] = act_dict[d]["actual_tank"]
                event_notes[d] = f"【実績確定】納品前残量: {act_dict[d]['actual_tank']:.0f}kg"
                if pre_limit > 0 and allocated_pre < pre_limit: allocated_pre += delivery_plan[d]
                elif mid_limit > 0 and allocated_mid < mid_limit: allocated_mid += delivery_plan[d]
            else: real_tank_morning[d] = evening_pred_tank
            pred_tank_morning[d] = real_tank_morning[d]
            evening_pred_tank = real_tank_morning[d] + delivery_plan[d] - df.loc[d, "act_feed_kg"]
        else:
            real_tank_morning[d] = np.nan
            pred_tank_morning[d] = evening_pred_tank
            if d in adj_dict:
                adj_info = adj_dict[d]
                adj_qty_val = adj_info["delivered"]
                delivery_plan[d] = adj_qty_val
                # 手動調整配車も、前期・中期の残り枠をまたぐ場合は「調整混載発注」として表記する
                rem_pre_adj = pre_limit - allocated_pre if pre_limit > 0 else 0
                rem_mid_adj = mid_limit - allocated_mid if mid_limit > 0 else 0
                if rem_pre_adj > 0:
                    if rem_pre_adj >= adj_qty_val:
                        allocated_pre += adj_qty_val
                        event_notes[d] = f"【調整発注】納品量: {adj_qty_val:.0f}kg"
                    else:
                        mix_next = adj_qty_val - rem_pre_adj
                        allocated_pre += rem_pre_adj
                        if mid_limit > 0:
                            allocated_mid += mix_next
                            event_notes[d] = f"【調整混載発注】前期: {rem_pre_adj:.0f}kg / 中期: {mix_next:.0f}kg"
                        else:
                            event_notes[d] = f"【調整混載発注】前期: {rem_pre_adj:.0f}kg / 仕上: {mix_next:.0f}kg"
                elif rem_mid_adj > 0:
                    if rem_mid_adj >= adj_qty_val:
                        allocated_mid += adj_qty_val
                        event_notes[d] = f"【調整発注】納品量: {adj_qty_val:.0f}kg"
                    else:
                        mix_fin = adj_qty_val - rem_mid_adj
                        allocated_mid += rem_mid_adj
                        event_notes[d] = f"【調整混載発注】中期: {rem_mid_adj:.0f}kg / 仕上: {mix_fin:.0f}kg"
                else:
                    event_notes[d] = f"【調整発注】納品量: {adj_qty_val:.0f}kg"
                if adj_info["actual_tank"] is not None:
                    pred_tank_morning[d] = adj_info["actual_tank"]
                    real_tank_morning[d] = adj_info["actual_tank"]
            else:
                tomorrow_need = df.loc[d, "act_feed_kg"]
                if pred_tank_morning[d] <= min_alert or pred_tank_morning[d] < tomorrow_need:
                    delivery_plan[d] = std_qty
                    
                    rem_pre = pre_limit - allocated_pre if pre_limit > 0 else 0
                    if rem_pre > 0:
                        if rem_pre >= std_qty:
                            allocated_pre += std_qty
                            event_notes[d] = f"【通常発注】前期: {std_qty}kg"
                        else:
                            mix_next = std_qty - rem_pre
                            allocated_pre += rem_pre
                            if mid_limit > 0:
                                allocated_mid += mix_next
                                event_notes[d] = f"【混載発注】前期: {rem_pre:.0f}kg / 中期: {mix_next:.0f}kg"
                            else:
                                event_notes[d] = f"【混載発注】前期: {rem_pre:.0f}kg / 仕上: {mix_next:.0f}kg"
                    else:
                        rem_mid = mid_limit - allocated_mid if mid_limit > 0 else 0
                        if rem_mid > 0:
                            if rem_mid >= std_qty:
                                allocated_mid += std_qty
                                event_notes[d] = f"【通常発注】中期: {std_qty}kg"
                            else:
                                mix_fin = std_qty - rem_mid
                                allocated_mid += rem_mid
                                event_notes[d] = f"【混載発注】中期: {rem_mid:.0f}kg / 仕上: {mix_fin:.0f}kg"
                        else:
                            event_notes[d] = f"【通常発注】仕上: {std_qty}kg"
            evening_pred_tank = pred_tank_morning[d] + delivery_plan[d] - df.loc[d, "act_feed_kg"]

    df["pred_tank_morning"] = pred_tank_morning
    df["real_tank_morning"] = real_tank_morning
    df["delivery_kg"] = delivery_plan
    df["event_notes"] = event_notes
    return df

# 等幅表示用ヘルパー関数
def get_east_asian_width(text):
    import unicodedata
    count = 0
    for c in text:
        if unicodedata.east_asian_width(c) in 'FWA': count += 2
        else: count += 1
    return count

def pad_to_width(text, target_width, align='left'):
    text = str(text)
    current_w = get_east_asian_width(text)
    if current_w >= target_width: return text
    padding = ' ' * (target_width - current_w)
    if align == 'center':
        left_pad = ' ' * ((target_width - current_w) // 2)
        right_pad = ' ' * (target_width - current_w - len(left_pad))
        return left_pad + text + right_pad
    elif align == 'right': return padding + text
    else: return text + padding


# ==============================================================================
# 🧱 Streamlit 画面構成（タブシステム）
# ==============================================================================

# 「過去データ読込」ボタンで保存された保留中のパラメータを、
# ウィジェットがまだ1つも描画されていないこの時点でウィジェットキーへ反映する。
# （ウィジェット描画後に session_state[key] を書き換えるとStreamlitAPIExceptionになるため、
#   必ずウィジェットが呼ばれる前のこの場所で行う必要がある）
if "_pending_params" in st.session_state:
    _pp = st.session_state.pop("_pending_params")
    for _k, _v in _pp.items():
        st.session_state[_k] = _v
if st.session_state.pop("_pending_clear_act_date", False):
    if "act_date_select" in st.session_state:
        del st.session_state["act_date_select"]

# 直前の保存・読込操作の結果メッセージ（タブ切り替えやrerun後も確実に見えるよう、タブの外側・最上部で表示する）
if "flash_message" in st.session_state:
    _msg_type, _msg_text = st.session_state["flash_message"]
    del st.session_state["flash_message"]
    if _msg_type == "success":
        st.success(_msg_text)
    else:
        st.error(_msg_text)

main_tabs = st.tabs(["📋 1. 飼料計算シミュレーター", "📸 2. 飼料発注"])

# ------------------------------------------------------------------------------
# 📋 タブ 1: シミュレーター
# ------------------------------------------------------------------------------
with main_tabs[0]:
    st.subheader("📂 ステップ1：初期条件・環境設定")

    col1, col2, col3 = st.columns(3)
    with col1:
        farm_name = st.text_input("農場名:", st.session_state.get("farm_name", "上川西農場"), key="farm_name")
        start_date = st.date_input(
            "入雛日:",
            st.session_state.get("start_date", date(2026, 6, 9)),
            key="start_date"
        )
        birds = st.number_input("入雛羽数(羽):", value=st.session_state.get("birds", 6600), step=100, key="birds")
        shipping_age = st.number_input("出荷日齢:", value=st.session_state.get("shipping_age", 46), max_value=56, key="shipping_age")
    with col2:
        house_no = st.text_input("鶏舎No./名:", st.session_state.get("house_no", "A棟"), key="house_no")
        tank_cap = st.number_input("タンク容量(kg):", value=st.session_state.get("tank_cap", 7000), step=500, key="tank_cap")
        min_alert = st.number_input("最低残量アラート(kg):", value=st.session_state.get("min_alert", 500), step=100, key="min_alert")
        first_qty = st.number_input("初回納品量(kg):", value=st.session_state.get("first_qty", 5000), step=500, key="first_qty")
    with col3:
        tank_no = st.text_input("タンクNo.:", st.session_state.get("tank_no", "No.1"), key="tank_no")
        std_qty = st.number_input("通常配送単位(kg):", value=st.session_state.get("std_qty", 4000), step=500, key="std_qty")
        pre_limit = st.number_input("前期飼料総量(kg):", value=st.session_state.get("pre_limit", 6000), step=500, key="pre_limit")
        mid_limit = st.number_input("中期飼料総量(kg):", value=st.session_state.get("mid_limit", 10000), step=500, key="mid_limit")

    with st.expander("🛠️ デバッグ情報（不具合調査用・通常は無視してください）"):
        st.write("USE_GITHUB_STORAGE:", USE_GITHUB_STORAGE)
        st.write("GitHub接続エラー:", _gh_init_error)
        st.write("接続先リポジトリ（secrets値）:", st.secrets.get("GITHUB_REPO", "(未設定)") if hasattr(st, "secrets") else "(取得不可)")
        st.write("loaded_params:", st.session_state.get("loaded_params"))
        st.write("現在のウィジェット値 → farm_name:", st.session_state.get("farm_name"),
                  " / start_date:", st.session_state.get("start_date"),
                  " / birds:", st.session_state.get("birds"))
        st.write("current_records の件数:", len(st.session_state.get("current_records", {})))
        st.write("current_records の中身:", st.session_state.get("current_records"))
        st.write("current_adjustments の中身:", st.session_state.get("current_adjustments"))

    if st.button("① 新規条件で台帳作成（全クリア）", type="primary"):
        st.session_state.current_records = {0: {"delivered": first_qty, "actual_tank": first_qty, "type": "確定"}}
        st.session_state.current_adjustments = {}
        if "loaded_params" in st.session_state:
            del st.session_state["loaded_params"]
        st.success("🆕 初期条件でクリアした台帳を作成しました。")

    st.markdown("---")
    st.subheader("🔍 ステップ2：過去データの絞り込み読込・保存")
    
    tree = scan_directory()
    farms = list(tree.keys()) if tree else ["(保存データなし)"]
    
    col_s1, col_s2, col_s3, col_s4 = st.columns(4)
    with col_s1:
        if "sel_farm" in st.session_state and st.session_state["sel_farm"] not in farms:
            del st.session_state["sel_farm"]
        sel_farm = st.selectbox("農場選択:", farms, key="sel_farm")
    with col_s2:
        houses = list(tree[sel_farm].keys()) if sel_farm in tree else ["—"]
        if "sel_house" in st.session_state and st.session_state["sel_house"] not in houses:
            del st.session_state["sel_house"]
        sel_house = st.selectbox("鶏舎選択:", houses, key="sel_house")
    with col_s3:
        tanks = list(tree[sel_farm][sel_house].keys()) if sel_farm in tree and sel_house in tree[sel_farm] else ["—"]
        if "sel_tank" in st.session_state and st.session_state["sel_tank"] not in tanks:
            del st.session_state["sel_tank"]
        sel_tank = st.selectbox("タンク選択:", tanks, key="sel_tank")
    with col_s4:
        dates = tree[sel_farm][sel_house][sel_tank] if sel_farm in tree and sel_house in tree[sel_farm] and sel_tank in tree[sel_farm][sel_house] else ["—"]
        if "sel_date" in st.session_state and st.session_state["sel_date"] not in dates:
            del st.session_state["sel_date"]
        sel_date = st.selectbox("入雛日選択:", dates, key="sel_date")

    col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 2])
    with col_btn1:
        if st.button("📂 選択したデータを読込", type="secondary"):
            if sel_farm != "(保存データなし)" and sel_date != "—":
                try:
                    loaded = None
                    if USE_GITHUB_STORAGE:
                        gh_path = gh_path_join(GH_DATA_PREFIX, sel_farm, sel_house, sel_tank, f"{sel_date}.json")
                        loaded = gh_read_json(gh_path)
                        if loaded is None:
                            raise FileNotFoundError("GitHub上にファイルが見つかりません")
                    else:
                        filepath = os.path.join(BASE_DIR, sel_farm, sel_house, sel_tank, f"{sel_date}.json")
                        with open(filepath, 'r', encoding='utf-8') as f:
                            loaded = json.load(f)
                    st.session_state.current_records = {int(k): v for k, v in loaded["records"].items()}
                    st.session_state.current_adjustments = {int(k): v for k, v in loaded.get("adjustments", {}).items()}
                    # 入力欄（農場名・入雛日など）に反映させたいが、ウィジェットが既に描画済みの
                    # session_state[key] を直接書き換えることはStreamlitの仕様上できない。
                    # そのため「_pending_params」に保存しておき、次回スクリプト実行の冒頭
                    # （ウィジェットがまだ描画される前）でウィジェットキーへ移し替える。
                    loaded_start_date_str = loaded.get("start_date", sel_date)
                    try:
                        loaded_start_date_obj = datetime.strptime(loaded_start_date_str, "%Y-%m-%d").date()
                    except Exception:
                        loaded_start_date_obj = date(2026, 6, 9)
                    st.session_state["_pending_params"] = {
                        "farm_name": loaded.get("farm_name", sel_farm),
                        "house_no": loaded.get("house_no", sel_house),
                        "tank_no": loaded.get("tank_no", sel_tank),
                        "start_date": loaded_start_date_obj,
                        "birds": loaded.get("birds", 6600),
                        "shipping_age": loaded.get("shipping_age", 46),
                        "tank_cap": loaded.get("tank_cap", 7000),
                        "min_alert": loaded.get("min_alert", 500),
                        "first_qty": loaded.get("first_qty", 5000),
                        "std_qty": loaded.get("std_qty", 4000),
                        "pre_limit": loaded.get("pre_limit", 6000),
                        "mid_limit": loaded.get("mid_limit", 10000),
                    }
                    st.session_state["_pending_clear_act_date"] = True
                    st.session_state["flash_message"] = (
                        "success",
                        f"📂 読込: {sel_farm}/{sel_house}/{sel_tank}/{sel_date} → "
                        f"loaded.farm_name={loaded.get('farm_name')!r}, "
                        f"loaded.start_date={loaded.get('start_date')!r}, "
                        f"loaded.house_no={loaded.get('house_no')!r}"
                    )
                    st.rerun()
                except Exception as e:
                    st.session_state["flash_message"] = ("error", f"⚠️ 読込失敗: {type(e).__name__}: {e}")
                    st.rerun()
    with col_btn2:
        if st.button("💾 全体の状態をファイルへ保存", type="secondary"):
            try:
                save_data = {
                    "farm_name": farm_name, "house_no": house_no, "tank_no": tank_no,
                    "start_date": start_date.strftime('%Y-%m-%d'),
                    "birds": birds, "shipping_age": shipping_age, "tank_cap": tank_cap,
                    "min_alert": min_alert, "first_qty": first_qty, "std_qty": std_qty,
                    "pre_limit": pre_limit, "mid_limit": mid_limit,
                    "records": st.session_state.current_records,
                    "adjustments": st.session_state.current_adjustments
                }
                if USE_GITHUB_STORAGE:
                    record_path = gh_path_join(GH_DATA_PREFIX, farm_name, house_no, tank_no, f"{start_date.strftime('%Y-%m-%d')}.json")
                    latest_path = gh_path_join(GH_DATA_PREFIX, "latest_session.json")
                    gh_write_json(record_path, save_data, f"台帳保存: {farm_name}/{house_no}/{tank_no} {start_date}")
                    gh_write_json(latest_path, save_data, "最新セッションの自動バックアップ更新")
                    st.session_state["flash_message"] = ("success", f"💾 GitHubに保存しました（{record_path}）。")
                else:
                    target_dir = os.path.join(BASE_DIR, farm_name, house_no, tank_no)
                    if not os.path.exists(target_dir): os.makedirs(target_dir)
                    filepath = os.path.join(target_dir, f"{start_date.strftime('%Y-%m-%d')}.json")
                    with open(filepath, 'w', encoding='utf-8') as f:
                        json.dump(save_data, f, ensure_ascii=False, indent=4)
                    with open(LATEST_SESSION_FILE, 'w', encoding='utf-8') as f:
                        json.dump(save_data, f, ensure_ascii=False, indent=4)
                    st.session_state["flash_message"] = ("success", f"💾 ローカルに保存しました（{filepath}）。")
                st.rerun() # ドロップダウンを更新するために再実行
            except Exception as e:
                st.session_state["flash_message"] = ("error", f"⚠️ 保存失敗: {type(e).__name__}: {e}")
                st.rerun()

    # 計算処理用パラメータのまとめ
    params = {
        "birds": birds, "shipping_age": shipping_age, "tank_cap": tank_cap,
        "min_alert": min_alert, "std_qty": std_qty, "pre_limit": pre_limit,
        "mid_limit": mid_limit, "start_date": start_date, "first_qty": first_qty
    }
    df_result = calculate_table_core(params, st.session_state.current_records, st.session_state.current_adjustments)

    st.markdown("---")
    st.subheader("📊 ステップ3：日付ベース実績・計画入力")

    st.caption(f"🛠️[デバッグ] 現在計算に使われているパラメータ → "
               f"farm_name={farm_name!r}, house_no={house_no!r}, tank_no={tank_no!r}, "
               f"start_date={start_date!r}, birds={birds!r}")
    
    # 対象日の選択肢（「日齢+日付」を表示ラベルにすることで、日付文字列の重複による誤選択を防ぐ）
    label_options = [f"{df_result.loc[idx, 'day']}日齢 ({df_result.loc[idx, 'date']})" for idx in range(len(df_result))]
    # start_date等が変わってlabel_optionsの中身が変化した場合、以前の選択値が
    # 新しいリストに存在しないことがあるため、安全にデフォルト位置へフォールバックする
    if "act_date_select" in st.session_state and st.session_state["act_date_select"] not in label_options:
        del st.session_state["act_date_select"]
    act_label = st.selectbox("対象の日付を選択:", label_options, key="act_date_select")

    target_day_idx = label_options.index(act_label)
    act_date = df_result.loc[target_day_idx, "date"]
    stock_val = df_result.loc[target_day_idx, "pred_tank_morning"]
    
    # インフォメーションボックス
    st.info(f"💡 選択した {act_date} の補正後予定残量（朝）: **{stock_val:,.1f} kg**")

    col_input1, col_input2 = st.columns(2)
    with col_input1:
        st.markdown("<b style='color:#f57c00;'>【A】手動調整・調整配車（未来予測の変更）</b>", unsafe_allow_html=True)
        adj_qty = st.number_input("調整納品数量(kg):", value=4000, step=500, key=f"adj_qty_{target_day_idx}")
        adj_tank = st.number_input("調整時実質残量(kg):", value=int(stock_val), step=500, key=f"adj_tank_{target_day_idx}")
        if st.button("⚙️ 調整配車として反映", key=f"btn_adj_{target_day_idx}"):
            st.session_state.current_adjustments[target_day_idx] = {"delivered": adj_qty, "actual_tank": adj_tank, "type": "調整配車"}
            st.session_state["flash_message"] = ("success", f"⚙️ {act_date} に手動調整を反映しました。")
            st.rerun()

    with col_input2:
        st.markdown("<b style='color:#388e3c;'>【B】実績の完全確定入力（過去データの固定化）</b>", unsafe_allow_html=True)
        act_delivered = st.number_input("実際の納品量(kg):", value=4000, step=500, key=f"act_delivered_{target_day_idx}")
        act_tank = st.number_input("納品時のタンク残量(kg):", value=1000, step=500, key=f"act_tank_{target_day_idx}")
        if st.button("🏁 実績として確定保存して再計算", key=f"btn_act_{target_day_idx}"):
            st.session_state.current_records[target_day_idx] = {"delivered": act_delivered, "actual_tank": act_tank, "type": "確定"}
            if target_day_idx in st.session_state.current_adjustments:
                del st.session_state.current_adjustments[target_day_idx]
            st.session_state["flash_message"] = ("success", f"🏁 {act_date} の実績データを固定しました。")
            st.rerun()

    st.markdown("---")
    st.subheader(f"📑 飼料総合管理台帳  [{farm_name} / {house_no} / {tank_no}]")
    
    # Streamlitの綺麗なデータフレームとして見せるためにデータを整形
    disp_df = pd.DataFrame()
    disp_df["日齢"] = df_result["day"].astype(str) + "日齢"
    disp_df["日付"] = df_result["date"]
    disp_df["標準体重"] = df_result["weight"].map('{:,.0f}g'.format)
    disp_df["補正採食(1羽)"] = df_result["act_intake_g"].map('{:.1f}g'.format)
    disp_df["期間補正率"] = df_result["adj_rate"].map('{:.1%}'.format)
    disp_df["1日消費(群)"] = df_result["act_feed_kg"].map('{:,.1f}kg'.format)
    disp_df["予測残量(朝)"] = df_result["pred_tank_morning"].map('{:,.1f}kg'.format)
    disp_df["実質残量(実績)"] = df_result["real_tank_morning"].apply(lambda x: f"{x:,.1f}kg" if not np.isnan(x) else "—")
    disp_df["納品計画"] = df_result["delivery_kg"].apply(lambda x: f"{x:,.0f}kg" if x > 0 else "")
    disp_df["運行・予測備考"] = df_result["event_notes"]

    st.dataframe(disp_df, use_container_width=True, height=500)

# ------------------------------------------------------------------------------
# 📸 タブ 2: 飼料発注
# ------------------------------------------------------------------------------
with main_tabs[1]:
    st.subheader("🚚 ２．飼料発注シミュレーション画像生成")
    st.caption("対象の農場と検索期間を選択し、ボタンを押すと、そのままメールやLINEで送信できるA4配車依頼書画像が立ち上がります。")
    
    col_r1, col_r2, col_r3 = st.columns(3)
    with col_r1:
        report_farm = st.selectbox("発注対象農場:", farms, key="report_farm")
    with col_r2:
        report_start = st.date_input("検索開始日:", date(2026, 6, 1))
    with col_r3:
        report_end = st.date_input("検索終了日:", date(2026, 7, 31))

    if st.button("📸 飼料発注プレビュー画面を起動", type="primary"):
        if report_farm == "(保存データなし)":
            st.error("⚠️ 農場が正しく選択されていません。")
        else:
            today_str = date.today().strftime('%Y年%m月%d日')
            W_DATE = 14; W_HOUSE = 12; W_TANK = 12; W_AGE = 10; W_NOTE = 44
            TOTAL_WIDTH = W_DATE + W_HOUSE + W_TANK + W_AGE + W_NOTE + 6

            lines = []
            lines.append("+" + "-" * (TOTAL_WIDTH - 2) + "+")
            lines.append(f"|{pad_to_width('【飼料 配車発注依頼書】', TOTAL_WIDTH - 2, 'center')}|")
            lines.append("+" + "-" * (TOTAL_WIDTH - 2) + "+")
            lines.append(f" 発信元：長門アグリスト")
            lines.append(f" 対象農場：{report_farm}")
            lines.append(f" レポート発注日：{today_str}")
            lines.append(f" 対象期間：{report_start.strftime('%Y/%m/%d')} 〜 {report_end.strftime('%Y/%m/%d')}")
            lines.append("-" * TOTAL_WIDTH)
            lines.append(" 下記の通り、指定期間内の飼料発注・配車を依頼いたします。ご確認のほどお願い致します。")
            lines.append("-" * TOTAL_WIDTH)
            lines.append("")

            SEP_LINE = "+" + "-" * W_DATE + "+" + "-" * W_HOUSE + "+" + "-" * W_TANK + "+" + "-" * W_AGE + "+" + "-" * W_NOTE + "+"
            lines.append(SEP_LINE)
            lines.append(f"|{pad_to_width('納品予定日', W_DATE, 'center')}|{pad_to_width('鶏舎名', W_HOUSE, 'center')}|{pad_to_width('タンク番号', W_TANK, 'center')}|{pad_to_width('日齢', W_AGE, 'center')}|{pad_to_width('指示備考詳細（数量・銘柄）', W_NOTE, 'center')}|")
            lines.append(SEP_LINE)
            
            all_plans = []
            loaded_records = []
            if USE_GITHUB_STORAGE:
                loaded_records = gh_collect_farm_records(report_farm)
            else:
                farm_dir = os.path.join(BASE_DIR, report_farm)
                if os.path.exists(farm_dir):
                    for house in sorted(os.listdir(farm_dir)):
                        house_path = os.path.join(farm_dir, house)
                        if not os.path.isdir(house_path): continue
                        for tank in sorted(os.listdir(house_path)):
                            tank_path = os.path.join(house_path, tank)
                            if not os.path.isdir(tank_path): continue
                            for file in sorted(os.listdir(tank_path)):
                                if file.endswith('.json') and file != "latest_session.json":
                                    try:
                                        with open(os.path.join(tank_path, file), 'r', encoding='utf-8') as f:
                                            loaded_records.append(json.load(f))
                                    except Exception:
                                        pass

            for loaded in loaded_records:
                try:
                    house = loaded.get("house_no", "—")
                    tank = loaded.get("tank_no", "—")
                    p = {
                        "birds": loaded["birds"], "shipping_age": loaded["shipping_age"], "tank_cap": loaded["tank_cap"],
                        "min_alert": loaded["min_alert"], "std_qty": loaded["std_qty"], "pre_limit": loaded["pre_limit"],
                        "mid_limit": loaded["mid_limit"], "start_date": loaded["start_date"], "first_qty": loaded["first_qty"]
                    }
                    raw_rec = loaded["records"]
                    rec_dict = {int(k): v for k, v in raw_rec.items()}
                    raw_adj = loaded.get("adjustments", {})
                    adj_dict = {int(k): v for k, v in raw_adj.items()}

                    res_df = calculate_table_core(p, rec_dict, adj_dict)
                    for idx, row in res_df.iterrows():
                        d_obj = row["date_obj"]
                        if isinstance(d_obj, str):
                            d_obj = datetime.strptime(d_obj, "%Y-%m-%d").date()

                        if report_start <= d_obj <= report_end:
                            if row["delivery_kg"] > 0 and row["day"] > 0:
                                clean_note = str(row["event_notes"]).replace("🚚", "").strip()
                                all_plans.append({
                                    "date_str": d_obj.strftime("%Y/%m/%d"), "house": house, "tank": tank,
                                    "age": f"{row['day']}日齢", "note": clean_note, "sort_date": d_obj
                                })
                except Exception:
                    pass

            if all_plans:
                all_plans.sort(key=lambda x: (x["sort_date"], x["house"], x["tank"]))
                for item in all_plans:
                    d_col = pad_to_width(item["date_str"], W_DATE, 'center')
                    h_col = pad_to_width(item["house"], W_HOUSE, 'center')
                    t_col = pad_to_width(item["tank"], W_TANK, 'center')
                    a_col = pad_to_width(item["age"], W_AGE, 'center')
                    n_col = pad_to_width(f" {item['note']}", W_NOTE, 'left')
                    lines.append(f"|{d_col}|{h_col}|{t_col}|{a_col}|{n_col}|")
                    lines.append(SEP_LINE)
                lines.append(f"\n 以上、合計 【 {len(all_plans)} 件 】。配車手配のほど宜しくお願い致します。")
            else:
                empty_msg = pad_to_width(" 指定された期間内に納品予定のあるタンクはありませんでした（エサは十分足りています）。", TOTAL_WIDTH - 2, 'left')
                lines.append(f"|{empty_msg}|")
                lines.append(SEP_LINE)

            # 画像レンダリング処理
            img_w, img_h = 1240, 1754
            image = Image.new("RGB", (img_w, img_h), "white")
            draw = ImageDraw.Draw(image)
            
            font = None
            if FONT_PATH:
                try:
                    font = ImageFont.truetype(FONT_PATH, 24)
                except:
                    try:
                        font = ImageFont.truetype(FONT_PATH, 24, index=0)
                    except:
                        font = None
            if font is None:
                font = ImageFont.load_default()

            margin_x, margin_y = 50, 70
            line_height = 34
            for i, line_txt in enumerate(lines):
                draw.text((margin_x, margin_y + (i * line_height)), line_txt, fill="black", font=font)
                
            st.success("📸 本日の日付を反映したA4高画質プレビュー画面を立ち上げました。")
            st.caption("💡 下の画像をそのままスマートフォンなら「長押し」、PCなら「右クリック」で保存して使用してください。")
            
            # Streamlit上に直接画像を出力
            st.image(image, caption="配車発注依頼書 プレビュー", use_container_width=True)
