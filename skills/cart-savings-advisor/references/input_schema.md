# cart.json 结构

`scripts/optimize.py` 读取这种输入。下列数值是合成规则示例，不代表任何平台的默认门槛。完整历史输入见 `examples/taobao_10items_2026.json`。

```json
{
  "platforms": {
    "taobao": {
      "cross_store": {"name": "跨店满减", "every": 300, "minus": 50, "threshold_on": "list"},
      "coupons": [
        {"name": "家电数码消费券 满3000减300", "threshold": 3000, "minus": 300, "threshold_on": "running",
         "only_categories": ["家电", "数码"], "stack_group": "消费券"}
      ],
      "membership": {
        "name": "88VIP", "fee": 88, "owned": false,
        "note": "88VIP 消费券 11 月 3 日 23:59 到期",
        "coupons": [
          {"name": "88VIP消费券 满3000减400", "threshold": 3000, "minus": 400, "threshold_on": "running", "stack_group": "消费券"}
        ]
      }
    }
  },
  "shops": {
    "taobao/某某旗舰店": {
      "coupons": [{"threshold": 300, "minus": 30, "threshold_on": "list"}],
      "shipping": {"fee": 8, "free_over": 59, "threshold_on": "running"}
    }
  },
  "items": [
    {"id": "A", "name": "商品短名", "category": "家电", "qty": 1, "must": true,
     "offers": [
       {"platform": "taobao", "shop": "某某旗舰店", "price": 5090, "list_price": 6790, "shown_price": 4690,
        "tags": ["tmall"], "subsidy": {"rate": 0.15, "cap": 2000, "confirmed": true, "group": "家电资格", "limit": 1}},
       {"platform": "jd", "shop": "某某京东自营", "price": 4999}
     ]}
  ],
  "assumptions": ["88VIP消费券和家电数码消费券一单只能用一张"]
}
```

## 商品字段

| 字段 | 含义 |
|---|---|
| `price` | **券前价（单件）**：扣除已经体现在单品上的自动直降。店铺券、平台券、会员券和国补只在后续结算中各扣一次 |
| `list_price` | 原价（划线价/优惠前），用来算「几折」 |
| `shown_price` | 购物车上标的「平台加补后/券后价」，仅作截图参考，不参与计算，不据此自动解释价格差异 |
| `category` | 品类：家电、数码、美妆、食品、宠物、家居、服饰、母婴、日用……品类券靠它判断能不能用 |
| `qty` | 正整数；搜索可按单件拆开下单 |
| `id` / `sku` / `spec` | id 须唯一；sku 和 spec 用于定位具体规格，重复提醒使用完整名称或SKU加规格 |
| `deposit_paid` | 报价中每件已付定金，默认0。price 是包含定金的完整券前价；总成本包含定金，本次待付扣除定金。不同报价的定金须一致，不能默认改店后可退款 |
| `must` | `false` = 想要但不急，计算器会算出「加它实际多花多少」 |
| `offers` | 同一件商品在不同平台/店铺的报价，多于一个时自动比价 |
| `requires_membership` | 报价只有平台会员才能购买时填 `true`；同时提供普通价和专享价可比较开卡净收益。兼容京东旧数据里的 `tags: ["plus"]` |
| `tags` | 标签，如 `tmall`、`apple`、`jd_self`（京东自营），给 `only_tags` 的券判断用 |
| `subsidy` | `rate` 比例、`cap` 单件上限。仅当当前账户资格已核实才填 `confirmed: true`。同一资格用相同的 `group`，不要把不同资格误写成同一组；`limit` 为确认的剩余件数，必须填写。`group` 也必须明确；缺失时排除补贴并提示。可填 `total_cap` 表示该组剩余总额度，跨单跨平台共享。名额跨订单与平台共享，不能按购买数量重复领取 |
| `cross_store` / `platform_coupon` | 默认 true；不参加跨店满减 / 不能用平台券时写 false |

## 优惠写法（店铺券、跨店满减、平台券、会员券通用）

- 满减：`{"threshold": 3000, "minus": 400, "threshold_on": "running"}`
- 每满减：`{"every": 300, "minus": 50, "threshold_on": "list"}`，可加 `max_times`、`cap`
- 折扣：`{"rate": 0.95, "cap": 100}`，可加 `threshold`
- `stack_group`：同一层的同一组一单只能用一张。不同组只有核实可叠加才录入；层内多组时每张券须填 `order`，不同组值不同，数值小的先算。默认组为该优惠层，平台券和会员券共享默认组
- `count`：持有同一种店铺券、平台券或会员券的张数，默认 1；拆单共用该库存。不同平台、店铺各自持有的券独立。跨店每满减若为每单自动生效可不写 `count`，活动有使用次数上限时明确填写。
- 已核实的优惠可直接写入券池；只看到活动广告或尚未确认持有的优惠可标 `available: false`，系统会排除该优惠并说明原因。店铺配置建议以 `平台/店名` 为键，防止不同平台的同名店共用同一张券。
- `deadline`：已知截止时间写在优惠规则上，或写在下面的 `coupon_meta` 中，格式为包含时区的 ISO 8601，例如 `2026-11-03T12:00:00+08:00`。过期、时间无法解析或来源冲突的优惠不计入实付。已确认可用但未提供截止时间的券可以参与试算，不推定具体日期。
- `only_categories` / `exclude_categories` / `only_tags`：适用范围
- `threshold_on`：`"list"`（适用商品的券前价）或 `"running"`（适用商品扣完前序优惠后的金额）。有 threshold 或 every 的优惠必须明确填写，缺失时排除优惠并提示；不能按平台统一设默认值

## 会员（membership）

挂在平台下面。`coupons` 是会员专享券，`owned` 是当前会籍状态，`fee` 是本账户实际开通价。非会员的开通价未知时，先按无会员权益报价并列为待确认；确认为免费开通才填 `fee: 0`。
计算器会分别算开/不开会员，已是会员时报告「这一车会员帮你省了多少」，不是会员时报告「开了净省多少或亏多少」；不根据券面额推导通用回本线。已付年费不重复计入，不能把已持有直接解释成历史上已回本。
会员带来的折扣（如部分店铺 95 折）可以写成 `rate` 型券放进 `coupons`。

## 计算顺序

店铺券 → 跨店满减 → 平台券/消费券/会员券 → 国补 → 运费及附加费 → 运费券。层内按 `order` 计算。规则不符合此顺序、优惠不能放弃或补贴不能按指定顺序领取时，不可直接套用本模型，须先核对可执行结算方式。

每个平台默认最多按全部商品件数拆单，可用 `--max-batches` 限制。每次精确求解最多18件，默认状态预算300000，可在根对象填 `search_budget` 提高。超限明确报错，不输出未证明最优的方案。可选商品最多8种，会枚举所有非空组合并重新计算会员；各组合互为备选，差额不能相加。空必买清单允许零购买。

## 给下单卡用的可选字段（meta / coupon_meta）

`coupon_meta` 中已知的有效期参与优化器计算；其他展示信息由下单卡读取。

```json
{
  "meta": {
    "title": "京东双11 拆单下单卡",
    "subtitle": "根据 X月X日 购物车录屏 + 会员截图计算 · 8 件商品",
    "hero_title": "分 3 单结算，这一车最省",
    "baseline_one_order": 3201.77
  },
  "coupon_meta": {
    "满2000减300": {"where": "领券中心 / 美妆会场", "deadline": "2026-11-03T23:59:00+08:00",
                     "tag": "美妆品类券", "urgent": false,
                     "tip": "美妆自营满2000减300，和全品类神券能同单叠，记得先领。"}
  }
}
```

- `meta.baseline_one_order`：录屏里"直接一单结算"的到手价，只展示参考金额，不自动当作同口径拆单收益。
- `coupon_meta` 的键是券名**子串**（如 `"满2000减300"` 能匹配到完整券名）；值是 `{where, deadline(ISO8601), tag, tip, urgent}`——填了就在领券清单显示"去哪领 / 什么时候过期 / 小魔女一句话提醒"，`urgent:true` 会加红色脉冲标签。
- `deadline` 写成含时区的 ISO 时间。同名券的适用范围或截止时间不同时，在各自优惠规则上写明，避免共用有歧义的券名子串。

## 运费与其他支出

店铺以 `平台/店名` 为键；每次该店参与结算分别收取以下费用：

```json
{
  "shipping": {"fee": 10, "free_over": 99, "threshold_on": "running"},
  "shipping_coupons": [{"name": "运费券", "minus": 10, "count": 1, "requires_membership": true}],
  "packaging_fee": 2,
  "service_fee": 3
}
```

`shipping.threshold_on` 有包邮门槛时必须填写，running 指所有商品优惠和国补之后。运费券只抵指定店铺实际运费，每店每单最多一张；不能登记成商品款券。目前不建模跨店共用的运费券池，不能把一张全平台运费券复制到每个店。`requires_membership` 可限制券只能在该平台会员启用时使用，实际会员配置仍须提供。

输出 `total` 为含本次新增会员费的总成本，`deposit_paid` 为其中已付定金，`due_now` 为本次待付（含新增会员费）；每笔 batch 也分开显示。定金超过优惠后总成本、需退款的情况不作为可执行订单。单件分摊用整数分保持守恒，是估算，不替代平台实际退款明细。

## 运行与结果边界

金额必须是有限非负数；券 count/max_times 为非负整数。未确认资格和门槛的优惠进入 warnings，不偷偷按某个平台默认值算入。商品报价和各项支出未包含的规则不能被解释成已核实为零；录入者须把已确认无运费等条件写入 assumptions。

模型按当前录入报价可逐件购买计算，不建模阶梯单价、赠品价值、随机红包、分期利息、定金膨胀或临时限购；出现这些条件先补充结算证据或单独计算，不声称覆盖。判断能否拆成独立子车时必须同时核对券、补贴、会员费和运费是否跨子车共享。
