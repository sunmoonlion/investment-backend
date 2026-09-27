# 财报体检专家包与它的评测

> 设计：k8s 库 `tree-build/SDD/modules/0008-info.md` 段四、`0009-semantic.md` 丙段。2026-09-27。

## 专家包 `FIN_REVIEW`

定义在 `app/app/domain/workbench/packs.py`，是数据不是代码。七步：

| # | 步骤 | 工具 | 交回物 | 不合格去向 |
| --- | --- | --- | --- | --- |
| 1 | `scope` 定范围 | 无 | `scope` | 返工 1 次，再交人 |
| 2 | `profile` 数据摸底 | `list_datasets`、`describe_schema`、`metric_definitions`、`run_sql` | `profile` | 返工 1 次，再交人 |
| 3 | `reconcile` 勾稽 | `run_sql` | `reconcile` | **直接交人，不返工** |
| 4 | `extract` 取数 | `describe_schema`、`run_sql` | `facts` | 返工 1 次，再退回第 2 步 |
| 5 | `metrics` 算指标 | `metric_definitions`、`query_metric`、`run_sql` | `metrics` | 返工 2 次，再交人 |
| 6 | `crosscheck` 对照官方 | `run_sql` | `crosscheck` | **直接交人，不返工** |
| 7 | `note` 成稿 | 无 | `note` | 返工 1 次，再交人 |

要点：

- 数据摸底与勾稽排在取数之前：数据有口径断点或勾稽不平时，后面算出来的指标没有意义。
- 勾稽与对照官方不给返工额度。返工的提示会催模型「按格式重交」，在这两步上等于催它把不平的数改成平的。
  用户在交人时可以选「再试一次」。
- 公司未入库时，第 2 步交回 `dataset: null`，验收不通过，交人。专家不得改用别的数据集。
- 指标优先按口径名查询（`query_metric`）：值与「是否适用」由数据集里的定义给出，专家照抄，不自己重算。
  工具不存在（知识服务的语义层没开）或口径不能按名查询时，按定义用 `run_sql` 算。
- 会计知识（例如新租赁准则的影响）只引用数据集说明里写了的，不凭模型自身知识断言。
- 成稿的结论栏必须留空；整份成稿里不得有买卖建议的措辞。

## 验收规则

在 `app/app/application/workbench/acceptance.py`，都是确定性的纯函数。为这个专家包新增三种：

| 规则 | 含义 | 用在哪 |
| --- | --- | --- |
| `all_equal` | 列表的每一项都有某个字段且等于给定值。空列表算通过，所以要和 `list_min` 配着用 | 勾稽：每条规则的不平期数为 0 |
| `list_empty` | 必须是列表而且是空的。缺这一栏不算空 | 勾稽：没有解释不了的断点；对照：没有对不上的数 |
| `blank` | 没有内容：缺失、空、空白文字 | 成稿：结论栏留空 |

另有两处改动对原有专家包没有影响：

- `no_positioning_advice` 不给 `path` 时检查交回物里的全部文字（以前不给 `path` 时什么都不查）；给了 `path` 时与以前一样。
- 步骤契约多一项 `input_max_chars`（默认 8000，与以前相同）。上游交回物超长被截断时，turn 输入里会注明。

## 评测：财报体检十三题

案例集 `app/eval/cases/fin_review_13.json`，冻结在数据版本 `sh600009-financials-9fd91db79529e208`
（info 按数据集自述第二版重建的 600009）。数据集原件在 `app/eval/fixtures/`。

| 项 | 说明 |
| --- | --- |
| 真值查询 | 15 条。期望的列都是数据集里的字段名或口径名，没有自造的列名。原稿里用了自造列名的查询留在 `reference_queries` 里作参考，不拿来判 |
| 去重比对 | 真值可以标 `distinct`：同一个数在多份年报里出现时，候选多选了出处、行数更多，事实相同就算对上 |
| 没有真值查询的题 | 三道（勾稽、跨期连续、边界）。它们靠「必须提醒」判 |
| 必须提醒 | 16 条 `caveat_checks`，在答案的文字里按关键词查。**这是近似检查**：通过不等于提醒写得对，不通过基本可以断定没有提醒。单列一栏，不进发布门 |
| 方言 | 评测重跑候选 SQL 用的方言要与知识服务一致：语义层开着用 `duckdb`，关着用 `sqlite`。15 条真值在两种方言下结果相同 |
| 按口径名查询 | 评测从工具的回报里取它实际执行的 SQL，和 `run_sql` 的 SQL 一样重跑 |

在线跑（要沙箱、模型的 key、开着语义层并登记了数据集的知识服务；段五之后才有条件）：

```
uv run python -m eval.run_eval --cases fin_review_13 --profile FIN_REVIEW \
    --dataset-id sh600009-financials --dialect duckdb --limit 13
```

问数二十题的跑法与结果不变；不给 `--dataset-id` 时基线臂的提示与以前逐字相同。

**还没做过的**：十三题还没有在线跑过一次。现在验过的是案例集本身（真值可执行、两种方言一致、与手工核对的数相同）
和评判器的规则，不是专家包的实际表现。

## 测试

| 文件 | 验什么 |
| --- | --- |
| `tests/test_workbench_acceptance.py` | 三种新规则与措辞检查的范围 |
| `tests/test_fin_review_pack.py` | 专家包的结构、每一步的验收、turn 输入与截断的注明 |
| `tests/test_fin_review_advisor_db.py` | 在顾问上跑：顺利走完、未入库、勾稽不平、对照不一致、取数退回、成稿含建议或结论 |
| `tests/test_eval_fin_review.py` | 案例集、去重比对、两种方言、必须提醒、跑臂的辅助函数 |
