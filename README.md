# StockV — LINE 股票查詢機器人

在 LINE 聊天（含群組）輸入簡單指令，立刻取得股票圖表與法人動向。

<p>
  <b>加好友：</b>LINE ID <code>@564vgteu</code>（帳號名稱 StockV）
</p>

## 指令

| 指令 | 功能 | 範例 |
|------|------|------|
| `#` + 代號/股名 | 極速報價卡（私訊可直接輸入代號/股名） | `#2330`、`#台積電`、`#BTC` |
| `P` + 代號/股名 | 當日即時走勢圖（含分時量） | `P2330`、`P玉晶光`、`P台股`、`P外匯` |
| `K` / `KW` / `KM` + 代號/股名 | 日 K（6月）/ 週 K（2年）/ 月 K（5年） | `K2330`、`KW台積電`、`KMAAPL` |
| `T` + 代號/股名 | 三大法人買賣超（上市 / 上櫃 / 大盤） | `T2330`、`T元太`、`T台股` |
| `D` + 代號/股名 | 歷年股利政策與預估殖利率 | `D2330`、`D0050`、`DAAPL` |
| `F` + 代號/股名 | 基本面（估值 / 月營收 / 融資融券） | `F2330`、`F玉晶光`、`FAAPL` |
| `help` / `指令` | 顯示使用說明與快捷按鈕 | |

- 台股支援數字/ETF 代號與中文股名（上市 `.TW`、上櫃/興櫃 `.TWO` 自動判斷），並支援大盤（`台股`）、美股、國際指數（`SOX`/`SP500`/`NASDAQ`）、匯率（`外匯`/`USD/TWD`/`日圓`）與加密貨幣（`BTC`/`ETH`）。
- 卡片底部提供一鍵切換按鈕（走勢 / 日K / 週K / 月K / 法人 / 股利 / 基本面），免重複輸入。
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

## 部署（CI/CD 自動部署）

每次 PR 合併至 `main` 後，GitHub Actions（`.github/workflows/ci.yml`）會自動執行：
1. **CI 檢查**：Ruff lint + pytest 單元測試（總覆蓋率與核心模組單檔分支覆蓋率皆須 ≥ 90%）。
2. **無金鑰自動部署（CD）**：CI 全部通過後，透過 GCP Workload Identity Federation（OIDC，無靜態金鑰）自動建置並部署至 Cloud Run（`max-instances=1`），最後執行健康檢查驗證。

如需手動觸發或從本機緊急部署：

```bash
gcloud run deploy stockv --source . --region asia-east1 --project stockv-linebot --max-instances=1
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
