# Ziroom Scout

自如房源 Excel 导出工具。

这个目录里的脚本会读取自如地图页保存下来的 HTML，解析房源列表，并同时导出为 `ziroom_houses.xlsx` 和 `ziroom_houses.csv`。导出时会自动计算价格、单位面积价格、规则命中、黑名单、收藏、通勤时间、标签拆分等字段。

## 快速运行

默认读取：

```text
source/自如网-租房信息网-提供地区的房屋合租信息及月租价格.html
```

运行：

```bash
./run_export.sh
```

导出结果：

```text
ziroom_houses.xlsx
ziroom_houses.csv
```

也可以指定输入和 Excel 输出名，CSV 会使用相同文件名主体：

```bash
./run_export.sh source/自如网-租房信息网-提供地区的房屋合租信息及月租价格.html result.xlsx
```

上面这条命令会生成：

```text
result.xlsx
result.csv
```

## 常用文件

```text
config.json                 全局配置
links.json                  房源ID 到自如详情页链接
labels.txt                  要拆成独立列的标签
通勤.json                    小区到公司骑行通勤时间
preference/rules.txt        显性筛选规则
preference/block.txt        拉黑规则
preference/favorites.txt    收藏规则
```

## config.json

示例：

```json
{
  "默认排序": "单位面积价格",
  "只显示满足规则": false
}
```

`默认排序`：导出后按这个列升序排序。可以写任意 Excel 表头，例如：

```json
"默认排序": "步行距离(米)"
```

`只显示满足规则`：  

```json
"只显示满足规则": true
```

开启后，只导出 `rules` 列为 `✅` 的房源。

## preference/rules.txt

每行一条规则。所有规则都满足时，Excel 第一列 `rules` 会显示 `✅`。

当前支持：

```text
<= >= == != < > 包含 不包含
```

示例：

```text
租金(元/月)<=3100
面积(㎡)>10
复式!=✅
独立阳台==✅
block!=❌
步行距离(米)<=800
是否顶层!=✅
骑行通勤<=20
```

字段名直接使用 Excel 表头。

## preference/block.txt

每行一个拉黑规则，命中后 `block` 列显示 `❌`。

规则会匹配整行房源信息，所以可以写房源 ID、名称、小区、面积、标签等关键词。

示例：

```text
合租·万树园3居+·11卧
顶层
最多签至2026/10/17
```

## preference/favorites.txt

每行一个收藏规则，命中后 `favorites` 列显示 `⭐`。

示例：

```text
圆明园西路3号院
合租·兰园3居·03卧12.09㎡
```

## labels.txt

每行一个要关注的标签。脚本会保留原始 `标签` 列，并额外为 `labels.txt` 里的每个标签新增一列，命中显示 `✅`。

示例：

```text
复式
独立阳台
```

这样 Excel 会新增：

```text
复式 | 独立阳台
```

## links.json

表示房源 ID 到详情页链接的映射。房源 ID 默认是 `名称 + 面积`，例如：

```json
{
  "合租·圆明园西路3号院3居·01卧14.69㎡": "https://www.ziroom.com/x/807772296.html"
}
```

导出时会把 Excel 的 `名称` 列变成可点击链接。如果页面里出现重复房源导致 `房源ID` 自动追加楼层，脚本仍会用原始 `名称 + 面积` 回退匹配。

## 通勤.json

表示小区到公司的骑行通勤时间，单位按你自己约定，目前建议填分钟。

示例：

```json
{
  "紫成嘉园": 20,
  "唐家岭新城": 19
}
```

导出时会写入 `骑行通勤` 列。脚本会优先按小区名匹配，也会做轻微近似匹配，例如 `紫城嘉园` 可以匹配页面里的 `紫成嘉园`。

## 主要导出字段

常用字段包括：

```text
rules
名称
房间
租金(元/月)
面积(㎡)
单位面积价格
楼层
当前楼层
总楼层
是否顶层
朝向
小区/公寓
小区地图最低价
小区地图房源数
地铁线
地铁站
步行距离(米)
骑行通勤
优惠信息
是否优惠
产品版本
是否可短签
是否价格标红
标签
状态
是否可预订
预计可入住日期
最多签至
block
favorites
房源ID
```

其中：

`租金(元/月)` 是从自如页面价格数字图里解码出来的。

`小区地图最低价` 和 `小区地图房源数` 来自地图上的小区聚合标记，比如 `图景嘉园 ¥2289起（7套）`。

`房源ID` 默认是 `名称 + 面积`，如果重复会自动追加楼层。

## 更新数据流程

1. 在浏览器保存新的自如地图页面到 `source/` 目录。
2. 确保文件名是：

```text
source/自如网-租房信息网-提供地区的房屋合租信息及月租价格.html
```

3. 按需修改：

```text
config.json
preference/rules.txt
preference/block.txt
preference/favorites.txt
links.json
labels.txt
通勤.json
```

4. 运行：

```bash
./run_export.sh
```

## 注意

- 当前脚本只依赖 Python 标准库，不需要安装 `pandas`、`openpyxl`、`bs4`。
- 如果 Excel 正在打开 `ziroom_houses.xlsx` 或 `ziroom_houses.csv`，系统可能生成临时文件或导致覆盖异常；建议先关闭 Excel 再导出。
- 如果规则字段写错，脚本会报 `Unknown rule field`，把字段名改成 Excel 表头即可。
