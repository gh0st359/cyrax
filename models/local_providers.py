"""
CYRAX Local Model Providers
Clients for Ollama, LM Studio, and other locally-hosted models.
"""

import json
from typing import Optional

import httpx

from models.api_providers import BaseModelClient
from utils.logging import get_logger


class OllamaClient(BaseModelClient):
    """Client for Ollama local model server."""

    def __init__(self, api_url: str = "http://localhost:11434", model: str = "llama3.1"):
        self.api_url = api_url.rstrip("/")
        self.model = model
        self.provider_name = "ollama"

    def generate(
        self,
        system: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> dict:
        logger = get_logger()

        # Build messages in Ollama chat format
        api_messages = [{"role": "system", "content": system}]
        for msg in messages:
            api_messages.append({"role": msg["role"], "content": msg["content"]})

        payload = {
            "model": self.model,
            "messages": api_messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        try:
            with httpx.Client(timeout=300.0) as client:
                response = client.post(
                    f"{self.api_url}/api/chat",
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()

            content = data.get("message", {}).get("content", "")
            tokens_in = data.get("prompt_eval_count", 0)
            tokens_out = data.get("eval_count", 0)

            logger.log_model_call(
                agent_id="model",
                provider=self.provider_name,
                model=self.model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )

            return {
                "content": content,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
            }

        except httpx.ConnectError:
            error_msg = (
                f"Cannot connect to Ollama at {self.api_url}. "
                "Ensure Ollama is running (ollama serve)."
            )
            logger.log_error("model", error_msg)
            raise ConnectionError(error_msg)
        except Exception as e:
            logger.log_error("model", f"Ollama error: {e}")
            raise

    def generate_stream(
        self,
        system: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ):
        """Stream response chunks from Ollama /api/chat."""
        logger = get_logger()

        api_messages = [{"role": "system", "content": system}]
        for msg in messages:
            api_messages.append({"role": msg["role"], "content": msg["content"]})

        payload = {
            "model": self.model,
            "messages": api_messages,
            "stream": True,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        full_content = []
        prompt_eval_count = 0
        eval_count = 0

        try:
            with httpx.stream(
                "POST",
                f"{self.api_url}/api/chat",
                json=payload,
                timeout=300.0,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    delta = data.get("message", {}).get("content", "")
                    if delta:
                        full_content.append(delta)
                        yield {"delta": delta, "done": False}

                    if data.get("done"):
                        prompt_eval_count = data.get("prompt_eval_count", 0)
                        eval_count = data.get("eval_count", 0)
                        break

            content = "".join(full_content)
            logger.log_model_call(
                agent_id="model",
                provider=self.provider_name,
                model=self.model,
                tokens_in=prompt_eval_count,
                tokens_out=eval_count,
            )
            yield {
                "delta": "",
                "done": True,
                "content": content,
                "tokens_in": prompt_eval_count,
                "tokens_out": eval_count,
            }

        except httpx.ConnectError:
            error_msg = (
                f"Cannot connect to Ollama at {self.api_url}. "
                "Ensure Ollama is running (ollama serve)."
            )
            logger.log_error("model", error_msg)
            raise ConnectionError(error_msg)
        except Exception as e:
            logger.log_error("model", f"Ollama streaming error: {e}")
            raise


class LMStudioClient(BaseModelClient):
    """Client for LM Studio local server (OpenAI-compatible API)."""

    def __init__(
        self,
        api_url: str = "http://localhost:1234/v1",
        model: str = "local-model",
    ):
        import openai

        self.client = openai.OpenAI(
            api_key="lm-studio",  # LM Studio doesn't require a real key
            base_url=api_url,
        )
        self.model = model
        self.provider_name = "lmstudio"

    def generate(
        self,
        system: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> dict:
        logger = get_logger()

        api_messages = [{"role": "system", "content": system}]
        for msg in messages:
            api_messages.append({"role": msg["role"], "content": msg["content"]})

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=api_messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            content = response.choices[0].message.content or ""
            tokens_in = response.usage.prompt_tokens if response.usage else 0
            tokens_out = response.usage.completion_tokens if response.usage else 0

            logger.log_model_call(
                agent_id="model",
                provider=self.provider_name,
                model=self.model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )

            return {
                "content": content,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
            }

        except Exception as e:
            logger.log_error("model", f"LM Studio error: {e}")
            raise


class VLLMClient(BaseModelClient):
    """Client for vLLM server (OpenAI-compatible API)."""

    def __init__(self, api_url: str, model: str, api_key: str = "EMPTY"):
        import openai

        self.client = openai.OpenAI(
            api_key=api_key,
            base_url=api_url,
        )
        self.model = model
        self.provider_name = "vllm"

    def generate(
        self,
        system: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> dict:
        logger = get_logger()

        api_messages = [{"role": "system", "content": system}]
        for msg in messages:
            api_messages.append({"role": msg["role"], "content": msg["content"]})

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=api_messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            content = response.choices[0].message.content or ""
            tokens_in = response.usage.prompt_tokens if response.usage else 0
            tokens_out = response.usage.completion_tokens if response.usage else 0

            logger.log_model_call(
                agent_id="model",
                provider=self.provider_name,
                model=self.model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )

            return {
                "content": content,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
            }

        except Exception as e:
            logger.log_error("model", f"vLLM error: {e}")
            raise
