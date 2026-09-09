---
name: fox-converter
description: 读取用户本机 Fox Converter 已转换的邮件存档——按关键词/发件人/日期搜索邮件、读取全文（Markdown/纯文本）、列出与下载附件。当用户要求"查邮件""搜存档""读某封邮件""总结邮件往来"时使用本技能。
---

# Fox Converter 邮件存档技能

读取本机 Fox Converter 软件中已导入的 Foxmail 邮件存档（含正文与附件）。

## 前置条件

1. 本机 Fox Converter 正在运行（托盘图标），服务地址：`http://127.0.0.1:8732`
2. 已获得用户提供的 API Key（软件「Agent 接入」页生成）

## 探活

```bash
curl http://127.0.0.1:8732/api/health
```

返回 `archives`（存档数）与 `emails`（邮件总数）即服务可用。

## 鉴权

除 `/api/health` 外，跨进程/跨机访问需带请求头：

```
X-API-Key: <用户提供的 Key>
```

本机同源访问可省略。

## API

### 搜索邮件（关键词/全文）

```bash
curl -H "X-API-Key: $KEY" "http://127.0.0.1:8732/api/mails?search=关键词&page=1&per_page=20"
```

- `search`：匹配主题/发件人/收件人/正文（支持中文）
- `order`：`date_desc`（默认）/ `date_asc` / `subject`
- 返回 `items[]`：`id, subject, from, date, has_attachments, attachment_count`

### 读取邮件全文

```bash
curl -H "X-API-Key: $KEY" http://127.0.0.1:8732/api/mails/{id}
```

返回字段：`subject / from / to / cc / date / body_html / body_md / body_text`、
`attachment_names[]`（附件名）、`attachment_files[]`（相对路径）。

### 存档列表

```bash
curl -H "X-API-Key: $KEY" http://127.0.0.1:8732/api/archives
```

返回每个存档的 `id / name / email_count / total_size`。

### 按存档过滤

```bash
curl -H "X-API-Key: $KEY" "http://127.0.0.1:8732/api/mails?archive_id={id}"
```

### 下载附件

```bash
curl -H "X-API-Key: $KEY" -o 文件名 "http://127.0.0.1:8732/api/attachment/{邮件id}/{attachment_files中的相对路径}"
```

## 直接读导出文件（无需 API）

导出目录（默认 `文档\foxmail2md_export\<存档名>\`）：

```
├── manifest.json        # 全部邮件索引（含 message_id/subject/from/date/附件路径）
├── *.md                 # 每封邮件一个 Markdown（YAML front matter）
├── plaintext/*.txt      # 纯文本版（含主题/发件人/日期头摘要）
└── attachments/<hash>/  # 附件原文件
```

批量分析建议直接读 `plaintext/*.txt`（单文件自含上下文）或 `manifest.json` 做索引。

## 典型任务

- **找某主题的邮件并总结**：`/api/mails?search=主题词` → 对每封 `/api/mails/{id}` 读 `body_md` → 汇总
- **统计某发件人的往来**：`/api/mails?search=邮箱地址` → 按日期分组
- **按时间段梳理**：`/api/mails?order=date_asc` 翻页 → 按 `date` 过滤
- **取出附件**：详情中 `attachment_files` → 下载接口落盘
