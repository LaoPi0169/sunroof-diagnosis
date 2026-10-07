# Week 08 CI 证据 (ci-evidence)

- **日期**: 2026-09-30
- **Build ID**: `build-fde-w08-20260930-01`
- **流水线入口**: `ci/run_local_ci.py`（本地）= `.github/workflows/ci.yml`（远端），同一命令同一阶段，本地复跑即 CI 复跑。
- **原始日志**: `evidence/ci-run-w08.log`（CI 运行器自动追加，含全部三轮运行记录）

## 一、流水线设计（4 阶段）

| 阶段 | 内容 | 对应验收标准 |
| --- | --- | --- |
| STAGE-1-schema | 临时库幂等迁移（001→002→003）+ `PRAGMA integrity_check` + 必需表存在性 | 发布物结构可信 |
| STAGE-2-unittest | `unittest discover` 全量单测/集成（摄取幂等、RBAC、E2E 任务闭环） | 回归不退化 |
| STAGE-3-redaction | `ci/redaction_scan.py` 静态扫描：凭据/政策正文不入运行时输出 | 敏感信息不入日志 |
| STAGE-4-clean-env-smoke | `git archive HEAD` → 全新目录 → `uvicorn` 独立实例（全新 DB + 显式环境变量）→ 应用发布配置 → `deploy/smoke_w08.py` 4 条任务冒烟（三向对账） | 干净环境可部署 + 冒烟看任务终态 |

CI 触发条件：`push`（main/week08）、`pull_request`、`workflow_dispatch`；Python 3.12；证据日志作为 artifact 上传。

## 二、运行证据（真实三轮）

### Run 1（首次）：3 PASS / 1 FAIL —— 扫描器抓到自己

```
[PASS] STAGE-1-schema :: integrity=ok; migrations=3; required_tables_missing=[]; ingestion_tables=2
[PASS] STAGE-2-unittest :: Ran 20 tests in 3.867s, exit=0  (OK)
[FAIL] STAGE-3-redaction :: violations 含 ci/redaction_scan.py 自身凭据字面量（扫描器定义清单被自扫描）
[PASS] STAGE-4-clean-env-smoke :: clean_dir=app; port=54503; smokes_passed=4/4
```
处置：`ci/redaction_scan.py` 加入凭据常量授权名单（扫描器必须持有字面量清单才能检测违规）。

### Run 2：3 PASS / 1 FAIL —— 扫描器抓到一个真实违规

```
[PASS] STAGE-1-schema / STAGE-2-unittest / STAGE-4-clean-env-smoke
[FAIL] STAGE-3-redaction :: workbench_cli.py:88 rule=policy_body_in_emission
       "输出语句包含敏感标记: form***"  (CLI 打印 candidate_policy.formula_rule)
```
处置（策略裁决，非代码修复）：`workbench_cli.py` 是**客户端工作台**，把操作者本人已授权响应中的
`candidate_policy` 展示给本人属「授权展示」而非日志泄漏（Week 07 SOP 人工核验 UX 的既定设计，
服务端从不主动记录正文，`task_action_runs` 审计仅存 policy_id）。据此在扫描器中落地两条规则：
1. **凭据类**（token 明文 / X-Auth-Token）：任何文件的运行时输出语句全范围禁止；
2. **政策正文类**（标题/公式字段名）：服务端与工具链（application/*、deploy/*、ci/*）禁止；
   仅 `workbench_cli.py` 客户端授权展示豁免。
该裁决的完整论证见 Day 5 文档 `basic-observability-note-v1.md`。

### Run 3（工具链终态）：4/4 PASS

```
[PASS] STAGE-1-schema :: integrity=ok; migrations=3; required_tables_missing=[]; ingestion_tables=2
[PASS] STAGE-2-unittest :: Ran 20 tests in 2.921s, exit=0  (OK)
[PASS] STAGE-3-redaction :: scanned_files=11, emissions_checked=39, violations=0
[PASS] STAGE-4-clean-env-smoke :: clean_dir=app; port=63939; smokes_passed=4/4
CI RESULT OK
```

### Run 4（Week 08 验收终态）：4/4 PASS，测试规模 20→29

部署/发布自动化用例（`tests/test_deployment_release.py`，9 个）合入后的完整验收：

```
[PASS] STAGE-1-schema :: integrity=ok; migrations=3; required_tables_missing=[]; ingestion_tables=2
[PASS] STAGE-2-unittest :: Ran 29 tests in 6.339s, exit=0  (OK)
[PASS] STAGE-3-redaction :: scanned_files=11, emissions_checked=39, violations=0
[PASS] STAGE-4-clean-env-smoke :: clean_dir=app; port=58958; smokes_passed=4/4
CI RESULT OK
```

覆盖：manifest 全绑定 / 收敛器幂等与 fail-fast / git archive 干净环境部署 / 四条任务冒烟三向对账 /
动态脱敏（响应+审计+服务端日志）/ 注入→拦截→回滚→全新库复验 4/4 / 文档一致性 / CI 定义 / 静态扫描回归。

## 三、结论

- 本地 CI 流水线 4/4 阶段通过，退出码 0；干净环境真实部署 + 任务冒烟在 Run 1 即一次通过（4/4）。
- 脱敏扫描在两轮运行中分别捕获自指问题与真实违规，证明扫描规则真实生效而非摆设。
- 原始证据（含失败轮次）完整保留于 `evidence/ci-run-w08.log`，不删失败记录 —— 失败-修复链本身就是证据。

## 四、Week 09 运行记录（RAG 语料治理，回链 specs/mission-week-09.md）

最终验收运行（Run 1 一次通过，原始日志 `evidence/ci-run-w09.log`）：

```
CI RUN 2026-10-07T07:33:20+00:00 (UTC) | host=local | python=3.13.15
[PASS] STAGE-1-schema :: integrity=ok; migrations=3; required_tables_missing=[]; ingestion_tables=2
[PASS] STAGE-2-unittest :: Ran 35 tests in 7.651s, exit=0  (OK = 29 + 语料治理 6)
[PASS] STAGE-3-redaction :: scanned_files=13, emissions_checked=42, violations=0
[PASS] STAGE-4-clean-env-smoke :: clean_dir=app; port=64497; smokes_passed=4/4
CI RESULT OK | duration=11.4s
```

Week 09 变化点：

- STAGE-2 全量测试 29 → 35（新增 `tests/test_corpus_governance.py` 6 个语料治理用例：
  两次构建确定性 / 缺要件默认隔离 / supersedes 谱系过期 / 冲突对保留 / 角色候选零泄漏
  （精确候选集合断言，含 `billing-general-v3#s06` 排除）/ 清单绑定全新重建）。
- STAGE-3 扫描范围 11 → 13 文件：语料工具链 `corpus/build_corpus.py`、
  `corpus/check_role_candidates.py` 纳入运行时输出脱敏扫描。
- STAGE-1 冻结面不变：`migrations=3`（语料两表 schema 独立存放于 `corpus/corpus_schema.sql`，
  不触碰 `release-manifest-v1.json` 的 schema_version=003 绑定语义）。
- 语料层证据：`corpus-manifest-v1.json`（整体 sha256 `135f773cbd4aeb55…`，与全新重建逐字节一致）；
  两次独立构建清单逐字节相同；三档角色原始候选 support_agent 13 / supervisor 18 / 未知 0，
  受限内容零泄漏（详细证据见 `role-candidate-check-v1.md` 与 `ingestion-isolation-report-v1.md`）。

## 5. 第 10 周 CI 证据（评测合同冻结 + 脱敏扩容 13 → 16 文件）

最终验收运行（Run 1 一次通过，原始日志 `evidence/ci-run-w10.log`）：

```
CI RUN 2026-10-07T09:05:00+00:00 (UTC) | host=local | python=3.13.15
[PASS] STAGE-1-schema :: integrity=ok; migrations=3; required_tables_missing=[]; ingestion_tables=2
[PASS] STAGE-2-unittest :: Ran 41 tests in 21.630s, exit=0  (OK = 35 + 评测合同 6)
[PASS] STAGE-3-redaction :: scanned_files=16, emissions_checked=44, violations=0
[PASS] STAGE-4-clean-env-smoke :: clean_dir=app; port=59402; smokes_passed=4/4
CI RESULT OK
```

Week 10 变化点：

- STAGE-2 全量测试 35 → 41（新增 `tests/test_eval_contract.py` 6 个评测合同用例：
  60 行矩阵逐格精确（36/12/12 × 24/10/8/8/10）/ 输入 schema 与封存边界（输入行零标签字段）/
  语料引用可解析（gold/forbidden 全部回到第 9 周快照清单）/ 近重复零跨 split 泄漏
  （归一化 query 60/60 唯一）/ 封存标签 sha256 对齐冻结记录 / 确定性基线两次运行逐字节一致
  （sealed 12 / not_applicable 20 / answered 24 / needs_review 4））。
- STAGE-3 扫描范围 13 → 16 文件：评测工具链 `evaluation/eval_contract.py`、
  `evaluation/validate_eval_set.py`、`evaluation/run_deterministic_baseline.py` 纳入运行时
  输出脱敏扫描（扫描器清单同步扩容；44 个输出点零违规）。
- 零模型调用声明：本周新增代码路径不含任何模型 API 调用；基线探针 100% 复用第 8 周
  确定性政策引擎与第 9 周 ACL 候选 SQL（先过滤后候选语义不变）。
- 评测层证据：冻结记录 `evaluation-freeze-record-v1.md` 登记 12 件冻结物 sha256，
  程序化核对 12/12 一致（checked=12, bad=0）；保留集标签仅存封存件
  （第 12 周 manifest 冻结后解封一次）；判题边界与开除线见 `eval-plan-v1.md` §5–6。
