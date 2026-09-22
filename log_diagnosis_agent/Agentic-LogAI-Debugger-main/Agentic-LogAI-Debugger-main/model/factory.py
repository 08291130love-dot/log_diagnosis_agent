from langchain_openai import ChatOpenAI
from openai import OpenAI


def create_chat_model(api_key: str, base_url: str, model: str) -> ChatOpenAI:
    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=0.1,
        timeout=60,
        max_retries=1,
        max_tokens=4096,
        stream_usage=True,
        extra_body={"enable_thinking": False},
    )


def create_openai_client(api_key: str, base_url: str) -> OpenAI:
    if not api_key.strip():
        raise ValueError("请先填写百炼 API Key。")
    return OpenAI(api_key=api_key, base_url=base_url, timeout=60, max_retries=1)
