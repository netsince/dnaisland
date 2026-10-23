# 一个「元素」在三个面上应该是怎样的

> 目的：在继续补世界卡之前，先把**成熟元素**（角色卡、茶馆）的整条链路摸清楚，
> 提炼出一份"元素清单"，再用它对照世界卡，看差距在哪。
>
> 方法：统计引用分布 → 逐层读关键文件（发布服务、审核、通知、列表层）→ 归纳。

## 一、引用分布：密度本身就是信号

```
                      Card   TeaPost   WorldCard
routes/admin.py        121      33         0
routes/api.py           91      16         0
routes/card_lists.py    76       0         0
routes/user.py          67      12         0
routes/main.py          56       9         0
routes/teahouse.py      15      46         0
routes/worlds.py         0       0        20
services/*              104      0         0
templates/admin/       382       4         0
templates/user/        123      10         0
templates/teahouse/     41      52         0
templates/worlds/        6       0        21
```

角色卡铺在 **6 个路由文件 + 7 个 service + 4 个模板目录**；世界卡只在
**1 个路由文件 + 1 个模板目录**，外加 `report_service` 里 2 行。

## 二、角色卡的全链路

### 服务端

| 环节 | 落点 | 关键内容 |
|---|---|---|
| 模型 | `models/card.py` | 主表 + 8 张附属表（标签/图片/对话样例/点赞/收藏/复制统计/评论/评论点赞） |
| 发布 | `services/card_publish_service.py` | **Web 与 App 共用** `create_card_from_payload`：字段规范化、图片 WEBP 压缩、**逐槽位宽高比强校验**（1:1 / 16:9 / 9:16，±2%）、**内容指纹幂等去重** |
| 列表层 | `routes/card_lists.py` | 列表查询集中一层，被 web 与 api 共用 |
| 装配 | `services/card_service.py` | `enrich_cards` 批量补封面/作者，避免 N+1 |
| 编辑 | `services/card_edit_service.py` | 编辑、置顶 |
| 隐匿 | `services/card_hidden_tags.py` | 仅超管可见的标签 + 派生的热度乘数 |
| 搜索 | `services/search_service.py` | bigram 倒排 + BM25F，事件驱动回填 |
| 审核 | `routes/admin.py` | `/review` 队列、`/review/<id>` 详情、approve / reject（带原因）、`/review/batch` 批量、AJAX 友好 |
| **审核后置** | 同上 | `notify(author, '已通过审核…', type_="review", related_card_id=...)` |
| 管理 | `routes/admin.py` | `/cards` 列表、编辑、隐匿标签、删除 |
| 可见性 | `Card.visible_to` + `is_public` | 双口径，与处罚 `hide_cards` 联动 |
| 双端接口 | `routes/api.py` | `/api/v1/cards/*` **复用** `card_detail_core` / `card_export_package` / `search_cards` |
| 举报 | `services/report_service.py` | target 注册 + 快照（名称/作者/简介） |

### 网页版

`publish/start → parse → edit → done` 四步发布向导；列表（`/`、`/explore`、`/search`）；
详情 `user/card_detail.html`；个人主页 tab；`/my/cards` `/my/favorites` `/my/likes`；
后台 review 队列 + cards 管理（`templates/admin/` 里 382 处 card 引用，是最大的模板簇）。

### 客户端

`card_publish_page.dart`（裁剪器锁定比例）→ 列表（`home_body` / `swipe_body` / `explore_body` / `search_page`）
→ 详情 `card_detail_page.dart`（2200 行，点赞/收藏/评论/举报/导出全在里面）
→ 「我」页（我的角色卡 / 收藏 / 点赞）→ `api/client.dart` 的方法。

## 三、茶馆的对照

茶馆**更轻**，但**该有的都有**：模型 7 张附属表、`routes/teahouse.py`（46 处）、
`admin.teahouse_moderation`（33 处）、`templates/teahouse/`（52 处）、
个人主页两个 tab（帖子 / 回复）、客户端 `teahouse_body` + `teahouse_post_page` + `teahouse_post_tile`。

**结论：元素复杂程度不同，但"面"的覆盖是齐的。** 没有哪个元素只活在"自己的栏目"里。

## 四、提炼：一个元素的 16 条清单

### 服务端
1. 主表 + 附属表 + 迁移
2. **发布服务**：web 与 api 共用同一个创建函数
3. **审核流水线**：pending → approved / rejected，队列 + 详情 + 通过/驳回 + 批量
4. **审核后置动作**：通知作者（可导航到该元素）
5. 可见性双口径（`visible_to` + `is_public`），与处罚联动
6. 独立的**列表查询层**，被两端共用
7. 搜索索引（事件驱动回填）
8. 互动（点赞/收藏/评论，或至少收藏）
9. **后台管理**：列表、编辑、隐藏、删除
10. 举报注册（target + 快照）

### 网页版
11. 发布入口（表单/向导）
12. 列表页 + 详情页
13. **个人主页 tab**
14. 我的（我的X / 收藏 / 点赞）
15. 后台审核页 + 管理页

### 客户端
16. 发布页 + 列表 + 详情 + 「我」页入口 + API 方法

## 五、世界卡的对照

| # | 清单项 | 状态 | 说明 |
|---|---|---|---|
| 1 | 模型 + 迁移 | ✅ | 3 张表，LONGTEXT payload |
| 2 | 发布服务 | ⚠️ | 有，但**没有服务端内容校验**（只验"是合法 JSON"），也没有独立的 service 层 |
| 3 | **审核流水线** | ❌ | **完全没有**。而发布写的是 `status="pending"`，`visible_to` 只放行 `approved` → **发布即消失** |
| 4 | 审核后置通知 | ❌ | 没有。且 `Notification` 只有 `related_card_id`，世界卡通知无处可去 |
| 5 | 可见性双口径 | ✅ | `visible_to` + `is_public` |
| 6 | 独立列表层 | ❌ | 查询直接写在路由里，而且 **web 与 api 各写一遍**（`worlds_list` 与 `worlds_web.index` 是两份几乎相同的代码） |
| 7 | 搜索索引 | ❌ | 只对 name/summary 做 LIKE，没进 `search_service` |
| 8 | 互动 | ❌ | 只有 `copy_count`，没有点赞/收藏/评论 |
| 9 | 后台管理 | ❌ | 没有 |
| 10 | 举报注册 | ⚠️ | 后端 target 加了，**网页版没有入口**（客户端有） |
| 11 | 网页发布入口 | ❌ | 网页只能浏览 + 下载 |
| 12 | 列表页 + 详情页 | ⚠️ | 有，但详情页套的是文章页的壳，缺浏览数/举报/作者其它作品 |
| 13 | **个人主页 tab** | ❌ | 没有。世界卡只活在自己的栏目里，和作者是断开的 |
| 14 | 我的 / 收藏 | ❌ | 没有 |
| 15 | 后台页 | ❌ | 没有 |
| 16 | 客户端五件套 | ⚠️ | 发布/列表/详情/API 有，**「我」页没有入口**，详情页缺浏览数 |

**6 项缺失、6 项打折、4 项齐全。**

## 六、两个比"缺功能"更根本的问题

**① 世界卡违反了这个代码库的架构约定：列表查询该集中一层、两端共用。**

角色卡是 `search_cards()` 一个函数，web 与 api 都调它。世界卡则是
`worlds_list`（API）和 `worlds_web.index`（网页）**各写一遍**同样的
`visible_to + q + tag + sort + paginate`。两份代码已经开始漂移的风险。

**② `Notification` 的模型不支持世界卡。**

`notify(user_id, message, type_, related_card_id)` —— 只有"关联角色卡"。
世界卡要做审核通知、被复制通知，模型上得先扩展（`related_world_id` 或改成通用
`related_type + related_id`）。这是**做审核的前置条件**，不是顺手就能加的。

## 七、建议的补齐顺序

按"先让功能不死，再让它有归属，最后做治理"：

1. **审核流水线 + 通知**（前置：扩展 Notification）—— 不做这个，世界卡在线上就是死的
2. **列表查询收拢成一层**，web 与 api 共用（顺手修掉重复代码）
3. **个人主页 tab + 「我」页入口** —— 让世界卡有归属，而不是孤岛
4. **后台管理页**（列表/下架/删除）
5. **网页版详情页按世界书形态重做**（词条分组/折叠/搜索）+ 补浏览数、举报入口
6. 互动（收藏优先于点赞——收藏 = "我以后要导入它"）
7. 搜索索引接入

## 八、一句话总结

角色卡和茶馆的差别是**复杂程度**，不是**覆盖范围**。一个元素要么在三个面上都"有位置"
（发布、审核、列表、详情、个人主页、我的、后台、举报），要么就不算做完了。
世界卡目前只在"自己的栏目"里成立，离开那个栏目就什么都不剩 —— 这才是"敷衍"的实质，
而不是少了某几个具体页面。
