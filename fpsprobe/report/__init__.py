"""JSON and Markdown report builders."""

from .json_report import build_json_report, write_json_report
from .markdown_report import build_markdown_report, write_markdown_report

__all__ = [
    "build_json_report",
    "build_markdown_report",
    "write_json_report",
    "write_markdown_report",
]
