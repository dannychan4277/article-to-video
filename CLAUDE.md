# article-to-video

把財經文章做成解說影片的 Claude Code 外掛（全部用財經圖表，文章裡的圖重畫成圖表，見 `docs/adr/0001-只用財經圖表風格.md`；zh-TW AI 配音，可選 edge-tts、ElevenLabs、Google）。

## Agent skills

### Issue tracker

規格與工作票放在 GitHub Issues（dannychan4277/article-to-video），用 `gh` 操作。See `docs/agents/issue-tracker.md`.

### Triage labels

用預設五個標籤：needs-triage、needs-info、ready-for-agent、ready-for-human、wontfix。See `docs/agents/triage-labels.md`.

### Domain docs

單一用語表：根目錄 `GLOSSARY.md` ＋ `docs/adr/`。See `docs/agents/domain.md`.
