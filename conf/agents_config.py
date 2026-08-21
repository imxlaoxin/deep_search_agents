from dataclasses import dataclass
from pathlib import Path
from pprint import pprint

from omegaconf import OmegaConf


@dataclass
class Tavily:
    name: str
    description: str
    system_prompt: str


@dataclass
class db:
    name: str
    description: str
    system_prompt: str


@dataclass
class ragflow:
    name: str
    description: str
    system_prompt: str


@dataclass
class SubAgents:
    tavily: Tavily
    db: db
    ragflow: ragflow


@dataclass
class MainAgent:
    system_prompt: str


@dataclass
class AgentsConfig:
    main_agent: MainAgent
    sub_agents: SubAgents


config_path = Path(__file__).parents[1] / 'prompts' / 'agent_prompts.yml'
config = OmegaConf.load(config_path)
schema = OmegaConf.structured(AgentsConfig)
agent_config: AgentsConfig = OmegaConf.to_object(OmegaConf.merge(schema, config))

if __name__ == '__main__':
    pprint(agent_config)
