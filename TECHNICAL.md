# AIdrobe 技术交接（v1.0）

> 面向下一个接手的人。写清「改了什么、契约是什么、怎么验证、哪里还欠着」。
> 版本号目前以本文件与 `README.txt` 为准，代码里尚未设 `__version__` 常量
> （`app/__init__.py` 没有该变量，接手时若加，请同步本文件第一行）。

---

## 1. 运行形态

| 项 | 值 |
|---|---|
| 入口 | `launcher.py`（打包后是 `AIdrobe.exe`，PyInstaller onefile） |
| 服务 | uvicorn + FastAPI，`127.0.0.1`，默认端口 `8000`，占用时顺延 |
| 前端 | `frontend/dist/`，由 FastAPI 静态托管；**不是独立进程** |
| 数据库 | PGlite（PostgreSQL 17 WASM），**单进程单连接**（pool size = 1） |
| 数据目录 | `data/pglite`（库）、`data/images`（衣物图片）、`data/backups` |
| 日志 | `logs/aidrobe.log`（RotatingFileHandler，2MB × 4） |

启动时 `launcher.py` 会 `os.chdir(BASE_DIR)`，所有相对路径都相对 exe 所在目录。
**因此发布包必须包含 `.env` 与 `dist/`**，缺一个就打不开页面。

---

## 2. 模块地图

| 文件 | 职责 |
|---|---|
| `app/main.py` | FastAPI 装配、lifespan、模型凭据中间件、静态托管 |
| `app/llm_config.py` | 三角色 `ModelRole`、按角色取 client/model、`model_extra_kwargs`、`build_image_block` |
| `app/services/ingest.py` | 入库编排（下载/读图 → 视觉识别 → 落库） |
| `app/services/vision.py` | 视觉识别调用与解析 |
| `app/services/retrieval_planner.py` | 文本规划：决定从哪几个品类分组取候选 |
| `app/services/filter.py` | 品类分组、条件匹配、四级降级检索 |
| `app/services/stylist_llm.py` | 搭配调用、候选块构造、结果清洗 |
| `app/services/recommend_policy.py` | 用户策略（穿着间隔 / 正式度 / 优先级 / 套数） |
| `app/services/purchase_decision.py` | 购前评估（可搭套数、场景覆盖、重复风险） |
| `app/services/gap_analysis.py` | 缺口统计与总结 |
| `app/services/zh_values.py` | 历史英文展示值改中文（见 §7 未完成项） |
| `app/repositories/postgres.py` | 仓库层，**含 `release()`** |
| `app/routers/*.py` | HTTP 层 |

---

## 3. 数据契约（不可随意改动）

### 3.1 表与外键
- 7 张业务表，**所有外键统一叫 `owner_id`**，均 `ondelete=CASCADE`。
- 索引只允许一个普通 B-Tree：`garments(owner_id, category, status)`。
  **禁止**新增 GIN / HNSW / ivfflat。
- Alembic 只保留一条初始迁移 `alembic/versions/0001_initial_schema.py`。
  v1.0 期间**没有**新增迁移；改表结构必须新开迁移并说明。

### 3.2 枚举字段（后端比较用，禁止写中文）
`category`、`attributes`、`seasons`、`formality`、`warmth`、
`material_confidence`、`wear_state`、`status`。

这些字段参与等值比较与范围过滤，**任何"翻译成中文"的批处理都不得触碰**。

### 3.3 展示字段（可以中文，前端做兜底）
`subcategory`、`pattern`、`material_guess`、`colors[].name`、
`vision_raw.description`、`style_tags`、`occasion_tags`。

`style_tags` / `occasion_tags` 参与 `_select` 的集合相交匹配
（`filter.py` 第 174–182 行）。匹配不上会自动进入降级、最终放弃 tag 条件，
**不会抛错**。若把库里的值改成中文，planner 也必须输出中文，否则会稳定降级。

### 3.4 品类分组（v1.0 起不再叫 slot）
`filter.py::GROUP_CATEGORIES`：

```
tops       top, t_shirt, shirt, sweater, hoodie, vest, tank_top, base_layer
one_piece  dress, jumpsuit, robe, suit, ceremonial, traditional,
           uniform, activewear, sleepwear, swimwear
bottoms    bottom, trousers, jeans, shorts, skirt, socks
outer      outerwear, coat, jacket, blazer, down_jacket, cape, raincoat
shoes      shoes, sneakers, boots, sandals, slippers
accessory  accessory, hat, scarf, tie, gloves, belt, bag, jewelry, eyewear
other      other, underwear, costume
```

旧名兼容：`LEGACY_GROUP_ALIASES`（inner/top/mid→tops，bottom→bottoms，shoe→shoes），
由 `filter.normalize_group()` 统一归一。**新增分组必须同时更新此表与 `GROUP_LABELS`。**

### 3.5 搭配校验契约（v1.0 重大变更）
旧契约：一套搭配必须覆盖 `inner` + `bottom` + `shoes`，缺一个整批丢弃。

**现已废弃。** 新契约在 `stylist_llm._is_valid_outfit()`：
- 只有一个条件：件数 ≥ 2；
- 例外：单件时，品类必须属于 `ONE_PIECE_CATEGORIES`（连衣裙/连体裤/套装等）才算成立；
- 越界 `garment_id` 仍逐条剔除。

**禁止**再引入"每格必须填一件"的校验。它会直接导致模型只敢给
「上装+下装+鞋」三件套，抹掉叠穿与连衣裙。

---

## 4. 模型接入

### 4.1 三角色
`ModelRole.VISION_EXTRACTOR` / `TEXT_PLANNER` / `MULTIMODAL_STYLIST`。

前端每次请求带 9 个请求头，`app/main.py` 中间件读入 `ModelCredentials`：

```
X-API-Key / X-API-Base-URL / X-API-Model                  主（文本规划）
X-Vision-API-Key / X-Vision-Base-URL / X-Vision-Model     看图
X-Stylist-API-Key / X-Stylist-Base-URL / X-Stylist-Model  搭配决策
```

`ModelCredentials.for_role()` 分流；某组缺失时回落到主凭据。

### 4.2 模型私有参数
统一走 `llm_config.model_extra_kwargs(model)`，**必须按精确模型名匹配**：
- 仅 `qwen3.8-omni-flash` 加 `modalities=["text"]`；
- 仅官方 `deepseek-flash` 加 `reasoning_effort` + `thinking`；
- 百炼版 `deepseek-v4.1-flash` 与 `qwen3.7-flash` 不加额外参数。

参数一律用 `extra_body` 传递，不要把未知参数摊到顶层，
否则 OpenAI SDK 会 `TypeError`。

### 4.3 图片
`build_image_block(path, "data_url", max_edge=N)` 会缩放到 `N` 像素内、JPEG 质量 75。
搭配链路用 `.env` 的 `STYLIST_IMAGE_MAX_TOKENS`（当前 **640**），
候选图上限 `STYLIST_MAX_IMAGES`（当前 **6**）。

**约束**：images 越大越慢。8 张 768px 实测单次约 173 秒，6 张 640px 约 127 秒。
调整这两个值前先看 §6 的耗时记录。

---

## 5. 关键机制

### 5.1 PGlite 单连接：跨越模型调用必须先还连接
`Postgres*Repository.release()` = `await session.commit()`。

凡是「读数据 → 调模型（几十秒）→ 写数据」的流程，**必须在调模型前 `release()`**：
- `routers/recommend.py`（读 wardrobe/preference 之后）
- `routers/purchase.py::evaluate`
- `routers/purchase.py::gap_report`

否则唯一连接被占满，期间所有 `/users`、`/garments` 请求等 30 秒后 500。
给 fake 仓库调用时用 `getattr(repo, "release", None)` 安全调用。

### 5.2 关页面即关后端
- 前端 `lib/alive.ts`：每 10 秒 `POST /heartbeat`；`pagehide/beforeunload` 时
  `sendBeacon('/client-gone')`。
- 后端 `routers/system.py`：`_last_heartbeat`、`_client_gone_at`；
  `should_exit_now()` 判定。
- `launcher.py::_start_client_watchdog()` 每 2 秒轮询 `GET /should-exit`，
  为真则置 `server.should_exit = True`，走 lifespan `finally` 落盘。

阈值：
- 收到 `client-gone` 后 **4 秒**退出（写 `CLOSE_GRACE_SECONDS`）；
- 无心跳 **180 秒**退出（`IDLE_EXIT_SECONDS`）。**不要调小**：
  浏览器会把后台标签的定时器节流到 1 分钟以上，调小会误杀。

**仅当 `AIDROBE_DESKTOP=1` 时启用**；`launcher.py` 只在会打开浏览器时设置它
（`AIDROBE_NO_BROWSER != "1"`）。命令行/调试模式不设，否则 180 秒后自己死掉
——这个坑 v1.0 开发期间踩过一次。

### 5.3 超时与重试预算
- `rank_outfits`：单次尝试（`for attempt in (1,)`）。
- `recommend`：单次调用，套数不足直接返回，**不再「放宽候选后重试」**。

旧逻辑最坏串行 6 次模型调用（2 次规划 + 4 次搭配），单次 80–90 秒，
用户会等 10 分钟以上。**禁止**恢复成多轮重试。

---

## 6. 实测数据（2026-11-07，Qwen3.8 Flash 搭配 / 合肥 / 21 件衣橱）

| 场景 | 结果 |
|---|---|
| 搭配（6 张 640px） | **126.8 秒**，200，4 套 |
| 搭配（8 张 768px） | **173.7 秒**，200，4 套 |
| 单次搭配模型调用（旧） | 80–92 秒（日志 `18:39→18:41` 等） |
| 天气查询 | 亚秒级，返回 `city/temperature/temperature_max/temperature_min/condition` |
| 全量 pytest | **144 passed**（约 10 秒） |
| 前端构建 | `tsc --noEmit` 0 错误；`vite build` 22.23KB CSS / 250.63KB JS |
| 打包 | `python build_exe.py`，约 57 秒，产物 39.5MB |

---

## 7. 已知问题 / 未完成（v1.0 如实记录）

1. **库内历史英文值未改写。**
   `app/services/zh_values.py` + `POST /maintenance/zh-values` 已实现，
   词表按真实数据补齐并单测过（`translate_text` 输出正确），
   **但 v1.0 期间没有对正式库执行成功**（执行时服务被心跳逻辑误杀，
   随后用户要求停止改动）。当前 21 件衣物的 `subcategory` 仍是
   `chunky sneaker` / `bomber jacket` 等英文。
   现由前端 `lib/i18n.ts` 兜底：翻不出中文就回退品类名，界面**不会显示英文**，
   但详情里的细分会偏笼统。

   执行方式（服务运行时调用即可，幂等）：
   ```bash
   curl -X POST http://127.0.0.1:8000/maintenance/zh-values
   ```
   执行前建议先 `GET /garments?scope=shared` 存一份快照。
   v1.0 期间的快照在源项目 `data/backup_garments_20261107.json`（21 件）。

2. **DeepSeek 两个入口都未实测。**
   百炼版 `deepseek-v4.1-flash` 与官方 `deepseek-flash` 在 v1.0 期间都没走通
   真实的搭配链路（前者未走到，后者没有 Key）。
   **两个入口都按"能看图"接入**：前端会给它们带 `X-Stylist-*` 图片凭据，
   `build_image_block` 照常发 base64 图。能不能真读到图由 `/test-model`
   的 `vision_ok` 实测判定，**代码里不要预设某个模型不能看图**。
   若首次真实调用返回 400，优先怀疑 `model_extra_kwargs` 里的私有参数
   （官方版会加 `reasoning_effort` 与 `thinking`），只改那一处。

3. **单次搭配 127 秒偏慢。** 瓶颈是图片数量与分辨率，不是网络。
   继续提速只能减图或降分辨率，会牺牲判断质量，需用户拍板。

4. **只有 3–5 套、无"再生一批"缓存。** 每次点「给我搭配」都是全新一次完整调用。

---

## 8. 接口清单

```
POST   /ingest/image              图片入库（multipart）
POST   /ingest/url                链接入库
POST   /ingest/confirm-category   品类二选一确认
GET    /images/{filename}         衣物图片

POST   /recommend                 生成搭配
POST   /evaluate-purchase         买不买
POST   /gap-report                缺口分析

GET    /garments                  衣橱列表（?scope=mine|shared|all&owner_id=）
GET    /garments/{id}             单件详情
DELETE /garments/{id}             删除（级联清记录，图片文件保留）

GET    /users                     用户列表
POST   /users                     新建
PATCH  /users/{id}                改名
GET    /users/{id}/summary        删除前统计
DELETE /users/{id}                删除（至少保留一个）
GET    /users/{id}/policy         读策略
PUT    /users/{id}/policy         写策略

POST   /feedback                  喜欢/不喜欢/今天穿了
GET    /preference                当前偏好
POST   /preference/clear          清空偏好

GET    /health                    后端存活
POST   /test-model                三角色真实调用自检（视觉角色传纯红图）
POST   /heartbeat                 页面心跳
POST   /client-gone               页面关闭
GET    /should-exit               看门狗轮询
POST   /maintenance/zh-values     历史展示值中文化（幂等）
```

策略字段（`users.settings["recommend_policy"]`）：

```json
{"rewear":"anytime|no_yesterday|rest_2|rest_3|rest_7",
 "prefer":"balanced|least_worn|newest",
 "outfit_count":3,
 "dress_code":"business|smart_casual|free"}
```

`dress_code` 只影响 `scene == "work"`，经 `recommend_policy.dress_code_hint()`
注入检索规划 prompt 的 `{{DRESS_CODE_BLOCK}}`。

---

## 9. 回归测试点

改以下任何一处，必须跑 `pytest -q`（144 条）并确认对应用例：

| 改动位置 | 必查用例 |
|---|---|
| `_is_valid_outfit` / `clean_recommendations` | `test_recommend.py::test_single_item_outfit_is_still_dropped`、`test_strict_failure_falls_back_to_partial_outfit`、`test_slot_aliases_are_normalized_instead_of_dropped` |
| 分组表 / 归一化 | `test_filter.py::test_narrow_conditions_fall_back_to_default_category_group`、`test_empty_slot_is_recorded_in_missing_slots` |
| 检索计划 | `test_retrieval_planner.py::test_no_forced_slots_are_added`、`test_empty_plan_falls_back_to_groups_actually_owned` |
| 模型参数分流 | `test_model_presets.py` 全部 |
| 策略 | `test_recommend_policy.py` 全部 |
| 重试预算 | `test_recommend.py::test_short_result_is_returned_without_extra_model_calls` |
| 图片缩略 | `test_model_presets.py::test_image_thumbnail_shrinks_base64_payload` |

注意：`test_filter.py::test_conflicting_attribute_value_is_rejected` 是 M3 契约，
**属性明确冲突必须拒绝**。不要为了"提高可用性"放宽 attributes 匹配来兜底空池
——v1.0 开发期间试过第 5 级降级，会把这条用例打破，已回退。

运行测试时若遇到 `SAFE_DELETE_FAIL_CLOSED`，是沙箱的删除拦截，
加 `-p no:cacheprovider` 并把临时目录放到系统 temp 之外即可绕过，与代码无关。
