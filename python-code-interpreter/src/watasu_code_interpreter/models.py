from __future__ import annotations

import json as jsonlib
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional, TypeVar, Union

from .charts import ChartTypes, _deserialize_chart

T = TypeVar("T")
OutputHandler = Callable[[T], Any]
RunCodeLanguage = Union[
    Literal["python", "javascript", "typescript", "r", "java", "bash"],
    str,
]


class MIMEType(str):
    """MIME type marker used by code execution results."""


@dataclass
class OutputMessage:
    """One stdout or stderr line emitted by code execution."""

    line: str
    timestamp: float = field(default_factory=time.time)
    error: bool = False

    def __str__(self) -> str:
        return self.line

    def to_json(self) -> str:
        return jsonlib.dumps({
            "line": self.line,
            "timestamp": self.timestamp,
            "error": self.error,
        })


@dataclass
class Logs:
    """Captured stdout and stderr output for an execution."""

    stdout: List[OutputMessage] = field(default_factory=list)
    stderr: List[OutputMessage] = field(default_factory=list)

    def to_json(self) -> str:
        return jsonlib.dumps({
            "stdout": [_message_line(message) for message in self.stdout],
            "stderr": [_message_line(message) for message in self.stderr],
        })


@dataclass
class ExecutionError:
    """Structured exception raised by user code inside the sandbox."""

    name: str
    value: str
    traceback: str

    def to_json(self) -> str:
        return jsonlib.dumps({
            "name": self.name,
            "value": self.value,
            "traceback": self.traceback,
        })


@dataclass
class Result:
    """Rich result produced by the last expression of a code execution."""

    text: Optional[str] = None
    html: Optional[str] = None
    markdown: Optional[str] = None
    svg: Optional[str] = None
    png: Optional[str] = None
    jpeg: Optional[str] = None
    pdf: Optional[str] = None
    latex: Optional[str] = None
    json: Any = None
    javascript: Optional[str] = None
    data: Any = None
    chart: Optional[ChartTypes] = None
    extra: Dict[str, Any] = field(default_factory=dict)
    is_main_result: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.chart, dict):
            self.chart = _deserialize_chart(self.chart)

    def __getitem__(self, item):
        return getattr(self, item)

    def formats(self) -> List[str]:
        """Return available display formats for this result."""

        names = [
            "text",
            "html",
            "markdown",
            "svg",
            "png",
            "jpeg",
            "pdf",
            "latex",
            "json",
            "javascript",
            "data",
            "chart",
        ]
        formats = [name for name in names if getattr(self, name) is not None]
        if self.extra:
            formats.extend(self.extra)
        return formats

    def to_json(self) -> Dict[str, Any]:
        payload = {
            "text": self.text,
            "html": self.html,
            "markdown": self.markdown,
            "svg": self.svg,
            "png": self.png,
            "jpeg": self.jpeg,
            "pdf": self.pdf,
            "latex": self.latex,
            "json": self.json,
            "javascript": self.javascript,
            "data": self.data,
            "chart": self.chart,
            "extra": self.extra,
            "is_main_result": self.is_main_result,
        }
        return {key: value for key, value in payload.items() if value is not None}

    def __repr__(self) -> str:
        if self.text:
            return f"Result({self.text})"
        return "Result(Formats: " + ", ".join(self.formats()) + ")"

    def __str__(self) -> str:
        return self.__repr__()

    def _repr_html_(self) -> Optional[str]:
        return self.html

    def _repr_markdown_(self) -> Optional[str]:
        return self.markdown

    def _repr_svg_(self) -> Optional[str]:
        return self.svg

    def _repr_png_(self) -> Optional[str]:
        return self.png

    def _repr_jpeg_(self) -> Optional[str]:
        return self.jpeg

    def _repr_pdf_(self) -> Optional[str]:
        return self.pdf

    def _repr_latex_(self) -> Optional[str]:
        return self.latex


@dataclass
class Execution:
    """Complete result of a sandbox code execution."""

    results: List[Result] = field(default_factory=list)
    logs: Logs = field(default_factory=Logs)
    error: Optional[ExecutionError] = None
    execution_count: Optional[int] = None

    @property
    def text(self) -> Optional[str]:
        """Text for the main result, when code produced one."""

        for result in self.results:
            if result.is_main_result and result.text is not None:
                return result.text
        for result in self.results:
            if result.text is not None:
                return result.text
        return None

    def to_json(self) -> str:
        return jsonlib.dumps({
            "results": serialize_results(self.results),
            "logs": self.logs.to_json(),
            "error": self.error.to_json() if self.error else None,
        })


@dataclass(init=False)
class Context:
    """Code execution context metadata."""

    id: str
    language: Optional[str] = None
    cwd: Optional[str] = None

    def __init__(
        self,
        id: Optional[str] = None,
        language: Optional[str] = None,
        cwd: Optional[str] = None,
        context_id: Optional[str] = None,
        **_: Any,
    ) -> None:
        self.id = str(context_id if context_id is not None else id)
        self.language = language
        self.cwd = cwd

    def to_json(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "language": self.language,
            "cwd": self.cwd,
        }

    @classmethod
    def from_json(cls, data: Dict[str, str]):
        return cls(
            context_id=data.get("id"),
            language=data.get("language"),
            cwd=data.get("cwd"),
        )


def serialize_results(results: List[Result]) -> List[Dict[str, Any]]:
    serialized = []
    for result in results:
        item = {}
        for key in result.formats():
            if key == "chart" and result.chart is not None:
                item[key] = result.chart.to_dict()
            else:
                item[key] = result[key]
        item["text"] = result.text
        serialized.append(item)
    return serialized


def _message_line(message: Any) -> str:
    return str(message.line) if hasattr(message, "line") else str(message)


def execution_from_api(payload: Dict[str, Any]) -> Execution:
    execution = payload.get("execution") or payload
    logs = execution.get("logs") or {}
    return Execution(
        results=[result_from_api(item) for item in execution.get("results") or []],
        logs=Logs(
            stdout=[
                output_message_from_api(item, error=False)
                for item in logs.get("stdout") or []
            ],
            stderr=[
                output_message_from_api(item, error=True)
                for item in logs.get("stderr") or []
            ],
        ),
        error=error_from_api(execution.get("error")),
        execution_count=execution.get("execution_count"),
    )


def context_from_api(payload: Dict[str, Any]) -> Context:
    return Context(
        id=payload.get("id"),
        language=payload.get("language"),
        cwd=payload.get("cwd"),
    )


def result_from_api(payload: Dict[str, Any]) -> Result:
    known = {
        "text",
        "html",
        "markdown",
        "svg",
        "png",
        "jpeg",
        "pdf",
        "latex",
        "json",
        "javascript",
        "data",
        "chart",
        "extra",
        "is_main_result",
    }
    return Result(
        text=payload.get("text"),
        html=payload.get("html"),
        markdown=payload.get("markdown"),
        svg=payload.get("svg"),
        png=payload.get("png"),
        jpeg=payload.get("jpeg"),
        pdf=payload.get("pdf"),
        latex=payload.get("latex"),
        json=payload.get("json"),
        javascript=payload.get("javascript"),
        data=payload.get("data"),
        chart=_deserialize_chart(payload.get("chart")),
        extra=payload.get("extra") or {
            key: value for key, value in payload.items() if key not in known
        },
        is_main_result=bool(payload.get("is_main_result")),
    )


def output_message_from_api(payload: Any, error: bool) -> OutputMessage:
    if isinstance(payload, dict):
        return OutputMessage(
            line=str(payload.get("line", "")),
            timestamp=float(payload.get("timestamp") or time.time()),
            error=bool(payload.get("error", error)),
        )
    return OutputMessage(line=str(payload), error=error)


def error_from_api(payload: Any) -> Optional[ExecutionError]:
    if not isinstance(payload, dict):
        return None
    return ExecutionError(
        name=str(payload.get("name") or ""),
        value=str(payload.get("value") or ""),
        traceback=str(payload.get("traceback") or ""),
    )
