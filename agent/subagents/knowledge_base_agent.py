from agent.tools.subagent.ragflow_tools import get_assistant_list, create_ask_delete
from conf.agents_config import agent_config

sub_agents_config = agent_config.sub_agents

knowledge_base_agent = {
    'name': sub_agents_config.ragflow.name,
    'description': sub_agents_config.ragflow.description,
    'system_prompt': sub_agents_config.ragflow.system_prompt,
    'tools': [get_assistant_list, create_ask_delete]
}