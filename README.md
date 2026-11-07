# AIdrobe
AIdrobe — Your Personal Local Smart Closet
====================================================
AIdrobe is a local wardrobe management & outfit recommendation tool developed by Neutrino in his PhD spare time, dedicated to Cindy and similar users.

AIdrobe (AI + wardrobe) is a local, high-availability Agentic Workflow. AI deeply participates in intake, outfit decisions, and purchase advice. It's a shared family digital closet that understands context-aware decisions, not just storage.
====================================================
To lower the barrier, intake is simplified (one photo for one-click intake), while recommendation quality is maximized for ease of use.

Hope everyone easily finds their perfect outfit!
====================================================

It solves 3 problems:

Lower item visibility as closets grow, causing duplicates & idle items.
High decision cost balancing weather, occasion, formality, etc.
Lack of quantitative combo assessment before buying, causing redundancy.

AIdrobe provides 3 capabilities:
Structured intake & display.
Context-aware outfit generation based on scenarios & preferences.
Pre-purchase combo count & redundancy risk evaluation.

Runs 100% locally. No cloud accounts needed. Data saved in local data/ folder.
====================================================
How to Use

Double-click AIdrobe.exe.
Service starts in background & opens browser (default: http://127.0.0.1:8000).
First use: Paste API Key in "Model Settings" (top), click "Test Connection".
"Intake" to upload photos; "What to wear" to select scenario/city and click "Outfit me".
Backend auto-exits seconds after closing browser, or right-click tray icon to "Exit". See guidance.html for details.
Folder Contents

AIdrobe.exe Main program (run it; don't rename)
.env Model & DB config. Contains API Key. DO NOT SHARE.
alembic/ DB schema scripts. Required (don't rename/delete).
alembic.ini Config for alembic.
frontend/ UI files (web in frontend/dist). Required (else 404).
data/ DB & clothing photos (all your data).
logs/ Runtime logs (check logs/aidrobe.log for issues).
README.txt This file.
guidance.html User manual (usage & options).
TECHNICAL.md Tech handover doc (changes, contracts, validation).

Changelog
v1.0 2026-11-07
5 features live: Intake, What to Wear, Buy or Not, Gaps, My Closet.
3 model roles, selectable from 5 models (prices shown).
Supports layering & dresses with shoes (not just top+bottom+shoes).
Added 3 office formality levels, wear-interval strategy, & model connectivity self-check.
Backend auto-exits when webpage closes.


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

1. 双击 AIdrobe.exe。
2. 程序会在后台启动服务，并自动打开浏览器（默认地址 http://127.0.0.1:8000）。
3. 第一次使用：顶部「模型设置」里粘贴 API Key，点「测试连接」确认三个角色都正常。
4. 左边「入库」上传衣服照片；「穿什么」选场景和城市，点「给我搭配」。

关闭浏览器页面后，后端会在几秒内自动退出；也可以在托盘图标右键选「退出」。
更细的用法见同目录的 guidance.html。


这个文件夹里有什么
--------------------------------------------------------------------

AIdrobe.exe     主程序（双击运行，程序名不要改）
.env            模型与数据库配置。里面含你的 API Key，不要外传
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

v1.0  2026-11-07
  五个功能可用：入库识别、穿什么、买不买、缺口、我的衣橱。
  三个模型角色各自下拉自选，共 5 个模型，界面标价。
  搭配不再按「上装+下装+鞋」填空，可以叠穿，也可以连衣裙配鞋。
  新增上班正式度三档与穿着间隔策略；新增模型连通性自检。
  关掉网页后后端自动退出。
