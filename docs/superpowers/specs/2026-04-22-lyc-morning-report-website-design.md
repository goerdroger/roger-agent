# LYC 早報網站設計規格

**日期：** 2026-04-22
**狀態：** 待實作

---

## 目標

將每日 LYC 早報從 Claude Code 對話輸出，轉為一個靜態 HTML 網頁，每天由 GitHub Actions 自動產生並部署到 GitHub Pages。

---

## 受眾

僅供頻道主（羅傑）私人使用，不對外公開內容。

---

## 功能範圍

### 包含
- 近期影片 Top 5（觀看數排序）：縮圖、標題、觀看數、讚數、留言數
- 新留言（昨天 00:00 UTC 之後）：依影片分組，含草擬回覆
- 今日行事曆：事件名稱 + 時間

### 不包含
- Gmail 信件（隱私考量）

---

## 技術方案

| 項目 | 選擇 |
|------|------|
| 前端框架 | 純 HTML + Tailwind CSS（CDN） |
| 資料產生 | Python（morning_report.py 新增 generate_html()） |
| 部署 | GitHub Pages（gh-pages branch） |
| 觸發 | GitHub Actions，每天 09:00 台灣時間（01:00 UTC） |
| Repo 可見度 | public（roger-agent 改為 public） |
| 網址 | https://goerdroger.github.io/roger-agent/ |

---

## 視覺設計

- **風格：** 淺色系，乾淨簡潔
- **背景：** `#FFFFFF`
- **卡片：** `#F9FAFB`
- **主色：** `#4F46E5`（靛藍，用於標題、強調）
- **字型：** 系統 sans-serif
- **RWD：** 支援手機（影片卡片手機 2 欄、桌機 5 欄）

---

## 頁面佈局

```
Header：📋 LYC 早報｜YYYY-MM-DD
────────────────────────────────
🎬 近期影片（Top 5 by 觀看數）
  [縮圖] 標題（最多兩行）
  👁 N  👍 N  💬 N
────────────────────────────────
💬 新留言（昨天之後）
  影片標題（小標）
    @作者 · 時間
    「留言內容」
    📝 草擬回覆：「...」（灰底縮排框）
────────────────────────────────
📅 今日行事曆
  🕐 HH:MM  事件名稱
  （若無）今日無行程
────────────────────────────────
Footer：最後更新：YYYY-MM-DD HH:MM 台灣時間
```

---

## 資料流程

```
GitHub Actions（01:00 UTC）
  ├── YouTube API → 影片清單 + 留言
  ├── Google Calendar API → 今日行程
  ▼
morning_report.py
  ├── generate_html() → index.html
  ▼
git commit + push → gh-pages branch
  ▼
https://goerdroger.github.io/roger-agent/
```

---

## 修改清單

1. `roger-agent` repo 改為 public
2. `scripts/morning_report.py` — 新增 `generate_html()` 函式
3. `.github/workflows/morning_report.yml` — 新增 gh-pages deploy 步驟
4. GitHub repo 設定啟用 GitHub Pages（來源：gh-pages branch）

---

## 注意事項

- Secrets（DISCORD_WEBHOOK、GOOGLE_CLIENT_ID 等）確認沒有 hardcode 在程式碼裡
- 草擬回覆僅顯示在頁面上，網站本身不會自動發送 YouTube 回覆
- 每次 Actions 執行覆蓋同一份 index.html（不保留歷史）
