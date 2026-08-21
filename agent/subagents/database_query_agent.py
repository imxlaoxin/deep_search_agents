from agent.tools.subagent.db_tools import list_database_tables, get_table_data, execute_sql_query
from conf.agents_config import agent_config

sub_agents_config = agent_config.sub_agents

database_query_agent = {
    'name': sub_agents_config.db.name,
    'description': sub_agents_config.db.description,
    'system_prompt': sub_agents_config.db.system_prompt,
    'tools': [list_database_tables, get_table_data, execute_sql_query]
}