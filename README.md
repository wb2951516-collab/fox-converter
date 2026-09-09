# Fox Converter — Foxmail 存档(.fox)转 Markdown 阅读器

将 Foxmail 邮件存档（`.fox`）一键转换为 Markdown + 附件，并提供本地 Web 阅读界面。可直接对接 openclaw 等 AI 工具做向量化入库（RAG）。

> 内部数据目录为 `%APPDATA%\foxmail2md`（历史原因），与显示名称 Fox Converter 指向同一软件。

## 功能

- **双击即用**：`FoxConverter.exe` 启动内置服务并自动打开界面（支持 Chrome/Edge 独立窗口模式）
- **便捷转换**：界面里选择 `.fox` 文件（支持文件选择器或直接粘贴路径）→ 实时进度 → 完成立即可读
- **三栏阅读器**：
  - 邮件列表：**全文搜索**（中文友好）+ 排序 + **分组**（按主题 / 发件人 / 收件人，主题自动归并 Re:/转发: 会话链）+ 分页
  - 阅读区四个视图：**原文**（全宽自适应、内嵌图直接显示）/ **Markdown** / **纯文本** / **附件**（有附件时出现，列出文件名与下载按钮）
  - 一键复制当前视图内容
- **批量导出**：`markdown/` 区（`*.md` + YAML front matter）+ `plaintext/` 区（`*.txt` 纯文本含邮件头摘要）+ `attachments/` 附件目录 + `manifest.json` 索引
- **深浅色主题**、主题色自定义、设置持久化
- **CLI 批处理** + 系统托盘（打开界面 / 打开导出目录 / 退出）

## 快速开始

### 方式一：直接运行 exe

```
dist/FoxConverter.exe       # 双击即可
```

### 方式二：源码运行

```bash
pip install -r requirements.txt
python run.py
```

### CLI 批量转换

```bash
python -m foxmail2md 存档.fox -o ./output              # 全量
python -m foxmail2md 存档.fox -o ./output --limit 20   # 测试前 20 封
python -m foxmail2md 存档.fox -o ./output --incremental # 增量（Message-ID 去重）
```

## 使用流程

1. 启动后点击左下角 **「导入 .fox 文件」**
2. 文件选择器选中存档（或粘贴完整路径后点「使用」）
3. **「开始转换」**，等待进度完成（实测 1.18GB / 1108 封约 60~90 秒）
4. 左侧列表浏览邮件（可切换分组方式）；点击阅读
5. 阅读区切换 原文 / Markdown / 纯文本 / 附件；附件视图内可下载

## 导出结果结构（供 RAG 入库）

```
output/
├── manifest.json                  # 全部邮件索引（供程序消费）
├── 20260817_主题_hash.md          # Markdown 区
├── ...                            # 每封邮件一个 .md
├── plaintext/
│   └── 20260817_主题_hash.txt     # 纯文本区（含邮件头摘要，AI 单文件可读）
└── attachments/
    └── <hash>/                    # 每封邮件的附件（含内嵌图）
        ├── 报表.xlsx
        └── image001.png
```

`manifest.json` 每条含 `md_file`、`text_file`、`attachment_files`、`inline_files`、主题/发件人/日期等字段。

### 对接 openclaw / RAG 的建议

- **分区入库**：`plaintext/` 喂 embedding（干净文本），`*.md` 用于人读与展示
- **切片**：按邮件整封切片，manifest 字段作元数据随向量存储
- **检索增强**：`subject`/`from`/`date` 做结构化过滤字段
- **附件**：Office/PDF 附件按 `attachment_files` 路径单独做文档解析

## 智能体接入（Agent Skills）

让 openclaw 系智能体（WinClaw 小龙虾等）、Claude 等直接读取已导入的邮件：

1. 打开软件左侧 **「Agent 接入」** 页
2. **复制提示词** 粘贴给你的智能体——它会自动下载并安装技能（`/skill/download`）
3. **复制 API Key** 发给智能体完成配置（长期有效，可随时重新生成）

之后即可对智能体说"搜一下关于 XX 的邮件""把这封邮件的附件取出来"。

也可以手动安装：技能文件在 `foxmail2md/skill/SKILL.md`（兼容 AgentSkills 规范），或直接调用本地 HTTP API（`/api/health` 探活，`/api/mails?search=` 搜索，详情含 `body_md` 与附件路径）。

## 设置

| 设置项 | 说明 |
|---|---|
| 界面风格 | 浅色 / 深色（深色下邮件原文自动置于浅色卡片保证可读） |
| 主题色 | 任意颜色 |
| 打开方式 | 系统默认浏览器 / Chrome / Edge 的 `--app` 独立窗口 |
| 导出目录 | 默认 `文档/foxmail2md_export/<存档名>/`（修改后立即对下次解析生效） |

## 性能与质量（1.18GB 真实存档实测）

| 指标 | 数值 |
|---|---|
| 邮件解析 | 1108/1108 成功（100%） |
| 主题分组 | 1108 封归并为 673 个会话主题 |
| 附件 | 763 个（724MB），中文名/内嵌图正常 |
| 编码容错 | GBK/GB18030/UTF-8/UTF-16，乱码率 0.09% |

## .fox 格式（逆向成果）

详见 [docs/fox-format.md](docs/fox-format.md)。要点：文件头 `ARCF` 魔数 + 索引区；每封邮件前有 16 字节魔数 `10×7 11×6 53 0D 0A`，其后是**明文标准 RFC822 MIME**。本工具据此流式 carve，无需安装 Foxmail。

## 项目结构

```
foxmail2md/
├── core/
│   ├── fox_parser.py    # .fox 流式解析（mmap + 魔数 carve）
│   ├── mail_parser.py   # MIME → 结构化邮件（编码容错/内嵌图/附件）
│   ├── exporter.py      # 导出（md/plaintext 双区/cid 替换/去重）
│   └── store.py         # SQLite 索引 + 中文搜索
├── server/app.py        # FastAPI（解析进度 SSE/列表分组/附件下载/设置）
├── web/                 # 前端（三栏布局，原生 JS，无构建链）
├── config.py            # 配置持久化
├── launcher.py          # 启动器（浏览器 + 托盘）
└── cli.py               # 命令行入口
```

## 从源码打包

```bash
pip install -r requirements-build.txt
python tools/make_icon.py
pyinstaller --onefile --noconsole --name FoxConverter --icon assets/icon.ico ^
  --add-data "foxmail2md/web;web" --add-data "assets;assets" ^
  --hidden-import pystray._win32 entry.py
```

## 注意事项

- 本工具只读解析 `.fox`，不改动原文件
- 存档包含完整邮件与附件，**注意隐私**：接入 RAG 前请确认部署环境访问控制
- Windows 下首次运行如被杀软拦截，为 PyInstaller 单文件常见误报，加白名单即可
- 若浏览器显示异常（旧缓存），Ctrl+F5 强制刷新一次即可
