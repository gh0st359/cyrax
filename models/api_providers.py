"""
CYRAX API Model Providers
Clients for OpenAI, Anthropic, Google, xAI, and custom API endpoints.
"""

import json
from abc import ABC, abstractmethod
from typing import Optional

from utils.logging import get_logger


class BaseModelClient(ABC):
    """Abstract base class for all model clients."""

    @abstractmethod
    def generate(
        self,
        system: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> dict:
        """
        Generate a response from the model.

        Args:
            system: System prompt.
            messages: List of conversation messages [{"role": "user"/"assistant", "content": "..."}].
            temperature: Sampling temperature.
            max_tokens: Maximum tokens to generate.

        Returns:
            dict with keys: "content" (str), "tokens_in" (int), "tokens_out" (int)
        """
        ...

    def generate_stream(
        self,
        system: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ):
        """
        Stream a response from the model, yielding chunks as they arrive.

        Yields:
            dict with key "delta" (str) for each chunk, or "done" (bool) for final chunk.
            Final yield includes "content", "tokens_in", "tokens_out".
        """
        # Default fallback: non-streaming generate
        result = self.generate(system, messages, temperature, max_tokens)
        yield {"delta": result["content"], "done": False}
        yield {"delta": "", "done": True, "content": result["content"],
               "tokens_in": result.get("tokens_in", 0),
               "tokens_out": result.get("tokens_out", 0)}


class OpenAIClient(BaseModelClient):
    """Client for OpenAI API (GPT-4, GPT-4 Turbo, o1, etc.)."""

    def __init__(self, api_key: str, model: str, api_url: Optional[str] = None):
        import openai

        kwargs = {"api_key": api_key}
        if api_url:
            kwargs["base_url"] = api_url
        self.client = openai.OpenAI(**kwargs)
        self.model = model
        self.provider_name = "openai"

    def generate(
        self,
        system: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> dict:
        logger = get_logger()

        # Build messages with system prompt
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
            logger.log_error("model", f"OpenAI API error: {e}")
            raise


    def generate_stream(
        self,
        system: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ):
        logger = get_logger()
        api_messages = [{"role": "system", "content": system}]
        for msg in messages:
            api_messages.append({"role": msg["role"], "content": msg["content"]})

        try:
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=api_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )

            full_content = []
            for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices[0].delta.content else ""
                if delta:
                    full_content.append(delta)
                    yield {"delta": delta, "done": False}

            content = "".join(full_content)
            logger.log_model_call(
                agent_id="model", provider=self.provider_name,
                model=self.model, tokens_in=0, tokens_out=0,
            )
            yield {"delta": "", "done": True, "content": content,
                   "tokens_in": 0, "tokens_out": 0}

        except Exception as e:
            logger.log_error("model", f"OpenAI streaming error: {e}")
            raise


class AnthropicClient(BaseModelClient):
    """Client for Anthropic API (Claude 3.5 Sonnet, Claude 3 Opus, etc.)."""

    def __init__(self, api_key: str, model: str):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.provider_name = "anthropic"

    def generate(
        self,
        system: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> dict:
        logger = get_logger()

        # Anthropic uses separate system parameter
        api_messages = []
        for msg in messages:
            api_messages.append({"role": msg["role"], "content": msg["content"]})

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=api_messages,
                temperature=temperature,
            )

            content = ""
            for block in response.content:
                if block.type == "text":
                    content += block.text

            tokens_in = response.usage.input_tokens
            tokens_out = response.usage.output_tokens

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
            logger.log_error("model", f"Anthropic API error: {e}")
            raise


    def generate_stream(
        self,
        system: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ):
        logger = get_logger()
        api_messages = []
        for msg in messages:
            api_messages.append({"role": msg["role"], "content": msg["content"]})

        try:
            with self.client.messages.stream(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=api_messages,
                temperature=temperature,
            ) as stream:
                full_content = []
                for text in stream.text_stream:
                    full_content.append(text)
                    yield {"delta": text, "done": False}

                content = "".join(full_content)
                # Get final message for token counts
                final = stream.get_final_message()
                tokens_in = final.usage.input_tokens if final else 0
                tokens_out = final.usage.output_tokens if final else 0

                logger.log_model_call(
                    agent_id="model", provider=self.provider_name,
                    model=self.model, tokens_in=tokens_in, tokens_out=tokens_out,
                )
                yield {"delta": "", "done": True, "content": content,
                       "tokens_in": tokens_in, "tokens_out": tokens_out}

        except Exception as e:
            logger.log_error("model", f"Anthropic streaming error: {e}")
            raise


class GoogleClient(BaseModelClient):
    """Client for Google Gemini API."""

    def __init__(self, api_key: str, model: str):
        import google.generativeai as genai

        genai.configure(api_key=api_key)
        self.model_name = model
        self.genai = genai
        self.provider_name = "google"

    def generate(
        self,
        system: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> dict:
        logger = get_logger()

        model = self.genai.GenerativeModel(
            model_name=self.model_name,
            system_instruction=system,
            generation_config=self.genai.GenerationConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
            ),
        )

        # Convert messages to Gemini format
        history = []
        for msg in messages[:-1]:
            role = "user" if msg["role"] == "user" else "model"
            history.append({"role": role, "parts": [msg["content"]]})

        try:
            chat = model.start_chat(history=history)
            last_message = messages[-1]["content"] if messages else ""
            response = chat.send_message(last_message)

            content = response.text
            # Gemini usage metadata
            tokens_in = 0
            tokens_out = 0
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                tokens_in = getattr(response.usage_metadata, "prompt_token_count", 0)
                tokens_out = getattr(
                    response.usage_metadata, "candidates_token_count", 0
                )

            logger.log_model_call(
                agent_id="model",
                provider=self.provider_name,
                model=self.model_name,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )

            return {
                "content": content,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
            }

        except Exception as e:
            logger.log_error("model", f"Google API error: {e}")
            raise


class XAIClient(BaseModelClient):
    """Client for xAI Grok API (OpenAI-compatible)."""

    def __init__(self, api_key: str, model: str):
        import openai

        self.client = openai.OpenAI(
            api_key=api_key,
            base_url="https://api.x.ai/v1",
        )
        self.model = model
        self.provider_name = "xai"

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
            logger.log_error("model", f"xAI API error: {e}")
            raise


class CustomAPIClient(BaseModelClient):
    """Client for any OpenAI-compatible API endpoint."""

    def __init__(self, api_url: str, api_key: str, model: str):
        import openai

        self.client = openai.OpenAI(
            api_key=api_key,
            base_url=api_url,
        )
        self.model = model
        self.provider_name = "custom"

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
            logger.log_error("model", f"Custom API error: {e}")
            raise
