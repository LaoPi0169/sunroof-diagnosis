# ADR-001：使用模块化单体建立确定性基线

- 状态：接受（课程模拟）
- 日期：2026-09-30
- Brief：northstar-discovery-brief-v1（第 4 周获准进入受限工程探索）
- 关联文档：`vertical-slice-contract.md`、`api-contract/policy-check-v1.json`、`migrations/001_init_schema.sql`

## 决定

用一个部署单元（单进程 FastAPI + SQLite）实现身份校验、政策查询和任务记录。应用内部分层为
模块化单体（Modular Monolith）：

| 模块 | 职责 |
| --- | --- |
| `application/main.py` | API 网关层：路由、服务端可信鉴权（`get_trusted_user`）、生命周期 |
| `application/models.py` | DTO / Pydantic Schema 契约层：`extra="forbid"` 封闭约束 |
| `application/services.py` | 业务逻辑层：100% 确定性规则引擎（金额门槛 / 冲突 / 无匹配 / answered） |
| `application/database.py` | 数据访问层：SQLite 连接、幂等迁移、确定性 SQL、`task_runs` 审计落盘 |

## 原因

- 本周范围只有一个只读查询任务：一个角色、一个任务、一个成功路径、若干显式异常路径；
- 需要快速验证业务状态与错误表达是否正确，而不是独立扩缩容；
- 同一进程内按模块解耦已足够维持边界，且更易运行、测试与修改；
- 确定性优先：同一版本、同一身份、同一输入必须产生可预测结果，纯代码 + SQL 规则可 100% 解释与回归。

## 未选方案

- **微服务**：当前没有独立团队、独立发布或扩缩容证据；先拆服务会把调试成本转移到网络与部署。
- **RAG / 大模型**：输入与规则完全结构化，且尚未建立正式评测集；模型会引入不可解释性与幻觉风险，
  违背"确定性优先"原则。
- **信任请求体身份**：任何客户端自报的角色都必须拒绝，身份只能来自服务端会话凭证。

## 代价

- 模块边界需要靠代码分层与集成测试维持，而不是网络隔离；
- SQLite 单写者限制，未来出现并发写负载时需要迁移到独立数据库服务。

## 对 mission.md 参考实现的必要修正（工程落地记录）

1. **平台适配**：默认数据库路径由沙盒路径 `/workspace/scratch/...` 改为项目内 `data/northstar.db`，
   保留 `FDE_DB_PATH` 环境变量覆盖，且改为调用时解析（集成测试注入临时库依赖此机制）；
2. **修复参考代码缺陷**：answered 分支 `best_policy = matched_policies`（list 不支持下标取值会抛
   TypeError）修正为 `matched_policies[0]`；
3. **幂等初始化**：迁移脚本使用 `CREATE TABLE IF NOT EXISTS` + `INSERT OR IGNORE`，
   重启不丢失 `task_runs` 审计记录；测试改为每个用例注入独立临时库保证确定性隔离。

## 重新评审条件

出现独立发布、隔离、负载或组织所有权证据时重新评审；在此之前，保持模块化单体不变。
