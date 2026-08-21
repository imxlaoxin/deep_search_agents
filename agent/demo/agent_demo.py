from pprint import pprint

from langchain.agents import create_agent

from agent.llm import model
from agent.tools.subagent.tavily_tools import internet_search

agent = create_agent(
    model=model,
    tools=[internet_search],
    system_prompt=
    """
        你是一个专业的网络信息查询助手，你可以根据用户的问题，从互联网中检索相关信息，你掌握的工具包括 internet_search 工具，此工具可以根据用户的问题，从互联网中检索非内部的公开信息。
      【强制执行规则】：
      1. 面对任何搜索任务，你**必须**拆分出至少 3 个不同的搜索关键词（例如针对AI话题，可拆分为"AI技术突破"、"AI商业应用"、"AI政策监管"）。
      2. 你必须且强制**并发调用 `internet_search` 工具至少 3 次**，每次传入不同的 `query`。绝对不允许只调用 1 次工具。
      3. 最多允许调用 5 次工具，超过 5 次将被强制终止。
    """
)

if __name__ == '__main__':
    ret = agent.invoke({
        'messages': [
            {'role': 'user', 'content': '最近关于ai的有什么话题吗？'}
        ]
    })
    pprint(ret)

"""
调用internet_search工具，参数为： AI技术突破 5 general False
调用internet_search工具，参数为： AI商业应用 5 general False
调用internet_search工具，参数为： AI政策监管 5 general False
"""
