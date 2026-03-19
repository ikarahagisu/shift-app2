import streamlit as st
import pandas as pd
import pulp
import calendar
import datetime
import jpholiday

st.set_page_config(page_title="シフト自動作成アプリ", layout="wide")

# --- 定数定義 ---
SHIFTS_WEEKDAY = ['宿直A', '宿直B', '外来宿直']
SHIFTS_HOLIDAY = ['宿直A', '宿直B', '外来宿直', '日直A', '日直B', '外来日直']

def is_holiday(date, custom_holidays):
    """土日・祝日・独自休日判定"""
    return date.weekday() >= 5 or jpholiday.is_holiday(date) or (date.day in custom_holidays)

def generate_template_csv(year, month):
    """選択された年月に合わせたひな形CSVを生成する"""
    num_days = calendar.monthrange(year, month)[1]
    
    # 医師IDを削除し、氏名始まりに変更
    base_cols = ['氏名', '医師優先度', '月間最小回数', '月間最大回数', '最低空ける日数', 
                 '最大_宿直A', '最大_宿直B', '最大_外来宿直', '最大_日直A', '最大_日直B', '最大_外来日直']
    
    day_cols = [str(d) for d in range(1, num_days + 1)]
    cols = base_cols + day_cols
    
    dummy_data = [
        ['田中 太郎', 5, 2, 6, 2, 2, 2, 2, 1, 1, 1] + [''] * num_days,
        ['佐藤 花子', 3, 2, 6, 1, 2, 2, 2, 1, 1, 1] + [''] * num_days,
        ['鈴木 一郎', 3, 2, 6, 1, 2, 2, 2, 1, 1, 1] + [''] * num_days,
    ]
    
    df_template = pd.DataFrame(dummy_data, columns=cols)
    
    if num_days >= 15:
        df_template.at[0, '1'] = 'NG'
        df_template.at[1, '5'] = '希望3'
        df_template.at[2, '15'] = '宿直A'
    
    return df_template.to_csv(index=False).encode('utf-8-sig')

def parse_single_csv(df, year, month):
    """1つのCSVから、ルール・希望・確定シフトを抽出する"""
    df.columns = df.columns.astype(str)
    num_days = calendar.monthrange(year, month)[1]
    
    reqs_data = []
    fixed_data = []
    
    for _, row in df.iterrows():
        doc_name = row['氏名'] # 医師IDを氏名に変更
        for day in range(1, num_days + 1):
            col_name = str(day)
            if col_name in df.columns:
                val = str(row[col_name]).strip()
                if val == 'nan' or val == '':
                    continue
                
                d = datetime.date(year, month, day)
                
                if val.upper() == 'NG':
                    reqs_data.append({'日付': d, '氏名': doc_name, '種別': 'NG', '優先度': 0})
                elif val.startswith('希望'):
                    priority = 1
                    try:
                        priority = int(val.replace('希望', ''))
                    except ValueError:
                        pass
                    reqs_data.append({'日付': d, '氏名': doc_name, '種別': '希望', '優先度': priority})
                elif val in SHIFTS_HOLIDAY + SHIFTS_WEEKDAY:
                    fixed_data.append({'日付': d, '氏名': doc_name, 'シフト名': val})
                    
    df_reqs = pd.DataFrame(reqs_data) if reqs_data else pd.DataFrame(columns=['日付', '氏名', '種別', '優先度'])
    df_fixed = pd.DataFrame(fixed_data) if fixed_data else pd.DataFrame(columns=['日付', '氏名', 'シフト名'])
    return df, df_reqs, df_fixed

def solve_shift(year, month, df_docs, df_reqs, df_fixed, custom_holidays):
    num_days = calendar.monthrange(year, month)[1]
    dates = [datetime.date(year, month, d) for d in range(1, num_days + 1)]
    
    prob = pulp.LpProblem("DoctorShift", pulp.LpMaximize)
    
    x = {}
    for idx, doc in df_docs.iterrows():
        doc_name = doc['氏名']
        x[doc_name] = {}
        for d in dates:
            x[doc_name][d] = {}
            shifts = SHIFTS_HOLIDAY if is_holiday(d, custom_holidays) else SHIFTS_WEEKDAY
            for s in shifts:
                # 変数名に日本語やスペースが入るとエラーになることがあるため、行番号(idx)を使用
                x[doc_name][d][s] = pulp.LpVariable(f"x_{idx}_{d.day}_{s}", cat='Binary')

    for d in dates:
        shifts = SHIFTS_HOLIDAY if is_holiday(d, custom_holidays) else SHIFTS_WEEKDAY
        for s in shifts:
            prob += pulp.lpSum(x[doc['氏名']][d][s] for _, doc in df_docs.iterrows()) == 1

    for _, doc in df_docs.iterrows():
        doc_name = doc['氏名']
        for d in dates:
            shifts = SHIFTS_HOLIDAY if is_holiday(d, custom_holidays) else SHIFTS_WEEKDAY
            prob += pulp.lpSum(x[doc_name][d][s] for s in shifts) <= 1

    if not df_fixed.empty:
        for _, row in df_fixed.iterrows():
            d = row['日付']
            if d in dates:
                prob += x[row['氏名']][d][row['シフト名']] == 1

    for _, doc in df_docs.iterrows():
        doc_name = doc['氏名']
        total_shifts = pulp.lpSum(x[doc_name][d][s] for d in dates for s in (SHIFTS_HOLIDAY if is_holiday(d, custom_holidays) else SHIFTS_WEEKDAY))
        prob += total_shifts >= doc['月間最小回数']
        prob += total_shifts <= doc['月間最大回数']
        
        prob += pulp.lpSum(x[doc_name][d]['宿直A'] for d in dates if '宿直A' in x[doc_name][d]) <= doc['最大_宿直A']
        prob += pulp.lpSum(x[doc_name][d]['宿直B'] for d in dates if '宿直B' in x[doc_name][d]) <= doc['最大_宿直B']
        prob += pulp.lpSum(x[doc_name][d]['外来宿直'] for d in dates if '外来宿直' in x[doc_name][d]) <= doc['最大_外来宿直']
        prob += pulp.lpSum(x[doc_name][d]['日直A'] for d in dates if '日直A' in x[doc_name][d]) <= doc['最大_日直A']
        prob += pulp.lpSum(x[doc_name][d]['日直B'] for d in dates if '日直B' in x[doc_name][d]) <= doc['最大_日直B']
        prob += pulp.lpSum(x[doc_name][d]['外来日直'] for d in dates if '外来日直' in x[doc_name][d]) <= doc['最大_外来日直']

        min_interval = doc['最低空ける日数']
        if min_interval > 0:
            for i in range(len(dates) - min_interval):
                interval_sum = pulp.lpSum(
                    x[doc_name][dates[j]][s] 
                    for j in range(i, i + min_interval + 1) 
                    for s in (SHIFTS_HOLIDAY if is_holiday(dates[j], custom_holidays) else SHIFTS_WEEKDAY)
                )
                prob += interval_sum <= 1

    objective = 0
    if not df_reqs.empty:
        for _, req in df_reqs.iterrows():
            d = req['日付']
            doc_name = req['氏名']
            req_type = req['種別']
            priority = req['優先度']
            doc_priority = df_docs[df_docs['氏名'] == doc_name]['医師優先度'].values[0]
            
            if d in dates and doc_name in x:
                shifts = SHIFTS_HOLIDAY if is_holiday(d, custom_holidays) else SHIFTS_WEEKDAY
                day_sum = pulp.lpSum(x[doc_name][d][s] for s in shifts)
                if req_type == 'NG':
                    prob += day_sum == 0
                elif req_type == '希望':
                    objective += day_sum * priority * doc_priority

    prob += objective
    status = prob.solve()
    
    if pulp.LpStatus[status] == 'Optimal':
        result_data = []
        for d in dates:
            shifts = SHIFTS_HOLIDAY if is_holiday(d, custom_holidays) else SHIFTS_WEEKDAY
            row_data = {'日付': d.strftime('%Y/%m/%d'), '曜日': ['月', '火', '水', '木', '金', '土', '日'][d.weekday()]}
            for s in shifts:
                for doc_name in x:
                    if pulp.value(x[doc_name][d][s]) == 1:
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
st.markdown("年月を指定してひな形をダウンロードし、条件を入力してアップロードしてください。")

col1, col2 = st.columns(2)
with col1:
    year = st.number_input("作成する年", min_value=2026, value=2026)
with col2:
    month = st.number_input("作成する月", min_value=1, max_value=12, value=4)

# --- 独自休日設定エリア ---
num_days = calendar.monthrange(year, month)[1]
all_days = list(range(1, num_days + 1))
custom_holidays = st.multiselect(
    f"🎍 {month}月の「独自の休日（年末年始や創立記念日など）」があれば選択してください",
    options=all_days,
    format_func=lambda x: f"{month}月{x}日"
)

# --- カレンダー表示エリア ---
st.subheader(f"📅 カレンダー確認（{month}月）")
st.markdown("※ 色付きの日（土・日・祝・独自休日）は休日用の6枠、色なしの平日は3枠でシフトが組まれます。")

cal_matrix = calendar.monthcalendar(year, month)
cal_formatted = []

for week in cal_matrix:
    week_str = []
    for i, d in enumerate(week):
        if d == 0:
            week_str.append("")
        else:
            date_obj = datetime.date(year, month, d)
            if jpholiday.is_holiday(date_obj):
                week_str.append(f"{d} (祝)")
            elif d in custom_holidays:
                week_str.append(f"{d} (休)")
            elif i == 5 or i == 6:
                week_str.append(f"{d} (休)")
            else:
                week_str.append(str(d))
    cal_formatted.append(week_str)

df_cal = pd.DataFrame(cal_formatted, columns=["月", "火", "水", "木", "金", "土", "日"])

def color_calendar(val):
    if val == "":
        return ""
    d = int(str(val).split()[0])
    date_obj = datetime.date(year, month, d)
    if date_obj.weekday() == 6 or jpholiday.is_holiday(date_obj) or (d in custom_holidays):
        return "color: #ff4b4b; font-weight: bold; background-color: #ffeeee;"
    elif date_obj.weekday() == 5:
        return "color: #1e90ff; font-weight: bold; background-color: #eef5ff;"
    return ""

if hasattr(df_cal.style, "map"):
    styled_cal = df_cal.style.map(color_calendar)
else:
    styled_cal = df_cal.style.applymap(color_calendar)

cal_height = len(df_cal) * 35 + 40
st.dataframe(styled_cal, use_container_width=True, hide_index=True, height=cal_height)
st.divider()

# --- ひな形ダウンロード＆ルール説明エリア ---
st.subheader("📝 1. ひな形のダウンロードと入力")
csv_template = generate_template_csv(year, month)
st.download_button(
    label="📥 この月用の入力ひな形（CSV）をダウンロード",
    data=csv_template,
    file_name=f"shift_template_{year}_{month:02d}.csv",
    mime="text/csv",
)

with st.expander("💡 ひな形（CSV）の入力ルール・書き方を確認する"):
    st.markdown("""
    ダウンロードしたCSVファイルの「日付の列（1〜31）」には、以下のルールで入力してください。
    
    * **空欄**: いつでもシフトに入れる状態です。
    * **`NG`**: 絶対にシフトに入れない日です（半角英字で入力）。
    * **`希望〇`**: シフトに入りたい希望日です。「希望」のあとに優先度（数字）をつけます。
        * 例：`希望1`、`希望3` など。数字が大きいほど優先的にシフトが割り当てられます。
    * **シフト名の直接入力**: 事前に確定しているシフトがある場合は、そのシフト名を**一言一句そのまま**入力してください。
        * 平日（色なしの日）に入力できるシフト：`宿直A`, `宿直B`, `外来宿直`
        * 休日（赤色・青色の日）に入力できるシフト：`宿直A`, `宿直B`, `外来宿直`, `日直A`, `日直B`, `外来日直`
        
    **【基本ルールの設定（左側の列）について】**
    * **月間最小/最大回数**: その月に割り当てる合計シフト数の範囲です。
    * **最低空ける日数**: 「1」なら連勤不可、「2」なら中2日必要になります。
    * **最大_〇〇**: そのシフトに入る最大回数です。
    """)

st.divider()

# --- アップロード＆実行エリア ---
st.subheader("📁 2. シフト設定CSVのアップロードと作成")
file_all_in_one = st.file_uploader("入力が完了したCSVファイルをここにアップロードしてください", type=['csv'])

if file_all_in_one:
    df_raw = pd.read_csv(file_all_in_one)
    
    with st.expander("読み込んだデータを確認"):
        st.dataframe(df_raw)

    if st.button("✨ シフトを自動作成する", type="primary"):
        with st.spinner('最適なシフトを計算中... (数秒かかる場合があります)'):
            df_docs, df_reqs, df_fixed = parse_single_csv(df_raw, year, month)
            df_result, success = solve_shift(year, month, df_docs, df_reqs, df_fixed, custom_holidays)
            
            if success:
                st.success("シフトの作成に成功しました！")
                st.dataframe(df_result, use_container_width=True)
                
                csv = df_result.to_csv(index=False).encode('utf-8-sig')
                st.download_button(
                    label="📥 完成したシフト表をCSVでダウンロード",
                    data=csv,
                    file_name=f"shift_result_{year}_{month:02d}.csv",
                    mime="text/csv",
                )
            else:
                st.error("エラー：条件が厳しすぎてシフトが組めませんでした。")
                st.warning("【調整のヒント】\n・誰かの「月間最小回数」が多すぎませんか？\n・NG日が重なりすぎて、割り当てられる医師がいない日はありませんか？\n・「最低空ける日数」の制限が厳しすぎませんか？\n・事前に確定したシフトとルールが矛盾していませんか？")
