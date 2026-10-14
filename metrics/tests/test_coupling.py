"""Testes do metrics/coupling.py contra um pacote de exemplo montado em tmp_path.

O pacote `helpdesk` de exemplo exercita cada forma de import da régua (requisito 3):
`import x`, `from . import a, b`, `from .a import X`, `from ..ports import X` (dois níveis),
`from helpdesk import x`, `from helpdesk.a import X`, além de bibliotecas externas,
duplicatas, auto-import e `__init__.py`, que não contam.
"""
import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import coupling  # noqa: E402

FILES = {
    "__init__.py": "from . import config\n",            # __init__ não é componente
    "config.py": "import os\nURL = os.environ.get('X')\n",
    "schemas.py": (
        "from pydantic import BaseModel\n"
        "class A(BaseModel): ...\n"
        "class B(BaseModel): ...\n"
    ),
    "ports.py": (
        "import abc\n"
        "from abc import ABC\n"
        "import typing\n"
        "from typing import Protocol, TypeVar\n"
        "T = TypeVar('T')\n"
        "class Gateway(Protocol): ...\n"
        "class Source(typing.Protocol): ...\n"
        "class Base(ABC): ...\n"
        "class Other(abc.ABC): ...\n"
        "class Generic(Protocol[T]): ...\n"
        "class Concrete: ...\n"
    ),
    "adapters/__init__.py": "",
    "adapters/gateway.py": (
        "import httpx\n"
        "from ..ports import Gateway\n"                    # relativo de dois níveis
        "from .. import config\n"
        "from ..schemas import A, B\n"
        "from ..schemas import A as A2\n"                  # duplicata: conta uma vez
        "class HttpGateway(Gateway): ...\n"                # herda de Protocol do projeto: concreta
    ),
    "adapters/deep/__init__.py": "",
    "adapters/deep/x.py": "from ...ports import Source\nfrom ..gateway import HttpGateway\n",
    "features.py": (
        "from . import config, schemas\n"                  # from . import a, b
        "from .ports import Gateway\n"                      # from .a import X
        "from . import features\n"                          # auto-import não conta
        "import json\n"
    ),
    "main.py": (
        "import helpdesk.features\n"                        # import absoluto
        "from helpdesk import config\n"                     # from pacote import módulo
        "from helpdesk.adapters.gateway import HttpGateway\n"
        "from helpdesk.adapters import gateway\n"           # mesmo componente: conta uma vez
        "from fastapi import FastAPI\n"
        "def build():\n"
        "    from .schemas import A\n"                      # import dentro de função conta
        "    return A\n"
    ),
    "orphan.py": "VALUE = 1\n",
}


@pytest.fixture
def package(tmp_path: Path) -> Path:
    root = tmp_path / "helpdesk"
    for name, source in FILES.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return root


@pytest.fixture
def rows(package):
    return {r.component: r for r in coupling.measure(package)}


def test_componentes_sao_modulos_com_caminho_pontuado_sem_init(rows):
    assert sorted(rows) == [
        "adapters.deep.x", "adapters.gateway", "config", "features",
        "main", "orphan", "ports", "schemas",
    ]


def test_dependencias_resolvidas_por_todas_as_formas_de_import(package):
    deps = coupling.dependencies(package)
    assert deps["features"] == {"config", "schemas", "ports"}
    assert deps["adapters.gateway"] == {"ports", "config", "schemas"}
    assert deps["adapters.deep.x"] == {"ports", "adapters.gateway"}
    assert deps["main"] == {"features", "config", "adapters.gateway", "schemas"}
    assert deps["config"] == set()
    assert deps["orphan"] == set()


def test_ca_ce_contam_componentes_distintos(rows):
    assert (rows["config"].ca, rows["config"].ce) == (3, 0)
    assert (rows["ports"].ca, rows["ports"].ce) == (3, 0)
    assert (rows["schemas"].ca, rows["schemas"].ce) == (3, 0)
    assert (rows["adapters.gateway"].ca, rows["adapters.gateway"].ce) == (2, 3)
    assert (rows["main"].ca, rows["main"].ce) == (0, 4)


def test_instabilidade_e_zero_sem_dependencias(rows):
    assert rows["orphan"].i == 0
    assert rows["config"].i == 0
    assert rows["main"].i == 1
    assert rows["adapters.gateway"].i == 0.6


def test_abstracao_conta_protocol_e_abc_nas_quatro_grafias(rows):
    # 5 abstratas (Protocol, typing.Protocol, ABC, abc.ABC, Protocol[T]) de 6 classes
    assert rows["ports"].a == round(5 / 6, 2)
    assert rows["schemas"].a == 0           # BaseModel não é abstrata
    assert rows["adapters.gateway"].a == 0  # herdar de um Protocol do projeto não torna abstrata
    assert rows["config"].a == 0            # sem classes


def test_distancia_e_arredondamento(rows):
    assert rows["ports"].d == round(abs(5 / 6 + 0 - 1), 2)
    assert rows["config"].d == 1
    assert rows["main"].d == 0
    assert rows["adapters.gateway"].d == 0.4


def test_import_relativo_acima_da_raiz_e_ignorado(tmp_path):
    root = tmp_path / "helpdesk"
    root.mkdir()
    (root / "a.py").write_text("from .. import fora\nfrom ...x import y\n")
    assert coupling.dependencies(root) == {"a": set()}


def test_cli_grava_csv_com_cabecalho_exato_e_png(package, tmp_path):
    out = tmp_path / "results"
    coupling.main([str(package), "--name", "exemplo", "--out-dir", str(out)])
    with (out / "exemplo.csv").open(newline="") as f:
        lines = list(csv.reader(f))
    assert lines[0] == ["component", "ca", "ce", "i", "a", "d"]
    assert [l[0] for l in lines[1:]] == sorted(l[0] for l in lines[1:])
    assert ["config", "3", "0", "0", "0", "1"] in lines
    assert ["ports", "3", "0", "0", "0.83", "0.17"] in lines
    png = (out / "exemplo.png").read_bytes()
    assert png.startswith(b"\x89PNG")
