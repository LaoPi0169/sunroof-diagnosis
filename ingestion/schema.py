"""
Ingestion Schemas and Data Contract Validation DTOs (Week 06)

数据合同 data-contract-v1.md 的代码化载体:
- 批次请求/结果、隔离原因码、隔离状态、批次状态、定向重放请求/结果
- 行级校验不在此处 (见 service.validate_row)，此处仅定义传输契约
"""
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class QuarantineReasonCode(str, Enum):
    """隔离原因码: 必须稳定、可测试、可操作 (运营者看到即知道下一步)"""

    MISSING_OWNER = "MISSING_OWNER"                          # 缺少知识所有者 -> 找知识库管理员补齐
    UNKNOWN_ACCESS_LEVEL = "UNKNOWN_ACCESS_LEVEL"            # 非法/缺失访问级别 -> 默认拒绝，找权限所有者
    INVALID_EFFECTIVE_RANGE = "INVALID_EFFECTIVE_RANGE"      # 失效时间早于等于生效时间 -> 确认日期或拒绝来源
    MISSING_BUSINESS_KEY = "MISSING_BUSINESS_KEY"            # 缺少业务键/核心标识字段
    INVALID_REGION = "INVALID_REGION"                        # 地区不在枚举范围
    DUPLICATE_BUSINESS_KEY = "DUPLICATE_BUSINESS_KEY"        # 业务主键重复 -> 核对上游是否重复推送
    INVALID_SCHEMA = "INVALID_SCHEMA"                        # 字段缺失或格式无法解析 (含日期格式非法)


class QuarantineStatus(str, Enum):
    QUARANTINED = "quarantined"   # 待修复 (可定向重放)
    RESOLVED = "resolved"         # 已修复并重放入主表 (历史保留)
    REJECTED = "rejected"         # 已判定不可修复 (保留审计)


class BatchStatus(str, Enum):
    PROCESSING = "processing"                       # 批次事务进行中 (崩溃后不应存在终态)
    COMPLETED = "completed"                         # 全部行均 accepted/duplicate，无隔离
    COMPLETED_WITH_QUARANTINE = "completed_with_quarantine"  # 有行进入隔离区 (批次完成但需人工跟进)
    FAILED = "failed"                               # 批次事务失败，整体回滚，不产生部分提交


class BatchIngestionRequest(BaseModel):
    """批次摄取请求: rows 为原始行字典列表 (业务键 = policy_id + source_version)"""

    batch_id: str = Field(..., description="唯一摄取批次标识 (幂等键)")
    source_id: str = Field(..., description="来源系统标识 (血缘: source_id)")
    source_version: str = Field(default="v1", description="批次级上游版本 (血缘)")
    correlation_id: Optional[str] = Field(
        default=None, description="跨系统关联标识 (仅用于结构化日志，不参与业务判定)"
    )
    rows: List[Dict[str, Any]] = Field(..., description="原始数据行列表")


class BatchIngestionResult(BaseModel):
    """批次摄取结果: 三处一致对账的批次侧口径 (input = accepted + duplicate + quarantined)"""

    batch_id: str
    source_id: str
    correlation_id: Optional[str] = None
    total_rows: int
    accepted_count: int
    duplicate_count: int
    quarantined_count: int
    status: BatchStatus
    is_idempotent_replay: bool = False   # True 表示该 batch_id 已处理过，返回的是既有对账结果
    quarantine_summary: Dict[str, int] = Field(default_factory=dict)


class ReplayRequest(BaseModel):
    """定向重放请求: fixed_payload 支持『完整行』或『部分补丁』(补丁字段覆盖隔离区原值)"""

    quarantine_id: str
    replayed_by: str
    fixed_payload: Dict[str, Any]
    notes: Optional[str] = None


class ReplayResult(BaseModel):
    """定向重放结果"""

    replay_id: str
    quarantine_id: str
    status: str  # 'success' | 'failed'
    accepted_policy_id: Optional[str] = None
    message: str
