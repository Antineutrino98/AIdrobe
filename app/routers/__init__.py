"""FastAPI 路由层（M2：入库接口）。

按 AIdrobe.md：路由函数只做参数校验和调用 service，
不直接写 SQL、不直接调用 LLM、不做全局 app 组装。
"""
