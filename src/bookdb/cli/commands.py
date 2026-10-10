"""CLI 命令（PRD §21）。

P0：init / add / list / show / edit / delete / search
CLI 只负责参数解析、交互与呈现；业务逻辑一律走 services 层（PRD §17）。
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .. import __version__
from ..config import CONFIG_FILENAME, Config
from ..db import database
from ..paths import display as display_path
from ..services import books as book_service
from ..services import search as search_service
from ..services import setup as setup_service
from ..services.context import Context

app = typer.Typer(
    name="book",
    help="本地优先的个人图书元数据数据库工具",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)


# ══════════════════════════════════════════════════════════════════════ helpers
def _load_config(ctx: typer.Context) -> Config:
    """从全局 --config 或当前目录的 book.toml 加载配置。"""
    config_path = ctx.obj.get("config") if ctx.obj else None
    return Config.load(config_path)


def _open(ctx: typer.Context) -> Context:
    """加载配置并打开数据库；若库尚未初始化，给出明确提示。"""
    config = _load_config(ctx)
    if not database.database_exists(config.db_path):
        err_console.print(
            f"[red]数据库不存在：[/red]{config.db_path}\n"
            f"请先运行 [bold]book init[/bold] 初始化。"
        )
        raise typer.Exit(code=1)
    return Context.open(config)


def _render_table(rows: list[dict]) -> None:
    """列表呈现（不含 abstract —— §8.3）。"""
    if not rows:
        console.print("[yellow]没有匹配的记录。[/yellow]")
        return

    table = Table(show_lines=False, header_style="bold cyan")
    table.add_column("ID", justify="right", style="dim")
    table.add_column("书名", overflow="fold")
    table.add_column("作者")
    table.add_column("出版社")
    table.add_column("年份", justify="right")
    table.add_column("CLC")
    table.add_column("类型", justify="center")
    table.add_column("文件", style="dim")

    for r in rows:
        table.add_row(
            str(r["id"]),
            r["title"] or "",
            r["author"] or "",
            r["publisher_name"] or "",
            str(r["publication_year"] or ""),
            r["clc_code"] or "",
            r["literature_type"] or "",
            (r["file_path"] or ""),
        )
    console.print(table)
    console.print(f"[dim]共 {len(rows)} 条[/dim]")


def _render_detail(book: dict, library_root: Path | None = None) -> None:
    """详情呈现：字段缺失显示为 —，含 abstract。"""
    from ..normalize import format_edition

    def val(key: str, fmt=None) -> str:
        v = book.get(key)
        if v in (None, ""):
            return "[dim]—[/dim]"
        return str(fmt(v) if fmt else v)

    body = "\n".join(
        [
            f"[bold]书名[/bold]      {val('title')}",
            f"[bold]作者[/bold]      {val('author')}",
            f"[bold]译者[/bold]      {val('translator')}",
            f"[bold]出版社[/bold]    {val('publisher_name')}",
            f"[bold]出版地[/bold]    {val('publication_place')}",
            f"[bold]出版年[/bold]    {val('publication_year')}",
            f"[bold]版次[/bold]      {val('edition', format_edition)}",
            f"[bold]ISBN[/bold]      {val('isbn')}",
            f"[bold]CLC[/bold]       {val('clc_code')}",
            f"[bold]文献类型[/bold]  {val('literature_type')}",
            f"[bold]文件[/bold]      {display_path(book.get('file_path'), library_root) or '—'}",
            "",
            "[bold]摘要[/bold]",
            (book.get("abstract") or "[dim]—[/dim]"),
            "",
            f"[dim]创建 {book.get('created_at') or '—'} · 更新 {book.get('updated_at') or '—'}[/dim]",
        ]
    )
    console.print(Panel(body, title=f"图书 #{book['id']}", expand=False))


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"bookdb {__version__}")
        raise typer.Exit()


# ═════════════════════════════════════════════════════════════════════ commands
@app.callback()
def _main(
    ctx: typer.Context,
    config: Path = typer.Option(
        None, "--config", "-c", help="配置文件路径（默认使用当前目录的 book.toml）"
    ),
    version: bool = typer.Option(
        False, "--version", "-V", callback=_version_callback, is_eager=True,
        help="显示版本并退出",
    ),
) -> None:
    """bookdb —— 本地优先的个人图书元数据数据库工具。"""
    ctx.obj = {"config": config}


@app.command()
def init(
    ctx: typer.Context,
    force: bool = typer.Option(
        False, "--force",
        help="库已存在时重新执行初始化（幂等，绝不清空数据）；并允许重新生成 book.toml",
    ),
    library_root: Path = typer.Option(
        None, "--library-root", help="相对路径的根目录（写入 book.toml）"
    ),
) -> None:
    """初始化数据库与配置文件（PRD §7.1）。

    幂等：重复执行只做 CREATE TABLE IF NOT EXISTS，绝不 DROP / DELETE。
    book.toml 已存在时，普通 init 不覆盖；仅 --force 才重新生成（保留已有配置值）。
    """
    config = _load_config(ctx)
    if library_root is not None:
        config.library_root = library_root.resolve()

    try:
        result = setup_service.initialize(config, force=force)
    except setup_service.DatabaseExistsError as exc:
        err_console.print(
            f"[yellow]数据库已存在：[/yellow]{exc.db_path}\n"
            f"如需重新执行初始化，请加 [bold]--force[/bold]"
            f"[dim]（幂等，不会清空数据）[/dim]。"
        )
        raise typer.Exit(code=1)

    console.print(
        f"[green]✓[/green] 数据库已{'就绪' if result.db_existed else '初始化'}：{result.db_path}"
    )

    if result.config_written:
        console.print(
            f"[green]✓[/green] 配置已"
            f"{'重新生成' if result.config_existed else '写入'}：{result.config_path}"
        )
    else:
        console.print(
            f"[yellow]•[/yellow] 配置文件已存在，未做改动：{result.config_path}\n"
            f"[dim]  如需重新生成，请加 --force。[/dim]"
        )

    if result.library_root:
        console.print(f"[green]✓[/green] library_root：{result.library_root}")
    else:
        console.print(
            "[yellow]•[/yellow] 未配置 library_root：相对路径将无法解析。\n"
            "[dim]  可用 --library-root 指定后重跑 book init。[/dim]"
        )


@app.command()
def add(
    ctx: typer.Context,
    title: str = typer.Option(None, "--title", "-t", help="书名（必填）"),
    author: str = typer.Option(None, "--author", "-a", help="作者"),
    translator: str = typer.Option(None, "--translator", help="译者"),
    publisher: str = typer.Option(None, "--publisher", "-p", help="出版社（不存在自动创建）"),
    place: str = typer.Option(None, "--place", help="出版地"),
    year: str = typer.Option(None, "--year", "-y", help="出版年份"),
    edition: str = typer.Option(None, "--edition", "-e", help="版次（存纯数字，如 2）"),
    isbn: str = typer.Option(None, "--isbn", help="ISBN"),
    clc: str = typer.Option(None, "--clc", help="中图法分类号"),
    literature_type: str = typer.Option(None, "--type", help="文献类型（默认 M）"),
    abstract: str = typer.Option(None, "--abstract", help="摘要"),
    file: Path = typer.Option(None, "--file", "-f", help="本地电子书文件路径"),
    interactive: bool = typer.Option(False, "--interactive", "-i", help="交互式表单录入"),
    yes: bool = typer.Option(False, "--yes", help="发现重复时直接继续，不询问"),
) -> None:
    """新增一本图书（PRD §7.2）。"""
    with _open(ctx) as octx:
        payload = {
            "title": title,
            "author": author,
            "translator": translator,
            "publisher": publisher,
            "publication_place": place,
            "publication_year": year,
            "edition": edition,
            "isbn": isbn,
            "clc_code": clc,
            "literature_type": literature_type,
            "abstract": abstract,
            "file_path": str(file) if file else None,
        }

        if interactive:
            payload = _interactive_form(payload)

        if not payload.get("title"):
            err_console.print("[red]缺少必填项 --title（书名）。[/red]")
            raise typer.Exit(code=1)

        try:
            book_id = book_service.add_book(octx, payload)
        except book_service.DuplicateBookError as exc:
            err_console.print(
                f"[yellow]⚠ 发现 {len(exc.existing)} 条疑似重复记录：[/yellow]"
            )
            _render_table(exc.existing)
            if not yes and not typer.confirm("仍然继续添加？", default=False):
                console.print("[dim]已取消。[/dim]")
                raise typer.Exit(code=0)
            book_id = book_service.add_book(octx, payload, allow_duplicate=True)
        except ValueError as exc:
            err_console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)

        console.print(f"[green]✓[/green] 已添加：#{book_id} 《{payload['title']}》")


def _interactive_form(payload: dict) -> dict:
    """交互式补全缺失字段（已有值不再询问）。"""
    prompts = [
        ("title", "书名", True),
        ("author", "作者", False),
        ("translator", "译者", False),
        ("publisher", "出版社", False),
        ("publication_place", "出版地", False),
        ("publication_year", "出版年份", False),
        ("edition", "版次", False),
        ("isbn", "ISBN", False),
        ("clc_code", "CLC 分类号", False),
    ]
    for key, label, required in prompts:
        if payload.get(key):
            continue
        value = typer.prompt(f"{label}{'（必填）' if required else '（可空）'}", default="", show_default=False)
        if value:
            payload[key] = value
    return payload


@app.command(name="list")
def list_books(
    ctx: typer.Context,
    limit: int = typer.Option(None, "--limit", "-n", help="最多显示条数"),
) -> None:
    """列出全部图书（PRD §7.6）。"""
    with _open(ctx) as octx:
        _render_table(book_service.list_books(octx, limit=limit))


@app.command()
def show(ctx: typer.Context, book_id: int = typer.Argument(..., help="图书 id")) -> None:
    """显示单条图书的完整元数据（PRD §7.3）。"""
    with _open(ctx) as octx:
        try:
            book = book_service.get_book(octx, book_id)
        except book_service.BookNotFoundError as exc:
            err_console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)
        _render_detail(book, octx.config.library_root)


@app.command()
def edit(
    ctx: typer.Context,
    book_id: int = typer.Argument(..., help="图书 id"),
    title: str = typer.Option(None, "--title", "-t"),
    author: str = typer.Option(None, "--author", "-a"),
    translator: str = typer.Option(None, "--translator"),
    publisher: str = typer.Option(None, "--publisher", "-p"),
    place: str = typer.Option(None, "--place"),
    year: str = typer.Option(None, "--year", "-y"),
    edition: str = typer.Option(None, "--edition", "-e"),
    isbn: str = typer.Option(None, "--isbn"),
    clc: str = typer.Option(None, "--clc"),
    literature_type: str = typer.Option(None, "--type"),
    abstract: str = typer.Option(None, "--abstract"),
    file: Path = typer.Option(None, "--file", "-f"),
    interactive: bool = typer.Option(False, "--interactive", "-i", help="交互式逐字段编辑"),
) -> None:
    """编辑图书字段（只改传入的字段，PRD §7.4）。"""
    with _open(ctx) as octx:
        try:
            current = book_service.get_book(octx, book_id)
        except book_service.BookNotFoundError as exc:
            err_console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)

        fields: dict = {}
        supplied = {
            "title": title, "author": author, "translator": translator,
            "publisher": publisher, "publication_place": place,
            "publication_year": year, "edition": edition, "isbn": isbn,
            "clc_code": clc, "literature_type": literature_type,
            "abstract": abstract,
            "file_path": str(file) if file else None,
        }
        for key, value in supplied.items():
            if value is not None:
                fields[key] = value

        if interactive:
            fields = _interactive_edit(current, fields)

        if not fields:
            console.print("[dim]未提供任何字段，未做修改。[/dim]")
            raise typer.Exit(code=0)

        try:
            updated = book_service.update_book(octx, book_id, fields)
        except ValueError as exc:
            err_console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)

        console.print(f"[green]✓[/green] 已更新：#{book_id}")
        _render_detail(updated, octx.config.library_root)


def _interactive_edit(current: dict, fields: dict) -> dict:
    """交互式编辑：显示当前值，回车保留。"""
    editable = [
        ("title", "书名"), ("author", "作者"), ("translator", "译者"),
        ("publisher_name", "出版社"), ("publication_place", "出版地"),
        ("publication_year", "出版年份"), ("edition", "版次"),
        ("isbn", "ISBN"), ("clc_code", "CLC"), ("literature_type", "文献类型"),
        ("abstract", "摘要"),
    ]
    console.print("[dim]回车保留当前值；输入新值则覆盖。[/dim]")
    for key, label in editable:
        cur = current.get(key)
        value = typer.prompt(
            f"{label} [{cur if cur not in (None, '') else '空'}]",
            default="", show_default=False,
        )
        if value:
            out_key = "publisher" if key == "publisher_name" else key
            fields[out_key] = value
    return fields


@app.command()
def delete(
    ctx: typer.Context,
    book_id: int = typer.Argument(..., help="图书 id"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认"),
) -> None:
    """删除图书（仅数据库记录，不删除 PDF 文件 —— PRD §7.5 / §23.3）。"""
    with _open(ctx) as octx:
        try:
            book = book_service.get_book(octx, book_id)
        except book_service.BookNotFoundError as exc:
            err_console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)

        console.print(f"将删除：[bold]#{book_id}[/bold] 《{book['title']}》")
        console.print("[dim]（只删数据库记录，实际 PDF 文件保持不动）[/dim]")
        if not yes and not typer.confirm("确认删除？", default=False):
            console.print("[dim]已取消。[/dim]")
            raise typer.Exit(code=0)

        book_service.delete_book(octx, book_id)
        console.print(f"[green]✓[/green] 已删除 #{book_id}")


@app.command()
def search(
    ctx: typer.Context,
    keyword: str = typer.Argument(None, help="关键词（书名/作者/译者/出版社/ISBN/CLC/摘要）"),
    author: str = typer.Option(None, "--author", "-a"),
    year: str = typer.Option(None, "--year", "-y", help="年份：2019 | 1800-1900 | 1800-"),
    clc: str = typer.Option(None, "--clc", help="CLC 前缀"),
    literature_type: str = typer.Option(None, "--type"),
    publisher: str = typer.Option(None, "--publisher", "-p"),
    has_file: bool = typer.Option(None, "--has-file/--no-file", help="仅显示有/无文件的记录"),
    limit: int = typer.Option(None, "--limit", "-n"),
) -> None:
    """检索图书（PRD §8）。"""
    with _open(ctx) as octx:
        try:
            rows = search_service.search(
                octx,
                keyword,
                author=author,
                year=year,
                clc=clc,
                literature_type=literature_type,
                publisher=publisher,
                has_file=has_file,
                limit=limit,
            )
        except ValueError as exc:
            err_console.print(f"[red]参数错误：{exc}[/red]")
            raise typer.Exit(code=1)
        _render_table(rows)
