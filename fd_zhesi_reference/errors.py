"""骨架期诚实错误：指名 tracking task，绝不编造数据。

错误码对齐 spec 的失败口径：blocked / upstream / unreachable / not_indexed / not_wired。
"""

from __future__ import annotations


class HonestError(RuntimeError):
    """工具未接线时的诚实回报。message 必须含 tracking task 编号。"""

    def __init__(self, tool: str, tracking_task: str):
        super().__init__(
            f"{tool} 未接线：数据摄取尚未完成（tracking task {tracking_task}）。"
            "不返回编造数据；接线后本工具按 spec 契约返回带 provenance 的真实结果。"
        )
        self.tool = tool
        self.tracking_task = tracking_task
