from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union


@dataclass
class ProcessInfo:
    pid: Union[int, str]
    tag: Optional[str] = None
    cmd: Optional[str] = None
    args: List[str] = field(default_factory=list)
    envs: Dict[str, str] = field(default_factory=dict)
    cwd: Optional[str] = None
