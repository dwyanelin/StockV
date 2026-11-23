# StockV — LINE 股票查詢機器人

在 LINE 聊天（含群組）輸入簡單指令，立刻取得股票圖表與法人動向。

<p>
  <b>加好友：</b>LINE ID <code>@564vgteu</code>（帳號名稱 StockV）
</p>

## 指令

| 指令 | 功能 | 範例 |
|------|------|------|
| `P` + 代號/股名 | 當日即時走勢圖 | `P2330`、`P玉晶光`、`PAAPL` |
| `K` + 代號/股名 | 日 K 線圖（近 6 個月，含 MA5/20/60 與成交量） | `K2330`、`K玉晶光`、`KTSLA` |
| `T` + 代號/股名 | 三大法人買賣超（近 10 個交易日）+ 外資持股比率 | `T2330`、`T台積電` |
| `help` / `指令` | 顯示使用說明 | |

- 台股支援數字/ETF 代號與中文股名（上市 `.TW`、上櫃/興櫃 `.TWO` 自動判斷），美股輸入英文代號。
- `T` 指令僅支援台股上市股票（資料來源：證交所）。
- 台股圖表紅漲綠跌、美股綠漲紅跌，符合各自市場慣例。
- 群組中只有符合指令格式的訊息會觸發回覆，不會干擾正常聊天。

## 系統架構

```
LINE 用戶 → LINE Platform → Cloud Run (FastAPI)
                              ├─ /webhook   LINE 事件入口（驗簽 → 解析指令）
                              ├─ /charts/*  圖表 PNG（回覆訊息中的圖片網址）
                              └─ /          健康檢查
資料來源：Yahoo Finance（台/美股價）、台灣證交所 T86 / MI_QFIIS（法人資料）
圖表：matplotlib + mplfinance（Noto Sans CJK 中文字型）
```

| 項目 | 值 |
|------|-----|
| GCP 專案 | `stockv-linebot` |
| Cloud Run 服務 | `stockv`（asia-east1） |
| 服務網址 | `https://stockv-129246509990.asia-east1.run.app` |
| LINE channel | StockV（channel ID 2010793948，provider「D」） |

## 本地開發

```bash
# 使用 Docker（建議，與正式環境一致）
docker build -t stockv .
docker run --rm -p 8080:8080 --env-file .env stockv

# 圖表煙霧測試（不需 LINE 憑證）
docker run --rm -v "$PWD/tests:/srv/tests" -v "$PWD/test-output:/srv/test-output" \
  stockv python tests/manual_test.py
```

`.env`（不進版控）需要兩個值，皆從 [LINE Developers Console](https://developers.line.biz/console/channel/2010793948/messaging-api) 取得：

```
LINE_CHANNEL_SECRET=<Basic settings 頁的 Channel secret>
LINE_CHANNEL_ACCESS_TOKEN=<Messaging API 頁的 Channel access token>
```

## 部署（更新程式後）

```bash
gcloud run deploy stockv --source . --region asia-east1 --project stockv-linebot
```

環境變數已存在服務設定中，重新部署不會遺失。若要更改：

```bash
gcloud run services update stockv --region asia-east1 --project stockv-linebot \
  --set-env-vars "^@^LINE_CHANNEL_SECRET=...@LINE_CHANNEL_ACCESS_TOKEN=...@BASE_URL=https://stockv-129246509990.asia-east1.run.app"
```

## 維運備忘

- **費用**：Cloud Run 依用量計費，個人/群組使用量幾乎落在免費額度內；LINE 用回覆訊息（reply）不計入月度推播額度。
- **冷啟動**：閒置後第一則指令約多等 5-10 秒屬正常。
- **記錄**：`gcloud run services logs read stockv --region asia-east1 --project stockv-linebot`
- **圖表暫存**：PNG 存於容器 `/tmp`，30 分鐘自動清除；LINE 在回覆當下就會抓取圖片，過期不影響已傳送的訊息。

## V2 可以加的東西

- `KW`/`KM` 週K月K、`T` 支援上櫃（櫃買中心 API）
- 到價提醒（需要資料庫 + 定時觸發 + push message）
- Rich menu 圖文選單、Flex Message 卡片式報價
- 自選股清單（記住每個用戶常查的股票）
