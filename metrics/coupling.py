#!/usr/bin/env python3
"""Acoplamento por componente (Ca, Ce, I, A, D) e gráfico A x I com a Main Sequence.

Uso:
    python metrics/coupling.py <caminho/para/app/helpdesk> --name <nome> [--out-dir metrics/results]

Grava <out-dir>/<nome>.csv (cabeçalho `component,ca,ce,i,a,d`) e <out-dir>/<nome>.png.

Régua (requisito 3 do desafio, fixa para que as medições sejam comparáveis):
- componente: cada módulo .py sob o caminho, inclusive subpastas, exceto __init__.py,
  identificado pelo caminho com pontos relativo à raiz (ex.: `adapters.gateway`);
- dependência: import relativo ou absoluto (`<pacote raiz>.x`) que resolve para OUTRO componente,
  contado uma vez por par; bibliotecas externas e da biblioteca padrão não contam.
  Para `from M import n`, se `M.n` é componente a dependência é `M.n`; senão, é `M`.
  Imports em qualquer lugar do módulo contam (inclusive dentro de funções);
- Ca = quantos componentes importam este; Ce = quantos este importa;
- I = Ce / (Ce + Ca), ou 0 quando ambos são zero;
- A = classes que herdam diretamente de Protocol/typing.Protocol/ABC/abc.ABC (inclusive
  `Protocol[T]`) ÷ total de classes definidas no módulo (inclusive aninhadas), ou 0 sem classes;
- D = |A + I - 1|; valores arredondados para 2 casas.
"""
import argparse
import ast
import csv
from dataclasses import dataclass
from pathlib import Path

HEADER = ["component", "ca", "ce", "i", "a", "d"]
ABSTRACT_NAMES = {"Protocol", "ABC"}
ABSTRACT_ATTRS = {("typing", "Protocol"), ("abc", "ABC")}


@dataclass(frozen=True)
class Row:
    component: str
    ca: int
    ce: int
    i: float
    a: float
    d: float


def components(root: Path) -> dict[str, Path]:
    """Nome pontuado -> arquivo, para cada .py sob root (exceto __init__.py)."""
    found = {}
    for path in sorted(root.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        found[".".join(path.relative_to(root).with_suffix("").parts)] = path
    return found


def _package_parts(component: str) -> list[str]:
    return component.split(".")[:-1]


def _resolve_from(component: str, node: ast.ImportFrom, root_name: str) -> list[str] | None:
    """Base pontuada (relativa à raiz) do `from ... import`, ou None se não é do projeto."""
    if node.level:
        package = _package_parts(component)
        up = node.level - 1
        if up > len(package):
            return None  # sobe acima da raiz do pacote medido
        base = package[: len(package) - up]
        return base + (node.module.split(".") if node.module else [])
    if node.module and node.module.split(".")[0] == root_name:
        return node.module.split(".")[1:]
    return None


def _module_dependencies(component: str, tree: ast.AST, known: set[str], root_name: str) -> set[str]:
    deps = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == root_name and ".".join(parts[1:]) in known:
                    deps.add(".".join(parts[1:]))
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_from(component, node, root_name)
            if base is None:
                continue
            for alias in node.names:
                submodule = ".".join(base + [alias.name])
                if submodule in known:
                    deps.add(submodule)
                elif ".".join(base) in known:
                    deps.add(".".join(base))
    deps.discard(component)
    return deps


def dependencies(root: Path) -> dict[str, set[str]]:
    """Componente -> conjunto de componentes que ele importa (Ce como conjunto)."""
    root = Path(root)
    found = components(root)
    known = set(found)
    return {
        name: _module_dependencies(name, ast.parse(path.read_text(encoding="utf-8")), known, root.name)
        for name, path in found.items()
    }


def _is_abstract_base(base: ast.expr) -> bool:
    if isinstance(base, ast.Subscript):  # Protocol[T]
        base = base.value
    if isinstance(base, ast.Name):
        return base.id in ABSTRACT_NAMES
    if isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name):
        return (base.value.id, base.attr) in ABSTRACT_ATTRS
    return False


def abstractness(tree: ast.AST) -> float:
    classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    if not classes:
        return 0.0
    abstract = [c for c in classes if any(_is_abstract_base(b) for b in c.bases)]
    return len(abstract) / len(classes)


def measure(root: Path) -> list[Row]:
    root = Path(root)
    found = components(root)
    deps = dependencies(root)
    rows = []
    for name in sorted(found):
        ce = len(deps[name])
        ca = sum(1 for other, targets in deps.items() if other != name and name in targets)
        i = ce / (ce + ca) if ce + ca else 0.0
        a = abstractness(ast.parse(found[name].read_text(encoding="utf-8")))
        rows.append(Row(name, ca, ce, round(i, 2), round(a, 2), round(abs(a + i - 1), 2)))
    return rows


def _fmt(value: float) -> str:
    return format(value, "g")  # 0 -> "0", 1.0 -> "1", 0.75 -> "0.75"


def write_csv(rows: list[Row], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(HEADER)
        for r in rows:
            writer.writerow([r.component, r.ca, r.ce, _fmt(r.i), _fmt(r.a), _fmt(r.d)])


def plot(rows: list[Row], path: Path, title: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7.5, 7.5))
    ax.fill_between([0, 0.5], 0, 0.5, color="tab:red", alpha=0.12, label="zona de dor (estável e concreto)")
    ax.fill_between([0.5, 1], 0.5, 1, color="tab:orange", alpha=0.12, label="zona de inutilidade (instável e abstrato)")
    ax.plot([0, 1], [1, 0], color="tab:green", linewidth=1.5, label="Main Sequence (A + I = 1)")

    # Componentes no mesmo ponto recebem um único rótulo.
    groups: dict[tuple[float, float], list[str]] = {}
    for r in rows:
        groups.setdefault((r.i, r.a), []).append(r.component)
    # Rótulos de pontos vizinhos (mesma altura, I a menos de 0,25) são empilhados para não
    # se sobreporem; o deslocado ganha uma linha até o ponto.
    placed: list[tuple[float, float, float]] = []  # (i, a, topo do rótulo em pontos)
    for (i, a), names in sorted(groups.items()):
        ax.scatter(i, a, s=60, color="tab:blue", zorder=3)
        near = [top for (pi, pa, top) in placed if abs(pi - i) < 0.25 and abs(pa - a) < 0.05]
        y_off = max(near) + 6 if near else 6
        ax.annotate("\n".join(names), (i, a), xytext=(6, y_off), textcoords="offset points", fontsize=8,
                    va="bottom", ha="right" if i > 0.85 else "left",
                    arrowprops={"arrowstyle": "-", "color": "gray", "lw": 0.6} if near else None)
        placed.append((i, a, y_off + 11 * len(names)))

    ax.set(xlim=(-0.05, 1.05), ylim=(-0.05, 1.05), xlabel="Instabilidade (I)", ylabel="Abstração (A)",
           title=f"A × I — {title}")
    ax.set_aspect("equal")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path, help="pasta do pacote a medir (ex.: app/helpdesk)")
    parser.add_argument("--name", required=True, help="nome da medição (ex.: v1-coupled)")
    parser.add_argument("--out-dir", type=Path, default=Path(__file__).resolve().parent / "results")
    args = parser.parse_args(argv)

    if not args.path.is_dir():
        parser.error(f"{args.path} não é uma pasta")
    rows = measure(args.path)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(rows, args.out_dir / f"{args.name}.csv")
    plot(rows, args.out_dir / f"{args.name}.png", args.name)

    print(",".join(HEADER))
    for r in rows:
        print(",".join([r.component, str(r.ca), str(r.ce), _fmt(r.i), _fmt(r.a), _fmt(r.d)]))
    print(f"-> {args.out_dir / (args.name + '.csv')} e .png")


if __name__ == "__main__":
    main()
