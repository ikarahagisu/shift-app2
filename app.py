import streamlit as st
import pandas as pd
import pulp
import calendar
import datetime
import jpholiday

st.set_page_config(page_title="シフト希望＆確定入力画面", layout="wide")

SHIFTS_WEEKDAY = ['宿直A', '宿直B', '外来宿直']
SHIFTS_HOLIDAY = ['宿直A', '宿直B', '外来宿直', '日直A', '日直B', '外来日直']

def is_holiday(date):
    return date.weekday() >= 5 or jpholiday.is_holiday(date)

def parse_single_csv(df, year, month):
    """1つのCSVから、ルール・希望・確定シフトを抽出する"""
    df.columns = df.columns.astype(str) # カラム名を文字列に統一
    num_days = calendar.monthrange(year, month)[1]
    
    reqs_data = []
    fixed_data = []
    
    for _, row in df.iterrows():
        doc_id = row['医師ID']
        for day in range(1, num_days + 1):
            col_name = str(day)
            if col_name in df.columns:
                val = str(row[col_name]).strip()
                if val == 'nan' or val == '':
                    continue
                
                d = datetime.date(year, month, day)
                
                if val.upper() == 'NG':
                    reqs_data.append({'日付': d, '医師ID': doc_id, '種別': 'NG', '優先度': 0})
                elif val.startswith('希望'):
                    priority = 1
                    try:
                        priority = int(val.replace('希望', ''))
                    except ValueError:
                        pass # 数字がなければ優先度1とする
                    reqs_data.append({'日付': d, '医師ID': doc_id, '種別': '希望', '優先度': priority})
                elif val in SHIFTS_HOLIDAY + SHIFTS_WEEKDAY:
                    fixed_data.append({'日付': d, '医師ID': doc_id, 'シフト名': val})
                    
    df_reqs = pd.DataFrame(reqs_data) if reqs_data else pd.DataFrame(columns=['日付', '医師ID', '種別', '優先度'])
    df_fixed = pd.DataFrame(fixed_data) if fixed_data else pd.DataFrame(columns=['日付', '医師ID', 'シフト名'])
    return df, df_reqs, df_fixed

def solve_shift(year, month, df_docs, df_reqs, df_fixed):
    num_days = calendar.monthrange(year, month)[1]
    dates = [datetime.date(year, month, d) for d in range(1, num_days + 1)]
    
    prob = pulp.LpProblem("DoctorShift", pulp.LpMaximize)
    
    x = {}
    for _, doc in df_docs.iterrows():
        doc_id = doc['医師ID']
        x[doc_id] = {}
        for d in dates:
            x[doc_id][d] = {}
            shifts = SHIFTS_HOLIDAY if is_holiday(d) else SHIFTS_WEEKDAY
            for s in shifts:
                x[doc_id][d][s] = pulp.LpVariable(f"x_{doc_id}_{d.day}_{s}", cat='Binary')

    # 制約A: 各シフト枠に必ず1人
    for d in dates:
        shifts = SHIFTS_HOLIDAY if is_holiday(d) else SHIFTS_WEEKDAY
        for s in shifts:
            prob += pulp.lpSum(x[doc_id][d][s] for _, doc in df_docs.iterrows() for doc_id in [doc['医師ID']]) == 1

    # 制約B: 1日1シフトまで
    for _, doc in df_docs.iterrows():
        doc_id = doc['医師ID']
        for d in dates:
            shifts = SHIFTS_HOLIDAY if is_holiday(d) else SHIFTS_WEEKDAY
            prob += pulp.lpSum(x[doc_id][d][s] for s in shifts) <= 1

    # 制約C: 確定済みシフト
    if not df_fixed.empty:
        for _, row in df_fixed.iterrows():
            d = row['日付']
            if d in dates:
                prob += x[row['医師ID']][d][row['シフト名']] == 1

    # 制約D: 回数と間隔の制限
    for _, doc in df_docs.iterrows():
        doc_id = doc['医師ID']
        total_shifts = pulp.lpSum(x[doc_id][d][s] for d in dates for s in (SHIFTS_HOLIDAY if is_holiday(d) else SHIFTS_WEEKDAY))
        prob += total_shifts >= doc['月間最小回数']
        prob += total_shifts <= doc['月間最大回数']
        
        prob += pulp.lpSum(x[doc_id][d]['宿直A'] for d in dates if '宿直A' in x[doc_id][d]) <= doc['最大_宿直A']
        prob += pulp.lpSum(x[doc_id][d]['宿直B'] for d in dates if '宿直B' in x[doc_id][d]) <= doc['最大_宿直B']
        prob += pulp.lpSum(x[doc_id][d]['外来宿直'] for d in dates if '外来宿直' in x[doc_id][d]) <= doc['最大_外来宿直']
        prob += pulp.lpSum(x[doc_id][d]['日直A'] for d in dates if '日直A' in x[doc_id][d]) <= doc['最大_日直A']
        prob += pulp.lpSum(x[doc_id][d]['日直B'] for d in dates if '日直B' in x[doc_id][d]) <= doc['最大_日直B']
        prob += pulp.lpSum(x[doc_id][d]['外来日直'] for d in dates if '外来日直' in x[doc_id][d]) <= doc['最大_外来日直']

        min_interval = doc['最低空ける日数']
        if min_interval > 0:
            for i in range(len(dates) - min_interval):
                interval_sum = pulp.lpSum(
                    x[doc_id][dates[j]][s] 
                    for j in range(i, i + min_interval + 1) 
                    for s in (SHIFTS_HOLIDAY if is_holiday(dates[j]) else SHIFTS_WEEKDAY)
                )
                prob += interval_sum <= 1

    # 目的関数（希望とNG）
    objective = 0
    if not df_reqs.empty:
        for _, req in df_reqs.iterrows():
            d = req['日付']
            doc_id = req['医師ID']
            req_type = req['種別']
            priority = req['優先度']
            doc_priority = df_docs[df_docs['医師ID'] == doc_id]['医師優先度'].values[0]
            
            if d in dates and doc_id in x:
                shifts = SHIFTS_HOLIDAY if is_holiday(d) else SHIFTS_WEEKDAY
                day_sum = pulp.lpSum(x[doc_id][d][s] for s in shifts)
                if req_type == 'NG':
                    prob += day_sum == 0
                elif req_type == '希望':
                    objective += day_sum * priority * doc_priority

    prob += objective
    status = prob.solve()
    
    if pulp.LpStatus[status] == 'Optimal':
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
        
        df_result = pd.DataFrame(result_data)
        cols = ['日付', '曜日'] + SHIFTS_HOLIDAY
        df_result = df_result.reindex(columns=cols).fillna('')
        return df_result, True
    else:
        return None, False

# --- Streamlit UI ---
st.title("🏥 シフト自動作成アプリ")
st.markdown("1つのCSVファイル（統合版）をアップロードしてシフトを作成します。")

col1, col2 = st.columns(2)
with col1:
    year = st.number_input("作成する年", min_value=2026, value=2026)
with col2:
    month = st.number_input("作成する月", min_value=1, max_value=12, value=4)

st.divider()

file_all_in_one = st.file_uploader("📁 シフト設定CSV（ルール・希望・確定 統合版）をアップロード", type=['csv'])

if file_all_in_one:
    df_raw = pd.read_csv(file_all_in_one)
    
    with st.expander("読み込んだデータを確認"):
        st.dataframe(df_raw)

    if st.button("シフトを自動作成する", type="primary"):
        with st.spinner('計算中... (エラーが出た場合はCSVの条件を少し緩めてみてください)'):
            # 1つのCSVを内部で3つのデータに分解
            df_docs, df_reqs, df_fixed = parse_single_csv(df_raw, year, month)
            
            df_result, success = solve_shift(year, month, df_docs, df_reqs, df_fixed)
            
            if success:
                st.success("シフトの作成に成功しました！")
                st.dataframe(df_result, use_container_width=True)
                
                csv = df_result.to_csv(index=False).encode('utf-8-sig')
                st.download_button(
                    label="📥 シフト表をCSVでダウンロード",
                    data=csv,
                    file_name=f"shift_{year}_{month:02d}.csv",
                    mime="text/csv",
                )
            else:
                st.error("エラー：条件が厳しすぎてシフトが組めませんでした。")
                st.warning("【調整のヒント】\n左側の表の「月間最小回数」を減らすか、右側のカレンダーの「NG」をいくつか消してから、再度アップロードしてお試しください。")
else:
    st.info("👆 シフト設定CSVをアップロードしてください。")
