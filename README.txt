AIdrobe —— 你的个人本地智能衣橱

====================================================
AIdrobe 是一个本地运行的衣橱管理与穿搭推荐工具，由 Neutrino 于其攻读 PhD 的闲
暇时间开发，献给 Cindy 及具有同类需求的用户。

AIdrobe 由 AI 与 wardrobe 两词拼合而成，其特点是能够反复调用AI进行交互和管理，
从入库、穿搭决策到购买建议，全都有 AI 进行深度参与，因此 AIdrobe 不是传统的软
件，而是跑在本地的高可用 Agentic Workflow（智能体工作流），它是一个不仅懂分
类存储，更懂「你在当时环境下面临什么决策」的家庭共用数字衣橱。

开发者深知衣物入库将是极大的使用门槛，因此一方面 AIdrobe 简化了入库流程（一张
照片即可一键入库），另一方面竭尽所能提升了推荐的质量，尽可能能用、好用、易用。

祝每个人都能方便快捷地穿上满意的搭配！
====================================================

它针对三类具体问题：

1. 衣橱规模增长后单品可见性下降，容易出现重复购置与长期闲置；
2. 日常着装需同时权衡天气、场合、正式度等多重约束，决策成本高；
3. 购前缺少对「可与现有衣橱组合的方案数量」的量化判断，易产生冗余单品。

AIdrobe对应提供三项能力：
1. 单品结构化入库并展示；
2. 在具体场景和个人偏好约束下生成成套搭配；
3. 购前评估可组合方案数与重复风险。

程序完全在本地运行，不需要联网注册或云端账号，数据全部保存在本机 data 目录。

怎么用
--------------------------------------------------------------------

【配置说明】API Key 保存在程序目录下的 .env 中，升级版本不会清除；
首次使用请在「设置 → 模型设置 / 测试连接」填写一次，之后一直有效。

1. 双击 AIdrobe.exe。程序会在后台启动服务，并弹出独立的桌面窗口，
   不再打开浏览器，也没有命令行黑框。
2. 第一次使用：点右上角齿轮打开「设置」→「模型设置 / 测试连接」，
   粘贴 API Key、为三个角色选模型，点「测试连接」确认都正常，然后保存。
3. 「我的衣橱」右下角「添加衣物」上传衣服照片；「穿搭推荐」选场景和城市，
   点「给我搭配」。「首页」看今日概览和最近入库，「购前决策」评估要不要买。
4. 顶栏只保留用户切换和设置齿轮；衣橱范围、用户管理、推荐策略、请求日志
   都在设置抽屉里。

点窗口右上角 X 后，窗口会先最小化到系统托盘，并提示「正在保存衣橱数据并
安全退出...」；数据保存完程序才完全退出，请等候几秒，这不是卡死。
托盘图标右键也可以直接「彻底退出」。更细的用法见同目录的 guidance.html。


这个文件夹里有什么
--------------------------------------------------------------------

AIdrobe.exe     主程序（双击运行，程序名不要改）
.env            模型与数据库配置。发布包内为空 Key 版本，首次启动后到
                「设置 → 模型设置」填入你自己的 API Key 即可；填好后
                不要把整个文件夹外传
.env.example    同一份配置的模板，仅供参考
alembic/        数据库表结构脚本。缺了会启动失败，不要删除或改名
alembic.ini     上面那个目录的配置
frontend/       界面文件（frontend/dist 里是网页）。缺了能启动但打开是 404
data/           数据库与衣物图片，就是全部身家
logs/           运行日志，出问题时看 logs/aidrobe.log
README.txt      本文件
guidance.html   使用手册（怎么用、每个选项什么含义）
TECHNICAL.md    技术交接文档（改了哪些文件、契约、怎么验证）


维护日志
--------------------------------------------------------------------

v0.9  2026-11-07
  五个功能可用：入库识别、穿什么、买不买、缺口、我的衣橱。
  三个模型角色各自下拉自选，共 5 个模型，界面标价。
  搭配不再按「上装+下装+鞋」填空，可以叠穿，也可以连衣裙配鞋。
  新增上班正式度三档与穿着间隔策略；新增模型连通性自检。
  关掉网页后后端自动退出。

v1.0  2026-11-08
  改为独立桌面窗口，顶栏收拢到设置齿轮，导航精简为首页 / 衣橱 / 推荐 / 购前四个页面。
  API Key 改存本机 .env；新增设置接口、用量接口、模型错误提示与今日 Token 统计。
  模型库扩到 13 个，新增 Claude / ChatGPT / Gemini；关闭窗口先藏到托盘并提示保存。
  数据库新增 0002 迁移；打包依赖 Edge WebView2 与 pythonnet，参数见 TECHNICAL.md。

v2.0  2026-11-10
  推荐策略新增「适中」档并设为默认：条件太窄时逐级放宽，避免温度合适却零搭配；严格与勉强两档保留。
  新增偏怕冷、偏怕热各三档体感偏好，仅作为参考信息交给模型，不设程序硬规则。
  温度覆盖支持「0-8」这类区间；今日用量按本地自然日统计；无合适搭配时给友好提示而不报错。
  界面语言、天气城市消歧、鼠标侧滑、长任务取消与「不喜欢」原因选择一并完善。


====================================================
ENGLISH VERSION
====================================================
AIdrobe — Your Personal Local AI Wardrobe

AIdrobe is a locally running wardrobe manager and outfit recommendation
tool, developed by Neutrino in the spare time of a PhD program and
dedicated to Cindy and to everyone with similar needs.

The name AIdrobe blends "AI" and "wardrobe". Its signature is that AI is
deeply involved at every step — item intake, outfit decisions and
purchase advice. AIdrobe is therefore not a conventional app but a
highly available Agentic Workflow running locally: a shared digital
wardrobe that understands not only how to categorize and store clothes,
but also "what decision you are facing right now".

Knowing that getting clothes into the system is the biggest barrier,
AIdrobe keeps intake to one photo per item, and does everything it can
to make the recommendations actually wearable, useful and easy.

May everyone dress in outfits they love, quickly and effortlessly!

It targets three concrete problems:

1. As a wardrobe grows, individual items lose visibility, leading to
   duplicate purchases and long-forgotten clothes;
2. Daily dressing has to balance weather, occasion and formality at the
   same time, which is costly to decide;
3. Before buying, there is no quantitative sense of "how many outfits
   could this combine with my existing wardrobe", so redundant items
   pile up easily.

AIdrobe answers with three capabilities:
1. Structured item intake and display;
2. Complete outfit generation under concrete scenes and personal
   preference constraints;
3. Pre-purchase evaluation of combinable outfits and duplication risk.

The program runs entirely locally. No online registration or cloud
account is needed; all data stays in the local data/ directory.

HOW TO USE
--------------------------------------------------------------------

[Configuration] API keys are stored in the .env file in the program
directory and survive upgrades. Fill them in once under
"Settings -> Model Settings / Test Connection"; they stay valid.

1. Double-click AIdrobe.exe. The program starts a local service in the
   background and opens a standalone desktop window — no browser, no
   console window.
2. First time: click the gear at the top right -> "Settings" ->
   "Model Settings / Test Connection", paste your API keys, pick a
   model for each of the three roles, click "Test Connection" until
   everything passes, then save.
3. "My Wardrobe": use "Add Clothing" at the bottom right to upload
   photos. "Outfit Recommendation": pick a scene and a city, then click
   the recommend button. "Home" shows today's overview and recent
   items; "Buy Decision" evaluates whether a purchase is worth it.
4. The top bar only keeps the user switcher and the settings gear;
   wardrobe scope, user management, recommendation policy and the
   request log all live in the settings drawer.

After clicking the window's X button, the window first minimizes to the
system tray with a balloon "Saving wardrobe data and exiting safely...".
The program only fully exits after data is saved — wait a few seconds,
this is not a freeze. Right-clicking the tray icon also offers "Quit
completely". For details see guidance.html in the same folder.

WHAT IS IN THIS FOLDER
--------------------------------------------------------------------

AIdrobe.exe     Main program (double-click to run; do not rename)
.env            Program configuration. Ships with EMPTY API keys — open
                "Settings → Model Settings" after the first launch and
                fill in your own keys. Keep the folder private once your
                keys are saved in it.
.env.example    Template of the same file, for reference only.
alembic/        Database schema scripts. Missing them breaks startup;
                do not delete or rename.
alembic.ini     Configuration for the directory above.
frontend/       UI files (frontend/dist holds the web pages). Without
                them the server starts but the page shows 404.
data/           Database and garment photos — everything that matters.
logs/           Runtime logs; see logs/aidrobe.log when something breaks.
README.txt      This file.
guidance.html   User manual (how to use, what every option means).
TECHNICAL.md    Technical handover document (changed files, contracts,
                how to verify).

CHANGELOG
--------------------------------------------------------------------

v0.9  2026-11-07
  Five features usable: intake & recognition, what to wear, buy or not,
  wardrobe gaps, and my wardrobe. Three model roles each with their own
  dropdown, 5 models total with prices shown in the UI. Outfits are no
  longer filled into "top + bottom + shoes" slots — layering works and
  a dress can pair with shoes. Added three work-formality levels, wear
  interval policy and model connectivity self-check. The backend exits
  automatically after the web page is closed.

v1.0  2026-11-08
  Converted to a standalone desktop window; the top bar collapsed into
  a settings gear; navigation simplified to Home / Wardrobe /
  Recommend / Buy. API keys moved to the local .env; added settings,
  usage and token statistics endpoints plus model error hints. The
  model library grew to 13 models including Claude / ChatGPT / Gemini.
  Closing the window first hides to tray and announces saving. Added
  database migration 0002; packaging depends on Edge WebView2 and
  pythonnet — see TECHNICAL.md.

v2.0  2026-11-10
  Added a "Balanced" recommendation policy and made it the default:
  when conditions are too narrow the filters relax step by step, so a
  warm-enough day no longer ends with zero outfits; the Strict and
  Relaxed options remain. Added cold/heat sensitivity preferences in
  three levels each, passed to the model as reference only — no hard
  program rules. Temperature override accepts ranges like "0-8"; daily
  usage statistics now follow the local calendar day; empty results
  show a friendly message instead of an error. UI language switching,
  weather city disambiguation, mouse drag-to-slide, cancellation of
  long tasks and dislike reasons were all polished in this release.
