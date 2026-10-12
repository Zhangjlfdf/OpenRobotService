"""部署链路 schema 同步引擎（backend/scripts/schema_sync.py）的纯逻辑测试。

覆盖重点：
  * 列分类（AUTOMATIC / MANUAL）——决定「哪些能自动补」的安全边界
  * 期望结构生成：MySQL 方言下的 DDL 编译（类型 / NULL / DEFAULT / COMMENT）
  * 结构比对：缺列 / 缺索引 / 表不存在跳过 / 类型不一致只告警 / 库多余列只统计
  * 执行动作：只产出 ADD COLUMN 与 CREATE INDEX，且不碰 MANUAL 项（绝不 DROP / MODIFY）

说明：本用例不依赖真实库，也不调用 load_metadata——测试基建（conftest）为了不连库
把 `app.core.database` 桩成了 MagicMock，在那种环境下导入全部模型会失真；而真实
导入链已在部署目标机上实测通过（54 表 / 617 列）。这里用自建 metadata 覆盖逻辑。
"""
import importlib.util
import os
import sys

import pytest
from sqlalchemy import Column, Integer, String, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import declarative_base

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SCRIPT = os.path.join(_BACKEND, "scripts", "schema_sync.py")


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sync = _load_module("ors_schema_sync_under_test", _SCRIPT)
DIALECT = mysql.dialect()


def _demo_metadata():
    """构造一张覆盖各分类的演示表。"""
    base = declarative_base()

    class Demo(base):
        __tablename__ = "demo"

        id = Column(Integer, primary_key=True, autoincrement=True)
        name = Column(String(64), nullable=True, comment="名称")
        counters = Column(Integer, nullable=False, server_default=text("0"))
        required = Column(String(32), nullable=False)  # NOT NULL 且无默认 → MANUAL

    return base.metadata


# ── 列分类：自动补列的边界 ──────────────────────────────────────────────
def test_classify_nullable_column_is_automatic():
    md = _demo_metadata()
    cols = sync.build_expected(md, DIALECT)["demo"]["columns"]
    assert cols["name"]["classification"] == "AUTOMATIC"


def test_classify_not_null_with_server_default_is_automatic():
    md = _demo_metadata()
    cols = sync.build_expected(md, DIALECT)["demo"]["columns"]
    assert cols["counters"]["classification"] == "AUTOMATIC"


def test_classify_not_null_without_default_is_manual():
    md = _demo_metadata()
    cols = sync.build_expected(md, DIALECT)["demo"]["columns"]
    assert cols["required"]["classification"] == "MANUAL"
    assert cols["required"]["reason"]  # 必须给出「为什么不能自动」


def test_classify_primary_key_is_manual():
    md = _demo_metadata()
    cols = sync.build_expected(md, DIALECT)["demo"]["columns"]
    assert cols["id"]["classification"] == "MANUAL"


# ── 期望结构的 DDL 编译 ─────────────────────────────────────────────────
def test_expected_ddl_keeps_type_nullability_and_comment():
    md = _demo_metadata()
    cols = sync.build_expected(md, DIALECT)["demo"]["columns"]
    assert "VARCHAR(64)" in cols["name"]["ddl"]
    assert "COMMENT" in cols["name"]["ddl"]
    assert "NOT NULL" in cols["counters"]["ddl"]
    assert "DEFAULT" in cols["counters"]["ddl"]


def test_expected_ddl_excludes_column_name_to_avoid_duplicate_in_alter():
    """列片段不得再带列名：执行侧会自行拼反引号列名，片段里若也含列名就会生成
    重复列名的非法 SQL（ADD COLUMN name name ...）——实测 CreateColumn 输出正是以列名开头。
    """
    md = _demo_metadata()
    cols = sync.build_expected(md, DIALECT)["demo"]["columns"]
    assert not cols["name"]["ddl"].startswith("name")
    assert not cols["counters"]["ddl"].startswith("counters")
    assert cols["counters"]["ddl"].startswith("INTEGER")


def test_expected_lists_indexes():
    md = _demo_metadata()
    # 单独声明索引，避免依赖模型里的 Index 定义
    from sqlalchemy import Index
    Index("ix_demo_name", md.tables["demo"].c.name)
    md2 = sync.build_expected(md, DIALECT)
    assert md2["demo"]["indexes"]["ix_demo_name"]["columns"] == ["name"]
    assert md2["demo"]["indexes"]["ix_demo_name"]["classification"] == "AUTOMATIC"


# ── 结构比对 ────────────────────────────────────────────────────────────
def _expected_demo():
    return sync.build_expected(_demo_metadata(), DIALECT)


def test_diff_reports_missing_column_with_classification():
    actual = {"demo": {"columns": {"id": {"type": "bigint", "nullable": False}},
                       "indexes": {}}}
    result = sync.diff_structure(_expected_demo(), actual)
    missing = {c["column"]: c["classification"] for c in result["missing_columns"]}
    assert missing["name"] == "AUTOMATIC"
    assert missing["required"] == "MANUAL"
    assert "id" not in missing  # 已存在


def test_diff_skips_table_that_does_not_exist():
    """表还没建：启动时 create_all 会整表新建，不当缺失处理。"""
    result = sync.diff_structure(_expected_demo(), {})
    assert result["missing_columns"] == []
    assert result["missing_indexes"] == []


def test_diff_reports_missing_index():
    actual = {"demo": {"columns": {
        "id": {"type": "bigint", "nullable": False},
        "name": {"type": "varchar(64)", "nullable": True},
        "counters": {"type": "int", "nullable": False},
        "required": {"type": "varchar(32)", "nullable": False},
    }, "indexes": {}}}
    md = _demo_metadata()
    from sqlalchemy import Index
    Index("ix_demo_name", md.tables["demo"].c.name)
    result = sync.diff_structure(sync.build_expected(md, DIALECT), actual)
    assert [i["index"] for i in result["missing_indexes"]] == ["ix_demo_name"]


def test_diff_type_mismatch_is_warn_only():
    """类型/长度不一致只告警，绝不自动改列。"""
    actual = {"demo": {"columns": {
        "id": {"type": "bigint", "nullable": False},
        "name": {"type": "varchar(128)", "nullable": True},
        "counters": {"type": "int", "nullable": False},
        "required": {"type": "varchar(32)", "nullable": False},
    }, "indexes": {}}}
    result = sync.diff_structure(_expected_demo(), actual)
    assert any(m["column"] == "name" for m in result["type_mismatch"])
    assert all(c["column"] != "name" for c in result["missing_columns"])


def test_normalize_type_folds_equivalent_spellings():
    """MySQL 的同义写法（INTEGER/int、BOOL/tinyint(1)、INT/INT(11)）不得误报。

    实测背景：首次在 test 环境跑只读校验时，几十条 type_mismatch 全来自这类同义
    写法，真正的差异信号被淹没。
    """
    assert sync.normalize_type("INTEGER") == sync.normalize_type("int")
    assert sync.normalize_type("BOOL") == sync.normalize_type("tinyint(1)")
    assert sync.normalize_type("BIGINT") == sync.normalize_type("bigint(20)")


def test_normalize_type_keeps_real_differences():
    """归一只针对写法差异，真实类型/长度差异必须保留告警。"""
    assert sync.normalize_type("BIGINT") != sync.normalize_type("int")
    assert sync.normalize_type("VARCHAR(64)") != sync.normalize_type("VARCHAR(100)")
    assert sync.normalize_type("ENUM('A','B')") != sync.normalize_type("ENUM('A','B','C')")


def test_diff_reports_extra_columns_without_removal_action():
    """库多出来的列只统计，不产生任何删除动作。"""
    actual = {"demo": {"columns": {
        "id": {"type": "bigint", "nullable": False},
        "name": {"type": "varchar(64)", "nullable": True},
        "counters": {"type": "int", "nullable": False},
        "required": {"type": "varchar(32)", "nullable": False},
        "legacy_col": {"type": "varchar(8)", "nullable": True},
    }, "indexes": {}}}
    result = sync.diff_structure(_expected_demo(), actual)
    assert [c["column"] for c in result["extra_columns"]] == ["legacy_col"]


# ── 执行动作：只加不减 ──────────────────────────────────────────────────
class _FakeCursor:
    def __init__(self):
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append(sql)


def test_apply_only_adds_automatic_items_and_never_drops():
    result = {
        "missing_columns": [
            {"table": "demo", "column": "name", "ddl": "VARCHAR(64) NULL", "classification": "AUTOMATIC"},
            {"table": "demo", "column": "required", "ddl": "VARCHAR(32) NOT NULL", "classification": "MANUAL"},
        ],
        "missing_indexes": [{"table": "demo", "index": "ix_demo_name", "columns": ["name"], "unique": False}],
    }
    cur = _FakeCursor()
    applied = sync._apply(cur, result, dry_run=False)

    joined = " ".join(cur.statements).upper()
    assert applied == 2  # name + 索引；required 被跳过
    assert "ADD COLUMN `NAME`" in joined
    assert "CREATE INDEX" in joined
    assert "REQUIRED" not in joined
    assert "DROP" not in joined and "MODIFY" not in joined


def test_apply_dry_run_executes_nothing():
    result = {
        "missing_columns": [{"table": "demo", "column": "name", "ddl": "VARCHAR(64) NULL", "classification": "AUTOMATIC"}],
        "missing_indexes": [],
    }
    cur = _FakeCursor()
    assert sync._apply(cur, result, dry_run=True) == 0
    assert cur.statements == []


# ── 连接参数解析 ────────────────────────────────────────────────────────
def test_resolve_db_config_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://u:p@1.2.3.4:3307/mydb")
    cfg = sync.resolve_db_config(None)
    assert cfg == {"user": "u", "password": "p", "host": "1.2.3.4", "port": 3307, "database": "mydb"}


def test_resolve_db_config_from_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text('APP_ENV=production\nDATABASE_URL="mysql+pymysql://u:p@db:3306/helpdesk_724"\n',
                        encoding="utf-8")
    cfg = sync.resolve_db_config(str(env_file))
    assert cfg["database"] == "helpdesk_724"


def test_resolve_db_config_requires_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit):
        sync.resolve_db_config(None)


def test_resolve_db_config_rejects_unparsable_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///tmp/x.db")
    with pytest.raises(SystemExit):
        sync.resolve_db_config(None)
