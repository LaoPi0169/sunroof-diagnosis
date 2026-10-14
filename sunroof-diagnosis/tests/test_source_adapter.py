"""
阶段 D 批次 2 三源取数适配器缝 —— 自动化集成测试

回链 specs/production-hardening-brief-v1.md D2-a/D2-b + mcptool/source_adapter.py
+ mcptool/{mes,qms,plm}_server.py + mcptool/report_aggregate.py + mcptool/report_server.py:
  01 默认注册表 = 合成夹具 (三源 Protocol 校验; 未知源 ValueError)
  02 只读证明: 经适配器缝全消费面遍历后三源 store 指纹前后逐字节一致 (D2-a 验收)
  03 委托逐字节等价: 合成适配器结果 == store 直调结果
  04 可选注入点: 换装 stub -> server/聚合反映新实现; reset 恢复; journal 留痕
  05 换装校验: 未知源 ValueError / 不满足 Protocol TypeError / 失败换装零副作用
  06 默认模式审计路径零改动: 未知 SN 仍 unknown_sn 分型 + 三因一致 not_available
  07 HTTP 默认关闭: configure_from_env({}) 零操作, 注册表仍合成
  08 http 模式缺任一端点 -> ValueError 快速失败, 注册表零变化 (绝不静默回退)
  09 http 模式三源换装 + 装配报告 + reset 恢复
  10 HttpSourceConfig 校验: scheme/timeout/retries/未知源全拒
  11 HTTP 载荷映射逐字节等价: MockTransport 回放冻结夹具 JSON -> 与 store 直调同形
  12 有限重试: 5xx 两次后 200 成功 (尝试次数 == 1+max_retries)
  13 不可达 -> SourceUnavailableError 精确带源; 非 JSON/4xx -> 立即拒 (不重试不编造)
  14 源不可达结构化拒答 (单源): 用户面 not_available + 审计 source_unavailable:mes + 零效果
  15 聚合面: mes 不可达 -> 整体 not_available + 主审计 source_unavailable:mes (绝不拼装部分合成)
  16 降级演练语义与 week-17 冻结结果一致 (drills=4 / 全 pass / forbidden=0)

运行: python -m unittest tests.test_source_adapter -v
"""
import json
import os
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import httpx  # noqa: E402

from mcptool import mes_server  # noqa: E402
from mcptool import mes_store  # noqa: E402
from mcptool import plm_store  # noqa: E402
from mcptool import qms_store  # noqa: E402
from mcptool import report_server  # noqa: E402
from mcptool import source_adapter as sa  # noqa: E402

PQE_A = {"tenant": "sunroof-training", "user": "pqe_chen_oema", "role": "pqe",
         "allowed_projects": ["OEM-A"]}
SUPERVISOR = {"tenant": "sunroof-training", "user": "supervisor_li",
              "role": "quality_supervisor", "allowed_projects": ["*"]}

STUB_SN = "ROOF-10099"
STUB_RECORD = {
    "oem_project": "OEM-C",
    "assembly_torque": [
        {"step": "frame_bolt_tightening", "tool_id": "DC-NUTRUNNER-99",
         "target_nm": 9.5, "measured_nm": 9.62, "angle_deg": 40,
         "station": "ST-99", "recorded_at": "2026-09-09T09:09:09Z"},
    ],
    "ecu_flash": {
        "ecu_part_no": "ECU-ROOF-C-9901", "sw_version": "SW-9.9.9",
        "flash_date": "2026-09-09", "calib_set": "CAL-ROOF-C-99",
        "checksum": "sha256:" + "9" * 64,
    },
    "sensor_waveform": {"channel": "vib_sensor_ch1", "unit": "mV",
                        "sample_rate_hz": 1024, "generator": "stub-int-v1"},
}


class StubMesSource:
    """测试换装 stub: 固定单记录 (证明注入缝真实生效, 与冻结 store 零接触)。"""

    def get_record(self, sn: str):
        return dict(STUB_RECORD) if sn == STUB_SN else None

    def waveform_segment(self, sn: str, segment: int):
        return None

    def missing_field_reason(self, sn: str, field: str):
        return None if field in STUB_RECORD else "FIELD_NOT_AVAILABLE"


def fixture_transport(store_module, segment_fn=None):
    """MockTransport: 把冻结 store 夹具作为 HTTP 载荷回放 (骨架语义同形验证)。"""

    def handler(request: httpx.Request) -> httpx.Response:
        parts = request.url.path.strip("/").split("/")
        if len(parts) == 3 and segment_fn is not None:
            payload = segment_fn(parts[0], int(parts[2]))
            return (httpx.Response(200, json=payload) if payload is not None
                    else httpx.Response(404))
        record = store_module.get_record(parts[0])
        return (httpx.Response(200, json=record) if record is not None
                else httpx.Response(404))

    return httpx.MockTransport(handler)


def constant_transport(status_code: int, counter: list, body: str = "json"):
    """MockTransport: 恒定状态码/载荷 (计数尝试次数, 供重试语义断言)。"""

    def handler(request: httpx.Request) -> httpx.Response:
        counter.append(1)
        if body == "json":
            return httpx.Response(status_code, json={"stub": True})
        return httpx.Response(status_code, text=body)

    return httpx.MockTransport(handler)


def failing_transport(counter: list, exc: Exception):
    """MockTransport: 每次请求抛传输层异常 (连接拒绝/超时类)。"""

    def handler(request: httpx.Request) -> httpx.Response:
        counter.append(1)
        raise exc

    return httpx.MockTransport(handler)


class TestSourceAdapterSeam(unittest.TestCase):
    """D2-a: 三源取数协议 + 注册表 + 默认合成实现逐字节等价。"""

    @staticmethod
    def _fingerprints():
        return (mes_store.store_fingerprint(), qms_store.store_fingerprint(),
                plm_store.store_fingerprint())

    def test_01_default_registry_is_synthetic(self):
        self.assertIsInstance(sa.resolve("mes"), sa.SyntheticMesSource)
        self.assertIsInstance(sa.resolve("qms"), sa.SyntheticQmsSource)
        self.assertIsInstance(sa.resolve("plm"), sa.SyntheticPlmSource)
        # 协议结构校验 (runtime_checkable Protocol)
        self.assertIsInstance(sa.resolve("mes"), sa.MesSource)
        self.assertIsInstance(sa.resolve("qms"), sa.QmsSource)
        self.assertIsInstance(sa.resolve("plm"), sa.PlmSource)
        with self.assertRaises(ValueError):
            sa.resolve("erp")

    def test_02_fingerprints_byte_identical_via_adapter(self):
        """D2-a 验收: 全消费面经适配器缝后, 三源 store 指纹逐字节一致。"""
        fps = self._fingerprints()
        mes_a, qms_a, plm_a = sa.resolve("mes"), sa.resolve("qms"), sa.resolve("plm")
        for sn in ("ROOF-10001", "ROOF-10002", "ROOF-10003", "ROOF-20001",
                   "ROOF-20002", "ROOF-99999"):
            mes_a.get_record(sn)
            for field in ("assembly_torque", "ecu_flash", "sensor_waveform"):
                mes_a.missing_field_reason(sn, field)
            for segment in range(4):
                mes_a.waveform_segment(sn, segment)
        for part in ("SR-FRAME-A01", "SR-GLASS-A01", "SR-MOTOR-A02",
                     "SR-RAIL-B01", "SR-XXXX-A99"):
            qms_a.get_record(part)
            plm_a.get_record(part)
            plm_a.eco_window(part, 0)
            plm_a.eco_window(part, 4)
            plm_a.missing_field_reason(part, "bom_snapshot")
            for field in ("sop_current", "quality_standard", "inspection_checklist"):
                rows = qms_a.field_rows(part, field)
                qms_a.current_rows(rows)
                qms_a.missing_field_reason(part, field)
        self.assertEqual(self._fingerprints(), fps,
                         "只读性质被破坏: 经适配器缝的三源 store 指纹变化")

    def test_03_synthetic_delegation_byte_equal(self):
        mes_a, qms_a, plm_a = sa.resolve("mes"), sa.resolve("qms"), sa.resolve("plm")
        self.assertEqual(mes_a.get_record("ROOF-10001"),
                         mes_store.get_record("ROOF-10001"))
        self.assertIsNone(mes_a.get_record("ROOF-99999"))
        self.assertEqual(mes_a.waveform_segment("ROOF-10001", 0),
                         mes_store.waveform_segment("ROOF-10001", 0))
        self.assertIsNone(mes_a.waveform_segment("ROOF-10003", 0),
                          "P-3: 无波形通道 -> None (上层转结构化缺失)")
        self.assertEqual(mes_a.missing_field_reason("ROOF-10003", "sensor_waveform"),
                         mes_store.missing_field_reason("ROOF-10003", "sensor_waveform"))
        self.assertEqual(qms_a.field_rows("SR-MOTOR-A02", "quality_standard"),
                         qms_store.field_rows("SR-MOTOR-A02", "quality_standard"))
        self.assertEqual(
            qms_a.current_rows(qms_a.field_rows("SR-FRAME-A01", "sop_current")),
            qms_store.current_rows(qms_store.field_rows("SR-FRAME-A01", "sop_current")))
        self.assertEqual(plm_a.eco_window("SR-FRAME-A01", 4),
                         plm_store.eco_window("SR-FRAME-A01", 4))


    def test_04_register_inject_and_reset(self):
        self.addCleanup(sa.reset_registry)
        fps = self._fingerprints()
        sa.register("mes", StubMesSource(), actor="test", reason="D2 注入演练")
        sink = []
        resp = mes_server.lookup_production_record(
            SUPERVISOR, {"sn": STUB_SN, "fields": ["assembly_torque", "ecu_flash"]},
            audit_sink=sink, run_id="run-stub")
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["result"]["oem_project"], "OEM-C")
        self.assertEqual(resp["result"]["fields"]["assembly_torque"]["payload"]["records"],
                         STUB_RECORD["assembly_torque"])
        self.assertEqual([e["reason_typed"] for e in sink], ["ok"])
        self.assertEqual([e["external_effects"] for e in sink], [0])
        # 聚合面同走注入缝 (主管跨项目上下文; qms/plm 仍默认合成)
        resp2 = report_server.draft_root_cause(
            SUPERVISOR, {"sn": STUB_SN, "part_no": "SR-MOTOR-A02",
                         "process_step": "torque_tightening"},
            audit_sink=[], run_id="run-stub-report")
        self.assertTrue(resp2["ok"])
        self.assertEqual(resp2["result"]["report"]["case"]["oem_project"], "OEM-C")
        # 复位恢复默认: ROOF-10099 回到三因一致 not_available
        sa.reset_registry()
        resp3 = mes_server.lookup_production_record(
            SUPERVISOR, {"sn": STUB_SN, "fields": ["assembly_torque"]})
        self.assertEqual(resp3, {"ok": False, "error": {"code": "not_available"}})
        journal = sa.registry_journal()
        self.assertEqual(journal[-1]["source"], "mes")
        self.assertEqual(journal[-1]["adapter_class"], "StubMesSource")
        self.assertEqual(journal[-1]["actor"], "test")
        self.assertEqual(self._fingerprints(), fps, "stub 注入零接触冻结 store")

    def test_05_register_validation(self):
        self.addCleanup(sa.reset_registry)
        with self.assertRaises(ValueError):
            sa.register("erp", StubMesSource())
        with self.assertRaises(TypeError):
            sa.register("mes", object())
        with self.assertRaises(TypeError):
            sa.register("qms", StubMesSource())  # 缺 field_rows/current_rows
        self.assertIsInstance(sa.resolve("mes"), sa.SyntheticMesSource,
                              "失败换装零副作用")

    def test_06_default_audit_paths_unchanged(self):
        sink = []
        resp = mes_server.lookup_production_record(
            PQE_A, {"sn": "ROOF-99999", "fields": ["assembly_torque"]},
            audit_sink=sink, run_id="run-unknown")
        self.assertEqual(resp, {"ok": False, "error": {"code": "not_available"}})
        self.assertEqual([e["reason_typed"] for e in sink], ["unknown_sn"],
                         "默认合成模式: 既有细分分型不变 (新 except 分支不触发)")
        self.assertEqual([e["external_effects"] for e in sink], [0])


class TestHttpSourceAdapterSkeleton(unittest.TestCase):
    """D2-b: HTTP 适配器骨架 —— 默认关闭 / 配置驱动 / 有限重试 / 绝不编造。"""

    @staticmethod
    def _fingerprints():
        return (mes_store.store_fingerprint(), qms_store.store_fingerprint(),
                plm_store.store_fingerprint())

    def test_07_http_default_off(self):
        report = sa.configure_from_env({})
        self.assertEqual(report, {"mode": "synthetic", "registered": [],
                                  "endpoints": {}})
        self.assertIsInstance(sa.resolve("mes"), sa.SyntheticMesSource,
                              "无 SUNROOF_SOURCE_MODE 时零操作 (默认关闭)")
        self.assertIsInstance(sa.resolve("qms"), sa.SyntheticQmsSource)
        self.assertIsInstance(sa.resolve("plm"), sa.SyntheticPlmSource)

    def test_08_http_mode_missing_endpoint_fails_fast(self):
        self.addCleanup(sa.reset_registry)
        env = {"SUNROOF_SOURCE_MODE": "http",
               "SUNROOF_SOURCE_MES_URL": "http://mes.test/api"}
        with self.assertRaises(ValueError):
            sa.configure_from_env(env)
        # 快速失败零副作用: 注册表仍是合成 (绝不部分换装/静默回退)
        self.assertIsInstance(sa.resolve("mes"), sa.SyntheticMesSource)
        self.assertIsInstance(sa.resolve("qms"), sa.SyntheticQmsSource)
        self.assertIsInstance(sa.resolve("plm"), sa.SyntheticPlmSource)

    def test_09_http_mode_wires_three_adapters(self):
        self.addCleanup(sa.reset_registry)
        env = {"SUNROOF_SOURCE_MODE": "http",
               "SUNROOF_SOURCE_MES_URL": "http://mes.test/api/",
               "SUNROOF_SOURCE_QMS_URL": "https://qms.test/api",
               "SUNROOF_SOURCE_PLM_URL": "http://plm.test/api",
               "SUNROOF_SOURCE_TIMEOUT_S": "1.5",
               "SUNROOF_SOURCE_MAX_RETRIES": "1"}
        report = sa.configure_from_env(env)
        self.assertEqual(report["mode"], "http")
        self.assertEqual(report["registered"], ["mes", "qms", "plm"])
        self.assertEqual(report["endpoints"]["mes"], "http://mes.test/api")
        for source in ("mes", "qms", "plm"):
            self.assertIsInstance(sa.resolve(source), sa.HttpSourceAdapter)
        self.assertEqual(sa.resolve("mes").config.timeout_s, 1.5)
        self.assertEqual(sa.resolve("mes").config.max_retries, 1)
        sa.reset_registry()
        self.assertIsInstance(sa.resolve("plm"), sa.SyntheticPlmSource,
                              "reset 恢复默认合成实现")

    def test_10_config_validation(self):
        with self.assertRaises(ValueError):
            sa.HttpSourceConfig(source="mes", base_url="ftp://mes.test")
        with self.assertRaises(ValueError):
            sa.HttpSourceConfig(source="mes", base_url="http://mes.test",
                                timeout_s=0)
        with self.assertRaises(ValueError):
            sa.HttpSourceConfig(source="mes", base_url="http://mes.test",
                                max_retries=6)
        with self.assertRaises(ValueError):
            sa.HttpSourceConfig(source="erp", base_url="http://erp.test")

    def test_11_http_payload_mapping_byte_equal_to_store(self):
        """骨架语义同形: MockTransport 回放冻结夹具 JSON -> 与 store 直调逐字节等价。"""
        fps = self._fingerprints()
        mes_h = sa.HttpSourceAdapter(
            sa.HttpSourceConfig("mes", "http://mes.test"),
            transport=fixture_transport(mes_store, mes_store.waveform_segment))
        qms_h = sa.HttpSourceAdapter(
            sa.HttpSourceConfig("qms", "http://qms.test"),
            transport=fixture_transport(qms_store))
        plm_h = sa.HttpSourceAdapter(
            sa.HttpSourceConfig("plm", "http://plm.test"),
            transport=fixture_transport(plm_store))
        for sn in ("ROOF-10001", "ROOF-10003", "ROOF-99999"):
            self.assertEqual(mes_h.get_record(sn), mes_store.get_record(sn))
            self.assertEqual(mes_h.waveform_segment(sn, 0),
                             mes_store.waveform_segment(sn, 0))
            self.assertEqual(mes_h.waveform_segment(sn, 3),
                             mes_store.waveform_segment(sn, 3))
            for field in ("assembly_torque", "ecu_flash", "sensor_waveform"):
                self.assertEqual(mes_h.missing_field_reason(sn, field),
                                 mes_store.missing_field_reason(sn, field))
        for part in ("SR-FRAME-A01", "SR-MOTOR-A02", "SR-RAIL-B01", "SR-XXXX-A99"):
            self.assertEqual(qms_h.get_record(part), qms_store.get_record(part))
            self.assertEqual(plm_h.eco_window(part, 0), plm_store.eco_window(part, 0))
            self.assertEqual(plm_h.eco_window(part, 4), plm_store.eco_window(part, 4))
            for field in ("sop_current", "quality_standard", "inspection_checklist"):
                self.assertEqual(qms_h.field_rows(part, field),
                                 qms_store.field_rows(part, field))
                self.assertEqual(qms_h.missing_field_reason(part, field),
                                 qms_store.missing_field_reason(part, field))
            self.assertEqual(plm_h.missing_field_reason(part, "bom_snapshot"),
                             plm_store.missing_field_reason(part, "bom_snapshot"))
        self.assertEqual(self._fingerprints(), fps)


    def test_12_limited_retry_then_success(self):
        attempts = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            if len(attempts) < 3:
                return httpx.Response(503, text="overloaded")
            return httpx.Response(200, json=mes_store.get_record("ROOF-10001"))

        adapter = sa.HttpSourceAdapter(
            sa.HttpSourceConfig("mes", "http://mes.test", max_retries=2),
            transport=httpx.MockTransport(handler))
        self.assertEqual(adapter.get_record("ROOF-10001"),
                         mes_store.get_record("ROOF-10001"))
        self.assertEqual(len(attempts), 3, "尝试次数 == 1 + max_retries (有限重试)")

    def test_13_unreachable_typed_error_and_non_json(self):
        attempts = []
        adapter = sa.HttpSourceAdapter(
            sa.HttpSourceConfig("mes", "http://mes.test", max_retries=2),
            transport=constant_transport(503, attempts))
        with self.assertRaises(sa.SourceUnavailableError) as ctx:
            adapter.get_record("ROOF-10001")
        self.assertEqual(ctx.exception.source, "mes")
        self.assertEqual(len(attempts), 3, "5xx 重试耗尽 -> 精确分型异常")
        # 非 JSON 200: 不重试不编造
        hits = []
        adapter2 = sa.HttpSourceAdapter(
            sa.HttpSourceConfig("qms", "http://qms.test", max_retries=3),
            transport=constant_transport(200, hits, body="not-json"))
        with self.assertRaises(sa.SourceUnavailableError) as ctx2:
            adapter2.get_record("SR-FRAME-A01")
        self.assertEqual(ctx2.exception.source, "qms")
        self.assertEqual(len(hits), 1, "非 JSON 载荷不重试")
        # 连接级失败 (transport error) 同样转分型异常
        adapter3 = sa.HttpSourceAdapter(
            sa.HttpSourceConfig("plm", "http://plm.test", max_retries=1),
            transport=failing_transport(
                [], httpx.ConnectError("connection refused")))
        with self.assertRaises(sa.SourceUnavailableError) as ctx3:
            adapter3.get_record("SR-FRAME-A01")
        self.assertEqual(ctx3.exception.source, "plm")
        # 其余 4xx: 不重试, 立即结构化拒绝
        hits4 = []
        adapter4 = sa.HttpSourceAdapter(
            sa.HttpSourceConfig("mes", "http://mes.test", max_retries=2),
            transport=constant_transport(403, hits4))
        with self.assertRaises(sa.SourceUnavailableError):
            adapter4.get_record("ROOF-10001")
        self.assertEqual(len(hits4), 1)

    def test_14_http_unreachable_structured_refusal_single_source(self):
        self.addCleanup(sa.reset_registry)
        sa.register("mes", sa.HttpSourceAdapter(
            sa.HttpSourceConfig("mes", "http://mes.test", max_retries=1),
            transport=constant_transport(503, [])))
        sink = []
        resp = mes_server.lookup_production_record(
            PQE_A, {"sn": "ROOF-10001", "fields": ["assembly_torque"]},
            audit_sink=sink, run_id="run-http-down")
        self.assertEqual(resp, {"ok": False, "error": {"code": "not_available"}},
                         "用户面三因一致封套 (P-1 零存在性泄漏)")
        self.assertEqual([e["reason_typed"] for e in sink],
                         ["source_unavailable:mes"])
        self.assertEqual([e["external_effects"] for e in sink], [0])

    def test_15_report_refusal_no_partial_synthetic_assembly(self):
        self.addCleanup(sa.reset_registry)
        sa.register("mes", sa.HttpSourceAdapter(
            sa.HttpSourceConfig("mes", "http://mes.test", max_retries=1),
            transport=constant_transport(503, [])))
        sink = []
        resp = report_server.draft_root_cause(
            SUPERVISOR, {"sn": "ROOF-10001", "part_no": "SR-FRAME-A01",
                         "process_step": "torque_tightening"},
            audit_sink=sink, run_id="run-http-down")
        self.assertEqual(resp, {"ok": False, "error": {"code": "not_available"}},
                         "整体结构化拒答: 绝不拼装部分合成数据冒充三源报告")
        main = [e for e in sink if not e["run_id"].endswith((":mes", ":qms", ":plm"))]
        self.assertEqual(main[-1]["reason_typed"], "source_unavailable:mes",
                         "沿用既有 source_unavailable 精细分型口径")
        nested = {e["tool"] for e in sink if e["run_id"].endswith(":mes")}
        self.assertEqual(nested, {"mes.lookup_production_record"})
        self.assertTrue(all(e["external_effects"] == 0 for e in sink))

    def test_16_week17_drill_frozen_semantics(self):
        from mcptool.run_degradation_drill import run_drill
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "drill-d2.jsonl")
            report = run_drill(out)
            with open(out, encoding="utf-8") as f:
                rows = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(report["drills"], 4)
        self.assertEqual(report["forbidden"], 0)
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(r["pass"] for r in rows), rows)
        self.assertEqual([r["drill_id"] for r in rows],
                         ["D-01", "D-02", "D-03", "D-04"],
                         "降级演练剧本与 week-17 冻结结果一致")


if __name__ == "__main__":
    unittest.main()