# 外部依赖登记（DEPENDENCIES.md）

依据系统编程严格模式 S35：每引入一个外部依赖，记录引入原因、替代方案评估、版本锁定策略、退出计划。

## Dart / Flutter（`dnaisland-app`）

### decimal

| 项 | 内容 |
| --- | --- |
| 版本 | `^3.2.6`（`dnaisland-app/pubspec.yaml`，实际解析版本见 `pubspec.lock`） |
| 引入原因 | 后端积分精度提升为 `DECIMAL(30,10)`（最多 10 位小数），且 JSON 以**字符串**无损传输。Dart 原生 `double` 只有约 15~16 位有效十进制数字，无法无损承载 10 位小数叠加 20 位整数部分，客户端必须使用任意精度十进制类型。 |
| 替代方案评估 | ① 继续用 `double`：会不可逆丢精度，等于伪支持（否决）。② 自实现定点数（整数 + scale）：需自行实现解析/格式化/比较/乘法，重复造轮子且易错（否决）。③ 全程按字符串处理、仅在显示时截断：无法做余额比较与消耗估算乘法（否决）。 |
| 版本锁定 | `^3.2.6`，实际版本锁定于 `pubspec.lock`。升级该包时必须同时复跑 `dnaisland-app/test/points_format_test.dart` 与 `dnaisland-app/test/points_page_test.dart`。 |
| 退出计划 | 该包仅出现在 `lib/utils/points_format.dart`、`lib/api/client.dart`、`lib/points_page.dart`、`lib/image_gen_body.dart`。若将来移除，替换点收敛在 `parsePoints()` / `formatPoints()` 两个函数及少量 Decimal 比较/乘法，改动面限于上述 4 个文件。 |

## Python（后端）

后端积分精度使用标准库 `decimal`（非外部依赖），精度常量与运算上下文单点定义在 `app/constants.py`。
