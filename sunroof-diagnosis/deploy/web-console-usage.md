# 交互控制台使用指南 —— 天窗总成只读诊断（阶段 C 节点 ⑮）

> 交付物：`application/static/console.html`（单文件零框架控制台）+
> `application/main.py` 追加端点 + `start_web_console.bat` 一键启动器。
> 定位：**API 网关面的交互外壳**——仅追加能力，不改既有端点语义；
> 零大模型、零业务写副作用。它不替代 8018 值守交接工作台，两者互补：

| | 本控制台（8000） | 值守交接工作台（8018） |
|---|---|---|
| 载体 | FastAPI `application/main.py` + `/ui` 静态页 | 标准库 `workbench_handoff/app.py` |
| 面向 | 开发/验收：直接操作冻结 API 契约 | 交接演示：双角色隔离 + 受控写 + 管理面 |
| 身份 | 服务端可信会话 `X-Auth-Token` | 角色选择器 + 管理员口令（env 注入） |
| 能力 | RAG 问答 / 规格核对 / 任务闭环 | 问答 / 工具 / 审批写 / 开关回滚审计 |

## 1. 启动（三选一）

```bat
:: 方式 A —— Windows 双击启动器（推荐）:
::   sunroof-diagnosis\start_web_console.bat
:: 自动: 检测 8000 占用(防呆) → 打开浏览器 http://127.0.0.1:8000/ui → 起服务
:: (启动器优先使用工作区 .venv 的解释器, 没有则回退系统 python)
```

```powershell
# 方式 B —— 手动启动 (Windows, 已装好依赖的 .venv):
cd d:\WebstormProjects\fde-course-week-05\sunroof-diagnosis
d:\WebstormProjects\fde-course-week-05\.venv\Scripts\python.exe -m uvicorn application.main:app --port 8000
# 浏览器打开 http://127.0.0.1:8000/ui   (访问 / 会自动重定向到 /ui)
```

```bash
# 方式 C —— Linux / macOS:
cd sunroof-diagnosis
python3 -m pip install -r requirements.txt
python3 -m uvicorn application.main:app --host 0.0.0.0 --port 8000
```

```bash
# 方式 D —— Docker (CI/CD 通道发布的预构建镜像; 用 command 覆盖默认的 8018 工作台):
docker run --rm -p 8000:8000 ghcr.io/laopi0169/sunroof-workbench:latest \
  python -m uvicorn application.main:app --host 0.0.0.0 --port 8000
```

> 首次启动若 `data/corpus-w09.db` 缺失会**确定性重建语料库**（数秒，幂等；
> `write_manifest=False`，绝不触碰冻结的 `corpus-manifest-v1.json`）。

## 2. 身份与演示令牌（教学模拟会话）

身份**只来自服务端 `X-Auth-Token` 请求头**，页面不留存令牌（只在本页内存）。
控制台「身份档案」下拉框与令牌的对应关系：

| 档位 | X-Auth-Token | 身份 | ACL（allowed_oem_projects） |
|---|---|---|---|
| 默认 PQE 培训身份 | （留空即可） | pqe_trainee_default | OEM-A |
| PQE · OEM-A | `token_pqe_oem_a` | pqe_chen_oema | OEM-A |
| PQE · OEM-B | `token_pqe_oem_b` | pqe_zhao_oemb | OEM-B |
| 质量主管 | `token_qa_supervisor_secret` | supervisor_li | *（跨项目只读） |

未知令牌一律 **401**（查询不执行、不落库）；请求体自报角色被封闭契约
（`extra="forbid"`）直接 **422**。令牌仅用于本教学沙盒，勿在真实系统复用。

## 3. 三个面板怎么用

### ① 只读 RAG 问答（Week-12 冻结门控决策树）
- 输入问句（或点示例 chips —— 均为冻结评测集真实问句）→「提交问答」。
- 结果卡片字段：`state` 三态（answered/abstained/escalate）与映射的
  `expected_task_state` 四态；`answer_text`（阻断/升级路径恒为空——内容零泄漏）；
  引用表（document_id/chunk_id/source_version/effective_status，answered 恰 1 条）；
  `reason_code`、`user_next_step`、`confidence_top1`、
  `restricted_candidate_count`（PQE 恒 0）、`business_effects`（恒 0）、
  `generator_mode`（deterministic-teaching-stand-in）。
- 建议演示脚本：SR-DEV-01（可答带引用）→ SR-DEV-23（冲突门 needs_review）→
  SR-DEV-27（过期门 refused）→ SR-DEV-21（无答案域 refused）→
  SR-DEV-31（PQE 问受限索赔域 → 升级且零泄漏）→ 切「质量主管」再问
  SR-DEV-34（主管受限池 answered）。

### ② 装配规格核对（确定性规则引擎）
- 表单预填了任务夹具事实（SN `SR-20261001-0001`、`MOTOR-MOUNT-BOLT-03`、
  `torque_tightening`、`2026-08-15`、`9.6`）→「提交核对」。
- 演示矩阵：9.6 → `answered`（偏差 +1.05%，命中 SPEC-SR-A-V5）；
  改实测值 11.5 → `needs_review / SPEC_HARD_LIMIT_EXCEEDED`（21.05% 超硬限）；
  零件换 `HEAT-SHIELD-02` + 工序 `gap_check` → `SPEC_CONFLICT`（双现行）；
  零件换 `UNKNOWN-PART-99` → `NO_MATCHING_SPEC`；
  测量日期改到规格生效窗口外 → `NO_MATCHING_SPEC`。

### ③ 任务工作台（双角色闭环）
- 「刷新任务队列」拉取 `/api/v1/tasks`（按身份 ACL 过滤：OEM-B PQE 看不到
  OEM-A 任务；主管全见）。
- 行内动作：`check_spec` / `confirm` / `refuse` / `escalate`
  （escalate 自动带幂等键，重复提交返回 `ALREADY_APPLIED`）；
  质量主管身份额外出现 `supervisor_resolve`。
- 越权演示：用 PQE 直调 `supervisor_resolve` → 响应 `denied /
  ACTION_NOT_ALLOWED`，任务状态不变，`task_action_runs` 落审计行。
- 每次 `correlation_id` 自动生成，响应 / `spec_check_tasks` 终态 / 审计表三向对账。

## 4. curl 等价命令（脚本化验收）

```bash
# 身份回显
curl -s http://127.0.0.1:8000/api/v1/session/whoami -H "X-Auth-Token: token_qa_supervisor_secret"

# RAG 问答 (中文务必 charset=utf-8 + --data-binary)
curl -s -X POST http://127.0.0.1:8000/api/v1/rag/answer \
  -H "Content-Type: application/json; charset=utf-8" -H "X-Auth-Token: token_pqe_oem_a" \
  --data-binary '{"query":"东区天窗紧固件复紧按什么顺序执行，工时怎么登记？"}'

# 语料元信息 / 健康检查 / Swagger
curl -s http://127.0.0.1:8000/api/v1/rag/corpus-meta
curl -s http://127.0.0.1:8000/health
# 浏览器打开 http://127.0.0.1:8000/docs
```

## 5. 新增端点一览（仅追加，既有端点零改动）

| 端点 | 说明 |
|---|---|
| `GET /` | 302 → `/ui` |
| `GET /ui` | 交互控制台（静态单文件） |
| `GET /api/v1/session/whoami` | 回显当前可信身份（不回显令牌） |
| `POST /api/v1/rag/answer` | 只读 RAG 问答（Week-12 门控树 + 抽取式替身） |
| `GET /api/v1/rag/corpus-meta` | 语料快照 ID / 文档数 / chunk 数 |

## 6. 测试与质量门

```bash
# 本套件 (7 用例, TestClient + 冻结评测集问句驱动):
python -m unittest tests.test_web_console -v
# 全量回归 (应为 87 tests) + 四阶段本地 CI:
python ci/run_local_ci.py
```

注：`ci/run_local_ci.py` STAGE-4 已做**布局自适配**——按「HEAD 中是否真实
跟踪项目内容」选择归档仓库与 pathspec，兼容独立 clone / 工作区子目录
（GitHub checkout 同构）/ 历史嵌套 `.git` 遗迹三种布局。

## 7. 常见问题

1. **8000 被占**：`netstat -ano | findstr :8000` 找 PID，`taskkill /PID <pid> /F`
   后重跑启动器（启动器自带防呆，会阻止在脏端口上启动）。
2. **401 Invalid authentication token**：令牌粘贴错误或多了空格；用 §2 矩阵核对。
3. **422**：请求体带了 `role` 等身份字段（封闭契约故意拒绝）或缺 `query`。
4. **首次问答慢 1-2 秒**：正在确定性重建语料库（仅首次，幂等）。
5. **Docker 里 /ui 打不开**：确认用了 §1 方式 D 的 command 覆盖——镜像默认
   CMD 是 8018 值守交接工作台，不是本控制台。

## 8. 诚实边界

生成层为确定性抽取式替身（零 LLM）；引用支持 1.0 是结构使然而非模型能力；
语料为合成教学材料；令牌为教学模拟会话。完整口径见 `README.md` 与
`workbench_handoff` 页脚声明。

## 9. 节点 ⑰ 追加（2026-10-13）：MES 生产记录抓取面板

第四面板「④ MES 生产记录抓取」对接只读 MCP 工具 `mes.lookup_production_record@1`
（`POST /api/v1/mes/production-record`，身份仍走 §2 同一 `X-Auth-Token` 矩阵）：

- **输入**：SN（冻结语法 `ROOF-#####`）+ 1..3 个字段复选（装配扭矩 / ECU 刷写 /
  传感器波形）+ 波形分页段 0..3（R-2 收口：128 点 × 32 点/段 × 4 段）；
- **成功**：按字段渲染拧紧记录表 / ECU 刷写档案 / 波形段点列（含
  truncated / next_segment 分页提示）；
- **not_available**（跨项目组 / 未知 SN / 角色不足）三者封套完全一致
  （零存在性泄漏），细分原因只进服务端受限审计；
- **字段级缺失**（如 ROOF-10003 无波形通道）：字段条目结构化
  `FIELD_NOT_AVAILABLE`，绝不编造数值（P-3）；
- 试玩样例：`ROOF-10001`（本项目组全字段）/ `ROOF-10003`（缺失字段）/
  `ROOF-20001`（跨项目，默认 OEM-A 身份下零泄漏拒绝）；
- 诚实边界：MES 为确定性合成夹具（synthetic=true，零真实 MES 连接），外部写
  效果恒 0（effect=read_only）。工具契约冻结于 `api-contract/mes-lookup-v1.json`。


## 10. 节点 ⑱ 追加（2026-10-13）：PLM 工程记录查询面板

第五面板「⑤ PLM 工程记录查询」对接只读 MCP 工具 `plm.lookup_engineering_record@1`
（`POST /api/v1/plm/engineering-record`，身份仍走 §2 同一 `X-Auth-Token` 矩阵）：

- 输入：零件号（冻结语法 `^SR-[A-Z]{3,10}-[AB]\d{2}$`，不归一化）+ 请求字段复选
  （`bom_snapshot` / `eco_history` / `drawing_registry`，1..3 个）+ ECO 分页偏移
  （0..12，留空按 0）；
- 输出：BOM 快照（revision / count / items 表格）、ECO 历史（4 条/页，截断时显示
  next_offset）、图纸登记元数据表格 + `content_disclosure=denied_by_policy` 提示
  （图纸内容/文件/链接永不出工具，主管亦不展示——矩阵 §2 跨 OEM 敏感图纸硬门槛）；
- `not_available` 字段（如 `SR-MOTOR-A02` 无图纸登记）显示字段级结构化缺失
  （`FIELD_NOT_AVAILABLE`），绝不编造数值（P-3）；
- 试玩样例：`SR-FRAME-A01`（本项目组全字段，6 条 ECO 可翻页）/ `SR-MOTOR-A02`
  （图纸字段结构化缺失）/ `SR-RAIL-B01`（跨项目，默认 OEM-A 身份下零泄漏拒绝）；
- 诚实边界：PLM 为确定性合成夹具（synthetic=true，零真实 PLM 连接），外部写
  效果恒 0（effect=read_only）。工具契约冻结于 `api-contract/plm-lookup-v1.json`。
