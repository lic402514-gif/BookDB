"""`book model` —— 内置本地视觉模型的下载 / 查看 / 删除（PRD §17）。

面向两类用户：
  · 普通用户：一般用 GUI 的「AI 模型」面板（有进度条、可取消）；
  · 高级用户 / 排错：命令行更直接，也能拿到完整错误信息。

下载逻辑全在 `services/model.py`（GUI 与 CLI 共用，不重复实现）。
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table

from ..services import model as model_service

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    name="model",
    help="管理内置的本地视觉模型（用于版权页元数据提取）。",
    no_args_is_help=True,
)


def _spec_or_exit(key: str) -> model_service.ModelSpec:
    try:
        return model_service.find(key)
    except model_service.ModelError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


def _source_label(st: model_service.ModelState) -> str:
    return {
        "local": "[green]已下载[/green]",
        "ollama": "[cyan]复用 Ollama[/cyan]",
        "none": "[dim]未安装[/dim]",
    }[st.source]


@app.command(name="list")
def list_models() -> None:
    """列出可选模型及其本机状态。"""
    from ..llm.adapters import llamacpp

    table = Table(title="本地视觉模型", show_lines=False, expand=False)
    table.add_column("KEY", style="bold", no_wrap=True)
    table.add_column("模型", no_wrap=True)
    table.add_column("大小", justify="right", no_wrap=True)
    table.add_column("状态", no_wrap=True)
    table.add_column("说明", style="dim")

    states = model_service.list_states()
    default = model_service.DEFAULT_KEY
    for st in states:
        key = f"{st.spec.key} *" if st.spec.key == default else st.spec.key
        table.add_row(
            key, st.spec.name, model_service.human_size(st.spec.total_size),
            _source_label(st), st.spec.note,
        )
    console.print(table)

    if not llamacpp.is_available():
        console.print(
            "\n[yellow]⚠ 未找到内置推理组件（llama-server）[/yellow]\n"
            "  从源码运行时请先执行： [bold]bash packaging/fetch_llama.sh[/bold]"
        )
    console.print("\n[dim]* = 默认使用；下载后无需 Ollama 即可提取元数据。[/dim]")


@app.command()
def status() -> None:
    """显示当前会实际使用哪个后端（内置 / Ollama / DeepSeek / 都不可用）。"""
    from ..config import Config
    from ..llm.adapters import llamacpp
    from ..services import llm as llm_service
    from ..llm.base import ModelUnavailableError

    cfg = Config.load()
    vlm = cfg.vlm

    console.print(f"配置后端：[bold]{vlm.backend}[/bold]　内置模型 key："
                  f"[bold]{vlm.builtin_model}[/bold]")
    console.print(f"llama-server：{'[green]已就位[/green]' if llamacpp.is_available() else '[dim]未安装[/dim]'}")
    console.print(f"Ollama 端点：{vlm.endpoint}　"
                  f"{'[green]可连通[/green]' if llm_service._ollama_reachable(vlm.endpoint) else '[dim]未运行[/dim]'}")

    key, source = llm_service.deepseek_key_info()
    key_state = {
        "env": "[green]已设置（环境变量，优先）[/green]",
        "file": "[green]已设置（GUI 加密保存）[/green]",
    }.get(source, "[dim]未配置[/dim]")
    console.print(f"DeepSeek API：{key_state}　模型 [bold]{vlm.deepseek_model}[/bold]　"
                  f"端点 {vlm.deepseek_endpoint}")

    try:
        adapter = llm_service.get_adapter(vlm)
    except ModelUnavailableError as exc:
        console.print(f"\n[yellow]当前状态：不可用[/yellow]\n{exc}")
        raise typer.Exit(code=1) from exc

    where = {
        "LlamaServerAdapter": "内置 llama.cpp（本地视觉）",
        "OllamaAdapter": "Ollama（本地视觉）",
        "DeepSeekAdapter": "DeepSeek 云端（deepseek-flash：文本 + 视觉）",
    }.get(type(adapter).__name__, type(adapter).__name__)
    console.print(f"\n[green]✓ 当前将使用：{where}[/green]")


@app.command()
def backend(
    name: str = typer.Argument(..., help="auto / builtin / ollama / deepseek"),
) -> None:
    """切换元数据提取后端（写回 book.toml 的 [vlm].backend）。"""
    from ..config import Config
    from ..llm.adapters.deepseek import ENV_API_KEY
    from ..services import llm as llm_service

    value = name.strip().lower()
    allowed = ("auto", "builtin", "ollama", "deepseek")
    if value not in allowed:
        err_console.print(f"[red]未知后端：{name}[/red]　可选：{' / '.join(allowed)}")
        raise typer.Exit(code=1)

    cfg = Config.load()
    if cfg.config_path is None:
        err_console.print(
            "[red]当前目录没有 book.toml。[/red]\n"
            "[dim]请先 `book init`，或切换到书库目录后再执行。[/dim]"
        )
        raise typer.Exit(code=1)

    cfg.vlm.backend = value
    path = cfg.save()

    console.print(f"[green]✓[/green] 后端已切换为 [bold]{value}[/bold]（{path}）")
    if value == "deepseek":
        key, source = llm_service.deepseek_key_info()
        if key:
            where = "环境变量" if source == "env" else "GUI 加密保存"
            console.print(
                f"[dim]Key 来源：{where}；有文本层只发文字，扫描件由 "
                f"[vlm] deepseek_vision 决定是否上传渲染图。[/dim]"
            )
        else:
            console.print(
                "[yellow]⚠ 还没配置 DeepSeek Key，切过去也无法提取。[/yellow]\n"
                "[dim]可在 GUI「AI 模型」面板点「设置 Key…」，"
                f'或设环境变量 {ENV_API_KEY}：[/dim]\n'
                '[dim]PowerShell：$env:DEEPSEEK_API_KEY = "sk-..."[/dim]'
            )


@app.command()
def pull(
    key: str = typer.Argument(
        model_service.DEFAULT_KEY,
        help="模型 key（见 `book model list`）",
    ),
    mirror: str = typer.Option(
        "", "--mirror",
        help=f"下载源（默认 {' → '.join(model_service.DEFAULT_MIRRORS)}）",
    ),
) -> None:
    """下载模型（支持断点续传；中断后重跑会接着下）。"""
    spec = _spec_or_exit(key)

    st = model_service.state(spec)
    if st.ready:
        console.print(f"[green]✓[/green] {spec.name} 已就绪"
                      f"（{_source_label(st)}），无需下载。")
        return

    mirrors = (mirror,) if mirror else model_service.DEFAULT_MIRRORS
    console.print(
        f"下载 [bold]{spec.name}[/bold]　共 "
        f"[bold]{model_service.human_size(spec.total_size)}[/bold]"
    )
    console.print(f"[dim]源：{' → '.join(mirrors)}[/dim]")
    console.print(f"[dim]目录：{model_service.models_dir()}[/dim]\n")

    columns = (
        TextColumn("[progress.description]{task.description}"),
        BarColumn(), DownloadColumn(), TransferSpeedColumn(), TimeRemainingColumn(),
    )
    try:
        with Progress(*columns, console=console) as progress:
            task = progress.add_task("下载中", total=spec.total_size)
            model_service.download(
                spec,
                mirrors=mirrors,
                on_progress=lambda got, _total: progress.update(task, completed=got),
            )
    except model_service.ModelError as exc:
        err_console.print(f"\n[red]下载失败[/red]\n{exc}")
        raise typer.Exit(code=1) from exc
    except KeyboardInterrupt:
        err_console.print("\n[yellow]已中断（下次 `book model pull` 会断点续传）[/yellow]")
        raise typer.Exit(code=130) from None

    console.print(f"\n[green]✓ 下载完成[/green]，现在可以直接使用版权页提取了。")


@app.command()
def remove(
    key: str = typer.Argument(..., help="模型 key（见 `book model list`）"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认"),
) -> None:
    """删除**本程序下载的**模型文件（不影响 Ollama 里的模型）。"""
    spec = _spec_or_exit(key)
    st = model_service.state(spec, prefer_ollama=False)
    if not st.ready:
        console.print(f"[dim]{spec.name} 本地没有下载文件，无需删除。[/dim]")
        return

    freed = st.downloaded
    if not yes and not typer.confirm(
        f"删除 {spec.name} 的本地文件（释放 {model_service.human_size(freed)}）？",
        default=False,
    ):
        console.print("[dim]已取消。[/dim]")
        return

    got = model_service.remove(spec)
    console.print(f"[green]✓[/green] 已释放 {model_service.human_size(got)}。")


# 模块加载时挂到主 app（见 cli/commands.py 的 add_typer）
__all__ = ["app"]
