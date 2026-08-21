from agent.tools.subagent.tavily_tools import internet_search
from conf.agents_config import agent_config

sub_agents_config = agent_config.sub_agents

network_search_agent = {
    'name': sub_agents_config.tavily.name,
    'system_prompt': sub_agents_config.tavily.system_prompt,
    'description': sub_agents_config.tavily.description,
    'tools': [internet_search]
}
