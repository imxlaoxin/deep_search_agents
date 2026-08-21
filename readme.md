## 项目概览

基于 DeepAgents（`create_deep_agent`）的多智能体协同系统：主 Agent 负责规划与文档生成，按需委派 3 个子 Agent——网络搜索助手（Tavily）、数据库查询助手（MySQL）、RAGFlow 知识库助手；执行过程通过 WebSocket 实时推送给 Vue 3 前端，高危操作（shell 执行、SQL 执行）触发 HITL 人工审批。

- 后端：FastAPI + WebSocket（`api/server.py`，端口 8000），Agent 运行时在 `agent/main_agent.py`
- 前端：Vue 3 + TypeScript + Vite（`ui/`），所有 API/WS 地址硬编码 `localhost:8000`，无代理配置
- Windows 专属项目：PDF 转换依赖本机 Word COM（pywin32）；Python 3.12 虚拟环境在 `.venv`（gitignored）

## 常用命令

```bash
# 启动后端（必须从项目根目录运行；导入 agent.main_agent 时会初始化 main_agent，较慢）
.venv/Scripts/python.exe api/server.py            # uvicorn 0.0.0.0:8000, reload=True
# 或: .venv/Scripts/python.exe -m uvicorn api.server:app --reload

# 不走 API 直接跑 Agent（main_agent.py 底部 __main__ 里有硬编码测试任务，改它即可）
.venv/Scripts/python.exe -m agent.main_agent

# 前端开发（默认端口 5173）
cd ui && npm run dev
# 前端构建: npm run build（vue-tsc -b && vite build）

# 数据库初始化: 导入 deep_search_agent.sql（pharma_db: drugs/inventory/sales_records 三张表）
```

多个工具模块（`db_tools.py`、`markdown_tool.py`、`pdf_tool.py`、`upload_file_read_tool.py`、`api/context.py`）带 `__main__` 自测块，用 `python -m <module>` 单独运行验证。

## 架构与核心链路

**任务执行链路**：`POST /api/task` → `run_deep_agent()`（`agent/main_agent.py`）→ `main_agent.astream(stream_mode="updates", subgraphs=True)` → `_process_stream_chunk()` 解析 `(namespace, chunk_data)` → `monitor` 上报 → WebSocket 推送前端。

- `main_agent` 在模块导入时构建（模块级单例）：`create_deep_agent` + `LocalShellBackend(root_dir=项目根, virtual_mode=True)` + `InMemorySaver`（重启即失忆）+ `skills=['skills']` + `interrupt_on={"execute": True, "execute_sql_query": True}`
- 三个子 Agent 是**普通 dict**（`name/description/system_prompt/tools`），定义在 `agent/subagents/*.py`，system_prompt 全部来自 `prompts/agent_prompts.yml`（经 `conf/agents_config.py` 的 OmegaConf + dataclass 加载）——改行为优先改 yml，不要硬编码 prompt
- **流式解析**：`subgraphs=True` 时 chunk 是 `(namespace, data)` 元组，空 namespace = 主 Agent，非空 = 子 Agent（`namespace[-1][0]` 取子 Agent 名）；节点 `__interrupt__` 出现即触发审批推送
- **HITL 审批闭环**：`monitor.report_interrupt()` 推送 `hitl_require_approval` → 前端 `POST /api/approve`（thread_id + decisions）→ `resume_deep_agent()` 用 `Command(resume={"decisions": decisions})` 唤醒

**会话隔离**（`api/context.py`）：`ContextVar` 保存 `thread_id` 和 `session_dir`，在 `run_deep_agent` 开头 set、`finally` 中 reset——工具函数无需传参即可取当前会话目录，这是并发安全的关键，新工具必须遵守此模式。

**前端推送**（`api/monitor.py`）：`ToolMonitor` 单例按 thread_id 定向发送事件（`tool_start`/`todos_updated`/`assistant_call`/`hitl_require_approval`/`task_result`/`session_created`），`ConnectionManager` 懒绑定事件循环；脚本模式（无 WebSocket）自动降级为控制台输出。

**目录约定**（均 gitignored）：
- `output/session_{thread_id}/` — Agent 工作目录，`_prepare_session_environment()` 创建并注入 prompt（新文件必须存这里，用相对路径）
- `updated/session_{thread_id}/` — 前端上传文件暂存区，任务启动时被复制进工作目录
- `large_tool_results/` — DeepAgents 框架自动落盘的大工具结果

## 关键机制与陷阱

- **子 Agent 熔断计数器**：tavily/db/ragflow 工具各自维护物理计数器 dict，key = `{thread_id}_{checkpoint_ns.split("|")[0]}`（从 `RunnableConfig` 注入的 checkpoint_ns 提取任务级稳定 ID），超过上限（搜索 3 次、SQL 重试 2 次、RAG 追问 2 次）直接返回"系统强制拦截"文案而非真执行，防止 LLM 死循环。`run_deep_agent` 的 `finally` 中按 thread_id 清理这些计数器
- **路径清洗**（`utils/path_utils.py`）：`resolve_path()` 处理 LLM 幻觉产生的路径——剥离虚拟前缀（`/workspace` 等）、识别 `updated/` 前缀、防止 session 目录嵌套重复；所有文件工具（read_file_content/generate_markdown/convert_md_to_pdf）都经它解析，以 ContextVar 中的 session_dir 为准
- **SQL 工具约束**：`execute_sql_query` 只允许 SELECT、最多返回 100 行，且执行前会触发 HITL 审批；MySQL 驱动是 `mysql-connector-python`（`import mysql.connector`），不是 pymysql
- **PDF 转换依赖本机 Word**：`utils/word_converter.py` 通过 win32com COM 把 MD→HTML→PDF，要求 Windows + 已安装 Microsoft Word；`requirements.txt` 里缺 `pywin32` 和 `markdown`，需单独安装
- **Skills 机制**：`skills/data-visualization/`（SKILL.md + `generate_chart.py`）是给 Agent 用的技能库，主 Agent 通过 `ls`/`read_file` 探索并用 `execute` 工具的 `command` 参数跑 `python skills/data-visualization/scripts/generate_chart.py --type bar --title "..." --x_labels "..." --y_values "..." --output "output/xxx.png"`——prompt 红线：严禁用 `code` 参数或把画图委派给子 Agent
