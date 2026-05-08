#!/usr/bin/env python3
from __future__ import annotations

# NOTE: Simple menu/CLI to inspect and try shared_lib utilities.

import argparse
import importlib
import inspect
import json
import pkgutil
import re
import shutil
import textwrap
import tomllib
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import shared_lib
from shared_lib import check_path, find_latest_files, generate_uid, get_path_size, validate_input_file, validate_output_file

EXCLUDED_MODULES = {"old", "tests"}

_REPO_ROOT = Path(__file__).resolve().parent
_MODULES_DATA_PROCESSOR = _REPO_ROOT / "modules_data_processor"

_README_PURPOSE_HEADINGS: tuple[str, ...] = (
    "## what this is for",
    "## purpose",
    "## overview",
)


def discover_data_processor_modules() -> tuple[str, ...]:
    """Packaged CLIs under modules_data_processor/ (directory + pyproject.toml)."""
    if not _MODULES_DATA_PROCESSOR.is_dir():
        return ()
    names: list[str] = []
    for path in sorted(_MODULES_DATA_PROCESSOR.iterdir()):
        if not path.is_dir():
            continue
        if path.name.startswith(".") or path.name in EXCLUDED_MODULES:
            continue
        if (path / "pyproject.toml").is_file():
            names.append(path.name)
    return tuple(names)


def _pyproject_description(module_dir: Path) -> str | None:
    pyproject = module_dir / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    project = data.get("project")
    if not isinstance(project, dict):
        return None
    desc = project.get("description")
    return desc.strip() if isinstance(desc, str) and desc.strip() else None


def _readme_purpose_text(readme_text: str) -> str | None:
    """Return a single-line purpose from README (purpose section or lead paragraph)."""
    lines = readme_text.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].startswith("# "):
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1

    # Explicit purpose-style section anywhere before first top-level paragraph fallback
    for j in range(i, len(lines)):
        low = lines[j].strip().lower()
        if low in _README_PURPOSE_HEADINGS:
            body: list[str] = []
            j += 1
            while j < len(lines):
                raw = lines[j]
                stripped = raw.strip()
                if stripped.startswith("## ") or stripped == "---" or stripped == "***":
                    break
                body.append(raw)
                j += 1
            purpose = _collapse_markdown_paragraph(body)
            if purpose:
                return purpose
            break

    # Lead paragraph: from first content line until blank, '---', or '##'
    body = []
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            break
        if stripped.startswith("## ") or stripped == "---" or stripped == "***":
            break
        body.append(lines[i])
        i += 1
    purpose = _collapse_markdown_paragraph(body)
    return purpose or None


def _collapse_markdown_paragraph(lines: list[str]) -> str:
    text = "\n".join(lines).strip()
    if not text:
        return ""
    first_block = text.split("\n\n", maxsplit=1)[0]
    one_line = " ".join(first_block.split())
    return re.sub(r"\*\*(.+?)\*\*", r"\1", one_line)


def data_processor_module_purpose(module_name: str) -> str:
    module_dir = _MODULES_DATA_PROCESSOR / module_name
    readme = module_dir / "README.md"
    if readme.is_file():
        try:
            raw = readme.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            raw = ""
        if raw:
            from_readme = _readme_purpose_text(raw)
            if from_readme:
                return from_readme
    return _pyproject_description(module_dir) or "No purpose text (README/pyproject)."


def discover_modules() -> tuple[str, ...]:
    """Discover public shared_lib modules from the package directory."""
    names: list[str] = []
    for item in pkgutil.iter_modules(shared_lib.__path__):
        if item.ispkg or item.name.startswith("_") or item.name in EXCLUDED_MODULES:
            continue
        names.append(item.name)
    return tuple(sorted(names))


def normalize_module_name(name: str) -> str:
    return name.removeprefix("shared_lib.").strip()


def load_module(name: str) -> ModuleType:
    short_name = normalize_module_name(name)
    if short_name not in discover_modules():
        raise ValueError(f"Unknown module: {name}")
    return importlib.import_module(f"shared_lib.{short_name}")


def public_functions(module: ModuleType) -> list[tuple[str, Callable[..., Any]]]:
    functions: list[tuple[str, Callable[..., Any]]] = []
    public_names = getattr(module, "__all__", None)
    if public_names is not None:
        candidates = ((name, getattr(module, name, None)) for name in public_names)
    else:
        candidates = inspect.getmembers(module, inspect.isfunction)

    for name, obj in candidates:
        if not inspect.isfunction(obj):
            continue
        if name.startswith("_"):
            continue
        if public_names is not None or getattr(obj, "__module__", "") == module.__name__:
            functions.append((name, obj))
    return functions


def module_note(module: ModuleType) -> str:
    """Return the module docstring or first top-level NOTE comment."""
    doc = inspect.getdoc(module)
    if doc:
        return doc.splitlines()[0]

    source = inspect.getsource(module)
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("# NOTE:"):
            return stripped.removeprefix("# NOTE:").strip()
    return "No module note."


def function_summary(func: Callable[..., Any]) -> str:
    """Return the first line of a function docstring."""
    doc = inspect.getdoc(func)
    if not doc:
        return "No function comment."
    return doc.splitlines()[0]


def clean_signature(func: Callable[..., Any]) -> str:
    """Return a compact signature without annotation quotes."""
    return str(inspect.signature(func)).replace("'", "")


def terminal_width(default: int = 100) -> int:
    return shutil.get_terminal_size((default, 24)).columns


def wrap_line(text: str, *, initial: str, subsequent: str, width: int | None = None) -> str:
    return textwrap.fill(
        text,
        width=width or terminal_width(),
        initial_indent=initial,
        subsequent_indent=subsequent,
        break_long_words=False,
        break_on_hyphens=False,
    )


def print_result(value: Any) -> None:
    if hasattr(value, "to_dict"):
        print(json.dumps(value.to_dict(), indent=2, default=str))
        return
    print(value)


def list_modules() -> None:
    modules = discover_modules()
    width = max((len(name) for name in modules), default=0)

    print()
    print("Shared Library Modules")
    print("======================")
    print()

    for index, name in enumerate(modules, start=1):
        try:
            module = load_module(name)
        except ImportError as exc:
            # e.g. shared_lib.cli_typer requires typer; keep listing without that env.
            detail = str(exc).strip() or exc.__class__.__name__
            print(f"{index:>2}. {name:<{width}}  (skipped: {detail})")
            continue
        print(f"{index:>2}. {name:<{width}}  {module_note(module)}")

    print()
    print("Tip: python3 shared_lib_explorer.py module <module_name>")


def list_data_processor_modules() -> None:
    modules = discover_data_processor_modules()
    if not modules:
        print()
        print("modules_data_processor")
        print("===================")
        print()
        print("No packaged modules found (expected modules_data_processor/<name>/pyproject.toml).")
        print()
        return

    width = max((len(name) for name in modules), default=0)
    print()
    print("modules_data_processor (purpose)")
    print("================================")
    print()

    for index, name in enumerate(modules, start=1):
        purpose = data_processor_module_purpose(name)
        left = f"{index:>2}. {name:<{width}}  "
        print(wrap_line(purpose, initial=left, subsequent=" " * len(left)))

    print()
    print("Tip: python3 shared_lib_explorer.py data-processor-modules")


def list_exports() -> None:
    print()
    print("Shared Library Exports")
    print("======================")
    print()
    for name in shared_lib.__all__:
        print(name)


def list_functions(module_name: str) -> None:
    module = load_module(module_name)
    short_name = normalize_module_name(module_name)
    functions = public_functions(module)
    if not functions:
        print(f"No public functions found in shared_lib.{short_name}")
        return

    width = max((len(name) for name, _ in functions), default=0)
    for name, func in functions:
        signature = clean_signature(func)
        left = f"{name:<{width}}  "
        print(wrap_line(signature, initial=left, subsequent=" " * len(left)))
        summary_left = f"{'':<{width}}  "
        print(wrap_line(function_summary(func), initial=summary_left, subsequent=summary_left))
        print()


def show_module(module_name: str) -> None:
    module = load_module(module_name)
    short_name = normalize_module_name(module_name)
    functions = public_functions(module)

    print()
    print(f"shared_lib.{short_name}")
    print("=" * (len("shared_lib.") + len(short_name)))
    print(module_note(module))
    print()
    print("Functions")
    print("---------")
    if functions:
        list_functions(short_name)
    else:
        print("No public functions found.")
    print(f"Tip: python3 shared_lib_explorer.py show {short_name} <function_name>")


def show_function(module_name: str, function_name: str) -> None:
    module = load_module(module_name)
    short_name = normalize_module_name(module_name)
    func = getattr(module, function_name, None)
    if func is None or not inspect.isfunction(func):
        raise ValueError(f"Function not found: shared_lib.{short_name}.{function_name}")
    print()
    print(f"shared_lib.{short_name}.{function_name}")
    print("=" * (len("shared_lib.") + len(short_name) + len(function_name) + 1))
    print()
    print(wrap_line(f"Signature: {function_name}{clean_signature(func)}", initial="", subsequent="  "))
    print()
    print(inspect.getdoc(func) or "No docstring.")


def run_size(path: str) -> None:
    print_result(get_path_size(path))


def run_check(path: str, expected: str) -> None:
    print_result(check_path(path, expected=expected))  # type: ignore[arg-type]


def run_validate_input(path: str, formats: str | None) -> None:
    supported = set(_split_csv(formats)) if formats else None
    print_result(validate_input_file(path, supported_formats=supported))


def run_validate_output(path: str, formats: str | None) -> None:
    supported = set(_split_csv(formats)) if formats else None
    print_result(validate_output_file(path, supported_formats=supported))


def run_uid(kind: str, length: int | None) -> None:
    print_result(generate_uid(kind, length=length))


def run_latest(
    directory: str,
    *,
    top_n: int,
    pattern: str | None,
    sort_by: str,
    recursive: bool,
) -> None:
    paths = find_latest_files(
        directory,
        top_n=top_n,
        pattern=pattern,
        sort_by=sort_by,
        recursive=recursive,
    )
    for p in paths:
        print(str(p))


def _split_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def prompt_choice(max_choice: int) -> int | None:
    raw = input("Choose option: ").strip()
    if raw.lower() in {"q", "quit", "exit"}:
        return None
    try:
        choice = int(raw)
    except ValueError:
        print("Enter a number, or q to quit.")
        return -1
    if choice < 1 or choice > max_choice:
        print("Choice out of range.")
        return -1
    return choice


def interactive() -> None:
    while True:
        print()
        print("Shared Lib Explorer")
        print("1. List modules")
        print("2. List package exports")
        print("3. Show module functions")
        print("4. Show function details")
        print("5. Get file/folder size")
        print("6. Check path")
        print("7. Validate input file")
        print("8. Validate output file")
        print("9. Generate ID")
        print("10. List modules_data_processor modules (purpose)")
        print("11. Quit")

        choice = prompt_choice(11)
        if choice is None or choice == 11:
            return
        if choice == -1:
            continue

        try:
            if choice == 1:
                list_modules()
            elif choice == 2:
                list_exports()
            elif choice == 3:
                list_modules()
                module_name = input("Module name: ").strip()
                show_module(module_name)
            elif choice == 4:
                module_name = input("Module name: ").strip()
                function_name = input("Function name: ").strip()
                show_function(module_name, function_name)
            elif choice == 5:
                run_size(input("File/folder path: ").strip())
            elif choice == 6:
                path = input("Path: ").strip()
                expected = input("Expected [any/file/directory]: ").strip() or "any"
                run_check(path, expected)
            elif choice == 7:
                path = input("Input file path: ").strip()
                formats = input("Supported formats, comma-separated [optional]: ").strip() or None
                run_validate_input(path, formats)
            elif choice == 8:
                path = input("Output file path: ").strip()
                formats = input("Supported formats, comma-separated [optional]: ").strip() or None
                run_validate_output(path, formats)
            elif choice == 9:
                kind = input("ID type [nanoid/uuid]: ").strip() or "nanoid"
                raw_length = input("Length [optional]: ").strip()
                length = int(raw_length) if raw_length else None
                run_uid(kind, length)
            elif choice == 10:
                list_data_processor_modules()
        except Exception as exc:
            print(f"error: {exc}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect and try shared_lib utilities.")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("modules", help="List shared_lib modules.")
    subparsers.add_parser(
        "data-processor-modules",
        help="List modules_data_processor packages and their purpose (README or pyproject).",
    )
    subparsers.add_parser("exports", help="List shared_lib package exports.")

    module = subparsers.add_parser("module", help="Show a module note and its public functions.")
    module.add_argument("module")

    functions = subparsers.add_parser("functions", help="List public functions in a module.")
    functions.add_argument("module")

    show = subparsers.add_parser("show", help="Show one function signature and doc.")
    show.add_argument("module")
    show.add_argument("function")

    size = subparsers.add_parser("size", help="Get file or folder size.")
    size.add_argument("path")

    check = subparsers.add_parser("check", help="Check if a path exists.")
    check.add_argument("path")
    check.add_argument("--expected", choices=["any", "file", "directory"], default="any")

    validate_input = subparsers.add_parser("validate-input", help="Validate an input file.")
    validate_input.add_argument("path")
    validate_input.add_argument("--formats", help="Comma-separated supported formats.")

    validate_output = subparsers.add_parser("validate-output", help="Validate an output file.")
    validate_output.add_argument("path")
    validate_output.add_argument("--formats", help="Comma-separated supported formats.")

    uid = subparsers.add_parser("uid", help="Generate a nanoid or UUID.")
    uid.add_argument("--kind", default="nanoid", help="ID type: nanoid or uuid.")
    uid.add_argument("--length", type=int, help="Optional nanoid length. Not valid for UUID.")

    latest = subparsers.add_parser("latest", help="Print newest regular files in a directory (find_latest_files).")
    latest.add_argument("directory", help="Folder to scan.")
    latest.add_argument("--top-n", type=int, default=5, help="Max paths to print (default: 5).")
    latest.add_argument("--pattern", default=None, help="Glob filter, e.g. *.csv (optional).")
    latest.add_argument(
        "--sort-by",
        choices=["mtime", "ctime"],
        default="mtime",
        help="Sort key: mtime (data change) or ctime (platform-dependent).",
    )
    latest.add_argument(
        "--recursive",
        action="store_true",
        help="Search subdirectories (rglob).",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command is None:
            interactive()
        elif args.command == "modules":
            list_modules()
        elif args.command == "data-processor-modules":
            list_data_processor_modules()
        elif args.command == "exports":
            list_exports()
        elif args.command == "module":
            show_module(args.module)
        elif args.command == "functions":
            list_functions(args.module)
        elif args.command == "show":
            show_function(args.module, args.function)
        elif args.command == "size":
            run_size(args.path)
        elif args.command == "check":
            run_check(args.path, args.expected)
        elif args.command == "validate-input":
            run_validate_input(args.path, args.formats)
        elif args.command == "validate-output":
            run_validate_output(args.path, args.formats)
        elif args.command == "uid":
            run_uid(args.kind, args.length)
        elif args.command == "latest":
            run_latest(
                args.directory,
                top_n=args.top_n,
                pattern=args.pattern,
                sort_by=args.sort_by,
                recursive=args.recursive,
            )
        else:
            parser.error(f"unknown command: {args.command}")
    except Exception as exc:
        print(f"error: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
