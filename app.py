import streamlit as st
import pandas as pd
import pulp
import calendar
import datetime
import jpholiday

# ページ設定（必ず最初に書く）
st.set_page_config(page_title="シフト希望＆確定入力画面", layout="wide")

# --- 定数定義 ---
SHIFTS_WEEKDAY = ['宿直A', '宿直B', '外来宿直']
SHIFTS_HOLIDAY = ['宿直A', '宿直B', '外来宿直', '日直A', '日直B', '外来日直']

def is_holiday(date):
    """土日・祝日判定"""
    return date.weekday() >= 5 or jpholiday.is_holiday(date)

def solve_shift(year, month, df_docs, df_reqs, df_fixed):
    # 1. 日付のセットアップ
    num_days = calendar.monthrange(year, month)[1]
    dates = [datetime.date(year, month, d) for d in range(1, num_days + 1)]
    
    # 2. 最適化問題の定義 (希望を最大化)
    prob = pulp.LpProblem("DoctorShift", pulp.LpMaximize)
    
    # 3. 変数の定義: x[doc_id][date][shift] = 0 or 1 (割り当てる場合1)
    x = {}
    for _, doc in df_docs.iterrows():
        doc_id = doc['医師ID']
        x[doc_id] = {}
        for d in dates:
            x[doc_id][d] = {}
            shifts = SHIFTS_HOLIDAY if is_holiday(d) else SHIFTS_WEEKDAY
            for s in shifts:
                x[doc_id][d][s] = pulp.LpVariable(f"x_{doc_id}_{d.day}_{s}", cat='Binary')

    # 4. 制約条件の追加
    # 制約A: 各シフト枠には必ず1人の医師を割り当てる
    for d in dates:
        shifts = SHIFTS_HOLIDAY if is_holiday(d) else SHIFTS_WEEKDAY
        for s in shifts:
            prob += pulp.lpSum(x[doc_id][d][s] for _, doc in df_docs.iterrows() for doc_id in [doc['医師ID']]) == 1

    # 制約B: 1人の医師は1日に最大1シフトのみ
    for _, doc in df_docs.iterrows():
        doc_id = doc['医師ID']
        for d in dates:
            shifts = SHIFTS_HOLIDAY if is_holiday(d) else SHIFTS_WEEKDAY
            prob += pulp.lpSum(x[doc_id][d][s] for s in shifts) <= 1

    # 制約C: 確定済みシフトの反映（絶対に通す設定）
    if df_fixed is not None and not df_fixed.empty:
        for _, row in df_fixed.iterrows():
            d = row['日付'].date()
            if d in dates:
                # 確定しているシフトを「1」に固定
                prob += x[row['医師ID']][d][row['シフト名']] == 1

    # 制約D: 各種回数・間隔の制限
    for _, doc in df_docs.iterrows():
        doc_id = doc['医師ID']
        
        # 月間の最小・最大回数
        total_shifts = pulp.lpSum(x[doc_id][d][s] for d in dates for s in (SHIFTS_HOLIDAY if is_holiday(d) else SHIFTS_WEEKDAY))
        prob += total_shifts >= doc['月間最小回数']
        prob += total_shifts <= doc['月間最大回数']
        
        # 各シフトの最大回数
        prob += pulp.lpSum(x[doc_id][d]['宿直A'] for d in dates if '宿直A' in x[doc_id][d]) <= doc['最大_宿直A']
        prob += pulp.lpSum(x[doc_id][d]['宿直B'] for d in dates if '宿直B' in x[doc_id][d]) <= doc['最大_宿直B']
        prob += pulp.lpSum(x[doc_id][d]['外来宿直'] for d in dates if '外来宿直' in x[doc_id][d]) <= doc['最大_外来宿直']
        prob += pulp.lpSum(x[doc_id][d]['日直A'] for d in dates if '日直A' in x[doc_id][d]) <= doc['最大_日直A']
        prob += pulp.lpSum(x[doc_id][d]['日直B'] for d in dates if '日直B' in x[doc_id][d]) <= doc['最大_日直B']
        prob += pulp.lpSum(x[doc_id][d]['外来日直'] for d in dates if '外来日直' in x[doc_id][d]) <= doc['最大_外来日直']

        # 最低空ける日数 (例: 1なら連勤不可。2なら中2日必要)
        min_interval = doc['最低空ける日数']
        if min_interval > 0:
            for i in range(len(dates) - min_interval):
                interval_sum = pulp.lpSum(
                    x[doc_id][dates[j]][s] 
                    for j in range(i, i + min_interval + 1) 
                    for s in (SHIFTS_HOLIDAY if is_holiday(dates[j]) else SHIFTS_WEEKDAY)
                )
                prob += interval_sum <= 1

    # 5. 目的関数の設定（NG日の除外と希望の反映）
    objective = 0
    if df_reqs is not None and not df_reqs.empty:
        for _, req in df_reqs.iterrows():
            d = req['日付'].date()
            doc_id = req['医師ID']
            req_type = req['種別'] # 'NG' or '希望'
            priority = req['優先度'] # 1, 2, 3などの数値
            
            # 医師の優先度も加味する
            doc_priority = df_docs[df_docs['医師ID'] == doc_id]['医師優先度'].values[0]
            
            if d in dates and doc_id in x:
                shifts = SHIFTS_HOLIDAY if is_holiday(d) else SHIFTS_WEEKDAY
                day_sum = pulp.lpSum(x[doc_id][d][s] for s in shifts)
                if req_type == 'NG':
                    # NG日は絶対にシフトを入れない
                    prob += day_sum == 0
                elif req_type == '希望':
                    # 希望日は目的関数に加算して入りやすくする（優先度掛け算）
                    objective += day_sum * priority * doc_priority

    prob += objective

    # 6. ソルバーの実行
    status = prob.solve()
    
    if pulp.LpStatus[status] == 'Optimal':
        # 結果のデータフレーム化
        result_data = []
        for d in dates:
            shifts = SHIFTS_HOLIDAY if is_holiday(d) else SHIFTS_WEEKDAY
            row_data = {'日付': d.strftime('%Y/%m/%d'), '曜日': ['月', '火', '水', '木', '金', '土', '日'][d.weekday()]}
            for s in shifts:
                for doc_id in x:
                    if pulp.value(x[doc_id][d][s]) == 1:
                        doc_name = df_docs[df_docs['医師ID'] == doc_id]['氏名'].values[0]
                        row_data[s] = doc_name
            result_data.append(row_data)
        
        # 曜日によってカラムが変わるため、見やすく整理
        df_result = pd.DataFrame(result_data)
        cols = ['日付', '曜日'] + SHIFTS_HOLIDAY # 全シフト枠を並べる
        df_result = df_result.reindex(columns=cols).fillna('')
        return df_result, True
    else:
        return None, False

# --- Streamlit UI構築 ---

st.title("🏥 シフト希望＆確定入力画面")
st.markdown("設定された条件や事前の確定シフトを元に、最適なシフト表を自動生成します。")

# 年月の選択
col1, col2 = st.columns(2)
with col1:
    year = st.number_input("作成する年", min_value=2026, value=2026)
with col2:
    month = st.number_input("作成する月", min_value=1, max_value=12, value=4)

st.divider() # 区切り線

# 1画面にファイルアップロードを配置
st.subheader("📁 CSVデータのアップロード")
st.markdown("必要なCSVファイルをアップロードしてください。")

# 3つのアップロード欄を横並びに配置
col_file1, col_file2, col_file3 = st.columns(3)
with col_file1:
    file_docs = st.file_uploader("1. 医師設定CSV (必須)", type=['csv'])
with col_file2:
    file_reqs = st.file_uploader("2. 希望・NG日CSV (任意)", type=['csv'])
with col_file3:
    file_fixed = st.file_uploader("3. 確定済みシフトCSV (任意)", type=['csv'])

st.divider() # 区切り線

if file_docs:
    # データの読み込み
    df_docs = pd.read_csv(file_docs)
    df_reqs = pd.read_csv(file_reqs, parse_dates=['日付']) if file_reqs else pd.DataFrame()
    df_fixed = pd.read_csv(file_fixed, parse_dates=['日付']) if file_fixed else pd.DataFrame()

    # 長い設定データは折りたたんで表示
    with st.expander("読み込んだ医師設定データを確認"):
        st.dataframe(df_docs)

    st.write("---")
    if st.button("シフトを自動作成する", type="primary"):
        with st.spinner('最適なシフトを計算中... (数秒かかる場合があります)'):
            df_result, success = solve_shift(year, month, df_docs, df_reqs, df_fixed)
            
            if success:
                st.success("シフトの作成に成功しました！")
                st.dataframe(df_result, use_container_width=True)
                
                # CSVダウンロード機能
                csv = df_result.to_csv(index=False).encode('utf-8-sig')
                st.download_button(
                    label="📥 シフト表をCSVでダウンロード",
                    data=csv,
                    file_name=f"shift_{year}_{month:02d}.csv",
                    mime="text/csv",
                )
            else:
                st.error("エラー：条件を満たすシフトが作成できませんでした。")
                st.warning("【解決のヒント】\n・誰かの「月間最小回数」が多すぎませんか？\n・NG日が重なりすぎて、割り当てられる医師がいない日はありませんか？\n・「最低空ける日数」の制限が厳しすぎませんか？\n・事前に確定したシフト(3番のCSV)とルールが矛盾していませんか？")
else:
    # サイドバーではなく、上部のアップロード欄を促すメッセージに変更
    st.info("👆 上のアップロード欄から「1. 医師設定CSV」をアップロードしてスタートしてください。")
