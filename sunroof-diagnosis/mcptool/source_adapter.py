"""三源取数适配器缝（阶段 D 批次 2, D2-a/D2-b）—— 取数协议 + 注册表 + HTTP 骨架。

D2-a 适配器缝:
  * 三源取数协议 (typing.Protocol, 结构化类型): MesSource/QmsSource/PlmSource 与
    mes_server/qms_server/plm_server 对各自 store 的消费面逐一同形 —— 换实现不改
    校验链 (schema→RBAC→ACL 全在 server 层, 本缝只决定「数据从哪来」);
  * 适配器注册表: resolve(source) 取当前实现; register() 进程内换装 (Protocol
    结构校验, 换装记入只追加 journal); reset_registry() 恢复默认合成实现;
  * 默认实现 Synthetic*Source: 逐方法委托冻结的 mes_store/qms_store/plm_store
    模块函数 —— 行为逐字节等价 (三源 store 指纹/合同互锁/既有基线零改动为证)。

D2-b HTTP 适配器骨架 (httpx, requirements 既有依赖):
  * 默认关闭: 注册表默认合成实现; 仅当 SUNROOF_SOURCE_MODE=http 且三源端点全部
    显式配置时 configure_from_env() 才换装 (缺任一端点 -> ValueError 快速失败,
    绝不静默回退合成夹具冒充真实源);
  * 配置驱动: 端点 SUNROOF_SOURCE_{MES,QMS,PLM}_URL / 超时 SUNROOF_SOURCE_TIMEOUT_S
    (缺省 2.0s) / 有限重试 SUNROOF_SOURCE_MAX_RETRIES (缺省 2, 上限 5);
  * 传输语义 (骨架): GET {base}/{key} -> 200 JSON 对象 | 404 -> None (诚实不存在) |
    5xx/连接失败/超时 -> 有限重试后 SourceUnavailableError | 其余 4xx/非 JSON ->
    立即 SourceUnavailableError —— 绝不编造、绝不回退合成数据;
  * 不可达下游口径: server 层捕获 SourceUnavailableError -> 沿用既有「源不可用
    结构化拒答」—— 用户面三因一致 not_available (P-1 零存在性泄漏), 受限审计
    source_unavailable:<源> 精细分型 (与 report_server 既有分型词表同值)。

诚实边界: HTTP 适配器是「缝」而非「集成」—— 真实 MES/QMS/PLM 接入需按其真实
API 契约另做载荷映射与行级过滤 (本骨架假定载荷与合成夹具同键结构, 行级变换镜像
store 语义, 零随机零时钟; discovery-brief Out-of-Scope 系统级禁止不因本缝解禁)。
"""
from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import httpx

from mcptool import mes_contract as MES_M
from mcptool import mes_store
from mcptool import plm_contract as PLM_M
from mcptool import plm_store
from mcptool import qms_contract as QMS_M
from mcptool import qms_store

# 封闭源枚举与既有 source_unavailable:<源> 分型词表 (与 report_server 同值)
SOURCES = ("mes", "qms", "plm")
SOURCE_UNAVAILABLE_PREFIX = "source_unavailable:"

# ---- 环境变量面 (默认关闭: 无 SUNROOF_SOURCE_MODE 时零操作) ----
MODE_ENV = "SUNROOF_SOURCE_MODE"
_URL_ENVS = {"mes": "SUNROOF_SOURCE_MES_URL",
             "qms": "SUNROOF_SOURCE_QMS_URL",
             "plm": "SUNROOF_SOURCE_PLM_URL"}
_TIMEOUT_ENV = "SUNROOF_SOURCE_TIMEOUT_S"
_RETRIES_ENV = "SUNROOF_SOURCE_MAX_RETRIES"
_DEFAULT_TIMEOUT_S = 2.0
_DEFAULT_MAX_RETRIES = 2
_MAX_RETRIES_CAP = 5


class SourceUnavailableError(Exception):
    """源不可达/不可用 (HTTP 骨架传输失败) —— server 层转既有结构化拒答, 绝不编造。"""

    def __init__(self, source: str, detail: str = ""):
        self.source = source
        self.detail = detail
        super().__init__(SOURCE_UNAVAILABLE_PREFIX + source +
                         (f" ({detail})" if detail else ""))


# ---------------------------------------------------------------------------
# D2-a 三源取数协议 (与 server 对 store 的消费面逐一同形)
# ---------------------------------------------------------------------------

@runtime_checkable
class MesSource(Protocol):
    """MES 取数消费面 (mes_server: get_record/waveform_segment/missing_field_reason)。"""

    def get_record(self, sn: str): ...
    def waveform_segment(self, sn: str, segment: int): ...
    def missing_field_reason(self, sn: str, field: str): ...


@runtime_checkable
class QmsSource(Protocol):
    """QMS 取数消费面 (qms_server: get_record/field_rows/current_rows/missing_field_reason)。"""

    def get_record(self, part_no: str): ...
    def field_rows(self, part_no: str, field: str): ...
    def current_rows(self, rows): ...
    def missing_field_reason(self, part_no: str, field: str): ...


@runtime_checkable
class PlmSource(Protocol):
    """PLM 取数消费面 (plm_server: get_record/eco_window/missing_field_reason)。"""

    def get_record(self, part_no: str): ...
    def eco_window(self, part_no: str, offset: int): ...
    def missing_field_reason(self, part_no: str, field: str): ...


class SyntheticMesSource:
    """默认实现: 逐方法委托冻结 mes_store (synthetic=true 语义零改动)。"""

    def get_record(self, sn: str):
        return mes_store.get_record(sn)

    def waveform_segment(self, sn: str, segment: int):
        return mes_store.waveform_segment(sn, segment)

    def missing_field_reason(self, sn: str, field: str):
        return mes_store.missing_field_reason(sn, field)


class SyntheticQmsSource:
    """默认实现: 逐方法委托冻结 qms_store (P-2 现行过滤留在取数层, 语义零改动)。"""

    def get_record(self, part_no: str):
        return qms_store.get_record(part_no)

    def field_rows(self, part_no: str, field: str):
        return qms_store.field_rows(part_no, field)

    def current_rows(self, rows):
        return qms_store.current_rows(rows)

    def missing_field_reason(self, part_no: str, field: str):
        return qms_store.missing_field_reason(part_no, field)


class SyntheticPlmSource:
    """默认实现: 逐方法委托冻结 plm_store (R-2 ECO 分页语义零改动)。"""

    def get_record(self, part_no: str):
        return plm_store.get_record(part_no)

    def eco_window(self, part_no: str, offset: int):
        return plm_store.eco_window(part_no, offset)

    def missing_field_reason(self, part_no: str, field: str):
        return plm_store.missing_field_reason(part_no, field)


# ---------------------------------------------------------------------------
# D2-a 适配器注册表 (进程内; 默认合成实现; 换装只追加 journal)
# ---------------------------------------------------------------------------

_PROTOCOLS = {"mes": MesSource, "qms": QmsSource, "plm": PlmSource}
_REGISTRY = {"mes": SyntheticMesSource(), "qms": SyntheticQmsSource(),
             "plm": SyntheticPlmSource()}
_JOURNAL = []


def resolve(source: str):
    """取当前源适配器 (默认 = 冻结合成夹具; 未知源 ValueError)。"""
    if source not in _REGISTRY:
        raise ValueError(f"未知源: {source!r} (封闭枚举 {SOURCES})")
    return _REGISTRY[source]


def register(source: str, adapter, actor: str = "", reason: str = "") -> None:
    """注入点: 进程内替换某源实现 (测试/运维注入; Protocol 结构校验 + journal 留痕)。"""
    if source not in _REGISTRY:
        raise ValueError(f"未知源: {source!r} (封闭枚举 {SOURCES})")
    protocol = _PROTOCOLS[source]
    if not isinstance(adapter, protocol):
        raise TypeError(f"{source} 适配器不满足 {protocol.__name__} 协议 "
                        f"(缺方法/形状不符), 拒绝换装")
    _REGISTRY[source] = adapter
    _JOURNAL.append({"source": source, "adapter_class": type(adapter).__name__,
                     "actor": actor, "reason": reason})


def reset_registry() -> None:
    """恢复三源默认合成实现 (只动进程内注册表; 冻结 store 模块零改动)。"""
    _REGISTRY["mes"] = SyntheticMesSource()
    _REGISTRY["qms"] = SyntheticQmsSource()
    _REGISTRY["plm"] = SyntheticPlmSource()


def registry_journal() -> list:
    """换装日志 (只追加, 进程内, 零 I/O; 供测试/运维对账)。"""
    return list(_JOURNAL)


# QMS 字段 -> 行键映射 (与 qms_store._FIELD_KEY 同值; 行级过滤镜像 store 语义)
_QMS_FIELD_KEY = {"sop_current": "sop_rows", "quality_standard": "standard_rows",
                  "inspection_checklist": "checklist_rows"}


@dataclass(frozen=True)
class HttpSourceConfig:
    """HTTP 适配器配置 (端点/超时/有限重试; 零秘密 —— 端点不含凭据)。"""

    source: str
    base_url: str
    timeout_s: float = _DEFAULT_TIMEOUT_S
    max_retries: int = _DEFAULT_MAX_RETRIES

    def __post_init__(self):
        if self.source not in SOURCES:
            raise ValueError(f"未知源: {self.source!r} (封闭枚举 {SOURCES})")
        base = str(self.base_url).strip().rstrip("/")
        if not (base.startswith("http://") or base.startswith("https://")):
            raise ValueError(f"{self.source} base_url 必须以 http(s):// 开头: "
                             f"{self.base_url!r}")
        object.__setattr__(self, "base_url", base)
        if not isinstance(self.timeout_s, (int, float)) or self.timeout_s <= 0:
            raise ValueError(f"{self.source} timeout_s 必须为正数: {self.timeout_s!r}")
        if not isinstance(self.max_retries, int) or \
                not (0 <= self.max_retries <= _MAX_RETRIES_CAP):
            raise ValueError(f"{self.source} max_retries 必须在 0..{_MAX_RETRIES_CAP}: "
                             f"{self.max_retries!r}")


class HttpSourceAdapter:
    """HTTP 取数骨架 (三源 union 消费面; 传输用 httpx, 行级变换镜像 store 语义)。

    * transport 供测试注入 httpx.MockTransport (零真实网络);
    * 每 surface 方法一次 GET (无缓存无时钟; 缓存/ETag/字段映射属真实集成范围);
    * 满足三源 Protocol —— 可 register 为任一源 (config.source 决定语义分支)。
    """

    def __init__(self, config: HttpSourceConfig, transport=None):
        self._config = config
        self._transport = transport
        self._client = None

    @property
    def config(self) -> HttpSourceConfig:
        return self._config

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(transport=self._transport,
                                        timeout=self._config.timeout_s,
                                        follow_redirects=False)
        return self._client

    def _fetch_json(self, path: str):
        """GET {base}/{path}: 200 JSON 对象 | None(404) | SourceUnavailableError。"""
        url = self._config.base_url + "/" + urllib.parse.quote(str(path), safe="/")
        last_detail = "unknown"
        for _ in range(1 + self._config.max_retries):
            try:
                resp = self._http().get(url)
            except httpx.TransportError as exc:      # 连接拒绝/超时/DNS 等
                last_detail = f"transport:{type(exc).__name__}"
                continue
            if resp.status_code == 404:
                return None                          # 诚实不存在 (与 store 同形)
            if 200 <= resp.status_code < 300:
                try:
                    payload = resp.json()
                except ValueError:
                    raise SourceUnavailableError(self._config.source, "non-json-body")
                if not isinstance(payload, dict):
                    raise SourceUnavailableError(self._config.source, "non-object-json")
                return payload
            if 500 <= resp.status_code < 600:
                last_detail = f"http_{resp.status_code}"
                continue                             # 5xx -> 有限重试
            raise SourceUnavailableError(self._config.source, f"http_{resp.status_code}")
        raise SourceUnavailableError(self._config.source, last_detail)

    # ---- MES 消费面 ----
    def get_record(self, key: str):
        return self._fetch_json(key)

    def waveform_segment(self, sn: str, segment: int):
        return self._fetch_json(f"{sn}/waveform/{int(segment)}")

    # ---- QMS 消费面 (行级变换镜像 qms_store) ----
    def field_rows(self, part_no: str, field: str):
        record = self._fetch_json(part_no)
        if record is None:
            return None
        row_key = _QMS_FIELD_KEY.get(field)
        if row_key is None or row_key not in record:
            return None
        return list(record[row_key])

    def current_rows(self, rows):
        """P-2 取数纪律: 只放行 is_current=1 的行 (镜像 qms_store.current_rows)。"""
        if rows is None:
            return None
        return [dict(r) for r in rows if r.get("is_current") == 1]

    # ---- PLM 消费面 (R-2 分页镜像 plm_store.eco_window) ----
    def eco_window(self, part_no: str, offset: int):
        record = self._fetch_json(part_no)
        if record is None or "eco_history" not in record:
            return None
        changes = record["eco_history"]
        window = changes[offset:offset + PLM_M.ECO_MAX_RETURN]
        has_more = offset + len(window) < len(changes)
        return {"changes": list(window), "total_changes": len(changes),
                "eco_offset": offset, "returned_changes": len(window),
                "max_return": PLM_M.ECO_MAX_RETURN, "truncated": has_more,
                "next_offset": (offset + len(window)) if has_more else None}

    # ---- 共有: 字段缺失结构化原因 (按 config.source 分支, 镜像各 store 语义) ----
    def missing_field_reason(self, key: str, field: str):
        record = self._fetch_json(key)
        if record is None:
            return None
        if self._config.source == "qms":
            row_key = _QMS_FIELD_KEY.get(field)
            if row_key is None or row_key in record:
                return None
            return QMS_M.FIELD_NOT_AVAILABLE
        if field in record:
            return None
        if self._config.source == "mes":
            return MES_M.FIELD_NOT_AVAILABLE
        return PLM_M.FIELD_NOT_AVAILABLE


def _env_float(raw, default: float, name: str) -> float:
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(str(raw).strip())
    except ValueError:
        raise ValueError(f"{name} 必须是数字: {raw!r}")


def _env_int(raw, default: int, name: str) -> int:
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        raise ValueError(f"{name} 必须是整数: {raw!r}")


def configure_from_env(environ=None) -> dict:
    """按环境变量装配适配器 (默认关闭; http 模式缺任一端点即快速失败)。

    返回装配报告 (供证据/日志; 只含端点与参数, 零秘密)。
    """
    env = os.environ if environ is None else environ
    mode = str(env.get(MODE_ENV, "synthetic")).strip().lower()
    if mode == "synthetic":
        return {"mode": "synthetic", "registered": [], "endpoints": {}}
    if mode != "http":
        raise ValueError(f"{MODE_ENV} 仅支持 synthetic/http: {mode!r}")
    timeout_s = _env_float(env.get(_TIMEOUT_ENV), _DEFAULT_TIMEOUT_S, _TIMEOUT_ENV)
    max_retries = _env_int(env.get(_RETRIES_ENV), _DEFAULT_MAX_RETRIES, _RETRIES_ENV)
    configs = []
    for source in SOURCES:
        url = str(env.get(_URL_ENVS[source], "")).strip()
        if not url:
            raise ValueError(f"{_URL_ENVS[source]} 缺失: http 模式要求三源端点"
                             f"全部显式配置 (绝不静默回退合成夹具)")
        configs.append(HttpSourceConfig(source=source, base_url=url,
                                        timeout_s=timeout_s, max_retries=max_retries))
    for cfg in configs:  # 全部构造校验通过后才换装 (零部分换装)
        register(cfg.source, HttpSourceAdapter(cfg), actor="configure_from_env",
                 reason="SUNROOF_SOURCE_MODE=http")
    return {"mode": "http", "registered": list(SOURCES),
            "endpoints": {c.source: c.base_url for c in configs},
            "timeout_s": timeout_s, "max_retries": max_retries}