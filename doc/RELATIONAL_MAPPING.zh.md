# 把 SQL Server、PostgreSQL、MySQL 当成图来用

你的数据库是一张张表、一列列字段，而 GraphXR 想看到的是点和线。代理会读取你的 catalog，
给出它自己的解读，再让你在**一份文档**里纠正这个解读。这份文档讲的就是那份文件，以及代理
不会替你猜的部分。

## 一个想法

| 你的 schema | GraphXR 看到的 |
|---|---|
| 一张记录表（`Person`） | 一个节点**标签**，每行一个节点 |
| 标识一行的那一列（`person_id`） | 节点的 **id**：`Person:1001` |
| 一张关联表（`Employment`）或一个外键 | 一个关系**类型**，比如 `WORKS_FOR` |
| 其余的列 | 节点的属性，或者关系的属性 |

id 是**用主键拼出来的**，不是从行的内部指针读出来的。正因为如此，expand 才能把一个 id
交回来、并重新找到同一行——它用的就是当初生成这个 id 的那个谓词。形式是 `<Label>:<key>`，
按**第一个**冒号切分，所以 key 里带冒号也不会被切坏。

**永远只有一列。** 一个标签必须有且只有一个 key 列：单列主键；没有就取单列
`NOT NULL` 唯一约束；再没有这张表就不能做节点。这不是偷懒——图约定每个种子节点只携带
**一对** key，用 `(order_id, line_no)` 做主键的表根本无法 expand。把它悄悄缩水成其中一列
比直接拒绝更糟：那会返回看起来很像真的错行。见
[`drivers/relational/mapping.py`](../src/graphxr_database_proxy/drivers/relational/mapping.py)。
下文所有"单列"讲的都是这条规则。

切开这两列的逗号**没有转义写法**：真叫 `unit, cost` 的那一列会被读成两列，拒绝时把两部分都
点出来，这条记录也就不进图里；这样的表请改用单列的代理键（surrogate）来映射。

**一条边的 endpoint 列，是边表自己的列。** 每一端写的是"某个标签 + **边表上**保存这一端
key 的那一列"，节点那一侧由它自己的 `key` 决定。所以连接条件是 `<edge>.<column> =
<node>.<key>`：`Employment.person_id` 连到 `Person.person_id`；`OrderLine.region_code`
也能连到 `Region.region_code`，哪怕两边名字不一样。

## 自动发现能给你什么

完全不写 mapping，代理就照着外键读出一份初稿。它先要表名，再**按一页表名**分批取列、
主键、外键——3 000 张表的 ERP schema 绝不会在一次调用里全量反射回来。行数来自引擎自己的
统计信息，所以只是估计值，页面上会写成 `~1,200,000`。

| 它发现的 | 它提出的 |
|---|---|
| 正好两个外键、都是单列，而且没有任何外键指回这张表 | 一条边，`type = upper(table)`，由这两个外键合成**一对**端点——它们是这条链接的两端，不是两条链接 |
| 记录表上的一个外键 | 一条边，**src 就是引用方那张表**——这个方向是 schema 里的事实，不是猜的 |
| 指回自己那张表的外键 | 一条两端同标签的边 |
| 其他任何表 | 一个节点标签，key 取单列主键，否则取最宽的单列 `NOT NULL` 唯一列 |
| 名字不是合法标识符，或者已经被占用 | 一个净化过的名字，冲突时加 `_2` |

每个猜测都带着它的理由，写在 `notes` 里——一份你无法反对的初稿谈不上可复核，只会被照单
全收：

```
EMPLOYMENT  <- Employment  notes: link table: exactly two foreign keys (person_id -> Person,
                                organization_id -> Organization) and nothing points back at it
FK_ACCOUNT_PARENT <- Account notes: Account.parent_account_id points back at Account itself,
                                      so the edge has one label on both ends
OrderItem   <- OrderItem   notes: not a node: its primary key is ['order_id', 'line_no'] and
                                the contract matches one column per key
```

## 它拒绝猜的部分

这些拒绝，就是"一张能用的图"和"看起来像没数据的空白画布"之间的区别。每一条都会带上原因，
所以一张表缺席是**有意为之**，而且你能看到是为什么。

| 情况 | 会发生什么 | 怎么办 |
|---|---|---|
| 主键是两列以上 | 不做节点；原因里会列出这两列 | 自己映射它，并指出真正能唯一标识一行的那一列 |
| 有唯一列，但可以为 NULL | 不能当 key——NULL 的 key 画不出一个点 | 加上 `NOT NULL`；硬写进去也允许，但会警告 expand 会在它上面失败 |
| 外键跨两列 | 不画这条边，并点名是哪个约束 | 自己声明这条边，用真正能连上的那一列 |
| 关联表的外键是复合的 | 被当成记录表，而不是关联表 | 自己把它声明成边，并写出两个端点列 |
| 边指向一张不在图里的表 | 不画这条边，并说明是哪一端 | 把那张表纳进来，或者放弃这条边 |
| 视图、引擎内部 schema、临时表和系统表 | 根本不参与，只在一条警告里计数 | 它们不是你的数据；如果某个视图确实是，就物化成表，或者映射它的基表 |

上限是同一类拒绝，只是发生在另一头：**200 个标签**、**400 条边**、每条记录 **200 个属性**
（[`catalog.py`](../src/graphxr_database_proxy/drivers/relational/catalog.py)）。超过上限时
保留的是最大的那些记录——没被统计过的表排在"统计结果为 0"的表**前面**，因为没人能证明它
小——剩下的会被丢掉，`notes` 里写着 `not mapped: the labels cap is 200`，同时页面给出
"Discovery stopped at a cap"。3 000 张表的 schema 不该变成 3 000 个标签的图：面板会彻底
没法用，而且第一次 expand 会在全部标签上扇出。想要什么就用 `include` 点名，不想要就用
`exclude`。

## mapping 文件

一个项目一份文件。里面所有内容都是可选的：你写下来的，就是代理不再猜的。代理自带一份带
注释的样例
[`drivers/relational/data/mapping.example.yml`](../src/graphxr_database_proxy/drivers/relational/data/mapping.example.yml)
——页面上的 **Example** 按钮显示的就是它，它既能解析也能通过校验，所以直接照它抄。

顶层字段：

| 字段 | 改变什么 |
|---|---|
| `version` | 文档格式版本；目前只有 `1` |
| `schema` | 下面所有表默认所在的 schema，条目自己写了就以条目为准。默认值：`dbo`（SQL Server）、`public`（PostgreSQL）、数据库本身（MySQL） |
| `include` | 一旦非空，图里**只有**点名的这些记录，其余全部出局 |
| `exclude` | 点名的记录出局——它是"某条目写 `include: false`"的简写 |
| `nodes` / `edges` | 记录与连接，按你写的顺序 |
| `tuning` | 预留给项目级的查询层覆盖；样例里列出了键名，每个键在它被读取的地方有说明 |

`include` / `exclude` 里的名字既能匹配标签也能匹配表名，且不区分大小写——你记得哪个就写
哪个。当有一份读到的 schema 可供比对时，一个什么都匹配不上的名字会被警告一次，而不是被
悄悄忽略。

节点条目：

| 字段 | 改变什么 |
|---|---|
| `label` | GraphXR 看到的名字。改名会重写所有指向它的边 |
| `table` | 物理表名；不写就等于 label |
| `schema` | 这张表所在的 schema，和文档默认值不同时才需要 |
| `key` | 标识一行的那一列；不写就用自动发现选出的候选 |
| `properties` | 一份**白名单**，见下文 |
| `exclude_properties` | 反向裁剪；大字段列（blob）就是这么处理的 |
| `include: false` | 把这个条目作为一条决定保留下来，但不进图 |
| `show_as` | 一行应该被**叫作**哪个属性 |
| `notes` | 为什么这张表是个节点，以及为什么它的 key 是这一列 |

边条目：`type`、`table`、`schema`、`include`、`properties`、`exclude_properties`、
`show_as`、`notes` 的含义和上面一样，只是对象换成了连接。边独有的：

| 字段 | 改变什么 |
|---|---|
| `endpoints` | 这个类型连接的所有 `(src, dst)`——每声明一种形状就有一对 |
| `key` | 边**自己的** key：它的单列主键，否则最宽的单列唯一列 |
| `flip` | 把整条连接反着读：交换**每一对**端点，不是只换一对 |

每个端点写成 `{label, column}`：一个节点标签，加上**边表上**的那一列（见
[一个想法](#一个想法)），还可以带一个可选的 `column_type`，带上它之后这份文件就不再需要
一个活的 catalog。

### 三个最容易搞错的地方

**`properties` 是白名单。** 一旦你列了名字，名单之外的列在 GraphXR 里就是隐形的——大多数
人发现某列没被映射，是一周之后在图里找它的时候。整个不写，就拿自动发现读到的那些列；一
旦写了，以后表上新增的列要等你点名才会进图。只写名字组成的列表也可以，这些列的类型会被
记成 `STRING`。无论怎么写，key 列和端点列都不会成为属性。

**边最好有自己的 key。** `Employment` 上的 `WORKS_FOR` 如果不写 `employment_id`，那么同
一个人在同一家公司不同日期的两条任职记录会被并成**一条**连线：没有自己的 key 时，id 由两
个端点的 key 排序后拼出来——翻转方向时它仍然稳定，但同一对记录之间的重复连线会塌缩成同一
个 id。表上有 key 时自动发现会填好，没有时会警告；而一份手写文件在**没有 catalog 可问**
的情况下是直接被拒绝，直到你把列名写出来——因为在那里保持沉默，等于交付一张缺了边的图。

**`flip` 是一句人话，不是一处错误。** 自动发现跟着外键走，那是一个真实的方向，但未必是
办公室里那句话的方向。照原样写的 `WORKS_FOR` 回答"谁在哪家工作"；翻转后回答"哪家雇了
谁"。两种都是合法的图。哪一个才是**你要**的那个，只有真实的数据能回答，schema 回答不了
——见[保存前先检查方向](#保存前先检查方向)。

### 你写的类型只是一个请求，不是一道命令

有两个读者会来看你写的类型，它们的回答方式并不一样。

**属性的声明类型，只要 schema 给得出答案，就会被改掉。** catalog 能叫出名字的列，会用它
自己的拼写覆盖你写的那一个：给一个数据库里其实是 `integer` 的列写 `varchar(50)`，最后绑定
用的就是 `INTEGER`，而且一句警告都没有。只有负责读取的那一方**压根没有答案**的时候——它会
发出 "Did not recognize type" 这条警告，然后写下 `NULL`——你的声明才是场上仅存的类型信息，
这时它留得下来，loader 也会说明这一点：*this schema names no type for column 'odd', so the declared
'varchar(100)' is what a filter on it binds as*。这是 mapping 唯一一次真的成为被拒的过滤器
所指的那条出路。

**key 的类型轮不到你来声明。** 文档里没有 `keyType` 这个字段，因为 id `<Label>:<key>` 和每
一次重新选中的 seed 都要按 key 列自己的类型来绑定，所以代理去 schema 里读它——只读一次。
这次读取只在这个 key 仍然表示"没人查过"时才发生：空、仍是 `STRING` 占位符、或者读取器
自身的 resolver 认不出这一列时交回来的 `NULL` 标记。读取器交回来的那个标记就是文档视角下
的 key 声明类型；如果活目录能叫出这一列的名字，`driver_mapping.py:92-119` 的限定目录读取会
把它纠正过来；如果叫不出来，同一次拒绝照样触发——只是这一次走的是“读了一次”而不是“根本
没读”。所以一个最终落在本引擎无法绑定的类型名上的 key，会让整条记
录被按名字拒绝，而且是在**每一条**触达它的语句上，不只是那些过滤 key 的语句。两种情况
会触发同一次拒绝，但消息不同：

- 标记类（空、`STRING`、`NULL`）：*node label 'Widget' is keyed by column 'id', whose type
  the schema did not name, so this engine has nothing to bind as the column's own type;
  a seed could not be re-selected, so the lookup is refused rather than answered with an
  empty result.*
- 真实名字但本引擎绑不了，比如 `geometry`：*node label 'Widget' is keyed by column 'id',
  whose declared type 'geometry' this engine cannot bind as the column's own type; a seed
  could not be re-selected, so the lookup is refused rather than answered with an empty
  result.*

两种消息都以同一句出路收尾：**把这个标签的 key 换到 schema 能叫出名字的一列，或者把这条
记录从图里挪走**（`exclude` 掉，或从 `include` 里清掉）。给 key 声明一个类型不在这条出
路里——文档里没有 `keyType` 字段，key 列也不会变成属性（`mapping.py:319-320` 读的是表的
列类型先于你的文件，`mapping_props.py:62-66` 把 key 丢掉）。

### 为什么理由要写在字段里

`notes` 和 `show_as` 的存在理由很实际：编辑器是**从文档重新生成文件**的，而 YAML 注释是
一次保存里最先被销毁的东西。样例文件为了第一次读的人写了大量注释；**导出的 mapping 一条
注释都没有**。写在字段里的意图，下次打开这一页时还在。

## 上传和编辑，改的是同一份文档

导航栏里的 Graph Mapping 页面列出你的项目；只有 SQL Server、PostgreSQL、MySQL 项目可以
选中，其余会被置灰并写明原因——图数据库已经自己命名了节点，没有需要从表里推断的东西。选中
一个项目会写进 URL，所以别人拿到同一个链接就看到同一次复核。

**Records**、**Links**、**YAML** 三个标签页是同一份草稿的三个视图，而 Save 是**一次调用**
提交整份草稿。旁边还有：**Upload mapping**、**Download**、**Re-discover**、**Example**、
**Reset to auto-discovery**、**Reload**、**Discard**、**Save mapping**。

最关键的一点是：文件和点击是同一回事。**浏览器从不解析、也从不生成 YAML**：上传的文件在
页面里被读成文本，交给代理，只有代理会把文本变成 mapping、再把 mapping 变回文本。所以一份
上传的文件，受的就是点击编辑时所受的同一套规则——同样的排除、同样的级联、同样的拒绝。
`.yml`、`.yaml`、`.json` 走同一条解析路径，因为 YAML 本身就能读 JSON。只有一个解析器，
就不存在第二份格式权威会跑偏。

实际含义：

- 代理不肯加载的文件会被**报告，而不是应用**。你拿到问题清单，现有 mapping 一字未动；
  任何情况下都不会悄悄截断。
- 读起来干净的文件进入的是**草稿**，不是存储——还得按 Save，卡片上会一直显示 `unsaved`。
  带着未保存的草稿离开页面会先问你。
- **Download** 写出的是**已保存**的那份，因为代理渲染的是它手里的那份；草稿未保存时，页面
  会先提议保存。
- **Re-discover** 用此刻 catalog 的提案替换草稿。在你保存之前，什么都不会落盘。
- 新建项目时也可以**顺手附上 mapping 文件**。这种情况下，一个加载不了的文件只会在项目建好
  之后换来一句警告，不会赔上项目本身。
- 保存会改变 GraphXR 读到的内容：schema 缓存的摘要键就是这份 mapping，所以下一次 schema
  探测看到的就是你改过的版本。

**YAML** 标签页随着你的点击实时显示代理自己的渲染结果，于是保存之前你就能看见这些点击
到底描述了些什么；把文本粘进去再 apply，会在服务端重新解析并刷新另外两个标签页，而带问题
的文本不会动它们。排除某个记录时，需要它的边会被丢掉，并点名是哪个记录导致的，而不是拒绝
整份文件——这条级联和 `mapping.py` 在处理上传时用的是同一条规则，所以编辑器的承诺和加载器
的规则本来就是一条规则。

文件上限 **1 MiB**，必须是 UTF-8。超限、顶层不是一个 mapping、或者文件是空的，代理会拒绝
整个文件并说明是哪一种。

## 校验精确到控件，而且上传和点击看到的是同一份清单

每个问题都点名它出错的那个控件，所以一次失败的上传点亮的是一个字段，而不是整张页面。下面
每一段都是一个真实文件，每一条消息都是加载它时真实打印出来的：

```yaml
version: 1
exclue:
  - sysdiagrams
```

```
exclue: not a field of the mapping document (a typo is refused, not ignored)
```

未知键是被拒绝而不是被丢弃，因为 `exclue:` 若被读成"什么都没排除"，回来看到的就是一份
静悄悄地不干活的文件。语法错误则报出行号：
`line 3: indentation uses a tab; YAML indents with spaces`。

```yaml
nodes:
  - label: OrderItem
    table: OrderItem
    key: [order_id, line_no]
```

```
nodes[0].key: node key 'order_id, line_no' is composite and the graph contract carries one key
pair per seed node (NodeRef.key_prop) and _expand_by_key reads group[0].key_prop, so a table
keyed by more than one column cannot be expanded at all; declare one of ['order_id', 'line_no']
as a surrogate or single column
```

写成列表的 key 会被保留成可读的形式，好让拒绝的消息能把两列都点出来，而不是对一份表达得
很清楚的文件回一句"不是字符串"。

最后这个文件的 `nodes` 里只声明了 `Person`：

```yaml
edges:
  - type: WORKS_FOR
    table: Employment
    key: employment_id
    endpoints:
      - src: {label: Person, column: person_id}
        dst: {label: Ogranization, column: organization_id}
```

```
edges[0].endpoints[0].dst.label: label 'Ogranization' is not declared under nodes; a typo here
is an edge type that silently never matches. Declared labels: Person
```

**图这边的名字是严格的，数据库那边的不是。** `label` 和 `type` 必须是字母、数字、下划线，而且
不能以数字开头：它们既被编译进生成的语句，也会进 `<Label>:<key>` 这样的 id，一个带空格的标签
在这两处都没有位置。而 `table`、`column` 和 key 列就按你数据库自己的写法用——`Order Items`、
`first name` 都可以，因为 SQL 边界的引号是替你加的——只有在为空、或含控制字符时才拒绝。

同样会被拒绝的：重复的标签或类型；一张表被映射成两个标签——那样 id
无法往返；没有任何端点对的边，因为那是一条永远画不出来的关系；以及同一个名字既当节点又当
边，因为 `<Employment>:<key>` 就会同时意味着两样东西。

**和线上数据库对不上只是警告，永远不是拦截。** 这张表不在当前 schema 里、某一列已经不存在
了、`include` 里的名字什么都匹配不上——每一条只说一次，保存照样成功。因为 mapping 常常是为
一个作者此刻还连不上的数据库写的。

## 保存前先检查方向

**方向反了的边不报错，它返回空。** 它不会失败，那条连线只是永远不出现，而 mapping 看起来
一切正常。这就是这一页存在的唯一理由，而且没有任何 schema 能回答它——外键只指一个方向，
而你心里那句话可能指另一个。

Links 页上每个端点对都有一个 **Check direction**，把这条边*正反两个方向*都拿你的真实数据跑
一遍，给出四种结论之一：

| 结论 | 含义 |
|---|---|
| **Settled** | 只有这个方向连得上东西。连线的意思就是 mapping 写的意思 |
| **Backwards** | 这个方向连不上，反方向连得上——点一下就能翻转 |
| **You decide** | 两个方向都连得上，数据库裁决不了；读一下下面的行，选那句是真话 |
| **Neither** | 表里有行，但两个方向都连不上，说明两个 key 列里至少有一个是错的 |

返回的行显示的是 `show_as` 的值而不是 id，因为 `1001 → 2001` 看不出哪里不对，而
`Avery Chen → Northwind Logistics` 看得出来；把鼠标悬上去能看到 id，因为一个念起来没问题
的标签，仍然可能连在错误的列上。"没有行"绝不会被报成"一切正常"；一个没能跑起来的检查也不
算结论——它没有排除任何方向。

**Records** 页可以展开每条记录的列：哪些是属性、哪些没进图（会计数并列出来，理由和白名单
那一段相同）、这个条目是带白名单还是取全部列，以及代理报告的那张表的 key 候选。

一行是否带来源标记——`auto`、`renamed`、`flipped`、`added`、`excluded`——取决于页面有没有
它的来源可显示，没有就什么都不显示；因为这一页把机器的提案和你的决定混在一起显示，你必须能
分辨自己正在覆盖的到底是哪一个。

## 图看起来不对的时候

| 症状 | 通常是因为 | 改哪里 |
|---|---|---|
| 少了一个标签 | 这张表没有单列 key，或者被上限挤掉，或者是视图 | 读它的 `notes`；声明 `key`，或用 `include` |
| 少了一条边 | 复合外键、某一端不在图里、或者它需要的记录被 `include: false` 排除了 | 自己声明这条边；把那条记录放回图里 |
| 一条边连不上任何东西 | 方向反了，或者端点列选错了 | 先 **Check direction**，然后 flip 或改列 |
| 某一列在 GraphXR 里看不到 | `properties` 是白名单，而这一列不在名单上 | 点名它，或者清掉白名单 |
| 一条查询被**按名字**拒绝——报错说 "has no type to bind as"，或者说 "cannot bind as the column's own type" | 这一列上的类型名是本引擎绑不了的。到这一步有两条路：名字是从 schema 报回来的那套**图侧**词表里抄的（`INT64`、`INT32`、`STRING`、`BYTES` 三个引擎都绑不了，`BOOL`、`DOUBLE`、`JSON` 只在其中个别引擎上能绑），或者这一列真的是这里没有绑定的类型，比如一个 `geometry` | 前一种：按你自己的数据库拼写这个类型的写法来写——不区分大小写，`(n)` 和 `unsigned` 可以省略——每个引擎的名单见[规则住在哪里](#规则住在哪里)。后一种：换个拼写解决不了——用 `exclude_properties` 排除这一列，或者把标签的 key 换到别的列上 |
| 同一对记录之间的两条边变成了一条 | 这条边没有自己的 key | 给它写上自己的 `key` |
| 边太多，或者冒出一张没人想要的表 | 自动发现把所有能定 key 的记录表都提了出来 | 用 `exclude`，或者用一份短的 `include` |
| 第一次 expand 很慢或者爆开 | 一个多态类型在每一对端点上扇出 | 只留下你需要的端点对，或者排除这个类型 |
| 画布上什么都没有 | 项目连不上数据库，因此 catalog 根本没被读到 | 先修连接——mapping 是对一份必须能读到的 catalog 的解读 |

## 建项目的时候要填什么

主机、端口、数据库、schema、用户名、密码，外加 TLS 开关。默认值就是引擎自己的：`1433`、
`5432`、`3306`，schema 分别是 `dbo` / `public` / 数据库本身——MySQL 那栏故意留空，因为
在那里 schema **就是**数据库。**只读账号就够了**：代理自始至终只做读取。表单没被要求建模的
引擎参数，放进连接的 `options`，它们会原样交给驱动。

**Browse tables（浏览表）**读的就是此刻表单上填的那个连接，项目还不存在也能读：选择器打开时
用的是按下那一刻的快照，所以打开之前什么都不会读；主机或数据库没填，会用一句话拒绝，而不是
回一份空列表。这样的草稿标记为 `draft:<name>`，绝不会和同名的已保存项目共用连接池。

## 规则住在哪里

| 想知道什么 | 看这个文件 |
|---|---|
| 文档的字段 | `models/graph_mapping.py` |
| 解析、1 MiB 上限、导出 | `drivers/relational/mapping_io.py` |
| 什么算合法，以及为什么不合法 | `drivers/relational/mapping.py`，以及 loader 拆出去的那四个 `mapping_props.py`／`mapping_index.py`／`mapping_pairs.py`／`mapping_scope.py` |
| 自动发现读什么、拒什么、上限 | `drivers/relational/catalog.py`、`reflect.py` |
| 每个引擎真正能绑定哪些列类型名 | `drivers/relational/engines/mssql.py`、`mysql.py`、`postgresql.py`——各自那张 `_COLUMN_TYPES` 表，声明的类型就是在这儿查表的 |
| 下游一切代码所依据的中间表示 | `drivers/relational/schemas.py` |
| 自带的样例文件 | `drivers/relational/data/mapping.example.yml` |
| 编辑器 | `frontend/src/pages/GraphMapping.tsx`、`frontend/src/components/mapping/` |

这里不写任何路由或请求/响应格式：mapping 相关的接口归
[`doc/RELATIONAL_API.md`](RELATIONAL_API.md)，本页描述的是管理后台的界面行为。
