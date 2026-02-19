"""
CYRAX Model Manager
Unified interface for any AI model provider.
"""

from typing import Optional

from models.api_providers import BaseModelClient
from utils.logging import get_logger


class ModelManager:
    """
    Unified interface for any AI model provider.
    User configures ONE model - this manager handles the connection.
    """

    def __init__(self, config: dict):
        self.provider = config["provider"]
        self.model_name = config.get("model_name", "")
        self.api_key = config.get("api_key", "")
        self.api_url = config.get("api_url", "")
        self.temperature = config.get("temperature", 0.7)
        self.max_tokens = config.get("max_tokens", 4096)

        self.client: BaseModelClient = self._init_client()
        self.total_tokens_in = 0
        self.total_tokens_out = 0

    def _init_client(self) -> BaseModelClient:
        """Initialize the appropriate client based on provider."""
        logger = get_logger()

        if self.provider == "openai":
            from models.api_providers import OpenAIClient

            logger.info(f"Initializing OpenAI client with model: {self.model_name}")
            return OpenAIClient(
                api_key=self.api_key,
                model=self.model_name,
                api_url=self.api_url or None,
            )

        elif self.provider == "anthropic":
            from models.api_providers import AnthropicClient

            logger.info(f"Initializing Anthropic client with model: {self.model_name}")
            return AnthropicClient(
                api_key=self.api_key,
                model=self.model_name,
            )

        elif self.provider == "google":
            from models.api_providers import GoogleClient

            logger.info(f"Initializing Google client with model: {self.model_name}")
            return GoogleClient(
                api_key=self.api_key,
                model=self.model_name,
            )

        elif self.provider == "xai":
            from models.api_providers import XAIClient

            logger.info(f"Initializing xAI client with model: {self.model_name}")
            return XAIClient(
                api_key=self.api_key,
                model=self.model_name,
            )

        elif self.provider == "ollama":
            from models.local_providers import OllamaClient

            url = self.api_url or "http://localhost:11434"
            logger.info(f"Initializing Ollama client: {self.model_name} at {url}")
            return OllamaClient(
                api_url=url,
                model=self.model_name,
            )

        elif self.provider == "lmstudio":
            from models.local_providers import LMStudioClient

            url = self.api_url or "http://localhost:1234/v1"
            logger.info(f"Initializing LM Studio client: {self.model_name} at {url}")
            return LMStudioClient(
                api_url=url,
                model=self.model_name,
            )

        elif self.provider == "vllm":
            from models.local_providers import VLLMClient

            logger.info(f"Initializing vLLM client: {self.model_name} at {self.api_url}")
            return VLLMClient(
                api_url=self.api_url,
                model=self.model_name,
                api_key=self.api_key or "EMPTY",
            )

        elif self.provider == "custom":
            from models.api_providers import CustomAPIClient

            logger.info(
                f"Initializing custom API client: {self.model_name} at {self.api_url}"
            )
            return CustomAPIClient(
                api_url=self.api_url,
                api_key=self.api_key,
                model=self.model_name,
            )

        else:
            raise ValueError(f"Unsupported model provider: {self.provider}")

    def generate(
        self,
        system: str,
        messages: list[dict],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        Generate a response from the model.

        Args:
            system: System prompt.
            messages: Conversation messages.
            temperature: Override default temperature.
            max_tokens: Override default max tokens.

        Returns:
            The model's text response.
        """
        temp = temperature if temperature is not None else self.temperature
        tokens = max_tokens if max_tokens is not None else self.max_tokens

        result = self.client.generate(
            system=system,
            messages=messages,
            temperature=temp,
            max_tokens=tokens,
        )

        self.total_tokens_in += result.get("tokens_in", 0)
        self.total_tokens_out += result.get("tokens_out", 0)

        return result["content"]

    def generate_stream(
        self,
        system: str,
        messages: list[dict],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        """
        Stream a response from the model, yielding chunks.

        Yields:
            dict with "delta" (str) for text chunks.
            Final yield includes "content", "tokens_in", "tokens_out".
        """
        temp = temperature if temperature is not None else self.temperature
        tokens = max_tokens if max_tokens is not None else self.max_tokens

        for chunk in self.client.generate_stream(
            system=system,
            messages=messages,
            temperature=temp,
            max_tokens=tokens,
        ):
            if chunk.get("done"):
                self.total_tokens_in += chunk.get("tokens_in", 0)
                self.total_tokens_out += chunk.get("tokens_out", 0)
            yield chunk

    def get_usage(self) -> dict:
        """Get cumulative token usage."""
        return {
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "total_tokens": self.total_tokens_in + self.total_tokens_out,
            "provider": self.provider,
            "model": self.model_name,
        }
