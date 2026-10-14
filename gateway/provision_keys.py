"""Cria ou atualiza as chaves virtuais declaradas em keys.yaml (idempotente).

Roda dentro do container do gateway, depois que o LiteLLM responde. Para cada chave:
`/key/info` diz se ela já existe; se não, `/key/generate` com o valor fixo; se sim,
`/key/update` reaplica os atributos declarados (o arquivo é a fonte da verdade).
Só usa a biblioteca padrão e o PyYAML que já vem na imagem do LiteLLM.
"""
import json
import os
import sys
import urllib.error
import urllib.request

import yaml

BASE = "http://127.0.0.1:4000"
MASTER = os.environ["LITELLM_MASTER_KEY"]


def resolve(value):
    if isinstance(value, str) and value.startswith("os.environ/"):
        return os.environ[value.removeprefix("os.environ/")]
    return value


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {MASTER}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


def main(path: str) -> None:
    with open(path, encoding="utf-8") as f:
        spec = yaml.safe_load(f)
    for item in spec["keys"]:
        key = resolve(item["key"])
        attrs = {k: resolve(v) for k, v in item.items() if k not in ("alias", "key")}
        attrs["key_alias"] = item["alias"]
        status, _ = call("GET", f"/key/info?key={key}")
        if status == 200:
            status, body = call("POST", "/key/update", {"key": key, **attrs})
            action = "atualizada"
        else:
            status, body = call("POST", "/key/generate", {"key": key, **attrs})
            action = "criada"
        if status != 200:
            sys.exit(f"provision_keys: falha em {item['alias']}: HTTP {status} {body}")
        print(f"provision_keys: chave {item['alias']} {action}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
