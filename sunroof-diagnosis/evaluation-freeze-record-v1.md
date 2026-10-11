# evaluation-freeze-record-v1 —— 第 10 周评测冻结记录

- 冻结时刻: 2026-10-10（阶段 B · 节点 ⑦）；分支 `sunroof-phase-b-node07`
- 冻结边界: 以下 12 件在运行任何 RAG 候选（第 11-12 周）之前冻结；指标、门槛、
  排除规则与失败动作见 `eval-plan-v1.md` §四，变更预算 0 次。
- 哈希口径: **sha256(LF) 为内容锁定口径**（行尾不敏感；防 CRLF 搅动误报），
  sha256(raw) 为行尾检测口径（raw 与 LF 不一致只说明行尾、不说明内容变化）。

## 一、冻结物清单（12 件）

| 文件 | sha256(raw) | sha256(LF) |
| --- | --- | --- |
| `eval-plan-v1.md` | `b7041a49a7795574f0d6074049df6e000246822f8d8f06b72b9de317ff9d2771` | `b2f8976b113dae7dab3371f3e63abc358e1d85a9252b04dd4a0f7bf53ed86a14` |
| `eval-set-v1.jsonl` | `777b43d9f7c2fc0b84eb2cdaf6d8be2d4069839733fb0108884f89e5aa0a3fa7` | `7e79f59bfeb0a5bf50e991310b846fec26c8df8f48387720195de79ca910eaca` |
| `eval-holdout-labels-sealed-v1.json` | `9aea60d11320fcdb7dc1a972071e6d292a06f3c3ca1e26f42e655450cea1d557` | `3473dc68ae260e32bd12c2331c002a1a2f5f4bd27a1299246c9059d7ec7b9242` |
| `labeling-guide-v1.md` | `fdb4efa240ce73d10077f8c161c2589b210373746138c302e16fe8df62933514` | `8a898ce4098041a396a762d17629c46053ab239bc74d9103be2306da6d39ed44` |
| `dataset-card-v1.md` | `7161e3373c5aa5ef5eb23f58e01ce126426add3d19b1394000cce5629073a225` | `e1d1e5f8938a187a0fd267d03feabf5d40d467f62b7a2a3c985af2f08f394145` |
| `split-leakage-check-v1.md` | `fa57ba8229ab267f881c946471a17a2763ccadf195c8e977a29c81e005827c6a` | `47695b8a0f848fd9f94f660aacf5fb56527cbe15ceff1d51c42296ee06ad0ddb` |
| `deterministic-baseline-results-v1.jsonl` | `92687cd5ebcea3269ed89c272d1456352361bc362c7cfe7d485216926033ac9f` | `92687cd5ebcea3269ed89c272d1456352361bc362c7cfe7d485216926033ac9f` |
| `evaluation/eval_contract.py` | `579ad3236f8a7ca32786a0fd36c92633a2b71d32699d7d33fe0ecddd2ec64d09` | `c900d52c4e749bb2af0dbefddd1537b7daea3eb2933c7afd5fec8fcb60d6cfde` |
| `evaluation/validate_eval_set.py` | `4a77a19661de63775c819272f73042399b2379fca721dfd0683493531cd4de39` | `536aaf0db5f8980956829ada2a54a8258b98333e3958b40ed5d35eec2e0c4b66` |
| `evaluation/run_deterministic_baseline.py` | `326a6b0f418b775607c74635008f81a8347541198ad4737cd4d46007274d2beb` | `8a7ff1ff186ae4912563f3083cc5807ce91cb04ad513a02ecc85481755352aa3` |
| `tests/test_eval_contract.py` | `6732d90da8bb9e5ff44c92ae013766341d77864aa023a44a64015ec6ec94cf43` | `855e7ada57143b854b7df39ffcf0b03f72497b2537bd0fae9cf039244a2eb0bd` |
| `specs/mission-week-10.md` | `8fa1a2783d864c02185d2b99d4149e797ad0b15366a1cc171f340a38da236035` | `679307712cc935c2f623fbc77b402d29efbd07f9ee994f365b3912a81233cda8` |

## 二、程序化复核方法（任何人可复跑）

```powershell
python - <<'PY'
import hashlib, json
FILES = ["eval-plan-v1.md","eval-set-v1.jsonl","eval-holdout-labels-sealed-v1.json",
         "labeling-guide-v1.md","dataset-card-v1.md","split-leakage-check-v1.md",
         "deterministic-baseline-results-v1.jsonl","evaluation/eval_contract.py",
         "evaluation/validate_eval_set.py","evaluation/run_deterministic_baseline.py",
         "tests/test_eval_contract.py","specs/mission-week-10.md"]
bad = []
for f in FILES:
    data = open(f,"rb").read()
    lf = hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()
    if lf[:8] not in open("evaluation-freeze-record-v1.md",encoding="utf-8").read():
        bad.append(f)
print(json.dumps({"checked": len(FILES), "bad": bad}))
PY
```

持续把守: `tests/test_eval_contract.py` test_05（封存件 sha256(LF) 对账本记录）、
test_06（基线逐字节可复现）；第 11-12 周测试以本记录为冻结基准复核。

## 三、冻结时实测快照

- 评测集校验: verdict=VALID（60 行 / 矩阵逐格 / 60 query 唯一 / 封存边界零标签）；
- 基线探针: `sunroof-app-w08-r0`，answered 20 / needs_review 8
  （SPEC_CONFLICT 4 + NO_MATCHING_SPEC 4）/ not_applicable 20 / sealed 12，
  两次运行逐字节一致，business_effects=0；
- 工程门: 全量测试 47/47（41 → 47），本地 CI 四阶段 PASS（见 ci-evidence §6）。

## 四、勘误

（暂无。任何冻结物改动须在此登记: 改动文件、原因、新旧 sha256、重考影响面。）
