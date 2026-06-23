import json
import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tracker.db")


def _connect(path=None):
    """Open tracker.db with WAL + a bounded busy-timeout.

    WAL removes the rollback-journal lock-upgrade deadlock that caused
    `database is locked`; busy_timeout makes a blocked writer wait (off the
    request path) instead of failing instantly. DB_PATH is resolved at call
    time so tests that monkeypatch db.DB_PATH are honored.
    """
    con = sqlite3.connect(path or DB_PATH, timeout=3.0)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=3000")
    con.execute("PRAGMA synchronous=NORMAL")
    return con

PRICING = {  # all prices in $/million tokens
    # ── Anthropic Claude 4 ─────────────────────────────────────────────────────
    "claude-fable-5":             {"input": 10.0,   "output": 50.0},
    "claude-opus-4-8":            {"input": 5.0,    "output": 25.0},
    "claude-opus-4-7":            {"input": 5.0,    "output": 25.0},
    "claude-opus-4-6":            {"input": 5.0,    "output": 25.0},
    "claude-opus-4-5":            {"input": 5.0,    "output": 25.0},
    "claude-opus-4-1":            {"input": 15.0,   "output": 75.0},
    "claude-opus-4-20250514":     {"input": 15.0,   "output": 75.0},
    "claude-sonnet-4-6":          {"input": 3.0,    "output": 15.0},
    "claude-sonnet-4-5":          {"input": 3.0,    "output": 15.0},
    "claude-sonnet-4-20250514":   {"input": 3.0,    "output": 15.0},
    "claude-haiku-4-5":           {"input": 1.0,    "output": 5.0},
    "claude-haiku-4-5-20251001":  {"input": 1.0,    "output": 5.0},
    # ── Anthropic Claude 3.x ───────────────────────────────────────────────────
    "claude-3-5-sonnet-20241022": {"input": 3.0,    "output": 15.0},
    "claude-3-5-sonnet-20240620": {"input": 3.0,    "output": 15.0},
    "claude-3-5-haiku-20241022":  {"input": 0.8,    "output": 4.0},
    "claude-3-opus-20240229":     {"input": 15.0,   "output": 75.0},
    "claude-3-sonnet-20240229":   {"input": 3.0,    "output": 15.0},
    "claude-3-haiku-20240307":    {"input": 0.25,   "output": 1.25},
    # ── OpenAI GPT-4.1 ────────────────────────────────────────────────────────
    "gpt-4.1":                    {"input": 2.0,    "output": 8.0},
    "gpt-4.1-mini":               {"input": 0.4,    "output": 1.6},
    "gpt-4.1-nano":               {"input": 0.1,    "output": 0.4},
    # ── OpenAI GPT-4o ─────────────────────────────────────────────────────────
    "gpt-5":                      {"input": 15.0,   "output": 60.0},
    "gpt-5-mini":                 {"input": 1.25,   "output": 5.0},
    "gpt-4o":                     {"input": 2.5,    "output": 10.0},
    "gpt-4o-2024-11-20":          {"input": 2.5,    "output": 10.0},
    "gpt-4o-2024-08-06":          {"input": 2.5,    "output": 10.0},
    "gpt-4o-2024-05-13":          {"input": 5.0,    "output": 15.0},
    "gpt-4o-mini":                {"input": 0.15,   "output": 0.60},
    "gpt-4o-mini-2024-07-18":     {"input": 0.15,   "output": 0.60},
    "chatgpt-4o-latest":          {"input": 5.0,    "output": 15.0},
    # ── OpenAI o-series (reasoning) ───────────────────────────────────────────
    "o1":                         {"input": 15.0,   "output": 60.0},
    "o1-2024-12-17":              {"input": 15.0,   "output": 60.0},
    "o1-pro":                     {"input": 150.0,  "output": 600.0},
    "o1-mini":                    {"input": 1.1,    "output": 4.4},
    "o1-mini-2024-09-12":         {"input": 1.1,    "output": 4.4},
    "o1-preview":                 {"input": 15.0,   "output": 60.0},
    "o3":                         {"input": 10.0,   "output": 40.0},
    "o3-mini":                    {"input": 1.1,    "output": 4.4},
    "o3-mini-2025-01-31":         {"input": 1.1,    "output": 4.4},
    "o4-mini":                    {"input": 1.1,    "output": 4.4},
    "o4-mini-2025-04-16":         {"input": 1.1,    "output": 4.4},
    # ── OpenAI GPT-4 / GPT-3.5 ────────────────────────────────────────────────
    "gpt-4-turbo":                {"input": 10.0,   "output": 30.0},
    "gpt-4-turbo-2024-04-09":     {"input": 10.0,   "output": 30.0},
    "gpt-4":                      {"input": 30.0,   "output": 60.0},
    "gpt-4-32k":                  {"input": 60.0,   "output": 120.0},
    "gpt-3.5-turbo":              {"input": 0.5,    "output": 1.5},
    "gpt-3.5-turbo-0125":         {"input": 0.5,    "output": 1.5},
    # ── Azure OpenAI ──────────────────────────────────────────────────────────
    "azure_ai/gpt-5.4":           {"input": 2.5,    "output": 15.0},
    "azure_ai/gpt-5.4-mini":      {"input": 0.75,   "output": 4.5},
    "azure_ai/gpt-5.4-nano":      {"input": 0.20,   "output": 1.25},
    "azure_ai/gpt-5.4-pro":       {"input": 30.0,   "output": 180.0},
    # ── Google Gemini ──────────────────────────────────────────────────────────
    "gemini/gemini-2.5-pro":              {"input": 1.25,   "output": 10.0},
    "gemini/gemini-2.5-pro-preview":      {"input": 1.25,   "output": 10.0},
    "gemini/gemini-2.5-flash":            {"input": 0.075,  "output": 0.30},
    "gemini/gemini-2.5-flash-8b":         {"input": 0.0375, "output": 0.15},
    "gemini/gemini-2.0-flash":            {"input": 0.075,  "output": 0.30},
    "gemini/gemini-2.0-flash-lite":       {"input": 0.0375, "output": 0.15},
    "gemini/gemini-2.0-flash-thinking-exp": {"input": 0.075, "output": 0.35},
    "gemini/gemini-1.5-pro":              {"input": 1.25,   "output": 5.0},
    "gemini/gemini-1.5-flash":            {"input": 0.075,  "output": 0.30},
    "gemini/gemini-1.5-flash-8b":         {"input": 0.0375, "output": 0.15},
    "gemini/gemini-exp-1206":             {"input": 0.0,    "output": 0.0},
    "gemini-2.5-pro":                     {"input": 1.25,   "output": 10.0},
    "gemini-2.5-flash":                   {"input": 0.075,  "output": 0.30},
    "gemini-2.0-flash":                   {"input": 0.075,  "output": 0.30},
    "gemini-2.0-flash-lite":              {"input": 0.0375, "output": 0.15},
    "gemini-1.5-pro":                     {"input": 1.25,   "output": 5.0},
    "gemini-1.5-flash":                   {"input": 0.075,  "output": 0.30},
    "gemini-1.0-pro":                     {"input": 0.5,    "output": 1.5},
    # ── Google Vertex AI (vertex_ai/ prefix) ──────────────────────────────────
    "vertex_ai/gemini-2.5-pro":           {"input": 1.25,   "output": 10.0},
    "vertex_ai/gemini-2.0-flash-001":     {"input": 0.075,  "output": 0.30},
    "vertex_ai/gemini-1.5-pro":           {"input": 1.25,   "output": 5.0},
    "vertex_ai/gemini-1.5-flash":         {"input": 0.075,  "output": 0.30},
    "vertex_ai/claude-sonnet-4-6":        {"input": 3.0,    "output": 15.0},
    "vertex_ai/claude-opus-4-7":          {"input": 5.0,    "output": 25.0},
    # ── Groq ──────────────────────────────────────────────────────────────────
    "groq/llama-3.3-70b-versatile":         {"input": 0.59,  "output": 0.79},
    "groq/llama-3.1-70b-versatile":         {"input": 0.59,  "output": 0.79},
    "groq/llama-3.1-8b-instant":            {"input": 0.05,  "output": 0.08},
    "groq/llama3-70b-8192":                 {"input": 0.59,  "output": 0.79},
    "groq/llama3-8b-8192":                  {"input": 0.05,  "output": 0.08},
    "groq/llama-3.2-90b-text-preview":      {"input": 0.90,  "output": 0.90},
    "groq/llama-3.2-11b-text-preview":      {"input": 0.18,  "output": 0.18},
    "groq/llama-3.2-3b-preview":            {"input": 0.06,  "output": 0.06},
    "groq/llama-3.2-1b-preview":            {"input": 0.04,  "output": 0.04},
    "groq/mixtral-8x7b-32768":              {"input": 0.24,  "output": 0.24},
    "groq/gemma2-9b-it":                    {"input": 0.20,  "output": 0.20},
    "groq/gemma-7b-it":                     {"input": 0.07,  "output": 0.07},
    "groq/qwen-qwq-32b":                    {"input": 0.29,  "output": 0.39},
    "groq/deepseek-r1-distill-llama-70b":   {"input": 0.75,  "output": 0.99},
    "groq/llama-4-scout-17b-16e-instruct":  {"input": 0.11,  "output": 0.34},
    "groq/llama-4-maverick-17b-128e-instruct": {"input": 0.50, "output": 0.77},
    "llama-3.3-70b-versatile":              {"input": 0.59,  "output": 0.79},
    "llama-3.1-70b-versatile":              {"input": 0.59,  "output": 0.79},
    "llama-3.1-8b-instant":                 {"input": 0.05,  "output": 0.08},
    "mixtral-8x7b-32768":                   {"input": 0.24,  "output": 0.24},
    # ── Mistral ───────────────────────────────────────────────────────────────
    "mistral/mistral-large-latest":     {"input": 2.0,   "output": 6.0},
    "mistral/mistral-large-2411":       {"input": 2.0,   "output": 6.0},
    "mistral/mistral-medium-latest":    {"input": 0.4,   "output": 2.0},
    "mistral/mistral-small-latest":     {"input": 0.1,   "output": 0.3},
    "mistral/mistral-small-2409":       {"input": 0.1,   "output": 0.3},
    "mistral/open-mistral-7b":          {"input": 0.25,  "output": 0.25},
    "mistral/open-mixtral-8x7b":        {"input": 0.7,   "output": 0.7},
    "mistral/open-mixtral-8x22b":       {"input": 2.0,   "output": 6.0},
    "mistral/mistral-nemo":             {"input": 0.15,  "output": 0.15},
    "mistral/open-mistral-nemo":        {"input": 0.15,  "output": 0.15},
    "mistral/codestral-latest":         {"input": 0.3,   "output": 0.9},
    "mistral/codestral-2501":           {"input": 0.3,   "output": 0.9},
    "mistral/pixtral-large-latest":     {"input": 2.0,   "output": 6.0},
    "mistral/pixtral-12b-2409":         {"input": 0.15,  "output": 0.15},
    "mistral-large-latest":             {"input": 2.0,   "output": 6.0},
    "mistral-small-latest":             {"input": 0.1,   "output": 0.3},
    "codestral-latest":                 {"input": 0.3,   "output": 0.9},
    "open-mistral-nemo":                {"input": 0.15,  "output": 0.15},
    # ── DeepSeek ──────────────────────────────────────────────────────────────
    "deepseek/deepseek-chat":           {"input": 0.14,  "output": 0.28},
    "deepseek/deepseek-v3":             {"input": 0.14,  "output": 0.28},
    "deepseek/deepseek-v3-0324":        {"input": 0.27,  "output": 1.10},
    "deepseek/deepseek-reasoner":       {"input": 0.55,  "output": 2.19},
    "deepseek/deepseek-r1":             {"input": 0.55,  "output": 2.19},
    "deepseek/deepseek-r1-zero":        {"input": 0.55,  "output": 2.19},
    "deepseek-chat":                    {"input": 0.14,  "output": 0.28},
    "deepseek-reasoner":                {"input": 0.55,  "output": 2.19},
    "deepseek-v3":                      {"input": 0.14,  "output": 0.28},
    # ── xAI Grok ──────────────────────────────────────────────────────────────
    "xai/grok-3":                       {"input": 3.0,   "output": 15.0},
    "xai/grok-3-beta":                  {"input": 3.0,   "output": 15.0},
    "xai/grok-3-mini":                  {"input": 0.3,   "output": 0.5},
    "xai/grok-3-mini-beta":             {"input": 0.3,   "output": 0.5},
    "xai/grok-3-fast":                  {"input": 5.0,   "output": 25.0},
    "xai/grok-2-1212":                  {"input": 2.0,   "output": 10.0},
    "xai/grok-2":                       {"input": 2.0,   "output": 10.0},
    "xai/grok-beta":                    {"input": 5.0,   "output": 15.0},
    "xai/grok-vision-beta":             {"input": 5.0,   "output": 15.0},
    "grok-3":                           {"input": 3.0,   "output": 15.0},
    "grok-3-mini":                      {"input": 0.3,   "output": 0.5},
    "grok-2-1212":                      {"input": 2.0,   "output": 10.0},
    "grok-beta":                        {"input": 5.0,   "output": 15.0},
    # ── Perplexity ────────────────────────────────────────────────────────────
    "perplexity/sonar-pro":             {"input": 3.0,   "output": 15.0},
    "perplexity/sonar":                 {"input": 1.0,   "output": 1.0},
    "perplexity/sonar-reasoning-pro":   {"input": 2.0,   "output": 8.0},
    "perplexity/sonar-reasoning":       {"input": 1.0,   "output": 5.0},
    "perplexity/llama-3.1-sonar-huge-128k-online": {"input": 5.0, "output": 5.0},
    "sonar-pro":                        {"input": 3.0,   "output": 15.0},
    "sonar":                            {"input": 1.0,   "output": 1.0},
    # ── Cohere ────────────────────────────────────────────────────────────────
    "cohere/command-r-plus":            {"input": 2.5,   "output": 10.0},
    "cohere/command-r-plus-08-2024":    {"input": 2.5,   "output": 10.0},
    "cohere/command-r":                 {"input": 0.15,  "output": 0.60},
    "cohere/command-r-08-2024":         {"input": 0.15,  "output": 0.60},
    "cohere/command-a-03-2025":         {"input": 2.5,   "output": 10.0},
    "cohere/command-light":             {"input": 0.3,   "output": 0.6},
    "command-r-plus":                   {"input": 2.5,   "output": 10.0},
    "command-r":                        {"input": 0.15,  "output": 0.60},
    "command-a-03-2025":                {"input": 2.5,   "output": 10.0},
    # ── Cerebras ──────────────────────────────────────────────────────────────
    "cerebras/llama-3.3-70b":           {"input": 0.85,  "output": 1.20},
    "cerebras/llama3.1-70b":            {"input": 0.85,  "output": 1.20},
    "cerebras/llama3.1-8b":             {"input": 0.10,  "output": 0.10},
    "cerebras/llama3.1-405b":           {"input": 6.0,   "output": 6.0},
    "cerebras/qwen-3-32b":              {"input": 0.40,  "output": 0.80},
    # ── Together AI ───────────────────────────────────────────────────────────
    "together_ai/meta-llama/Llama-3-70b-chat-hf":   {"input": 0.9,   "output": 0.9},
    "together_ai/meta-llama/Llama-3-8b-chat-hf":    {"input": 0.2,   "output": 0.2},
    "together_ai/meta-llama/Llama-3.1-405B-Instruct-Turbo": {"input": 5.0, "output": 5.0},
    "together_ai/meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo": {"input": 0.88, "output": 0.88},
    "together_ai/meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo":  {"input": 0.18, "output": 0.18},
    "together_ai/mistralai/Mixtral-8x22B-Instruct-v0.1": {"input": 1.2, "output": 1.2},
    "together_ai/Qwen/Qwen2-72B-Instruct":           {"input": 0.9,  "output": 0.9},
    "together_ai/deepseek-ai/DeepSeek-V3":           {"input": 1.25, "output": 1.25},
    "together_ai/deepseek-ai/DeepSeek-R1":           {"input": 7.0,  "output": 7.0},
    "together_ai/google/gemma-2-27b-it":             {"input": 0.8,  "output": 0.8},
    # ── Fireworks AI ──────────────────────────────────────────────────────────
    "fireworks_ai/accounts/fireworks/models/llama-v3p3-70b-instruct":     {"input": 0.9,  "output": 0.9},
    "fireworks_ai/accounts/fireworks/models/llama-v3p1-70b-instruct":     {"input": 0.9,  "output": 0.9},
    "fireworks_ai/accounts/fireworks/models/llama-v3p1-8b-instruct":      {"input": 0.2,  "output": 0.2},
    "fireworks_ai/accounts/fireworks/models/qwen2p5-72b-instruct":        {"input": 0.9,  "output": 0.9},
    "fireworks_ai/accounts/fireworks/models/deepseek-v3":                 {"input": 1.2,  "output": 1.2},
    "fireworks_ai/accounts/fireworks/models/deepseek-r1":                 {"input": 8.0,  "output": 8.0},
    # ── Amazon Bedrock ────────────────────────────────────────────────────────
    "bedrock/amazon.nova-pro-v1:0":              {"input": 0.8,   "output": 3.2},
    "bedrock/amazon.nova-lite-v1:0":             {"input": 0.06,  "output": 0.24},
    "bedrock/amazon.nova-micro-v1:0":            {"input": 0.035, "output": 0.14},
    "bedrock/meta.llama3-70b-instruct-v1:0":     {"input": 2.65,  "output": 3.5},
    "bedrock/meta.llama3-8b-instruct-v1:0":      {"input": 0.3,   "output": 0.6},
    "bedrock/meta.llama3-1-70b-instruct-v1:0":   {"input": 2.65,  "output": 3.5},
    "bedrock/meta.llama3-2-90b-instruct-v1:0":   {"input": 2.0,   "output": 2.0},
    "bedrock/mistral.mistral-large-2402-v1:0":   {"input": 4.0,   "output": 12.0},
    "bedrock/mistral.mixtral-8x7b-instruct-v0:1":{"input": 0.45,  "output": 0.7},
    "bedrock/cohere.command-r-plus-v1:0":        {"input": 3.0,   "output": 15.0},
    "bedrock/cohere.command-r-v1:0":             {"input": 0.5,   "output": 1.5},
    "bedrock/ai21.jamba-1-5-large-v1:0":         {"input": 2.0,   "output": 8.0},
    "bedrock/ai21.jamba-1-5-mini-v1:0":          {"input": 0.2,   "output": 0.4},
    # ── Ollama (local — free) ──────────────────────────────────────────────────
    "ollama/llama3":                {"input": 0.0, "output": 0.0},
    "ollama/llama3.1":              {"input": 0.0, "output": 0.0},
    "ollama/llama3.2":              {"input": 0.0, "output": 0.0},
    "ollama/llama3.3":              {"input": 0.0, "output": 0.0},
    "ollama/mistral":               {"input": 0.0, "output": 0.0},
    "ollama/mistral-nemo":          {"input": 0.0, "output": 0.0},
    "ollama/phi3":                  {"input": 0.0, "output": 0.0},
    "ollama/phi4":                  {"input": 0.0, "output": 0.0},
    "ollama/qwen2.5":               {"input": 0.0, "output": 0.0},
    "ollama/qwen2.5-coder":         {"input": 0.0, "output": 0.0},
    "ollama/gemma2":                {"input": 0.0, "output": 0.0},
    "ollama/deepseek-r1":           {"input": 0.0, "output": 0.0},
    "ollama/codellama":             {"input": 0.0, "output": 0.0},
    # ── HuggingFace Inference API (most free / very cheap) ────────────────────
    "huggingface/meta-llama/Llama-3.1-70B-Instruct": {"input": 0.59, "output": 0.79},
    "huggingface/mistralai/Mixtral-8x7B-Instruct-v0.1": {"input": 0.5, "output": 0.5},
    "huggingface/google/gemma-2-27b-it":              {"input": 0.6,  "output": 0.6},
    # ── Replicate ─────────────────────────────────────────────────────────────
    "replicate/meta/llama-3.1-405b-instruct": {"input": 9.5,  "output": 9.5},
    "replicate/meta/llama-3.1-70b-instruct":  {"input": 0.65, "output": 0.65},
    "replicate/meta/llama-3.1-8b-instruct":   {"input": 0.05, "output": 0.25},
    "replicate/mistralai/mixtral-8x7b-instruct-v0.1": {"input": 0.3, "output": 1.0},
    # ── Anyscale ──────────────────────────────────────────────────────────────
    "anyscale/meta-llama/Llama-3-70B-Instruct":  {"input": 1.0, "output": 1.0},
    "anyscale/meta-llama/Llama-3-8B-Instruct":   {"input": 0.15,"output": 0.15},
    "anyscale/mistralai/Mixtral-8x22B-Instruct-v0.1": {"input": 0.9, "output": 0.9},
    # ── OpenRouter ────────────────────────────────────────────────────────────
    "openrouter/openai/gpt-4o":                    {"input": 2.5,   "output": 10.0},
    "openrouter/openai/gpt-4o-mini":               {"input": 0.15,  "output": 0.60},
    "openrouter/anthropic/claude-sonnet-4-6":      {"input": 3.0,   "output": 15.0},
    "openrouter/anthropic/claude-opus-4-7":        {"input": 5.0,   "output": 25.0},
    "openrouter/google/gemini-2.0-flash":          {"input": 0.075, "output": 0.30},
    "openrouter/google/gemini-2.5-pro":            {"input": 1.25,  "output": 10.0},
    "openrouter/meta-llama/llama-3.3-70b-instruct":{"input": 0.12,  "output": 0.4},
    "openrouter/deepseek/deepseek-chat":           {"input": 0.14,  "output": 0.28},
    "openrouter/x-ai/grok-3-mini-beta":            {"input": 0.3,   "output": 0.5},
    "openrouter/mistralai/mistral-large":          {"input": 2.0,   "output": 6.0},
    "openrouter/qwen/qwq-32b":                     {"input": 0.15,  "output": 0.6},
    "default":                                      {"input": 3.0,   "output": 15.0},
}
HAIKU_PRICING = {"input": 1.0, "output": 5.0}  # claude-haiku-4-5 baseline


def calc_cost(model, input_tok, output_tok, cache_read=0, cache_creation=0, cache_creation_1h=0):
    # Try exact match, then provider/model prefix variants
    p = PRICING.get(model)
    if not p and "/" in model:
        # e.g. "groq/llama-3.3-70b-versatile" → try bare name too
        p = PRICING.get(model.split("/", 1)[1])
    p = p or PRICING["default"]
    # cache_creation is the TOTAL cache-write tokens; cache_creation_1h is the
    # 1-hour-TTL portion. 1h writes cost 2x base input, 5-minute writes 1.25x.
    # Old rows have cache_creation_1h=0 → all priced at 1.25x, unchanged.
    cache_5m = max(cache_creation - cache_creation_1h, 0)
    return (
        input_tok         * p["input"] +
        output_tok        * p["output"] +
        cache_read        * p["input"] * 0.10 +
        cache_5m          * p["input"] * 1.25 +
        cache_creation_1h * p["input"] * 2.00
    ) / 1_000_000


def _naive_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def _period_clause(period):
    if period == "today": return "AND date(ts, 'localtime') = date('now', 'localtime')"
    if period == "7d":    return "AND ts >= datetime('now', '-7 days')"
    if period == "30d":   return "AND ts >= datetime('now', '-30 days')"
    return ""


def _fmt_ms(ms):
    if not ms:
        return "0s"
    ms = int(ms)
    if ms < 1000:
        return f"{ms}ms"
    s = ms // 1000
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    return f"{m}m {s}s" if s else f"{m}m"


def init_db():
    con = _connect()
    con.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            ts                    TEXT,
            source                TEXT,
            model                 TEXT,
            input_tokens          INTEGER,
            output_tokens         INTEGER,
            cache_read_tokens     INTEGER DEFAULT 0,
            cache_creation_tokens INTEGER DEFAULT 0,
            cost_usd              REAL,
            duration_ms           INTEGER,
            status                INTEGER,
            user_agent            TEXT,
            stop_reason           TEXT,
            tool_call_count       INTEGER DEFAULT 0,
            tools_json            TEXT
        )
    """)
    con.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    existing = {row[1] for row in con.execute("PRAGMA table_info(requests)")}
    for col, defn in [
        ("cache_read_tokens",        "INTEGER DEFAULT 0"),
        ("cache_creation_tokens",    "INTEGER DEFAULT 0"),
        ("cache_creation_1h_tokens", "INTEGER DEFAULT 0"),
        ("user_agent",            "TEXT"),
        ("stop_reason",           "TEXT"),
        ("tool_call_count",       "INTEGER DEFAULT 0"),
        ("tools_json",            "TEXT"),
        ("effort",                "TEXT DEFAULT 'standard'"),
        ("prompt_preview",        "TEXT DEFAULT ''"),
        ("msg_uuid",              "TEXT"),
        ("auto_thinking",         "INTEGER DEFAULT 0"),
        ("optimizations_json",    "TEXT"),
        ("optimizer_savings_usd", "REAL DEFAULT 0"),
    ]:
        if col not in existing:
            con.execute(f"ALTER TABLE requests ADD COLUMN {col} {defn}")
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_msg_uuid "
        "ON requests(msg_uuid) WHERE msg_uuid IS NOT NULL"
    )
    con.commit()
    con.close()


def save_request(source, model, input_tok, output_tok, cache_read, cache_creation,
                 cost, duration_ms, status, user_agent="", stop_reason=None,
                 tool_call_count=0, tools_json=None, effort="standard",
                 prompt_preview="", msg_uuid=None, auto_thinking=False,
                 optimizations_json=None, optimizer_savings_usd=0, cache_creation_1h=0,
                 ts=None):
    con = _connect()
    con.execute(
        """INSERT OR IGNORE INTO requests
           (ts,source,model,input_tokens,output_tokens,cache_read_tokens,cache_creation_tokens,
            cost_usd,duration_ms,status,user_agent,stop_reason,tool_call_count,tools_json,
            effort,prompt_preview,msg_uuid,auto_thinking,optimizations_json,optimizer_savings_usd,
            cache_creation_1h_tokens)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ts or datetime.now(timezone.utc).isoformat(), source, model,
         input_tok, output_tok, cache_read, cache_creation,
         cost, duration_ms, status, user_agent, stop_reason, tool_call_count, tools_json,
         effort or "standard", prompt_preview or "", msg_uuid, 1 if auto_thinking else 0,
         optimizations_json, optimizer_savings_usd, cache_creation_1h),
    )
    con.commit()
    con.close()


def get_raw_logs(limit: int = 500):
    con = _connect()
    rows = con.execute(
        """SELECT id, ts, source, model, input_tokens, output_tokens,
                  cache_read_tokens, cache_creation_tokens, cost_usd,
                  duration_ms, status, stop_reason, tool_call_count,
                  effort, prompt_preview, tools_json, auto_thinking
           FROM requests
           ORDER BY id DESC
           LIMIT ?""",
        (min(limit, 500),)
    ).fetchall()
    con.close()
    cols = ["id","ts","source","model","input_tokens","output_tokens",
            "cache_read_tokens","cache_creation_tokens","cost_usd",
            "duration_ms","status","stop_reason","tool_call_count",
            "effort","prompt_preview","tools_json","auto_thinking"]
    result = []
    for r in rows:
        d = dict(zip(cols, r))
        # Parse tools_json string → list for the frontend
        tj = d.pop("tools_json", None)
        if tj:
            try:
                import json as _json
                d["tools"] = _json.loads(tj)
            except Exception:
                d["tools"] = []
        else:
            d["tools"] = []
        result.append(d)
    return result


def get_optimizer_stats(period="7d"):
    """
    Get optimizer performance stats: all optimizations with savings.
    Returns {
        "total_saved": float,
        "actual_spent": float,
        "roi_percent": float,
        "event_count": int,
        "by_type": {
            "routing": {...},
            "cache": {...},
            ...
        },
        "daily": [{date, total_saved, breakdown}, ...],
        "recent_events": [{id, ts, source, model, type, saved_usd, details}, ...]
    }
    """
    clause = _period_clause(period)
    con = _connect()

    # Get all requests with optimizations (include token/effort data for routing table)
    rows = con.execute(
        f"""SELECT id, ts, source, model, optimizations_json, optimizer_savings_usd,
                   input_tokens, output_tokens, effort
           FROM requests
           WHERE optimizations_json IS NOT NULL AND optimizer_savings_usd > 0.0001 {clause}
           ORDER BY ts DESC
           LIMIT 500"""
    ).fetchall()

    # Routing groups: aggregate directly from DB for full accuracy (not limited to 100 events)
    routing_rows = con.execute(
        f"""SELECT
               json_extract(opt.value, '$.from') AS from_model,
               json_extract(opt.value, '$.to')   AS to_model,
               COUNT(*)                           AS cnt,
               SUM(json_extract(opt.value, '$.saved_usd')) AS saved,
               MAX(r.ts)                          AS ts_last,
               AVG(r.input_tokens)                AS avg_in,
               AVG(r.output_tokens)               AS avg_out,
               r.effort
           FROM requests r,
                json_each(r.optimizations_json) AS opt
           WHERE json_extract(opt.value, '$.type') = 'routing'
             AND r.optimizations_json IS NOT NULL
             {clause}
           GROUP BY from_model, to_model, r.effort
           ORDER BY saved DESC"""
    ).fetchall()

    # Get total actual cost (all requests in period, not just optimized ones)
    total_cost_row = con.execute(
        f"SELECT SUM(cost_usd) FROM requests WHERE 1=1 {clause}"
    ).fetchone()
    actual_spent = total_cost_row[0] or 0

    con.close()

    if not rows:
        return {
            "total_saved": 0,
            "actual_spent": actual_spent,
            "roi_percent": 0,
            "event_count": 0,
            "by_type": {},
            "daily": [],
            "recent_events": []
        }

    import json as _json
    from collections import defaultdict
    from datetime import datetime as dt

    total_saved = 0
    by_type = defaultdict(lambda: {"count": 0, "saved": 0})
    daily = defaultdict(lambda: {"saved": 0, "breakdown": defaultdict(float)})
    recent_events = []

    for row in rows:
        rid, ts_str, source, model, opt_json_str, saved, inp_tok, out_tok, eff = row
        total_saved += saved or 0

        # Parse optimizations
        try:
            opts = _json.loads(opt_json_str) if opt_json_str else []
        except:
            opts = []

        # Extract date for daily rollup
        ts_dt = dt.fromisoformat(ts_str)
        date_key = ts_dt.strftime("%Y-%m-%d")

        for opt in opts:
            opt_type = opt.get("type", "unknown")
            opt_saved = opt.get("saved_usd", 0)

            by_type[opt_type]["count"] += 1
            by_type[opt_type]["saved"] += opt_saved
            daily[date_key]["saved"] += opt_saved
            daily[date_key]["breakdown"][opt_type] += opt_saved

        # Recent events (last 100)
        for opt in opts:
            recent_events.append({
                "id": rid,
                "ts": ts_str,
                "source": source,
                "model": model,
                "type": opt.get("type"),
                "saved_usd": opt.get("saved_usd", 0),
                "input_tokens": inp_tok,
                "output_tokens": out_tok,
                "effort": eff,
                "details": {k: v for k, v in opt.items() if k not in ("type", "saved_usd")}
            })

    # Sort events by recency
    recent_events = recent_events[:100]

    # Convert daily to sorted list
    daily_list = []
    for date_key in sorted(daily.keys()):
        daily_list.append({
            "date": date_key,
            "total_saved": daily[date_key]["saved"],
            "breakdown": dict(daily[date_key]["breakdown"])
        })

    # Calculate ROI
    if total_saved + actual_spent > 0:
        roi = (total_saved / (total_saved + actual_spent)) * 100
    else:
        roi = 0

    # Build routing_groups from DB aggregates
    routing_groups = []
    for rr in routing_rows:
        from_m, to_m, cnt, saved_sum, ts_last, avg_in, avg_out, eff = rr
        routing_groups.append({
            "from": from_m or "?",
            "to":   to_m   or "?",
            "count":    cnt,
            "saved":    round(saved_sum or 0, 4),
            "ts_last":  ts_last,
            "avg_input_tokens":  round(avg_in  or 0),
            "avg_output_tokens": round(avg_out or 0),
            "effort": eff or "standard",
        })

    return {
        "total_saved": round(total_saved, 4),
        "actual_spent": round(actual_spent, 4),
        "roi_percent": round(roi, 1),
        "event_count": len(recent_events),
        "by_type": {k: {"count": v["count"], "saved": round(v["saved"], 4)} for k, v in by_type.items()},
        "daily": daily_list,
        "recent_events": recent_events,
        "routing_groups": routing_groups,
    }


def get_sessions(period="7d", limit=50):
    clause = _period_clause(period)
    con = _connect()
    rows = con.execute(
        f"SELECT ts, source, cost_usd, duration_ms, tools_json, model, "
        f"input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens "
        f"FROM requests WHERE 1=1 {clause} ORDER BY ts"
    ).fetchall()
    con.close()
    if not rows:
        return []

    SESSION_GAP = 30 * 60
    sessions, current = [], []
    for row in rows:
        ts = row[0]
        if current:
            prev_dt = _naive_dt(current[-1][0])
            curr_dt = _naive_dt(ts)
            if (curr_dt - prev_dt).total_seconds() > SESSION_GAP:
                sessions.append(current)
                current = []
        current.append(row)
    if current:
        sessions.append(current)

    result = []
    for session in reversed(sessions[-limit:]):
        start_dt = _naive_dt(session[0][0])
        end_dt   = _naive_dt(session[-1][0])
        wall_ms  = int((end_dt - start_dt).total_seconds() * 1000)
        api_ms   = sum(r[3] or 0 for r in session)
        efficiency = round(api_ms / wall_ms * 100, 1) if wall_ms > 1000 else 100.0
        sources  = list({r[1] for r in session})
        costs    = [r[2] or 0.0 for r in session]

        total_input       = sum(r[6] or 0 for r in session)
        total_output      = sum(r[7] or 0 for r in session)
        total_cache_write = sum(r[9] or 0 for r in session)

        input_cost = output_cost = 0.0
        for r in session:
            model = r[5] or "default"
            p = PRICING.get(model) or PRICING["default"]
            input_cost  += (r[6] or 0) * p["input"]          / 1e6
            input_cost  += (r[8] or 0) * p["input"] * 0.10   / 1e6  # cache_read
            input_cost  += (r[9] or 0) * p["input"] * 1.25   / 1e6  # cache_creation
            output_cost += (r[7] or 0) * p["output"]         / 1e6

        tool_counts: dict = {}
        for r in session:
            tj = r[4]
            if tj:
                try:
                    for t in json.loads(tj):
                        tool_counts[t] = tool_counts.get(t, 0) + 1
                except Exception:
                    pass
        top_tools = sorted(tool_counts.items(), key=lambda x: x[1], reverse=True)[:5]

        result.append({
            "start":        session[0][0],
            "end":          session[-1][0],
            "wall_ms":      wall_ms,
            "api_ms":       api_ms,
            "wall_fmt":     _fmt_ms(wall_ms),
            "api_fmt":      _fmt_ms(api_ms),
            "efficiency":   efficiency,
            "req_count":    len(session),
            "sources":      sources,
            "total_cost":   round(sum(costs), 5),
            "max_cost":     round(max(costs), 5),
            "top_tools":    [{"name": k, "count": v} for k, v in top_tools],
            "total_input":       total_input,
            "total_output":      total_output,
            "total_cache_write": total_cache_write,
            "input_cost":        round(input_cost, 5),
            "output_cost":       round(output_cost, 5),
        })
    return result


def _tool_breakdown(period):
    clause = _period_clause(period)
    con = _connect()
    rows = con.execute(
        f"SELECT tools_json FROM requests WHERE 1=1 {clause} AND tools_json IS NOT NULL"
    ).fetchall()
    con.close()
    counts: dict = {}
    for (tj,) in rows:
        try:
            for t in json.loads(tj):
                key = t[0].upper() + t[1:] if t else t
                counts[key] = counts.get(key, 0) + 1
        except Exception:
            pass
    return sorted([{"name": k, "count": v} for k, v in counts.items()],
                  key=lambda x: x["count"], reverse=True)


def _hourly_heatmap():
    con = _connect()
    rows = con.execute("""
        SELECT
            date(ts, 'localtime') as day,
            CAST(strftime('%H', ts, 'localtime') AS INTEGER) as hour,
            ROUND(SUM(cost_usd), 6) as total_cost,
            COUNT(*) as reqs
        FROM requests
        WHERE date(ts, 'localtime') >= date('now', '-7 days', 'localtime')
        GROUP BY day, hour
        ORDER BY day, hour
    """).fetchall()
    con.close()
    return [{"day": r[0], "hour": r[1], "total_cost": r[2], "reqs": r[3]} for r in rows]


def _daily_trend(period):
    con = _connect()
    if period == "today":
        rows = con.execute("""
            SELECT strftime('%H', ts, 'localtime') as lbl,
                   ROUND(SUM(cost_usd), 5) as cost, COUNT(*) as reqs
            FROM requests WHERE date(ts, 'localtime') = date('now', 'localtime')
            GROUP BY lbl ORDER BY lbl
        """).fetchall()
    elif period in ("7d", "30d"):
        days = "7" if period == "7d" else "30"
        rows = con.execute(f"""
            SELECT date(ts, 'localtime') as lbl,
                   ROUND(SUM(cost_usd), 5) as cost, COUNT(*) as reqs
            FROM requests WHERE ts >= datetime('now', '-{days} days')
            GROUP BY lbl ORDER BY lbl
        """).fetchall()
    else:
        rows = con.execute("""
            SELECT strftime('%Y-%m', ts, 'localtime') as lbl,
                   ROUND(SUM(cost_usd), 5) as cost, COUNT(*) as reqs
            FROM requests GROUP BY lbl ORDER BY lbl
        """).fetchall()
    con.close()
    return [{"label": r[0], "cost": r[1] or 0, "reqs": r[2]} for r in rows]


def _projection(period="7d"):
    """Monthly projection consistent with the selected period."""
    con = _connect()
    if period == "today":
        row = con.execute(
            "SELECT ROUND(SUM(cost_usd),4) FROM requests "
            "WHERE date(ts,'localtime')=date('now','localtime')"
        ).fetchone()
        daily = row[0] or 0
    elif period == "30d":
        row = con.execute(
            "SELECT ROUND(SUM(cost_usd),4) FROM requests "
            "WHERE ts >= datetime('now','-30 days')"
        ).fetchone()
        daily = (row[0] or 0) / 30
    else:  # 7d and all — use stable 7-day rolling average
        row = con.execute(
            "SELECT ROUND(SUM(cost_usd),4) FROM requests "
            "WHERE ts >= datetime('now','-7 days')"
        ).fetchone()
        daily = (row[0] or 0) / 7
    con.close()
    return round(daily * 30, 2)


def _cost_breakdown(period):
    clause = _period_clause(period)
    con = _connect()
    rows = con.execute(f"""
        SELECT model,
               SUM(input_tokens) as inp, SUM(output_tokens) as out,
               SUM(cache_read_tokens) as cr, SUM(cache_creation_tokens) as cw
        FROM requests WHERE 1=1 {clause} GROUP BY model
    """).fetchall()
    con.close()
    bd = {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_creation": 0.0}
    for model, inp, out, cr, cw in rows:
        p = PRICING.get(model) or PRICING["default"]
        bd["input"]          += (inp or 0) * p["input"]        / 1e6
        bd["output"]         += (out or 0) * p["output"]       / 1e6
        bd["cache_read"]     += (cr  or 0) * p["input"] * 0.10 / 1e6
        bd["cache_creation"] += (cw  or 0) * p["input"] * 1.25 / 1e6
    return {k: round(v, 5) for k, v in bd.items()}


def _cache_by_tool(period):
    clause = _period_clause(period)
    con = _connect()
    rows = con.execute(f"""
        SELECT REPLACE(source,'-history','') as source, model,
               COUNT(*) as reqs,
               SUM(input_tokens) as inp,
               SUM(cache_read_tokens) as cr,
               SUM(cache_creation_tokens) as cw,
               ROUND(SUM(cost_usd), 5) as cost
        FROM requests WHERE 1=1 {clause}
        GROUP BY REPLACE(source,'-history',''), model
    """).fetchall()
    con.close()
    tools: dict = {}
    for source, model, reqs, inp, cr, cw, cost in rows:
        p = PRICING.get(model) or PRICING["default"]
        saved = (cr or 0) * p["input"] * 0.9 / 1e6
        if source not in tools:
            tools[source] = {"reqs": 0, "inp": 0, "cr": 0, "cw": 0, "cost": 0.0, "saved": 0.0}
        t = tools[source]
        t["reqs"] += reqs; t["inp"] += (inp or 0); t["cr"] += (cr or 0)
        t["cw"] += (cw or 0); t["cost"] += (cost or 0); t["saved"] += saved
    result = []
    for source, t in sorted(tools.items(), key=lambda x: x[1]["cost"], reverse=True):
        total_toks = t["inp"] + t["cr"] + t["cw"]
        hit_rate   = round(t["cr"] / total_toks * 100, 1) if total_toks else 0
        result.append({
            "source":    source,
            "reqs":      t["reqs"],
            "hit_rate":  hit_rate,
            "cache_read": t["cr"],
            "saved":     round(t["saved"], 4),
            "cost":      round(t["cost"], 4),
            "avg_cost":  round(t["cost"] / t["reqs"], 5) if t["reqs"] else 0,
        })
    return result


def _haiku_savings(period):
    clause = _period_clause(period)
    con = _connect()
    rows = con.execute(f"""
        SELECT model,
               SUM(input_tokens) as inp, SUM(output_tokens) as out,
               SUM(cache_read_tokens) as cr, SUM(cache_creation_tokens) as cw,
               ROUND(SUM(cost_usd), 5) as actual, COUNT(*) as reqs,
               AVG(input_tokens) as avg_inp, AVG(output_tokens) as avg_out
        FROM requests WHERE 1=1 {clause} AND model NOT LIKE '%haiku%'
        GROUP BY model
    """).fetchall()
    effort_rows = con.execute(f"""
        SELECT effort, COUNT(*) as cnt
        FROM requests WHERE 1=1 {clause} AND model NOT LIKE '%haiku%'
        GROUP BY effort
    """).fetchall()
    routing_rows = con.execute(f"""
        SELECT DISTINCT optimizations_json FROM requests
        WHERE optimizations_json IS NOT NULL {clause}
    """).fetchall()
    con.close()

    import json as _json
    hp = HAIKU_PRICING
    total_actual = total_haiku = total_reqs = 0.0
    total_inp = total_out = 0.0
    for _, inp, out, cr, cw, actual, reqs, _, _ in rows:
        total_haiku  += ((inp or 0)*hp["input"] + (out or 0)*hp["output"] +
                         (cr or 0)*hp["input"]*0.10 + (cw or 0)*hp["input"]*1.25) / 1e6
        total_actual += (actual or 0)
        total_reqs   += reqs
        total_inp    += (inp or 0)
        total_out    += (out or 0)
    effort_counts = {row[0]: row[1] for row in effort_rows}
    avg_input  = round(total_inp / total_reqs) if total_reqs > 0 else 0
    avg_output = round(total_out / total_reqs) if total_reqs > 0 else 0

    # Extract unique original models from routing optimizations_json
    seen = set()
    original_models = []
    for (opt_str,) in routing_rows:
        try:
            for opt in _json.loads(opt_str):
                if opt.get("type") == "routing" and opt.get("from"):
                    m = opt["from"].split("[")[0].strip()
                    if m and m not in seen:
                        seen.add(m)
                        original_models.append(m)
        except Exception:
            pass

    return {
        "actual": round(total_actual, 4), "haiku_equivalent": round(total_haiku, 4),
        "savings": round(total_actual - total_haiku, 4), "requests": int(total_reqs),
        "avg_input_tokens": avg_input, "avg_output_tokens": avg_output,
        "effort_counts": effort_counts,
        "original_models": original_models,
    }


def _cache_savings(period):
    clause = _period_clause(period)
    con = _connect()
    rows = con.execute(f"""
        SELECT model, SUM(cache_read_tokens) as cr
        FROM requests WHERE 1=1 {clause} GROUP BY model
    """).fetchall()
    con.close()
    saved = 0.0
    for model, cr in rows:
        if cr:
            p = PRICING.get(model) or PRICING["default"]
            saved += cr * p["input"] * 0.9 / 1_000_000
    return round(saved, 4)


def _pause_analysis(period):
    """Analyze inter-request pauses to recommend 5min vs 1h cache TTL."""
    clause = _period_clause(period)
    period_days = {"today": 1, "7d": 7, "30d": 30}.get(period, 7)

    con = _connect()
    rows = con.execute(
        f"SELECT ts, cache_creation_tokens, cache_creation_1h_tokens "
        f"FROM requests WHERE 1=1 {clause} ORDER BY ts"
    ).fetchall()
    con.close()

    if len(rows) < 3:
        return None

    SESSION_GAP = 30 * 60
    TTL_5M      =  5 * 60
    TTL_1H      = 60 * 60

    timestamps = [_naive_dt(r[0]) for r in rows]
    cw_all     = [r[1] or 0 for r in rows]
    cw_1h_all  = [r[2] or 0 for r in rows]

    within_gaps = []   # intra-session gap durations (seconds)
    sessions    = 1

    for i in range(1, len(timestamps)):
        gap = (timestamps[i] - timestamps[i-1]).total_seconds()
        if gap >= SESSION_GAP:
            sessions += 1
        else:
            within_gaps.append(gap)

    if not within_gaps:
        return None

    n     = len(within_gaps)
    hot   = sum(1 for g in within_gaps if g < TTL_5M)           # cache warm
    mid   = sum(1 for g in within_gaps if TTL_5M <= g < TTL_1H) # 5m expires; 1h survives
    long_ = sum(1 for g in within_gaps if g >= TTL_1H)           # expires regardless

    avg_cw = sum(cw_all) / max(len(cw_all), 1)
    ip     = (PRICING.get("claude-sonnet-4-6") or PRICING["default"])["input"]

    # Each "mid" gap avoided by 1h TTL: saves (1.25→0.10) per re-write token
    # Extra cost of 1h vs 5m write: (2.00-1.25) per original write token
    saved_period = mid     * avg_cw * ip * (1.25 - 0.10) / 1e6
    extra_period = sessions * avg_cw * ip * (2.00 - 1.25) / 1e6
    net_period   = saved_period - extra_period

    avg_gap_min = round(sum(within_gaps) / n / 60, 1)

    # Observed write TTL from the actual cache-write breakdown. Old rows (pre
    # split-tracking) report 1h=0, so a period of only old data reads as "5 min"
    # and the recommendation behaves exactly as before. Threshold: predominantly
    # 1h (>=80% of write tokens) → "1h"; predominantly 5m (<=20%) → "5 min".
    sum_cw       = sum(cw_all)
    sum_1h       = sum(cw_1h_all)
    pct_1h       = round(sum_1h / sum_cw * 100) if sum_cw else 0
    observed_ttl = "1h" if pct_1h >= 80 else ("5 min" if pct_1h <= 20 else "mixed")

    return {
        "sessions":       sessions,
        "within_gaps":    n,
        "hot_count":      hot,
        "mid_count":      mid,
        "long_count":     long_,
        "hot_pct":        round(hot  / n * 100),
        "mid_pct":        round(mid  / n * 100),
        "long_pct":       round(long_ / n * 100),
        "avg_gap_min":    avg_gap_min,
        "net_period":     round(net_period, 4),
        "net_monthly":    round(net_period / period_days * 30, 2),
        "recommendation": "1h"   if mid > 0 and net_period > 0 else "5min",
        "mid_per_day":    round(mid / period_days, 1),
        "cache_1h_pct":   pct_1h,
        "observed_ttl":   observed_ttl,
    }


def _health_grade(summary, period):
    clause     = _period_clause(period)
    total_reqs = summary.get("total_requests") or 0
    if total_reqs < 5:
        return None

    total_inp = summary.get("total_input")            or 0
    total_cr  = summary.get("total_cache_read")        or 0
    total_cw  = summary.get("total_cache_creation")    or 0
    avg_inp   = total_inp / max(total_reqs, 1)

    con = _connect()
    max_tok_count = con.execute(
        f"SELECT COUNT(*) FROM requests WHERE stop_reason='max_tokens' AND 1=1 {clause}"
    ).fetchone()[0]
    con.close()

    score   = 0
    details = []

    # 1. Cache hit rate (35 pts)
    cacheable = total_cr + total_inp
    hit_rate  = (total_cr / cacheable * 100) if cacheable > 0 else 0
    if   hit_rate >= 60: cache_pts = 35
    elif hit_rate >= 40: cache_pts = 25
    elif hit_rate >= 20: cache_pts = 15
    elif hit_rate >=  5: cache_pts = 5
    else:                cache_pts = 0
    score += cache_pts
    if cache_pts < 25:
        details.append(f"Cache hit {hit_rate:.0f}% — keep sessions open longer")

    # 2. Average context size (25 pts)
    if   avg_inp <= 20_000:  ctx_pts = 25
    elif avg_inp <= 50_000:  ctx_pts = 20
    elif avg_inp <= 100_000: ctx_pts = 10
    elif avg_inp <= 200_000: ctx_pts = 5
    else:                    ctx_pts = 0
    score += ctx_pts
    if ctx_pts < 20:
        details.append(f"Avg input {avg_inp/1000:.0f}k tok — run /compact more often")

    # 3. Cache write ROI (25 pts)
    roi = (total_cr / total_cw) if total_cw > 0 else None
    if roi is None:
        cw_pts = 15  # no caching — neutral
    elif roi >= 3:   cw_pts = 25
    elif roi >= 2:   cw_pts = 20
    elif roi >= 1:   cw_pts = 12
    elif roi >= 0.5: cw_pts = 5
    else:            cw_pts = 0
    score += cw_pts
    if roi is not None and cw_pts < 12:
        details.append(f"Cache ROI {roi:.1f}× — sessions restart too often, cache rebuilt each time")

    # 4. Max-tokens truncation rate (15 pts)
    max_tok_pct = (max_tok_count / total_reqs * 100) if total_reqs > 0 else 0
    if   max_tok_pct <= 1:  mt_pts = 15
    elif max_tok_pct <= 5:  mt_pts = 10
    elif max_tok_pct <= 10: mt_pts = 5
    else:                   mt_pts = 0
    score += mt_pts
    if mt_pts < 10:
        details.append(f"{max_tok_pct:.0f}% requests truncated — hit max_tokens limit")

    if   score >= 90: grade = "A"
    elif score >= 75: grade = "B"
    elif score >= 60: grade = "C"
    elif score >= 45: grade = "D"
    else:             grade = "F"

    color = {"A": "#16a34a", "B": "#65a30d", "C": "#d97706", "D": "#ea580c", "F": "#dc2626"}[grade]

    return {
        "score":          score,
        "grade":          grade,
        "color":          color,
        "details":        details[:3],
        "cache_hit_rate": round(hit_rate, 1),
        "cache_roi":      round(roi, 1) if roi is not None else None,
        "max_tokens_pct": round(max_tok_pct, 1),
    }


def _task_breakdown(period):
    clause = _period_clause(period)
    con    = _connect()
    rows   = con.execute(
        f"SELECT tools_json, cost_usd, input_tokens, output_tokens, tool_call_count "
        f"FROM requests WHERE 1=1 {clause}"
    ).fetchall()
    con.close()

    # Map individual tools → semantic group
    TOOL_GROUP = {
        "Edit": "coding",      "Write": "coding",   "MultiEdit": "coding", "NotebookEdit": "coding",
        "Bash": "bash",
        "Agent": "delegation", "TaskOutput": "delegation", "TaskStop": "delegation",
        "WebFetch": "web",     "WebSearch": "web",
        "TodoWrite": "planning", "EnterPlanMode": "planning", "ExitPlanMode": "planning",
        "Read": "exploration", "Glob": "exploration", "Grep": "exploration",
    }

    def classify(groups: set, tools_raw: list) -> str:
        # testing: bash + coding tools together (build-test-fix cycle)
        has_test = any(
            t in ("Bash",) for t in tools_raw
        ) and any(
            t in ("Edit", "Write", "MultiEdit", "Read", "Grep") for t in tools_raw
        ) and len(tools_raw) >= 3
        if "delegation" in groups:                        return "delegation"
        if "coding" in groups and "planning" in groups:   return "feature_dev"
        if "coding" in groups and "bash" in groups and has_test: return "testing"
        if "coding" in groups:                            return "coding"
        if "bash"   in groups and "exploration" in groups: return "debugging"
        if "bash"   in groups:                            return "build_run"
        if "web"    in groups:                            return "web"
        if "planning" in groups:                          return "planning"
        if "exploration" in groups:                       return "exploration"
        return "conversation"

    buckets: dict = {}
    total = max(len(rows), 1)
    for tools_json, cost, inp, out, tool_count in rows:
        groups: set = set()
        tools_raw: list = []
        has_mcp = False
        if tools_json:
            try:
                tools_raw = json.loads(tools_json)
                for t in tools_raw:
                    g = TOOL_GROUP.get(t)
                    if g:
                        groups.add(g)
                    elif str(t).startswith("mcp__"):
                        has_mcp = True
            except Exception:
                pass

        cat = classify(groups, tools_raw)
        if cat == "conversation" and has_mcp:
            cat = "mcp"

        b = buckets.setdefault(cat, {"reqs": 0, "cost": 0.0, "input": 0, "output": 0, "one_shot": 0})
        b["reqs"]   += 1
        b["cost"]   += cost or 0.0
        b["input"]  += inp  or 0
        b["output"] += out  or 0
        if (tool_count or 0) == 0:
            b["one_shot"] += 1

    result = []
    for cat in sorted(buckets, key=lambda c: -buckets[c]["cost"]):
        b = buckets[cat]
        one_shot_pct = round(b["one_shot"] / b["reqs"] * 100) if b["reqs"] else None
        result.append({
            "category":     cat,
            "reqs":         b["reqs"],
            "pct":          round(b["reqs"] / total * 100),
            "cost":         round(b["cost"], 5),
            "avg_input":    round(b["input"] / max(b["reqs"], 1)),
            "one_shot_pct": one_shot_pct,
        })
    return result


def _tool_breakdown_split(period):
    clause = _period_clause(period)
    con = _connect()
    rows = con.execute(
        f"SELECT tools_json FROM requests WHERE 1=1 {clause} AND tools_json IS NOT NULL"
    ).fetchall()
    con.close()
    core_counts: dict = {}
    mcp_servers: dict = {}
    for (tj,) in rows:
        try:
            for t in json.loads(tj):
                if str(t).startswith("mcp__"):
                    parts = t.split("__", 2)
                    server = parts[1] if len(parts) > 1 else t
                    mcp_servers[server] = mcp_servers.get(server, 0) + 1
                else:
                    key = t[0].upper() + t[1:] if t else t
                    core_counts[key] = core_counts.get(key, 0) + 1
        except Exception:
            pass
    core = sorted([{"name": k, "count": v} for k, v in core_counts.items()],
                  key=lambda x: x["count"], reverse=True)
    mcp  = sorted([{"name": k, "count": v} for k, v in mcp_servers.items()],
                  key=lambda x: x["count"], reverse=True)
    return {"core": core, "mcp": mcp}


def _effort_breakdown(period):
    clause = _period_clause(period)
    con = _connect()
    rows = con.execute(f"""
        SELECT model,
               COALESCE(effort, 'standard') as effort,
               COUNT(*)                     as reqs,
               ROUND(SUM(cost_usd), 4)      as cost,
               ROUND(AVG(cost_usd), 5)      as avg_cost,
               SUM(output_tokens)           as total_out,
               ROUND(AVG(output_tokens))    as avg_out,
               ROUND(AVG(input_tokens))     as avg_inp
        FROM requests WHERE 1=1 {clause}
        GROUP BY model, effort
        ORDER BY cost DESC
    """).fetchall()
    con.close()
    return [{"model": r[0], "effort": r[1], "reqs": r[2], "cost": r[3],
             "avg_cost": r[4], "total_out": r[5], "avg_out": r[6], "avg_inp": r[7]} for r in rows]


def _recommendations(summary, haiku_savings, by_model=None, period="7d"):
    recs = []
    cr    = summary.get("total_cache_read") or 0
    inp   = (summary.get("total_input") or 0) + cr + (summary.get("total_cache_creation") or 0)
    reqs  = summary.get("total_requests") or 0
    cost  = summary.get("total_cost") or 0
    out   = summary.get("total_output") or 0

    # Scale factor: period cost → monthly estimate
    period_days = {"today": 1, "7d": 7, "30d": 30}.get(period, 30)
    monthly = lambda c: round(c / period_days * 30, 2) if c and period_days else None

    # ── Monthly projection (always show if enough data)
    proj = _projection(period)
    if reqs >= 5 and proj >= 0.50:
        recs.append({
            "level": "info",
            "title": f"~${proj:.2f} projected this month",
            "problem": f"At current pace (${cost:.2f} in {period_days}d) you'll spend ~${proj:.2f} this calendar month.",
            "action": "Review the insights below to identify what to cut.",
            "savings_usd": None,
        })

    # ── Cache hit rate
    if reqs >= 5 and inp > 0:
        hit_rate = cr / inp * 100
        cw_cost = (summary.get("total_cache_creation") or 0) * (PRICING.get("claude-sonnet-4-6") or PRICING["default"])["input"] * 1.25 / 1e6
        if hit_rate < 20:
            potential = monthly(cw_cost * 0.5) or 0  # rough: 50% of cache write cost recovered if sessions longer
            recs.append({
                "level": "warn",
                "title": f"Cache hit rate {hit_rate:.0f}% — you're paying for cache that isn't being reused",
                "problem": f"You wrote ${cw_cost:.3f} in cache but only reused {hit_rate:.0f}% of it. Each session restart discards the cache and you pay to rebuild it next time.",
                "action": "Keep Claude Code / VS Code sessions open longer. Use /compact when context gets large instead of closing. Aim for sessions > 30 min.",
                "savings_usd": potential if potential > 0.10 else None,
            })
        elif hit_rate > 65:
            recs.append({
                "level": "good",
                "title": f"Cache hit rate {hit_rate:.0f}% — excellent, keep long sessions going",
                "problem": f"Cache saved ${haiku_savings.get('actual',0)*0.0:.3f} this period. Long sessions are working well.",
                "action": "Continue working in long sessions. Cache is already optimized.",
                "savings_usd": None,
            })

    # ── Opus on small tasks
    for m in (by_model or []):
        model_name = m.get("model") or ""
        if "opus" in model_name.lower():
            m_reqs = m.get("reqs") or 0
            m_out  = m.get("out") or 0
            m_inp  = m.get("inp") or 0
            m_cost = m.get("cost") or 0
            if m_reqs >= 3 and m_out / m_reqs < 200:
                p_opus   = PRICING.get(model_name) or PRICING["default"]
                p_sonnet = PRICING.get("claude-sonnet-4-6") or PRICING["default"]
                sonnet_cost = ((m_inp or 0) * p_sonnet["input"] + (m_out or 0) * p_sonnet["output"]) / 1e6
                period_savings = round(max(0, m_cost - sonnet_cost), 3)
                recs.append({
                    "level": "warn",
                    "title": f"Opus used for tiny tasks — avg {m_out//m_reqs} output tokens",
                    "problem": f"{m_reqs} Opus requests produced < 200 tokens each. You're paying Opus price (${p_opus['output']}/M) for outputs that Sonnet (${p_sonnet['output']}/M) handles equally well.",
                    "action": "Add to CLAUDE.md: 'use claude-sonnet-4-6 by default'. Or set env: export ANTHROPIC_MODEL=claude-sonnet-4-6. Reserve Opus only for complex reasoning tasks.",
                    "savings_usd": monthly(period_savings) if period_savings > 0.10 else None,
                })
                break

    # ── Large context bloat
    avg_inp = (summary.get("total_input") or 0) / max(reqs, 1)
    if avg_inp > 40_000 and reqs >= 5:
        p_default = PRICING.get("claude-sonnet-4-6") or PRICING["default"]
        input_cost = (summary.get("total_input") or 0) * p_default["input"] / 1e6
        period_savings = round(input_cost * 0.55, 3)
        recs.append({
            "level": "warn",
            "title": f"Context bloat — avg {avg_inp/1000:.0f}k tokens input per request",
            "problem": f"Long sessions accumulate context. You're sending the same historical conversation on every request, paying for it each time. At {avg_inp/1000:.0f}k tokens avg, this adds up fast.",
            "action": "Run /compact in terminal every 30–60 minutes of work. It compresses history to ~5–10k tokens, saving cost on every request after.",
            "savings_usd": monthly(period_savings) if period_savings > 0.05 else None,
        })

    # ── Output verbosity
    eff_inp = max((summary.get("total_input") or 0) + cr, 1)
    if out / eff_inp > 2.5 and reqs >= 5:
        recs.append({
            "level": "tip",
            "title": f"Output is {out/eff_inp:.1f}× your input — verbose responses",
            "problem": "Output tokens cost 5× more than input. Long detailed responses are expensive even if you didn't ask for detail.",
            "action": "Add to prompts: 'be concise'. For code tasks: 'give me just the code, no explanation'. Try Haiku for quick lookups — it's faster and cheaper.",
            "savings_usd": None,
        })

    # ── Haiku savings
    if haiku_savings.get("savings", 0) > 0.20:
        recs.append({
            "level": "tip",
            "title": f"${haiku_savings['savings']:.2f} this period could be saved with Haiku",
            "problem": f"{haiku_savings['requests']} requests cost ${haiku_savings['actual']:.2f} on Sonnet/Opus. The same requests on Haiku would cost ${haiku_savings['haiku_equivalent']:.2f} — a {round((1-haiku_savings['haiku_equivalent']/max(haiku_savings['actual'],0.001))*100)}% reduction.",
            "action": "For simple tasks (quick edits, short lookups, yes/no questions) use Haiku. Add to CLAUDE.md: 'use claude-haiku-4-5 for simple tasks'.",
            "savings_usd": monthly(haiku_savings["savings"]) if period_days < 31 else round(haiku_savings["savings"], 2),
        })

    return recs[:5]


def _action_plan(summary, haiku_savings, by_model, period, pause=None):
    period_days = {"today": 1, "7d": 7, "30d": 30}.get(period, 30)
    cost        = summary.get("total_cost") or 0
    daily_rate  = cost / period_days if period_days else 0

    con = _connect()
    today_cost = (con.execute(
        "SELECT ROUND(SUM(cost_usd),4) FROM requests "
        "WHERE date(ts,'localtime')=date('now','localtime')"
    ).fetchone()[0] or 0)
    con.close()

    proj    = _projection(period)
    actions = []

    # ── 1. Opus → Sonnet (exact from real token counts)
    opus_saving_period = 0.0
    opus_reqs          = 0
    for m in (by_model or []):
        if "opus" not in (m.get("model") or "").lower():
            continue
        m_inp  = m.get("inp")  or 0
        m_out  = m.get("out")  or 0
        m_cost = m.get("cost") or 0
        m_cr   = m.get("cache_read") or 0
        p_son  = PRICING.get("claude-sonnet-4-6") or PRICING["default"]
        sonnet_cost = (m_inp * p_son["input"] + m_out * p_son["output"] +
                       m_cr  * p_son["input"] * 0.10) / 1e6
        opus_saving_period += max(0.0, m_cost - sonnet_cost)
        opus_reqs          += m.get("reqs") or 0

    if opus_saving_period > 0.005:
        daily = round(opus_saving_period / period_days, 4)
        actions.append({
            "id": "sonnet",
            "title": "Switch Opus → Sonnet",
            "description": (f"{opus_reqs} Opus requests this period. "
                            "Sonnet handles 90% of tasks at 1/5 the price."),
            "command": '// ~/.claude/settings.json\n{"model": "claude-sonnet-4-6"}\n// or: export ANTHROPIC_MODEL=claude-sonnet-4-6',
            "daily_saving":   daily,
            "monthly_saving": round(daily * 30, 2),
            "certainty":      "exact",
        })

    # ── 2. /compact — estimate from avg context size
    avg_inp = (summary.get("total_input") or 0) / max(summary.get("total_requests") or 1, 1)
    if avg_inp > 15_000:
        p_son           = PRICING.get("claude-sonnet-4-6") or PRICING["default"]
        input_cost      = (summary.get("total_input") or 0) * p_son["input"] / 1e6
        compact_saving  = input_cost * 0.40  # rough: /compact cuts ~40% of accumulated context
        daily = round(compact_saving / period_days, 4)
        if daily > 0.005:
            actions.append({
                "id": "compact",
                "title": "Run /compact every 30–60 min",
                "description": (f"Avg context {avg_inp/1000:.0f}k tokens/request. "
                                "/compact compresses history to ~5k — saves on every request after."),
                "command": "/compact\n# or focused: /compact focus on code changes\n/clear  # start fresh between unrelated tasks",
                "daily_saving":   daily,
                "monthly_saving": round(daily * 30, 2),
                "certainty":      "estimate",
            })

    # ── 3. Haiku for simple tasks (from real haiku_savings)
    if haiku_savings.get("savings", 0) > 0.05:
        daily = round(haiku_savings["savings"] / period_days, 4)
        pct   = round((1 - haiku_savings["haiku_equivalent"] /
                       max(haiku_savings["actual"], 0.001)) * 100)
        actions.append({
            "id": "haiku",
            "title": "Haiku for simple requests",
            "description": (f"{haiku_savings['requests']} requests: "
                            f"${haiku_savings['actual']:.2f} on Sonnet/Opus "
                            f"→ ${haiku_savings['haiku_equivalent']:.2f} on Haiku ({pct}% cheaper)."),
            "command": "// ~/.claude/settings.json\n{\"model\": \"claude-haiku-4-5\"}\n// or per-session: /model haiku",
            "daily_saving":   daily,
            "monthly_saving": round(daily * 30, 2),
            "certainty":      "estimate",
        })

    # ── 4. Cache TTL recommendation (data-driven from pause analysis)
    cw = summary.get("total_cache_creation") or 0
    cr = summary.get("total_cache_read")     or 0
    if cw > 1_000:
        p_son     = PRICING.get("claude-sonnet-4-6") or PRICING["default"]
        inp_price = p_son["input"]
        total_toks = (summary.get("total_input") or 0) + cr + cw
        hit_rate   = round(cr / total_toks * 100, 1) if total_toks else 0
        cw_cost_paid = round(cw * inp_price * 1.25 / 1e6, 4)
        no_cache_cost = round((cw + cr) * inp_price / 1e6, 4)
        cr_cost_paid  = round(cr * inp_price * 0.10 / 1e6, 4)

        if pause:
            rec          = pause["recommendation"]
            net_monthly  = pause["net_monthly"]
            daily        = round(net_monthly / 30, 4)
            mid_count    = pause["mid_count"]
            mid_per_day  = pause["mid_per_day"]
            observed_ttl = pause.get("observed_ttl", "5 min")
            # Label for what the client is actually writing at, from the observed
            # cache-write breakdown (not an assumption).
            current_ttl_label = {"1h": "1 hour",
                                 "mixed": "mixed (5 min + 1h)"}.get(observed_ttl, "5 min")

            if observed_ttl == "1h":
                # Already writing at 1h — the mid-session gaps it would otherwise
                # flag are absorbed by the 1h cache. Recommendation is moot.
                actions.append({
                    "id": "cache1h",
                    "title": "Cache TTL: already on 1-hour (optimal)",
                    "description": (
                        f"You're already writing cache at 1h TTL ({pause['cache_1h_pct']}% of "
                        f"cache-write tokens). Your {mid_count} within-session pauses of 5–60 min "
                        f"(≈{mid_per_day}/day) are absorbed by the 1h cache instead of forcing "
                        f"re-writes — no action needed."
                    ),
                    "command": "# No change needed. Already on 1h cache TTL.",
                    "daily_saving":   0,
                    "monthly_saving": 0,
                    "certainty":      "exact",
                    "cache_detail": {
                        "current_ttl":   "1 hour",
                        "available_ttl": "1 hour",
                        "hit_rate":      hit_rate,
                        "cw_cost":       cw_cost_paid,
                        "cr_saved":      round(no_cache_cost - cr_cost_paid, 4),
                        "cw_tokens":     cw,
                        "cr_tokens":     cr,
                    },
                    "pause": pause,
                })
            elif rec == "1h" and net_monthly > 0:
                actions.append({
                    "id": "cache1h",
                    "title": "Enable 1-hour cache TTL",
                    "description": (
                        f"Detected {mid_count} within-session pauses of 5–60 min "
                        f"(≈{mid_per_day}/day) — your 5 min cache expired each time, "
                        f"forcing a re-write. With 1h TTL those become cheap reads instead. "
                        f"Write price: 5m=1.25× vs 1h=2× base — but you avoid {mid_count} re-writes."
                    ),
                    "command": (
                        "// In cache_control blocks send:\n"
                        '{"type": "ephemeral", "ttl": "1h"}\n'
                        "// Claude Code: configure in client settings\n"
                        "// Or inject via proxy (ask Claude)"
                    ),
                    "daily_saving":   daily,
                    "monthly_saving": net_monthly,
                    "certainty":      "estimate",
                    "cache_detail": {
                        "current_ttl":   current_ttl_label,
                        "available_ttl": "1 hour",
                        "hit_rate":      hit_rate,
                        "cw_cost":       cw_cost_paid,
                        "cr_saved":      round(no_cache_cost - cr_cost_paid, 4),
                        "cw_tokens":     cw,
                        "cr_tokens":     cr,
                    },
                    "pause": pause,
                })
            else:
                # 5 min is optimal — still show as info
                actions.append({
                    "id": "cache1h",
                    "title": "Cache TTL: 5 min is optimal for you",
                    "description": (
                        f"Work pattern: {pause['hot_pct']}% of pauses under 5 min, "
                        f"only {pause['mid_pct']}% in the 5–60 min range. "
                        f"5 min TTL fits your pattern — 1h would cost more in write fees than it saves."
                    ),
                    "command": "# No change needed. Keep current 5-min cache TTL.",
                    "daily_saving":   0,
                    "monthly_saving": 0,
                    "certainty":      "exact",
                    "cache_detail": {
                        "current_ttl":   current_ttl_label,
                        "available_ttl": "1 hour",
                        "hit_rate":      hit_rate,
                        "cw_cost":       cw_cost_paid,
                        "cr_saved":      round(no_cache_cost - cr_cost_paid, 4),
                        "cw_tokens":     cw,
                        "cr_tokens":     cr,
                    },
                    "pause": pause,
                })
        else:
            # No pause data (< 3 requests) — generic fallback
            daily = round(cw * inp_price * 0.20 / 1e6 / period_days, 4)
            if daily > 0.001:
                actions.append({
                    "id": "cache1h",
                    "title": "Consider 1-hour cache TTL",
                    "description": (
                        f"Not enough requests yet to analyze your work pattern. "
                        f"If you take breaks > 5 min, 1h TTL may save on re-writes. "
                        f"Write cost: 5m=1.25× vs 1h=2× base input price."
                    ),
                    "command": '{"type": "ephemeral", "ttl": "1h"}  // in cache_control',
                    "daily_saving":   daily,
                    "monthly_saving": round(daily * 30, 2),
                    "certainty":      "rough",
                    "cache_detail": {
                        "current_ttl":   "5 min",
                        "available_ttl": "1 hour",
                        "hit_rate":      hit_rate,
                        "cw_cost":       cw_cost_paid,
                        "cr_saved":      round(no_cache_cost - cr_cost_paid, 4),
                        "cw_tokens":     cw,
                        "cr_tokens":     cr,
                    },
                    "pause": None,
                })

    # ── 5. Lower effort level (rough: ~12% of daily cost)
    effort_saving = round(daily_rate * 0.12, 4)
    if effort_saving > 0.02 and len(actions) < 5:
        actions.append({
            "id": "effort",
            "title": "Lower effort to medium",
            "description": ("Extended thinking (effort=high/xhigh) burns extra tokens on "
                            "routine tasks. Set medium as default, use high only for complex decisions."),
            "command": "// ~/.claude/settings.json\n{\"effortLevel\": \"medium\",\n \"alwaysThinkingEnabled\": false}",
            "daily_saving":   effort_saving,
            "monthly_saving": round(effort_saving * 30, 2),
            "certainty":      "rough",
        })

    raw_daily   = round(sum(a["daily_saving"]   for a in actions), 4)
    raw_monthly = round(sum(a["monthly_saving"] for a in actions), 2)

    # Savings can't exceed 90% of what you actually project to spend.
    # The 10% floor reflects irreducible output-token and minimal-input costs.
    max_monthly = round(proj * 0.90, 2) if proj > 0 else 0
    max_daily   = round(daily_rate * 0.90, 4) if daily_rate > 0 else 0
    total_monthly = min(raw_monthly, max_monthly)
    total_daily   = min(raw_daily,   max_daily)

    return {
        "today_cost":           today_cost,
        "period_cost":          cost,
        "monthly_projection":   proj,
        "optimized_monthly":    round(max(0, proj - total_monthly), 2),
        "daily_rate":           round(daily_rate, 4),
        "actions":              actions,
        "total_daily_saving":   total_daily,
        "total_monthly_saving": total_monthly,
    }


def get_stats(period="7d"):
    clause = _period_clause(period)
    con = _connect()
    con.row_factory = sqlite3.Row

    summary = dict(con.execute(f"""
        SELECT COUNT(*)                   AS total_requests,
               SUM(input_tokens)          AS total_input,
               SUM(output_tokens)         AS total_output,
               SUM(cache_read_tokens)     AS total_cache_read,
               SUM(cache_creation_tokens) AS total_cache_creation,
               ROUND(SUM(cost_usd),4)     AS total_cost,
               ROUND(AVG(duration_ms))    AS avg_ms,
               SUM(duration_ms)           AS total_api_ms,
               SUM(tool_call_count)       AS total_tool_calls
        FROM requests WHERE 1=1 {clause}
    """).fetchone())

    by_source = [dict(r) for r in con.execute(f"""
        SELECT
               REPLACE(source, '-history', '') as source,
               COUNT(*) as reqs,
               ROUND(SUM(cost_usd),4) as total_cost,
               ROUND(AVG(cost_usd),5) as avg_cost,
               ROUND(AVG(input_tokens + output_tokens + cache_read_tokens)) as avg_tokens
        FROM requests WHERE 1=1 {clause}
        GROUP BY REPLACE(source, '-history', '') ORDER BY total_cost DESC
    """).fetchall()]

    by_model = [dict(r) for r in con.execute(f"""
        SELECT model, COUNT(*) as reqs,
               SUM(input_tokens) as inp, SUM(output_tokens) as out,
               SUM(cache_read_tokens) as cache_read,
               SUM(cache_creation_tokens) as cache_creation,
               ROUND(AVG(duration_ms)) as avg_ms,
               ROUND(SUM(cost_usd),4)  as cost,
               SUM(CASE WHEN tool_call_count = 0 THEN 1 ELSE 0 END) as one_shots
        FROM requests WHERE 1=1 {clause}
        GROUP BY model ORDER BY cost DESC
    """).fetchall()]
    for m in by_model:
        total_toks = (m.get("inp") or 0) + (m.get("cache_read") or 0) + (m.get("cache_creation") or 0)
        m["cache_hit_rate"] = round((m.get("cache_read") or 0) / total_toks * 100, 1) if total_toks else 0
        m["one_shot_pct"]   = round((m.get("one_shots") or 0) / m["reqs"] * 100) if m.get("reqs") else None
        reqs = m.get("reqs") or 1
        m["avg_inp"] = round((m.get("inp") or 0) / reqs)
        m["avg_out"] = round((m.get("out") or 0) / reqs)

    top_requests = [dict(r) for r in con.execute(f"""
        SELECT ts, source, model, input_tokens, output_tokens, cache_read_tokens,
               cache_creation_tokens,
               stop_reason, tool_call_count, tools_json,
               ROUND(cost_usd,5) as cost_usd, duration_ms, status
        FROM requests WHERE 1=1 {clause}
        ORDER BY cost_usd DESC LIMIT 10
    """).fetchall()]

    recent = [dict(r) for r in con.execute(f"""
        SELECT ts, source, model, input_tokens, output_tokens,
               cache_read_tokens, cache_creation_tokens,
               stop_reason, tool_call_count,
               ROUND(cost_usd,5) as cost_usd, duration_ms, status
        FROM requests WHERE 1=1 {clause}
        ORDER BY id DESC LIMIT 20
    """).fetchall()]

    heatmap = [dict(r) for r in con.execute("""
        SELECT date(ts, 'localtime') as day,
               ROUND(SUM(cost_usd),4) as total, COUNT(*) as reqs
        FROM requests WHERE ts >= date('now','-365 days')
        GROUP BY day ORDER BY day
    """).fetchall()]

    stop_reasons = [dict(r) for r in con.execute(f"""
        SELECT stop_reason, COUNT(*) as count
        FROM requests WHERE 1=1 {clause} AND stop_reason IS NOT NULL
        GROUP BY stop_reason ORDER BY count DESC
    """).fetchall()]

    con.close()

    haiku = _haiku_savings(period)
    pause = _pause_analysis(period)
    summary["total_api_fmt"]    = _fmt_ms(summary.get("total_api_ms"))
    summary["output_per_dollar"] = (
        round((summary.get("total_output") or 0) / summary["total_cost"])
        if (summary.get("total_cost") or 0) > 0 else 0
    )

    return {
        "period":          period,
        "summary":         summary,
        "by_source":       by_source,
        "by_model":        by_model,
        "top_requests":    top_requests,
        "recent":          recent,
        "heatmap":         heatmap,
        "sessions":        get_sessions(period),
        "cache_saved":     _cache_savings(period),
        "cost_breakdown":  _cost_breakdown(period),
        "cache_by_tool":   _cache_by_tool(period),
        "tool_breakdown":  _tool_breakdown(period),
        "tool_breakdown_split": _tool_breakdown_split(period),
        "hourly_heatmap":  _hourly_heatmap(),
        "haiku_savings":   haiku,
        "stop_reasons":    stop_reasons,
        "daily_trend":     _daily_trend(period),
        "projection":      _projection(period),
        "recommendations": _recommendations(summary, haiku, by_model, period),
        "action_plan":     _action_plan(summary, haiku, by_model, period, pause),
        "effort_breakdown": _effort_breakdown(period),
        "pause_analysis":  pause,
        "health_grade":    _health_grade(summary, period),
        "task_breakdown":  _task_breakdown(period),
    }


def weekly_digest():
    con = _connect()
    con.row_factory = sqlite3.Row
    cur  = dict(con.execute(
        "SELECT COUNT(*) as reqs, ROUND(SUM(cost_usd),2) as cost FROM requests WHERE ts >= datetime('now','-7 days')"
    ).fetchone())
    prev = dict(con.execute(
        "SELECT ROUND(SUM(cost_usd),2) as cost FROM requests WHERE ts >= datetime('now','-14 days') AND ts < datetime('now','-7 days')"
    ).fetchone())
    top  = con.execute(
        "SELECT REPLACE(source,'-history','') as source, ROUND(SUM(cost_usd),2) as cost FROM requests WHERE ts >= datetime('now','-7 days') GROUP BY REPLACE(source,'-history','') ORDER BY cost DESC LIMIT 1"
    ).fetchone()
    con.close()
    cost      = cur["cost"]  or 0.0
    prev_cost = prev["cost"] or 0.0
    delta     = f"  {cost / prev_cost * 100 - 100:+.0f}% vs prev" if prev_cost else ""
    print("━" * 42)
    print(f"  Last 7 days: ${cost:.2f}{delta}")
    print(f"  Requests: {cur['reqs'] or 0}")
    if top:
        print(f"  Top source: {top['source']} (${top['cost']:.2f})")
    print("━" * 42)
