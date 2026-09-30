"""Week 05 确定性垂直薄片 —— 模块化单体应用包。

分层结构 (Modular Monolith):
- models.py    DTO / Pydantic Schema 契约层
- database.py  数据访问层 (SQLite 连接 / 初始化 / 确定性 SQL / 审计落盘)
- services.py  业务逻辑层 (100% 确定性规则引擎，零大模型调用)
- main.py      API 网关层 (FastAPI 路由 / 服务端可信鉴权)
"""
