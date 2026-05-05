import Anthropic from '@anthropic-ai/sdk';
import { GoogleGenerativeAI } from '@google/generative-ai';
import OpenAI from 'openai';
import { ChatMessage, ModelResponse, StreamDelta } from '../types/common.js';
import { CyraxConfig } from '../config/defaults.js';

export interface ModelClient {
  generate(system: string, messages: ChatMessage[], temperature?: number, maxTokens?: number): Promise<ModelResponse>;
  generateStream(system: string, messages: ChatMessage[], temperature?: number, maxTokens?: number): AsyncGenerator<StreamDelta>;
}

export class OpenAIModelClient implements ModelClient {
  private readonly client: OpenAI;
  constructor(private readonly apiKey: string, private readonly model: string, apiUrl?: string, private readonly provider = 'openai') {
    this.client = new OpenAI({ apiKey, baseURL: apiUrl });
  }

  async generate(system: string, messages: ChatMessage[], temperature = 0.7, maxTokens = 4096): Promise<ModelResponse> {
    const response = await this.client.chat.completions.create({
      model: this.model,
      messages: [{ role: 'system', content: system }, ...messages],
      temperature,
      max_tokens: maxTokens,
    });
    return { content: response.choices[0]?.message.content ?? '', tokensIn: response.usage?.prompt_tokens ?? 0, tokensOut: response.usage?.completion_tokens ?? 0 };
  }

  async *generateStream(system: string, messages: ChatMessage[], temperature = 0.7, maxTokens = 4096): AsyncGenerator<StreamDelta> {
    const stream = await this.client.chat.completions.create({
      model: this.model,
      messages: [{ role: 'system', content: system }, ...messages],
      temperature,
      max_tokens: maxTokens,
      stream: true,
    });
    const chunks: string[] = [];
    for await (const chunk of stream) {
      const delta = chunk.choices[0]?.delta.content ?? '';
      if (delta) {
        chunks.push(delta);
        yield { delta, done: false };
      }
    }
    yield { delta: '', done: true, content: chunks.join(''), tokensIn: 0, tokensOut: 0 };
  }
}

export class AnthropicModelClient implements ModelClient {
  private readonly client: Anthropic;
  constructor(private readonly apiKey: string, private readonly model: string) {
    this.client = new Anthropic({ apiKey });
  }

  async generate(system: string, messages: ChatMessage[], temperature = 0.7, maxTokens = 4096): Promise<ModelResponse> {
    const response = await this.client.messages.create({ model: this.model, system, messages, temperature, max_tokens: maxTokens });
    const content = response.content.map((part) => part.type === 'text' ? part.text : '').join('');
    return { content, tokensIn: response.usage.input_tokens, tokensOut: response.usage.output_tokens };
  }

  async *generateStream(system: string, messages: ChatMessage[], temperature = 0.7, maxTokens = 4096): AsyncGenerator<StreamDelta> {
    const stream = await this.client.messages.stream({ model: this.model, system, messages, temperature, max_tokens: maxTokens });
    const chunks: string[] = [];
    for await (const event of stream) {
      if (event.type === 'content_block_delta' && event.delta.type === 'text_delta') {
        chunks.push(event.delta.text);
        yield { delta: event.delta.text, done: false };
      }
    }
    const final = await stream.finalMessage();
    yield { delta: '', done: true, content: chunks.join(''), tokensIn: final.usage.input_tokens, tokensOut: final.usage.output_tokens };
  }
}

export class GoogleModelClient implements ModelClient {
  private readonly client: GoogleGenerativeAI;
  constructor(apiKey: string, private readonly model: string) {
    this.client = new GoogleGenerativeAI(apiKey);
  }

  async generate(system: string, messages: ChatMessage[], temperature = 0.7, maxTokens = 4096): Promise<ModelResponse> {
    const model = this.client.getGenerativeModel({ model: this.model, systemInstruction: system, generationConfig: { temperature, maxOutputTokens: maxTokens } });
    const prompt = messages.map((m) => `${m.role}: ${m.content}`).join('\n');
    const response = await model.generateContent(prompt);
    return { content: response.response.text(), tokensIn: 0, tokensOut: 0 };
  }

  async *generateStream(system: string, messages: ChatMessage[], temperature = 0.7, maxTokens = 4096): AsyncGenerator<StreamDelta> {
    const model = this.client.getGenerativeModel({ model: this.model, systemInstruction: system, generationConfig: { temperature, maxOutputTokens: maxTokens } });
    const prompt = messages.map((m) => `${m.role}: ${m.content}`).join('\n');
    const result = await model.generateContentStream(prompt);
    const chunks: string[] = [];
    for await (const chunk of result.stream) {
      const delta = chunk.text();
      if (delta) {
        chunks.push(delta);
        yield { delta, done: false };
      }
    }
    yield { delta: '', done: true, content: chunks.join(''), tokensIn: 0, tokensOut: 0 };
  }
}

export class OllamaClient implements ModelClient {
  constructor(private readonly apiUrl = 'http://localhost:11434', private readonly model = 'llama3.1:70b') {}

  async generate(system: string, messages: ChatMessage[], temperature = 0.7, maxTokens = 4096): Promise<ModelResponse> {
    const response = await fetch(`${this.apiUrl.replace(/\/$/, '')}/api/chat`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ model: this.model, messages: [{ role: 'system', content: system }, ...messages], stream: false, options: { temperature, num_predict: maxTokens } }),
    });
    if (!response.ok) throw new Error(`Ollama error: ${response.status} ${response.statusText}`);
    const data = await response.json() as { message?: { content?: string }; prompt_eval_count?: number; eval_count?: number };
    return { content: data.message?.content ?? '', tokensIn: data.prompt_eval_count ?? 0, tokensOut: data.eval_count ?? 0 };
  }

  async *generateStream(system: string, messages: ChatMessage[], temperature = 0.7, maxTokens = 4096): AsyncGenerator<StreamDelta> {
    const response = await fetch(`${this.apiUrl.replace(/\/$/, '')}/api/chat`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ model: this.model, messages: [{ role: 'system', content: system }, ...messages], stream: true, options: { temperature, num_predict: maxTokens } }),
    });
    if (!response.ok || !response.body) throw new Error(`Ollama error: ${response.status} ${response.statusText}`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffered = '';
    const chunks: string[] = [];
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffered += decoder.decode(value, { stream: true });
      const lines = buffered.split('\n');
      buffered = lines.pop() ?? '';
      for (const line of lines) {
        if (!line.trim()) continue;
        const data = JSON.parse(line) as { message?: { content?: string }; done?: boolean; prompt_eval_count?: number; eval_count?: number };
        const delta = data.message?.content ?? '';
        if (delta) {
          chunks.push(delta);
          yield { delta, done: false };
        }
        if (data.done) yield { delta: '', done: true, content: chunks.join(''), tokensIn: data.prompt_eval_count ?? 0, tokensOut: data.eval_count ?? 0 };
      }
    }
  }
}

export class ModelManager {
  provider: string;
  modelName: string;
  temperature: number;
  maxTokens: number;
  client: ModelClient;

  constructor(config: CyraxConfig['model']) {
    this.provider = config.provider;
    this.modelName = config.model_name;
    this.temperature = config.temperature;
    this.maxTokens = config.max_tokens;
    this.client = createClient(config);
  }

  generate(system: string, messages: ChatMessage[]): Promise<ModelResponse> {
    return this.client.generate(system, messages, this.temperature, this.maxTokens);
  }

  generateStream(system: string, messages: ChatMessage[]): AsyncGenerator<StreamDelta> {
    return this.client.generateStream(system, messages, this.temperature, this.maxTokens);
  }

  setModel(modelName: string): void {
    this.modelName = modelName;
  }
}

function createClient(config: CyraxConfig['model']): ModelClient {
  if (config.provider === 'anthropic') return new AnthropicModelClient(config.api_key, config.model_name);
  if (config.provider === 'openai') return new OpenAIModelClient(config.api_key, config.model_name, config.api_url);
  if (config.provider === 'google') return new GoogleModelClient(config.api_key, config.model_name);
  if (config.provider === 'xai') return new OpenAIModelClient(config.api_key, config.model_name, config.api_url ?? 'https://api.x.ai/v1', 'xai');
  if (config.provider === 'ollama') return new OllamaClient(config.api_url ?? 'http://localhost:11434', config.model_name);
  return new OpenAIModelClient(config.api_key, config.model_name, config.api_url);
}
