from __future__ import annotations

import enum
from typing import Any, List, Optional, Tuple, Union


class ChartType(str, enum.Enum):
    """Supported chart kinds returned by code execution."""

    LINE = "line"
    SCATTER = "scatter"
    BAR = "bar"
    PIE = "pie"
    BOX_AND_WHISKER = "box_and_whisker"
    SUPERCHART = "superchart"
    UNKNOWN = "unknown"


class ScaleType(str, enum.Enum):
    """Supported chart axis scale kinds."""

    LINEAR = "linear"
    DATETIME = "datetime"
    CATEGORICAL = "categorical"
    LOG = "log"
    SYMLOG = "symlog"
    LOGIT = "logit"
    FUNCTION = "function"
    FUNCTIONLOG = "functionlog"
    ASINH = "asinh"
    UNKNOWN = "unknown"


class Chart:
    """Extracted chart data for custom visualizations."""

    type: ChartType
    title: str
    elements: List[Any]

    def __init__(self, **kwargs: Any) -> None:
        self._raw_data = dict(kwargs)
        self.type = _chart_type(kwargs.get("type"))
        self.title = str(kwargs.get("title") or "")
        self.elements = list(kwargs.get("elements") or [])

    def to_dict(self) -> dict:
        return self._raw_data


class Chart2D(Chart):
    x_label: Optional[str]
    y_label: Optional[str]
    x_unit: Optional[str]
    y_unit: Optional[str]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.x_label = kwargs.get("x_label")
        self.y_label = kwargs.get("y_label")
        self.x_unit = kwargs.get("x_unit")
        self.y_unit = kwargs.get("y_unit")


class PointData:
    label: str
    points: List[Tuple[Union[str, float], Union[str, float]]]

    def __init__(self, **kwargs: Any) -> None:
        self.label = str(kwargs.get("label") or "")
        self.points = [(x, y) for x, y in kwargs.get("points") or []]


class PointChart(Chart2D):
    x_ticks: List[Union[str, float]]
    x_tick_labels: List[str]
    x_scale: ScaleType
    y_ticks: List[Union[str, float]]
    y_tick_labels: List[str]
    y_scale: ScaleType
    elements: List[PointData]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.x_ticks = list(kwargs.get("x_ticks") or [])
        self.x_tick_labels = list(kwargs.get("x_tick_labels") or [])
        self.x_scale = _scale_type(kwargs.get("x_scale"))
        self.y_ticks = list(kwargs.get("y_ticks") or [])
        self.y_tick_labels = list(kwargs.get("y_tick_labels") or [])
        self.y_scale = _scale_type(kwargs.get("y_scale"))
        self.elements = [PointData(**item) for item in kwargs.get("elements") or []]


class LineChart(PointChart):
    type = ChartType.LINE


class ScatterChart(PointChart):
    type = ChartType.SCATTER


class BarData:
    label: str
    group: str
    value: str

    def __init__(self, **kwargs: Any) -> None:
        self.label = str(kwargs.get("label") or "")
        self.value = str(kwargs.get("value") or "")
        self.group = str(kwargs.get("group") or "")


class BarChart(Chart2D):
    type = ChartType.BAR
    elements: List[BarData]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.elements = [BarData(**item) for item in kwargs.get("elements") or []]


class PieData:
    label: str
    angle: float
    radius: float

    def __init__(self, **kwargs: Any) -> None:
        self.label = str(kwargs.get("label") or "")
        self.angle = float(kwargs.get("angle") or 0)
        self.radius = float(kwargs.get("radius") or 0)


class PieChart(Chart):
    type = ChartType.PIE
    elements: List[PieData]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.elements = [PieData(**item) for item in kwargs.get("elements") or []]


class BoxAndWhiskerData:
    label: str
    min: float
    first_quartile: float
    median: float
    third_quartile: float
    max: float
    outliers: List[float]

    def __init__(self, **kwargs: Any) -> None:
        self.label = str(kwargs.get("label") or "")
        self.min = float(kwargs.get("min") or 0)
        self.first_quartile = float(kwargs.get("first_quartile") or 0)
        self.median = float(kwargs.get("median") or 0)
        self.third_quartile = float(kwargs.get("third_quartile") or 0)
        self.max = float(kwargs.get("max") or 0)
        self.outliers = [float(item) for item in kwargs.get("outliers") or []]


class BoxAndWhiskerChart(Chart2D):
    type = ChartType.BOX_AND_WHISKER
    elements: List[BoxAndWhiskerData]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.elements = [
            BoxAndWhiskerData(**item) for item in kwargs.get("elements") or []
        ]


class SuperChart(Chart):
    type = ChartType.SUPERCHART
    elements: List[
        Union[LineChart, ScatterChart, BarChart, PieChart, BoxAndWhiskerChart, Chart]
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.elements = [
            _deserialize_chart(item) or Chart(**item)
            for item in kwargs.get("elements") or []
            if isinstance(item, dict)
        ]


ChartTypes = Union[
    LineChart, ScatterChart, BarChart, PieChart, BoxAndWhiskerChart, SuperChart
]


def _deserialize_chart(data: Optional[dict]) -> Optional[ChartTypes]:
    if not data:
        return None

    chart_type = _chart_type(data.get("type"))
    if chart_type == ChartType.LINE:
        return LineChart(**data)
    if chart_type == ChartType.SCATTER:
        return ScatterChart(**data)
    if chart_type == ChartType.BAR:
        return BarChart(**data)
    if chart_type == ChartType.PIE:
        return PieChart(**data)
    if chart_type == ChartType.BOX_AND_WHISKER:
        return BoxAndWhiskerChart(**data)
    if chart_type == ChartType.SUPERCHART:
        return SuperChart(**data)
    return Chart(**data)


def _chart_type(value: Any) -> ChartType:
    try:
        return ChartType(value)
    except ValueError:
        return ChartType.UNKNOWN


def _scale_type(value: Any) -> ScaleType:
    try:
        return ScaleType(value)
    except ValueError:
        return ScaleType.UNKNOWN
