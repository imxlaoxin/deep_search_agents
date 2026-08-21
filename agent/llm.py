import os

from dotenv import load_dotenv, find_dotenv
from langchain.chat_models import init_chat_model

load_dotenv(find_dotenv())  # find_dotenv会递归查找，保证找到.env


model = init_chat_model(
    model=os.getenv('llm_qwen_max'),
    model_provider='openai'
)

if __name__ == '__main__':
    ret = model.invoke("你好呀")
    print(ret.content)
