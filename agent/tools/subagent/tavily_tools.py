import os
from typing import Literal

from dotenv import load_dotenv, find_dotenv
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from tavily import TavilyClient

from api.context import get_thread_context
from api.monitor import monitor

load_dotenv(find_dotenv())

tavily_client = TavilyClient(api_key=os.getenv("tavily_api_key"))

# 全局字典，用于按 thread_id 记录每个会话的搜索次数（物理计数器）
_search_counters = {}


@tool
def internet_search(
        query: str,
        reflection_on_previous: str = "初次搜索无需反思",
        max_results: int = 5,
        topic: Literal["general", "news", "finance"] = "general",
        include_raw_content: bool = False,
        config: RunnableConfig = None  # LangChain 自动注入，LLM 无感知
):
    """
    根据问题进行网络查询，当需要获取外部互联网的公开信息、最新新闻或特定主题数据时使用此工具
    参数说明：
    query: 下一步要搜索的核心问题 / 关键词。
    reflection_on_previous: 强制要求！在进行第2次及以后的搜索时，你必须在此参数中填写详细的反思（例如：“上次搜索找到了A，但还缺少B，因此我这次要搜B”）。
    max_results: 控制返回结果数量，免费版建议不超过5
    topic: 限定搜索内容类型，提升结果相关性
    include_raw_content: 是否返回详细新闻，False简略版本 True详细版本
    返回值：
        dict: Tavily API 返回的结构化结果，包含以下核心字段：
            - query: 原始搜索词
            - results: 搜索结果列表，每个元素包含 url、content（摘要）、raw_content（原始内容，可选）等
        str: 初始化失败时返回错误提示字符串
    异常处理：
        捕获搜索过程中的所有异常并重新抛出，确保 Agent 能感知到搜索失败并处理
    """

    # 1. 获取当前会话ID
    thread_id = get_thread_context() or "default_thread"
    # 从 LangGraph 上下文中提取当前子 Agent 调用的唯一命名空间 (namespace)
    configurable = config.get("configurable", {}) if config else {}
    raw_ns = configurable.get("checkpoint_ns", "main")
    # 切分调用栈，只保留第一层的稳定任务 ID
    stable_subagent_id = raw_ns.split("|")[0]

    # 构造针对“本次子 Agent 任务”的独立 Key
    cur_subagent_id = f"{thread_id}_{stable_subagent_id}"
    # 获取当前会话的搜索次数
    current_count = _search_counters.get(cur_subagent_id, 0)

    # 2. 【核心拦截逻辑】：如果超过 3 次，直接物理熔断！
    if current_count >= 3:
        # 重置计数器（防止后续其他任务卡死）
        _search_counters[cur_subagent_id] = 0
        warning_msg = "【系统强制拦截】你已达到最大搜索上限（3次）！禁止再调用任何工具！请立即根据目前已获取的所有信息，直接输出最终的文本总结！"
        monitor.report_tool("网络搜索助手-internet_search-系统强制拦截", {"警告": warning_msg})
        return warning_msg

    # 3. 计数器加 1
    _search_counters[cur_subagent_id] = current_count + 1

    # 4. 上报搜索次数、反思内容与搜索问题到前端
    monitor.report_tool('internet_search', args={
        'reflection_on_previous': reflection_on_previous,
        'query': query,
        'search_count': f'{current_count + 1}',
        'cur_subagent_id': cur_subagent_id
    })

    # 5. 执行真正搜索
    try:
        return tavily_client.search(
            query=query,
            max_results=max_results,
            topic=topic,
            include_raw_content=include_raw_content
        )
    except Exception as e:
        return f"搜索失败: {str(e)}"


"""
    在 LangChain / LangGraph 架构中，提供了一个极其优雅的原生特性：RunnableConfig。
只要在工具的参数列表里加上 config: RunnableConfig = None，LangChain 就会自动注入当前运行节点的上下文，且绝对不会暴露给大语言模型（不影响 LLM 的 JSON Schema）。

checkpoint_ns参数: 
    命名空间标识：在 LangGraph 构建的复杂图（Graph）或 Multi-Agent（多智能体）架构中，系统可能包含多个子图或并行运行的 Agent。
checkpoint_ns 用来区分当前执行上下文属于哪一个子节点或子 Agent（例如主图为 "main"，子图可能为 "subgraph_1"）。

'checkpoint_ns' = {str} 'tools:2f1f9262-63f4-25bd-a334-f3cae7c53a04|tools:20404d4e-b38e-2155-f082-afa529b01a0c'
分析checkpoint_ns参数: 
在 LangGraph 的底层逻辑中，checkpoint_ns（命名空间）记录的是当前的调用栈树（Call Stack Tree），并用管道符 | 分隔：
    前半段 (tools:a1c62a9d...)：这是主智能体（Main Agent）调用 task 工具来拉起这个“网络搜索助手”时，LangChain 生成的任务级 UUID。
只要这个子智能体还在干活，这个 ID 就是绝对稳定不变的。
    后半段 (tools:44be... / tools:805b...)：这是子智能体内部调用 internet_search 时，LangChain 为“这一次具体工具调用”生成的动作级 UUID。
子智能体每搜一次，这个 UUID 就变一次。

提问1：
    请深入检索并分析最新发布的《国家基本药物目录》中对中成药（尤其是心脑血管、调脂减重类中成药）的调入标准和政策导向，
并收集该政策对中药企业（如沃华医药）市场拓展的行业评估与专家解读细节。

"""
