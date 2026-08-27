"""Componentes visuais compartilhados pelos configuradores de skills Onmyōji."""
from __future__ import annotations

import os
import shutil
import sys


class Ui:
    enabled = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    reset = "\x1b[0m"; bold = "\x1b[1m"; violet = "\x1b[38;5;141m"; cyan = "\x1b[38;5;80m"; slate = "\x1b[38;5;245m"; green = "\x1b[38;5;78m"; red = "\x1b[38;5;203m"

    @classmethod
    def text(cls, value: str, *styles: str) -> str:
        return "".join(styles) + value + cls.reset if cls.enabled and styles else value


def unicode_supported() -> bool:
    try:
        "╭─╮│╰╯›".encode(sys.stdout.encoding or "utf-8")
        return True
    except UnicodeEncodeError:
        return False


def screen(skill: str, title: str, subtitle: str = "") -> None:
    width = max(56, min(shutil.get_terminal_size((78, 24)).columns, 96))
    if unicode_supported():
        left, line, right, side, bottom_left, bottom_right = "╭", "─", "╮", "│", "╰", "╯"
    else:
        left, line, right, side, bottom_left, bottom_right = "+", "-", "+", "|", "+", "+"
    print("\n" + Ui.text(left + line * (width - 2) + right, Ui.violet))
    print(side + Ui.text(f" {skill.upper()}  /  {title.upper()}".ljust(width - 2), Ui.bold, Ui.violet) + side)
    if subtitle:
        print(side + " " + Ui.text(subtitle[:width - 4].ljust(width - 4), Ui.slate) + " " + side)
    print(Ui.text(bottom_left + line * (width - 2) + bottom_right, Ui.violet))


def item(key: str, label: str, value: str = "") -> None:
    shortcut = Ui.text(f"{key:>4}", Ui.bold, Ui.cyan)
    print(f"  {shortcut}  {label:<30}" + (Ui.text(value, Ui.slate) if value else ""))


def prompt(label: str) -> str:
    marker = "›" if unicode_supported() else ">"
    return input(Ui.text(f"{marker} {label}", Ui.bold, Ui.violet))


def result(ok: bool, message: str) -> None:
    label, color = ("+ OK", Ui.green) if ok else ("! ERRO", Ui.red)
    print("\n    " + Ui.text(label, Ui.bold, color) + "  " + Ui.text(message, color))


def note(message: str) -> None:
    print("\n    " + Ui.text("i INFO", Ui.bold, Ui.slate) + "  " + Ui.text(message, Ui.slate))
