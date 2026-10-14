import os

OPENAI_API_KEY = os.environ["FAKE_OPENAI_KEY"]
ANTHROPIC_API_KEY = os.environ["FAKE_ANTHROPIC_KEY"]

OPENAI_BASE_URL = "http://provider-fake:8090/openai/v1"
ANTHROPIC_BASE_URL = "http://provider-fake:8090/anthropic"

# AI Gateway (F1 já migrada; as demais features migram na Etapa 6).
GATEWAY_URL = os.environ["GATEWAY_URL"]
GATEWAY_API_KEY = os.environ["GATEWAY_API_KEY"]
# Abaixo dos 30 s da borda: a app não continua esperando por uma requisição que a borda já abandonou.
GATEWAY_TIMEOUT = 25.0

# A extração roda na OpenAI; a sugestão e o relatório, na Anthropic.
EXTRACTION_MODEL = "gpt-fake-large"
SUGGESTION_MODEL = "claude-fake-large"
REPORT_MODEL = "claude-fake-large"

MAX_OUTPUT_TOKENS = 2000

TICKETS_FILE = "/data/tickets.jsonl"
TICKETS_PER_CALL = 150
