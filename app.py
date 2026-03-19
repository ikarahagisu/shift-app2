<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>シフト希望＆確定入力画面</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://unpkg.com/tabulator-tables@5.5.2/dist/css/tabulator.min.css" rel="stylesheet">
    <script type="text/javascript" src="https://unpkg.com/tabulator-tables@5.5.2/dist/js/tabulator.min.js"></script>
    <style>
        body { background-color: #f8f9fa; padding: 20px; }
        #shift-table { margin-top: 20px; background-color: white; box-shadow: 0 0 10px rgba(0,0,0,0.1); }
        .control-panel { margin-top: 20px; padding: 15px; background-color: white; border-radius: 8px; box-shadow: 0 0 10px rgba(0,0,0,0.1); }
    </style>
</head>
<body>

    <div class="container-fluid">
        <h2 class="mb-3">シフト希望・確定入力グリッド (2026年4月)</h2>
        <p>セルをクリックして、「希望」「NG」または「確定シフト（手動ロック）」を選択できます。<br>右下のボタンを押すと、空いているセルをPythonが自動で埋めます。</p>
        
        <div id="shift-table"></div>

        <div class="control-panel d-flex justify-content-end align-items-center">
            <span class="me-3 text-muted">※1日・2日は平日（3枠）、3日〜5日は休日（6枠）です。</span>
            <button id="send-data-btn" class="btn btn-primary px-4 py-2 fw-bold">
                シフト自動作成をスタート (空欄を埋める)
            </button>
        </div>
    </div>

    <script>
        // 1. セルの色分けルール（Formatter）
        var colorFormatter = function(cell, formatterParams, onRendered) {
            var value = cell.getValue();
            var el = cell.getElement();
            
            if (value === "NG") {
                el.style.backgroundColor = "#ffe6e6"; // 薄い赤
                el.style.color = "#cc0000";
            } else if (value === "希望") {
                el.style.backgroundColor = "#e6f2ff"; // 薄い青
                el.style.color = "#0066cc";
            } else if (["宿直A", "宿直B", "外来宿直", "日直A", "日直B", "外来日直"].includes(value)) {
                el.style.backgroundColor = "#fff0b3"; // 薄い黄色（確定シフト）
                el.style.fontWeight = "bold";
            } else {
                el.style.backgroundColor = ""; // 空白はそのまま
            }
            return value || "";
        };

        // 2. 選択肢（リスト）
        var cellOptions = ["", "希望", "NG", "宿直A", "宿直B", "外来宿直", "日直A", "日直B", "外来日直"];

        // 3. 【重要】ダミーデータを6人に増やす（休日6枠に対応するため）
        var tableData = [
            {id: 1, name: "A先生", day1: "", day2: "NG", day3: "希望", day4: "", day5: ""},
            {id: 2, name: "B先生", day1: "宿直A", day2: "", day3: "", day4: "NG", day5: ""},
            {id: 3, name: "C先生", day1: "", day2: "希望", day3: "外来日直", day4: "", day5: "宿直B"},
            {id: 4, name: "D先生", day1: "", day2: "", day3: "", day4: "", day5: ""},
            {id: 5, name: "E先生", day1: "", day2: "", day3: "", day4: "", day5: ""},
            {id: 6, name: "F先生", day1: "", day2: "", day3: "", day4: "", day5: ""},
        ];

        // 4. 共通の列設定（入力をリスト形式にし、色分けを適用）
        var dayColumnConfig = { editor: "list", editorParams: {values: cellOptions, autocomplete: true}, formatter: colorFormatter, width: 100, align: "center", headerSort: false };

        // 5. Tabulatorの作成
        var table = new Tabulator("#shift-table", {
            data: tableData,
            layout: "fitData",
            columns: [
                {title: "医師名", field: "name", frozen: true, width: 120, cssClass: "fw-bold", headerSort: false},
                {title: "1日(水)", field: "day1", ...dayColumnConfig},
                {title: "2日(木)", field: "day2", ...dayColumnConfig},
                {title: "3日(金・祝)", field: "day3", ...dayColumnConfig},
                {title: "4日(土)", field: "day4", ...dayColumnConfig},
                {title: "5日(日)", field: "day5", ...dayColumnConfig},
            ],
        });

        // 6. 送信ボタンを押した時の処理（Pythonと通信する）
        document.getElementById("send-data-btn").addEventListener("click", function() {
            var currentData = table.getData();
            
            // ボタンの文字を変えて、処理中であることをアピール
            var btn = document.getElementById("send-data-btn");
            btn.innerText = "シフト計算中...";
            btn.disabled = true;

            // Python（FastAPI）の窓口へデータを送信
            fetch("http://127.0.0.1:8000/generate_shift", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(currentData)
            })
            .then(response => response.json())
            .then(data => {
                // Pythonから返事が返ってきた時の処理
                
                // 計算ステータスを確認（Optimalなら大成功）
                if (data.status === "success" && data.calculated_status === "Optimal") {
                    // 【ここが最大のポイント！】
                    // Pythonが空白を埋めた「答え」のデータ（data.calculated_data）を受け取る
                    var resultData = data.calculated_data;
                    
                    // Tabulatorの表を、新しいデータ（答え）で丸ごと更新する
                    table.setData(resultData);
                    
                    alert("シフトの自動生成に成功しました！\n空いているセルをPythonが最適に埋めました。");
                    console.log("計算結果:", resultData);
                } else if (data.calculated_status === "Infeasible") {
                    // 計算不能（パズル失敗）の場合
                    alert("シフトを自動生成できませんでした（計算不能）。\n条件が厳しすぎるか、人が足りません。NGや確定シフトを減らして試してください。");
                } else {
                    alert("Pythonサーバーからの返事：\n" + data.message);
                }
            })
            .catch(error => {
                alert("エラーが発生しました。Pythonサーバーが起動しているか確認してください。");
                console.error("Error:", error);
            })
            .finally(() => {
                // ボタンを元の状態に戻す
                btn.innerText = "シフト自動作成をスタート (空欄を埋める)";
                btn.disabled = false;
            });
        });
    </script>

</body>
</html>