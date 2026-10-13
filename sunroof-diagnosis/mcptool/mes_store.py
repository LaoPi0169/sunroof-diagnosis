"""MES 生产记录只读库（阶段 C 节点 ⑰）—— 确定性合成夹具（零真实 MES, synthetic=true）。

物理隔离: 本模块零写入口 (无 INSERT/UPDATE/DELETE/setter; 测试以 store 指纹
前后对照复验只读性质, 呼应 ci-evidence §9.3 根因 5 的教训 —— 读取/自愈类改动
以冻结物指纹前后对照为标准回归检查)。
波形为纯整数确定性生成 (mes-waveform-int-v1): 无随机、无时钟、无浮点三角函数
—— 任何平台任何时刻逐字节一致 (Linux CI / Windows 本地 / Docker 镜像三处同值)。
"""
from __future__ import annotations

import hashlib
import json

from mcptool import mes_contract as M

# 5 条合成 MES 生产记录 (oem_project 供 ACL 下推; ROOF-10003 无波形通道 -> P-3 场景)
_RECORDS = {
    "ROOF-10001": {
        "oem_project": "OEM-A",
        "assembly_torque": [
            {"step": "frame_bolt_tightening", "tool_id": "DC-NUTRUNNER-07",
             "target_nm": 9.5, "measured_nm": 9.62, "angle_deg": 41,
             "station": "ST-30", "recorded_at": "2026-03-14T08:21:05Z"},
            {"step": "rail_bolt_tightening", "tool_id": "DC-NUTRUNNER-11",
             "target_nm": 6.0, "measured_nm": 5.97, "angle_deg": 33,
             "station": "ST-32", "recorded_at": "2026-03-14T08:23:41Z"},
        ],
        "ecu_flash": {
            "ecu_part_no": "ECU-ROOF-A-2210", "sw_version": "SW-2.3.1",
            "flash_date": "2026-03-14", "calib_set": "CAL-ROOF-A-05",
            "checksum": "sha256:9f2c4a17d0e83b5c6a1f04e2d7b98c53a4e1f6b2c8d70915"
                        "34e2a6b1f8c3d04e",
        },
        "sensor_waveform": {
            "channel": "vib_sensor_ch1", "unit": "mV", "sample_rate_hz": 1024,
            "generator": "mes-waveform-int-v1",
        },
    },
    "ROOF-10002": {
        "oem_project": "OEM-A",
        "assembly_torque": [
            {"step": "frame_bolt_tightening", "tool_id": "DC-NUTRUNNER-07",
             "target_nm": 9.5, "measured_nm": 9.48, "angle_deg": 39,
             "station": "ST-30", "recorded_at": "2026-04-02T10:11:22Z"},
        ],
        "ecu_flash": {
            "ecu_part_no": "ECU-ROOF-A-2210", "sw_version": "SW-2.3.1",
            "flash_date": "2026-04-02", "calib_set": "CAL-ROOF-A-05",
            "checksum": "sha256:1b7e23d9c4a05f8637e9d1c20b48a5f6e3d7c9a0b2f45186"
                        "97c3e0a5d2f8b614",
        },
        "sensor_waveform": {
            "channel": "vib_sensor_ch1", "unit": "mV", "sample_rate_hz": 1024,
            "generator": "mes-waveform-int-v1",
        },
    },
    "ROOF-10003": {
        "oem_project": "OEM-A",
        "assembly_torque": [
            {"step": "frame_bolt_tightening", "tool_id": "DC-NUTRUNNER-07",
             "target_nm": 9.5, "measured_nm": 10.98, "angle_deg": 52,
             "station": "ST-30", "recorded_at": "2026-04-18T14:02:57Z"},
            {"step": "rail_bolt_tightening", "tool_id": "DC-NUTRUNNER-11",
             "target_nm": 6.0, "measured_nm": 6.11, "angle_deg": 35,
             "station": "ST-32", "recorded_at": "2026-04-18T14:05:19Z"},
        ],
        "ecu_flash": {
            "ecu_part_no": "ECU-ROOF-A-2210", "sw_version": "SW-2.2.0",
            "flash_date": "2026-04-18", "calib_set": "CAL-ROOF-A-04",
            "checksum": "sha256:5d30a8c1f2e6b4970d8c3a15e7f92b64c0e5d8a1f3b62740"
                        "82e9d5c1a6f0b739",
        },
        # 无 sensor_waveform 键: 波形通道缺失 (P-3 结构化缺失场景, 绝不编造)
    },
    "ROOF-20001": {
        "oem_project": "OEM-B",
        "assembly_torque": [
            {"step": "frame_bolt_tightening", "tool_id": "DC-NUTRUNNER-21",
             "target_nm": 9.5, "measured_nm": 9.55, "angle_deg": 40,
             "station": "ST-30B", "recorded_at": "2026-05-09T09:30:11Z"},
        ],
        "ecu_flash": {
            "ecu_part_no": "ECU-ROOF-B-3301", "sw_version": "SW-3.0.4",
            "flash_date": "2026-05-09", "calib_set": "CAL-ROOF-B-02",
            "checksum": "sha256:c81f0e5a3d7b2964a0c8f1d5e2b74930a6d1c8f5b3e07264"
                        "91a4d8c2e5f0a731",
        },
        "sensor_waveform": {
            "channel": "vib_sensor_ch1", "unit": "mV", "sample_rate_hz": 1024,
            "generator": "mes-waveform-int-v1",
        },
    },
    "ROOF-20002": {
        "oem_project": "OEM-B",
        "assembly_torque": [
            {"step": "rail_bolt_tightening", "tool_id": "DC-NUTRUNNER-21",
             "target_nm": 6.0, "measured_nm": 6.03, "angle_deg": 34,
             "station": "ST-32B", "recorded_at": "2026-06-21T16:44:38Z"},
        ],
        "ecu_flash": {
            "ecu_part_no": "ECU-ROOF-B-3301", "sw_version": "SW-3.0.4",
            "flash_date": "2026-06-21", "calib_set": "CAL-ROOF-B-02",
            "checksum": "sha256:e4a91b6c0d3f8527c1e6a0d4f8b35297d0a6c3e1b8f54270"
                        "63d9a5c4e1b0f826",
        },
        "sensor_waveform": {
            "channel": "vib_sensor_ch1", "unit": "mV", "sample_rate_hz": 1024,
            "generator": "mes-waveform-int-v1",
        },
    },
}


def get_record(sn: str):
    """按 SN 取静态记录 (只读; 不存在返回 None)。"""
    return _RECORDS.get(sn)


def waveform_points(sn: str):
    """确定性波形生成 (纯整数模运算, 零随机零时钟): 全量 128 点, mV 整数。

    记录不存在或该 SN 无波形通道时返回 None (P-3: 上层转结构化缺失, 不编造)。
    """
    record = _RECORDS.get(sn)
    if record is None or "sensor_waveform" not in record:
        return None
    sn_num = int(sn.split("-")[1])
    return [2048 + ((i * 37 + sn_num * 13) % 97) - 48
            for i in range(M.WAVEFORM_TOTAL_POINTS)]


def waveform_segment(sn: str, segment: int):
    """按段返回波形分页载荷 (R-2): 永不超过单段冻结上限 (32 点)。"""
    points = waveform_points(sn)
    if points is None:
        return None
    window = points[segment * M.WAVEFORM_SEGMENT_POINTS:
                    (segment + 1) * M.WAVEFORM_SEGMENT_POINTS]
    meta = _RECORDS[sn]["sensor_waveform"]
    return {
        "channel": meta["channel"], "unit": meta["unit"],
        "sample_rate_hz": meta["sample_rate_hz"], "generator": meta["generator"],
        "total_points": M.WAVEFORM_TOTAL_POINTS,
        "segment_points": M.WAVEFORM_SEGMENT_POINTS,
        "segment": segment, "points": window,
        "points_returned": len(window),
        "truncated": segment < M.WAVEFORM_SEGMENTS - 1,
        "next_segment": (segment + 1) if segment < M.WAVEFORM_SEGMENTS - 1 else None,
    }


def missing_field_reason(sn: str, field: str):
    """已知记录的字段缺失结构化原因 (P-3); 字段存在返回 None。"""
    record = _RECORDS.get(sn)
    if record is None or field in record:
        return None
    return M.FIELD_NOT_AVAILABLE


def store_fingerprint() -> str:
    """静态记录 + 波形探针 (每 SN 前 8 点) 的 sha256 —— 只读性质回归检查锚点。

    对任何代码路径 (含未来可能的"自愈/重建"改动) 前后对照, 指纹逐字节一致
    才算只读性质未被破坏 (ci-evidence §9.3 教训的标准落地)。
    """
    probe = {sn: (waveform_points(sn) or [])[:8] for sn in _RECORDS}
    canonical = json.dumps({"records": _RECORDS, "waveform_probe": probe},
                           ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
