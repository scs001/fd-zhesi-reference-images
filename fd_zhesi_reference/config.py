"""环境配置。参照系库是可重建缓存（design D3），v1 用 SQLite 单文件，私有化零依赖。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    db_path: str = os.environ.get("FD_ZHESI_REF_DB", "")
    # 外部通道代理（本地开发可空=直连；生产摄取走爬虫平台的另行通道）
    http_proxy: str = field(default_factory=lambda: os.environ.get("FD_ZHESI_REF_PROXY", ""))
    sep_base: str = "https://plato.stanford.edu"
    atlas_base: str = "https://philosophyatlas.com.br"
    wikidata_sparql: str = "https://query.wikidata.org/sparql"

    @classmethod
    def from_env(cls) -> "Settings":
        db = os.environ.get("FD_ZHESI_REF_DB", "")
        if not db:
            db = str(Path.home() / ".fd-zhesi" / "reference.db")
        return cls(db_path=db)

    def connect(self):
        """打开参照系库（sqlite3，WAL，调用方负责事务）。"""
        import sqlite3

        path = Path(self.db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn
