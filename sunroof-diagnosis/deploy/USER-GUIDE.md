# 天窗总成只读诊断系统 · 用户使用手册（USER-GUIDE）

> 版本 v1.0（2026-10-13，阶段 C 收官版）· 适用交付物：交互控制台七面板（8000）
> + 值守交接工作台（8018）· 读者：首次拿到本仓库的验收者 / 演示者 / 课程评审。
> 本手册一步一步带你从**零**跑到**完整互动演示**，全程约 10 分钟。

---

## 1. 这是什么

本系统是《天窗总成只读诊断》课程的最终可交互交付物：为天窗总线 PQE（质量
工程师）提供一个**只读**的跨系统（MES / QMS / PLM）数据调取、对账与报告
入口。打开一个网页，你可以：

| 能力 | 面板 | 背后工具 |
|---|---|---|
| 只读知识问答（带引用，权限门控） | ① RAG 问答 | Week-12 门控决策树（零 LLM） |
| 装配规格核对（偏差硬限判定） | ② 规格核对 | 确定性规则引擎 |
| 双角色任务闭环（确认/拒绝/升级） | ③ 任务工作台 | 任务状态机 + 审计 |
| 按 SN 抓生产记录（扭矩/ECU/波形） | ④ MES 抓取 | `mes.lookup_production_record@1` |
| 按零件查工程记录（BOM/ECO/图纸登记） | ⑤ PLM 查询 | `plm.lookup_engineering_record@1` |
| 按零件+工序查现行检验标准 | ⑥ QMS 查询 | `qms.lookup_inspection_standard@1` |
| 生成《根因分析报告草稿》（带出处） | ⑦ 报告草稿 | `report.draft_root_cause@1` |

三条硬安全承诺（每个面板都在兑现）：**越权零泄漏**（跨项目查询返回与"查无
此物"逐字节一致的封套）、**过期 SOP 零引用**（被替代版本在取数层被排除）、
**缺失不编造**（结构化 gap + 原因码）；全部操作**外部写效果恒 0**。

## 2. 环境要求与安装

- Windows 10/11（本手册按 Windows 写；Linux/macOS 见 §3 方式 C）；
- Python 3.11 或 3.12（`python --version` 验证）；
- 依赖只有 4 个包：`fastapi / uvicorn / pydantic / httpx`。

```powershell
# 一次性安装（在 sunroof-diagnosis 目录下执行）:
cd d:\WebstormProjects\fde-course-week-05\sunroof-diagnosis
python -m pip install -r requirements.txt
```

> 如果工作区已有 `d:\WebstormProjects\fde-course-week-05\.venv` 虚拟环境，
> 依赖已装好，跳过本步（启动器会自动优先使用它）。

## 3. 启动（三选一）

**方式 A —— 双击启动器（推荐，最省事）**

双击 `sunroof-diagnosis\start_web_console.bat`。它会：
1. 检查 8000 端口是否被占用（占用则阻止启动并提示处理办法）；
2. 自动用默认浏览器打开 `http://127.0.0.1:8000/ui`；
3. 在前台窗口保持运行（**别关那个黑窗口**，关了 = 停服务）。

**方式 B —— 命令行启动**

```powershell
cd d:\WebstormProjects\fde-course-week-05\sunroof-diagnosis
python -m uvicorn application.main:app --host 127.0.0.1 --port 8000
# 浏览器打开 http://127.0.0.1:8000/ui
```

**方式 C —— Linux / macOS**

```bash
cd sunroof-diagnosis && python3 -m pip install -r requirements.txt
python3 -m uvicorn application.main:app --host 0.0.0.0 --port 8000
```

**方式 D —— Docker（CI/CD 预构建镜像）**

```bash
docker run --rm -p 8000:8000 ghcr.io/laopi0169/sunroof-workbench:latest \
  python -m uvicorn application.main:app --host 0.0.0.0 --port 8000
```

**验证启动成功**：浏览器访问 `http://127.0.0.1:8000/health`，看到
`{"status":"ok",...}` 即在线；控制台右上角圆点变绿 = 页面自检通过。

## 4. 一步步操作（核心章节）

以下每一步都给出：操作 → 预期看到什么 → 这一步证明了什么。

### 第 1 步：打开控制台并认识身份卡

浏览器打开 `http://127.0.0.1:8000/ui`。页面顶部是**身份卡**：本系统身份
**只来自服务端令牌**（页面不保存令牌到磁盘/日志）。四档身份：

| 下拉档位 | 令牌（粘贴进"访问令牌"框） | 身份 | 可见项目组 |
|---|---|---|---|
| 默认 PQE 培训身份 | **留空** | pqe_trainee_default | OEM-A |
| PQE · OEM-A | `token_pqe_oem_a` | pqe_chen_oema | OEM-A |
| PQE · OEM-B | `token_pqe_oem_b` | pqe_zhao_oemb | OEM-B |
| 质量主管 | `token_qa_supervisor_secret` | supervisor_li | *（跨项目只读） |

→ 操作：保持"默认 PQE 培训身份"，点 **应用身份**。
→ 预期：身份卡显示 `当前身份：pqe_trainee_default · 角色 pqe · ACL 项目组 OEM-A`。
→ 证明：免令牌的培训会话可用；令牌错误会得到 401（查询不执行、不落审计）。

### 第 2 步：面板① RAG 问答（带引用的知识问答）

→ 操作：点示例圆片 **「SR-DEV-01 · 可答」**（自动填入问句）→ 点 **提交问答**。
→ 预期：绿色 `answered 已作答` 徽标 + 一句抽取式答案 + **引用表**（document_id /
chunk_id / source_version / effective_status）+ `business_effects=0`。
→ 证明：回答带精确出处；零大模型调用（generator_mode=deterministic-teaching-stand-in）。

再试两个门控演示：**SR-DEV-27 · 过期门**（返回 refused，绝不引用已被替代口径）、
**SR-DEV-31 · 受限域**（PQE 问索赔域 → escalate 且**内容零泄漏**，只给升级下一步；
切到"质量主管"身份再问 SR-DEV-34 则可答）。

### 第 3 步：面板② 装配规格核对（确定性偏差判定）

→ 操作：表单已预填（SN `SR-20261001-0001` / 零件 `MOTOR-MOUNT-BOLT-03` /
工序 `torque_tightening` / 日期 `2026-08-15` / 实测 `9.6`）→ 点 **提交核对**。
→ 预期：`answered` + 匹配规格 `SPEC-SR-A-V5` + 偏差 +1.05% + `conforming`。
→ 改实测值为 `11.5` 再提交 → `needs_review / SPEC_HARD_LIMIT_EXCEEDED`
（偏差 21.05% 超 15% 硬限，响应里**不含任何规格内容**）。
→ 零件换 `HEAT-SHIELD-02` + 工序 `gap_check` → `SPEC_CONFLICT`（双现行冲突）。
→ 证明：判定 100% 可复算、缺数据/冲突/超限绝不"猜"。

### 第 4 步：面板③ 任务工作台（双角色闭环）

→ 操作：点 **刷新任务队列**。表格按你的 ACL 过滤（OEM-A 身份只见 OEM-A 任务）。
→ 每行有动作按钮：`check_spec / confirm / refuse / escalate`；质量主管身份
额外出现 `supervisor_resolve`。
→ 越权演示：保持 PQE 身份，在任务行里点 `supervisor_resolve` → 预期返回
`denied / ACTION_NOT_ALLOWED`，任务状态**不变**，服务端审计落一行
（`task_action_runs`）。
→ 幂等演示：对同一任务连续两次 `escalate` → 第二次返回 `ALREADY_APPLIED`。
→ 证明：RBAC 一票否决 + 动作幂等 + 全量审计。

### 第 5 步：面板④ MES 生产记录抓取（节点 ⑰）

→ 操作：SN 保持 `ROOF-10001`，三个字段全勾，波形段 0 → 点 **抓取生产记录**。
→ 预期：装配扭矩表（frame_bolt 9.62 / 目标 9.5 等）+ ECU 刷写档案 +
波形第 0 段 32 个整数点（`truncated`，next_segment=1）。
→ 换 SN `ROOF-10003` → 波形字段显示 `not_available / FIELD_NOT_AVAILABLE`
（该 SN 无波形通道，**结构化缺失，绝不编造**）。
→ 换 SN `ROOF-20001`（跨项目 OEM-B）→ `denied`，且与"未知 SN"返回**完全一致**
的封套（零存在性泄漏——你无法从响应分辨"存在但无权"和"根本不存在"）。
→ 证明：R-2 分页纪律（128 点 × 32/段 × 4 段，绝不全量回传）+ P-1 + P-3。

### 第 6 步：面板⑤ PLM 工程记录查询（节点 ⑱）

→ 操作：零件号保持 `SR-FRAME-A01`，全字段，ECO 偏移 0 → 点 **查询工程记录**。
→ 预期：BOM 快照（R4，3 部件）+ ECO 历史第 1 页 4 条（`truncated`，
next_offset=4；把偏移改 4 再查 → 末页 2 条）+ 图纸登记元数据
（`content_disclosure=denied_by_policy`——图纸**内容**永不出工具，主管也不行）。
→ 零件号 `SR-MOTOR-A02` 只勾 drawing_registry → `FIELD_NOT_AVAILABLE`。
→ 零件号 `SR-RAIL-B01` → 与未知件号一致的 `not_available`（跨项目零泄漏）。
→ 证明：图纸硬门槛（P-1）+ ECO 分页上限（绝不全量回传）。

### 第 7 步：面板⑥ QMS 检验标准查询（节点 ⑲）

→ 操作：零件号 `SR-FRAME-A01` + 工序 `torque_tightening`，全字段 →
点 **查询检验标准**。
→ 预期：现行检验规程 `QMS-SOP-SR-04 v5`（生效 2026-01-20）+ 现行质量标准
`QS-SR-FRM-TQ-01 v2`（目标 9.5 N·m，公差 +0.5/-0.5，出处引用
`QMS-SOP-SR-04-v5#Section-3.2`）+ 3 步检验要点清单；payload 全部带
`exclusion_policy=current_only`。
→ **P-2 演示**：零件号换 `SR-MOTOR-A02` → sop_current 与 quality_standard
显示 `not_available / NO_CURRENT_SOP`——该件号**只有被替代的旧版**（v2，现行
v3 待发布），系统宁可结构化拒引也绝不引用过期版本；你不会在响应里看到
任何被替代版本号。
→ 检验要点：同件号只勾 inspection_checklist → `FIELD_NOT_AVAILABLE`（清单
缺失，不编造）。
→ 工序换 `gap_check` 查 FRAME-A01 → 该工序无行 → `FIELD_NOT_AVAILABLE`。
→ 证明：保护指标 P-2（过期 SOP 零引用）+ P-3。

### 第 8 步：面板⑦ 根因分析报告草稿（节点 ⑳，收官演示）

→ 操作：SN `ROOF-10001` + 零件号 `SR-FRAME-A01` + 工序 `torque_tightening`
→ 点 **生成报告草稿**。
→ 预期一张完整报告卡：
- 结论徽标 **draft_ready**（绿）+ 注记「依据充分，可出具带引用草稿（非合格判定）」；
- **发现表**（每行带出处引用）：装配扭矩偏差（frame_bolt：9.62 vs 9.5 →
  **+1.26%**，`conforming`，引用 `MES:ROOF-10001#assembly_torque[step=…] +
  QMS:QMS-SOP-SR-04-v5#Section-3.2`）；ECU 刷写档案（SW-2.3.1 / CAL-ROOF-A-05）；
  现行 SOP；检验要点（3 步）；BOM 版本 R4；最近工程变更 ECO-2026-027；
- **结构化缺口表**：rail_bolt 无现行标准对接 → `NO_STANDARD_FOR_STEP`；
  波形 → `OMITTED_BY_R2_PAGINATION_CONTRACT`（需要时去面板④分页取，报告
  **绝不嵌 128 点全量载荷**）；
- 引用清单（6 条，覆盖 MES/QMS/PLM 三源）+ 静态免责声明。

→ **硬限演示**：SN 换 `ROOF-10003` 再生成 → 结论 **needs_review /
SPEC_HARD_LIMIT_EXCEEDED**（frame_bolt 10.98 → **+15.58%** 超硬限，转质量主管）。
→ **P-2 贯通演示**：零件号换 `SR-MOTOR-A02` 再生成 → 结论 **needs_review /
NO_CURRENT_SOP**，发现表里**没有**扭矩偏差行（无现行标准就不算，绝不脑补）。
→ **跨项目演示**：SN 换 `ROOF-20001`（默认 OEM-A 身份）→ 整体 `denied`，
封套与"未知 SN"逐字节一致（任一源拒绝 = 整体拒绝，零存在性泄漏）。
→ 证明：discovery-brief 交付承诺闭环——三源只读聚合 + 带精确引用 + 缺失/
冲突/超限显式转人工。

### 第 9 步（可选）：换身份再走一遍

在身份卡切「质量主管」（粘贴 `token_qa_supervisor_secret` → 应用身份），
重跑第 5/7/8 步的跨项目样例（`ROOF-20001` / `SR-RAIL-B01`）→ 全部放行且
数据完整；这就是权限矩阵「主管跨项目只读」的交互验证。

## 5. 值守交接工作台（8018，可选第二产品面）

除 8000 控制台外，还有一个 Week-18 的**双角色值守交接工作台**（业务用户
免登录只见权限内操作；注入/开关/回滚/审计等管理面要求管理员登录，服务端
403 强制）：

```powershell
# 双击 start_workbench_handoff.bat（或别名 start_sunroof_workbench.bat）
# 或命令行:
python -m uvicorn workbench_handoff.app:app --host 127.0.0.1 --port 8018
# 浏览器打开 http://127.0.0.1:8018
```

两个面互补：**8000 = 冻结 API 契约的验收操作台**（本手册主角）；
**8018 = 交接演示剧本**（问答 → 受控写 → 开关/回滚 → 审计/复验）。

## 6. API 直调（脚本化验收，可选）

所有面板能力都有等价 HTTP 端点（Swagger UI：`http://127.0.0.1:8000/docs`）。

```bash
# 健康检查 / 语料元信息
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/api/v1/rag/corpus-meta

# 身份回显（换任一令牌验证矩阵）
curl -s http://127.0.0.1:8000/api/v1/session/whoami -H "X-Auth-Token: token_qa_supervisor_secret"

# ⑥ QMS 现行检验标准（节点 ⑲）
curl -s -X POST http://127.0.0.1:8000/api/v1/qms/inspection-standard \
  -H "Content-Type: application/json" -H "X-Auth-Token: token_pqe_oem_a" \
  -d '{"part_no":"SR-FRAME-A01","process_step":"torque_tightening","fields":["sop_current","quality_standard","inspection_checklist"]}'

# ⑦ 根因分析报告草稿（节点 ⑳）
curl -s -X POST http://127.0.0.1:8000/api/v1/report/draft \
  -H "Content-Type: application/json" -H "X-Auth-Token: token_pqe_oem_a" \
  -d '{"sn":"ROOF-10001","part_no":"SR-FRAME-A01","process_step":"torque_tightening"}'

# 跨项目拒绝演示（默认 OEM-A 身份; 与"未知件号"封套逐字节一致）
curl -s -X POST http://127.0.0.1:8000/api/v1/qms/inspection-standard \
  -H "Content-Type: application/json" \
  -d '{"part_no":"SR-RAIL-B01","process_step":"torque_tightening","fields":["sop_current"]}'
```

PowerShell 用户把 `\` 换成反引号 `` ` `` 或写成单行即可。中文问句的 RAG
调用务必带 `charset=utf-8` 并用 `--data-binary`（见
`deploy/web-console-usage.md` §4）。

## 7. 停止服务

- 方式 A 启动的：直接关闭那个黑色控制台窗口；
- 命令行启动的：在窗口按 `Ctrl+C`；
- 确认已停：`http://127.0.0.1:8000/health` 不再响应即已停止。
- 数据说明：服务是**只读诊断**，没有可丢失的业务写入；首次启动会确定性
  重建语料库（数秒、幂等，不触碰冻结 manifest），再次启动秒开。

## 8. 常见问题（FAQ）

| 现象 | 原因与处理 |
|---|---|
| 双击启动器提示"端口 8000 已被占用" | 上一个实例还活着。`netstat -ano | findstr :8000` 找 PID → `taskkill /PID <pid> /F` → 重跑启动器 |
| 任何请求都 401 Invalid authentication token | 令牌粘贴错误/带空格。按 §4 第 1 步的矩阵核对；或干脆留空用默认身份 |
| 请求返回 422 | 请求体带了 `role` 等身份字段（封闭契约故意拒绝），或缺必填键，或语法不符（如件号 `SR-FRAME-C01`、SN `ROOF-1`）——这是特性不是故障 |
| 查询返回 `not_available` | 三种可能：跨项目 / 未知 SN·件号 / 角色不足——三者封套**故意逐字节一致**（零存在性泄漏）；细分原因只进服务端受限审计 |
| 字段显示 `NO_CURRENT_SOP` | 该件号该工序只有被替代的旧版 SOP（P-2 保护：宁可拒引也不引用过期版本）。试 `SR-MOTOR-A02` |
| 首次问答慢 1-2 秒 | 正在确定性重建语料库（仅首次，幂等） |
| Docker 里 /ui 打不开 | 镜像默认 CMD 是 8018 工作台；按 §3 方式 D 加 command 覆盖 |
| 想跑全部自动化测试 | `python -m unittest discover -s tests`（139 用例）或 `python ci/run_local_ci.py`（四阶段门） |

## 9. 诚实边界（请务必连同演示一起宣读）

- 本系统为**教学模拟**：MES / QMS / PLM 均为确定性合成夹具（synthetic=true），
  无真实系统对接；SN / 件号 / SOP 编号 / 扭矩值全为构造数据；
- **零大模型**：问答是 Week-12 冻结的门控决策树 + 抽取式替身；报告草稿是
  确定性规则聚合 + 100% 可复算的偏差计算；
- `draft_ready` = 「依据充分、可出具带引用草稿」，**不是**「合格判定」；
  报告仅供 PQE 参考核对；
- 全部工具 `effect=read_only`，外部写效果恒 0（绝不创建返修单/隔离单，
  绝不发 8D 报告）；令牌为教学模拟会话，上线前需按真实 SSO 重审权限矩阵；
- 完整口径见 `README.md`「诚实边界」与 `specs/discovery-brief-v1.md`。