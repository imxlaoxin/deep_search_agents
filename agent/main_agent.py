import asyncio
import os
import shutil
import sys
import uuid
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend, LocalShellBackend
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent.llm import model
from agent.subagents.database_query_agent import database_query_agent
from agent.subagents.knowledge_base_agent import knowledge_base_agent
from agent.subagents.network_search_agent import network_search_agent
from agent.tools.main.markdown_tool import generate_markdown
from agent.tools.main.pdf_tool import convert_md_to_pdf
from agent.tools.main.upload_file_read_tool import read_file_content
from api.context import reset_session_context, set_thread_context, set_session_context
from api.monitor import monitor
from conf.agents_config import agent_config
from agent.tools.subagent.tavily_tools import _search_counters
from agent.tools.subagent.db_tools import _db_counters
from agent.tools.subagent.ragflow_tools import _rag_counters

main_agent_config = agent_config.main_agent
project_root = Path(__file__).parents[1]

# 1. 组装环境变量，确保当前 Python 环境的 bin/Scripts 目录优先置于 PATH 中
python_bin_dir = str(Path(sys.executable).parent)
scripts_dir = str(Path(sys.executable).parent / 'Scripts')
env_path = f"{python_bin_dir};{scripts_dir};{os.environ.get('PATH', '')}"

# 2. 初始化 Backend：使用 LocalShellBackend 替换 FilesystemBackend，提供真实文件操作与脚本执行能力
backend = LocalShellBackend(
    root_dir=project_root.resolve(),
    virtual_mode=True,
    env={'PATH': env_path}
)

main_agent = create_deep_agent(
    model=model,
    system_prompt=main_agent_config.system_prompt,
    checkpointer=InMemorySaver(),
    backend=backend,
    skills=['skills'],
    tools=[read_file_content, generate_markdown, convert_md_to_pdf],
    subagents=[network_search_agent, database_query_agent, knowledge_base_agent],
    interrupt_on={
        "execute": True,               # 主 Agent 的命令行执行
        "execute_sql_query": True      # 数据库 Agent 的 SQL 执行
    }
)

async def resume_deep_agent(thread_id: str, decisions: list):
    """
    接收前端审批结果，唤醒挂起的智能体继续执行
    """
    print(f"--- Resume Task: {thread_id} ---")
    session_dir_str, _, _ = _prepare_session_environment(thread_id)
    thread_token = set_thread_context(thread_id)
    session_token = set_session_context(session_dir_str)

    config = {"configurable": {"thread_id": thread_id}}

    try:
        # 使用 Command 恢复执行
        async for chunk in main_agent.astream(
            Command(resume={"decisions": decisions}),
            config=config,
            stream_mode="updates",
            subgraphs=True
        ):
            _process_stream_chunk(chunk, thread_id)
    except Exception as e:
        monitor._emit("error", f"Resume failed: {e}")
    finally:
        if 'session_token' in locals():
            reset_session_context(session_token, thread_token)


def _prepare_session_environment(thread_id: str):
    """
    初始化会话运行环境（会话文件夹,以及相对路径，上传文件的信息！）。
    目标：
    1. 创建独立的物理工作空间。
    2. 处理用户上传的文件。
    3. 生成供 Agent 和前端使用的路径上下文（提示词）。

    执行步骤：
    1. 创建绝对路径：`project_root/output/session_{uuid}`。
    2. 标准化路径：转换为 POSIX 风格 (`/`) 以兼容 LLM 和跨平台。
    3. 文件迁移：将 `updated/session_{uuid}` 中的文件复制到工作目录。
    4. 构造提示词：生成包含已上传文件列表的 Context 文本。

    Returns:
        tuple: (
            session_dir_str (str): 物理工作目录的绝对路径 (当前会话对应文件存储位置)。
            relative_session_dir (str): 相对于项目根目录的路径 (用于提示词)。
            uploaded_info (str): 注入到 Prompt 中的文件列表描述。
        )
    """
    # 1. [创建] 定义并创建会话的绝对输出路径
    session_dir = project_root / "output" / f"session_{thread_id}"
    session_dir.mkdir(parents=True, exist_ok=True)

    # 2. [标准化] 路径转为 POSIX 风格 (防止大模型因反斜杠产生幻觉)
    session_dir_str = str(session_dir).replace("\\", "/")

    # 3. [相对化] 获取相对路径 (用于提示词展示，如 "output/session_123")
    relative_session_dir = str(session_dir.relative_to(project_root)).replace("\\", "/")

    # 4. [迁移] 检查并处理上传文件
    upload_dir = project_root / "updated" / f"session_{thread_id}"
    uploaded_info = ""

    if upload_dir.exists():
        files = [f.name for f in upload_dir.iterdir() if f.is_file()]

        if files:
            for f in files:
                # 核心动作：将文件从临时上传区复制到正式工作区
                shutil.copy2(upload_dir / f, session_dir / f)

            # 5. [构造] 生成文件列表提示词
            uploaded_info = (f"\n    [已上传文件] 已加载到工作目录:\n" +
                             "\n".join([f"    - {f}" for f in files]) +
                             "\n    请优先使用工具读取并参考这些文件。")

    return session_dir_str, relative_session_dir, uploaded_info,


def _process_stream_chunk(chunk, thread_id):
    """
    处理 LangGraph 流式输出的增量状态 (Stream Processing)。
    支持 subgraphs=True 带来的 (namespace, data) 结构解析。
    目标：
    1. 解析 Agent 的每一步思考和行动。
    2. 识别关键事件（工具调用、子 Agent 委派、最终回复）。
    3. 通过 Monitor 实时上报状态给前端。
    核心逻辑：
    - 监听 `tool_calls` -> 记录日志，若是 'task' 则上报子 Agent 状态。
    - 监听 `content` -> 若无工具调用，则视为 Agent 的最终回复。
    Args:
        chunk (dict): 增量状态字典，如 {"node_name": {"messages": [AIMessage(...)]}}
    """
    # 1. 解析 (namespace, chunk_data) 结构
    if isinstance(chunk, tuple) and len(chunk) == 2:
        namespace, chunk_data = chunk
    else:
        return

    if not isinstance(chunk_data, dict):
        return

    # 2. 识别当前节点所属层级（空元组代表主 Agent，否则是子 Agent）
    is_main_agent = not namespace
    agent_context = f"{thread_id}_主 Agent" if is_main_agent else f"{thread_id}_子 Agent ({namespace[-1][0]})"

    # 3. 遍历并处理状态更新
    for node_name, state in chunk_data.items():

        # 捕获中断挂起事件
        if node_name == "__interrupt__":
            # LangGraph 的 interrupt 结构解析
            interrupt_data = state[0].value if isinstance(state, tuple) else state.value
            action_requests = interrupt_data.get('action_requests', [])
            print(f"\n [{agent_context}] 触发高危操作拦截，等待前端审批: {action_requests}")
            # 推送给前端
            monitor.report_interrupt(action_requests)
            continue

        if not state or "messages" not in state:
            continue

        messages = state["messages"]
        if isinstance(messages, list) and messages:
            last_msg = messages[-1]

            # 分支 1：处理模型决策消息
            if node_name == "model" and isinstance(last_msg, AIMessage):
                # 决定调用工具 (Tool Call)
                if hasattr(last_msg, 'tool_calls') and last_msg.tool_calls:
                    print(f'\n[{agent_context} | 节点: {node_name}] 决定调用工具/委派任务:')
                    for tool in last_msg.tool_calls:
                        tool_name = tool.get('name')
                        tool_args = tool.get('args')
                        print(f"   ├─ 工具名称: {tool_name}")
                        print(f"   └─ 参数内容: {tool_args}")

                        # ========== 核心修改区域 ==========
                        if is_main_agent:
                            if tool_name == 'task':
                                # 委派给子智能体
                                monitor.report_assistant(
                                    tool_args.get('subagent_type', 'Agent'),
                                    {"desc": tool_args.get('description')}
                                )
                            elif tool_name == 'write_todos':
                                # 【单独展示区】拦截 to_do-list 工具，发送专属事件
                                todos_list = tool_args.get('todos', [])
                                monitor.report_todos_update(todos_list)
                            else:
                                # 主智能体的其他工具 (execute, generate_markdown 等)
                                monitor.report_main_agent_tool(tool_name, tool_args)
                        # =================================

                # 只有主 Agent 的最终文本回复，才算作任务完成进行上报
                elif getattr(last_msg, 'content', None) and is_main_agent:
                    monitor.report_task_result(last_msg.content)

            # 分支 2：控制台打印工具执行结果 (便于后台 Debug 追踪)
            elif node_name == "tools":
                tool_name = getattr(last_msg, "name", "tool")
                print(f'\n[{agent_context} | 工具执行结果 - 工具名: {tool_name}]:')

                content_str = str(last_msg.content)
                if len(content_str) > 500:
                    content_str = content_str[:500] + ' ...\n[内容过长，已截断]'
                print(f'   └─ 输出: {content_str}')


# ====================== 核心执行逻辑 ======================
async def run_deep_agent(task_query: str, thread_id: str = None):
    """
    DeepAgents 核心执行入口 (Agent Execution Runtime)。

    目标：
    1. 接收用户的自然语言任务。
    2. 准备独立的运行环境 (Workspace)。
    3. 启动 LangGraph 智能体，并通过流式 (Stream) 实时处理每一步。
    4. 确保上下文隔离和异常安全。

    执行步骤：
    1. ID 初始化：确保每个任务有唯一的 `thread_id`。
    2. 环境准备：创建目录、迁移文件、生成路径信息。
    3. 上下文绑定：将 `thread_id` 和 `session_dir` 绑定到当前线程 (ContextVar)。
    4. 提示词构建：将环境信息注入到 Prompt。
    5. 流式执行：驱动 LangGraph 运行，并实时解析/上报每一个 Chunk。
    6. 资源清理：任务结束后（无论成功失败）重置上下文。
    """
    # 1. [ID 初始化] 确保有唯一的会话 ID
    if not thread_id: thread_id = str(uuid.uuid4())  # 这行代码用于测试使用
    print(f"--- Start Task: {task_query} (Thread: {thread_id}) ---")

    # 2. [环境准备] 创建目录、处理上传文件
    session_dir_str, relative_session_dir, uploaded_info = _prepare_session_environment(thread_id)

    # 3. [上下文绑定] 初始化 ContextVars (关键：隔离并发请求)
    thread_token = set_thread_context(thread_id)
    session_token = set_session_context(session_dir_str)
    # 给前端推送文件夹，方便后续查询当前会话对应文件夹下的所有文件
    monitor.report_session_dir(session_dir_str)

    # 4. [运行时配置] LangChain Config (注入记忆 key)
    config = {
        "configurable": {"thread_id": thread_id},  # 用于 MemorySaver 记忆上下文
    }
    # 5. [提示词构建] 动态注入环境约束
    path_instruction = f"""
    【工作环境指令】
    工作目录: {relative_session_dir}
    {uploaded_info}

    规则：
    1. 新生成文件必须保存到工作目录：'{relative_session_dir}/filename'
    2. 使用相对路径，禁止使用绝对路径
    3. 若存在上传文件，请先分析内容
    """

    # 6. [流式执行] 启动 Agent 循环
    try:
        # astream: 异步生成器，像流水线一样逐个吐出 Agent 的思考片段
        async for chunk in main_agent.astream(
                {"messages": [{"role": "user", "content": task_query + path_instruction}]},
                config=config,
                stream_mode="updates",   # 默认就是 "updates" 模式
                subgraphs=True
        ):
            # 实时处理每一个片段 (上报前端)
            _process_stream_chunk(chunk, thread_id)
        return "Done"
    except Exception as e:
        # 7. [异常处理] 兜底捕获
        print(f"Error: {e}")
        monitor._emit("error", f"Execution failed: {e}")
        return f"Error: {e}"
    finally:
        # 8. [资源清理] 必须重置 ContextVars，防止线程池复用导致的上下文污染
        if 'session_token' in locals():
            reset_session_context(session_token, thread_token)
        # 清空所有子智能体的物理计数器
        _del_counters(thread_id, _search_counters)
        _del_counters(thread_id, _db_counters)
        _del_counters(thread_id, _rag_counters)


def _del_counters(thread_id, counters):
    keys_to_del = [counter for counter in counters if counter.startswith(thread_id)]
    for key in keys_to_del:
        counters.pop(key, None)


# ====================== 本地测试入口 ======================
if __name__ == "__main__":
    task = "查询数据库中的药品信息，生成一个pdf文件！"
    asyncio.run(run_deep_agent(task))
