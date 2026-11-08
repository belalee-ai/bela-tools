#!/usr/bin/env python3
"""
大促购物车算账器（淘宝 / 京东 / 其他平台通用）

输入 cart.json（结构见 references/input_schema.md），输出：
  1. 最省下单方案：一共分几次结算、每次勾哪几件、用哪张券、应付多少
  2. 会员值不值：开/不开 88VIP、京东 PLUS 各算一遍，扣掉年费比较；比较本车会员权益的增量收益
  3. 凑单建议：每个「想要但不急」的商品，加进来实际要多花多少钱
  4. 按品类汇总、同款同规格重复录入提醒

用法：
    python3 optimize.py cart.json                  # 打印 Markdown 报告
    python3 optimize.py cart.json --json out.json  # 另存完整结果
    python3 optimize.py cart.json --max-batches 5

每一次结算（batch）内部的计算顺序：
    券前价（已扣官方立减/单品直降）
    → 店铺券（每店每个叠加组挑最省一张）
    → 跨店满减
    → 平台券 / 消费券 / 会员券（每个叠加组一张，每张券全局只能用一次）
    → 国补（按行计算，比例 + 单件上限）
    → 运费
门槛规则须明确 threshold_on="list" 或 "running"，缺失时不计入该优惠。
"""
import argparse
import itertools
import json
import math
from decimal import Decimal, ROUND_HALF_UP
import sys
from collections import defaultdict
from datetime import datetime

EPS = 1e-9


class NoFeasiblePlan(ValueError):
    pass


def nonnegative(value, label):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{label}必须是有限的非负数。")
    return value


def validate_coupon(c):
    for key in ("threshold", "minus", "cap", "every", "rate", "order"):
        if key in c:
            nonnegative(c[key], f"优惠券 {key} ")
    if "rate" in c and not 0 <= c["rate"] <= 1:
        raise ValueError("优惠券 rate 必须在0到1之间")
    if "every" in c and not c["every"]:
        raise ValueError("优惠券 every 必须大于0")
    if "rate" in c and ("minus" in c or "every" in c):
        raise ValueError("同一优惠不能同时使用折扣和满减")
    if "rate" not in c and "minus" not in c:
        raise ValueError("优惠缺少 rate 或 minus")
    for key in ("count", "max_times"):
        if key in c and (type(c[key]) is not int or c[key] < 0):
            raise ValueError(f"优惠券 {key} 必须是非负整数")


def r2(x):
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# ============================================================ 输入

def coupon_deadline(coupon, meta):
    """返回各来源一致的截止时刻。未给日期与明确填写了错误日期分别处理。"""
    name = describe(coupon)
    sources = [coupon] + [v for k, v in meta.items() if k and k in name and isinstance(v, dict)]
    deadlines = set()
    for source in sources:
        if "deadline" not in source:
            continue
        value = source["deadline"]
        try:
            deadline = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if deadline.tzinfo is None:
                raise ValueError
        except (AttributeError, TypeError, ValueError):
            raise ValueError("截止时间无效，需提供带时区的 ISO 日期时间") from None
        deadlines.add(deadline)
    if len(deadlines) > 1:
        raise ValueError("多个来源的截止时间冲突")
    return next(iter(deadlines), None)


def filter_coupons(data, now):
    """先核实可领/持有状态和时效，再把真实可用的库存交给所有结算场景。"""
    meta = data.get("coupon_meta") or {}
    owners = list(data["shops"].values()) + list(data["platforms"].values())
    owners.extend(p["membership"] for p in data["platforms"].values() if p.get("membership"))
    cross_stores = [(p, {"coupons": [p["cross_store"]]}) for p in data["platforms"].values()
                    if p.get("cross_store")]
    owners.extend(owner for _, owner in cross_stores)
    shipping_owners = [(shop, {"coupons": shop.get("shipping_coupons", [])}) for shop in data["shops"].values()]
    owners.extend(owner for _, owner in shipping_owners)
    by_name = defaultdict(list)
    for owner in owners:
        for c in owner.get("coupons", []):
            by_name[describe(c)].append(c)
    for owner in owners:
        valid = []
        for c in owner.get("coupons", []):
            name = describe(c)
            try:
                validate_coupon(c)
                if ("available" in c and c["available"] is not True
                        or "confirmed" in c and c["confirmed"] is not True):
                    raise ValueError("未确认可用")
                if c.get("threshold", 0) or c.get("every"):
                    if c.get("threshold_on") not in ("list", "running"):
                        raise ValueError("门槛口径未确认，需填写 threshold_on=list 或 running")
                deadline = coupon_deadline(c, meta)
                if len(by_name[name]) > 1 and "deadline" not in c and any(
                        "deadline" in other for other in by_name[name]):
                    raise ValueError("同名券存在独立截止时间，需分别确认")
                if deadline is not None:
                    if len(by_name[name]) > 1 and "deadline" not in c:
                        raise ValueError("同名券无法唯一匹配截止时间，需写到具体券上")
                    if deadline <= now:
                        raise ValueError("已到期")
                    c["deadline"] = deadline.isoformat()
                valid.append(c)
            except ValueError as exc:
                data["warnings"].append(f"「{name}」未计入：{exc}。")
        owner["coupons"] = valid
    for shop, owner in shipping_owners:
        shop["shipping_coupons"] = owner["coupons"]
    for platform, owner in cross_stores:
        platform["cross_store"] = next(iter(owner["coupons"]), None)


def load(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("platforms", {})
    data.setdefault("shops", {})
    if not isinstance(data.get("items"), list):
        raise ValueError("items 必须是商品数组。")
    if type(data.get("search_budget", 300000)) is not int or data.get("search_budget", 300000) < 1:
        raise ValueError("search_budget 必须是正整数。")
    now = datetime.now().astimezone()
    data["as_of"] = now.isoformat()
    data["warnings"] = []
    for platform, rules in data["platforms"].items():
        membership = rules.get("membership")
        if membership and "owned" in membership and type(membership["owned"]) is not bool:
            data["warnings"].append(f"「{platform}」持有状态不明确，本次按未开通会员计算。")
            membership["owned"] = False
        if membership and not membership.get("owned"):
            fee = membership.get("fee")
            if type(fee) not in (int, float) or not math.isfinite(fee) or fee < 0:
                data["warnings"].append(f"「{platform}」会员开通价未确认，本次不使用其会员专属报价和权益。")
                rules.pop("membership")
    filter_coupons(data, now)
    # 多个可叠加组必须给定平台实际的计算顺序，不能任意排列求出虚低价格。
    coupon_sets = [s.get("coupons", []) for s in data["shops"].values()]
    coupon_sets.extend(p.get("coupons", []) + p.get("membership", {}).get("coupons", [])
                       for p in data["platforms"].values())
    for coupons in coupon_sets:
        groups_by_order = defaultdict(set)
        if len({c.get("stack_group", "default") for c in coupons}) > 1:
            for c in coupons:
                if "order" not in c:
                    raise ValueError("多个优惠叠加组须逐张填写 order，确认组间实际计算顺序。")
                groups_by_order[c["order"]].add(c.get("stack_group", "default"))
            if any(len(groups) > 1 for groups in groups_by_order.values()):
                raise ValueError("不同叠加组的 order 必须不同。")
    for key, shop in data["shops"].items():
        for field in ("packaging_fee", "service_fee"):
            nonnegative(shop.get(field, 0), f"店铺 {key} {field} ")
        shipping = shop.get("shipping", {})
        nonnegative(shipping.get("fee", 0), f"店铺 {key} 运费 ")
        if "free_over" in shipping:
            nonnegative(shipping["free_over"], f"店铺 {key} 包邮门槛 ")
            if shipping.get("threshold_on") not in ("list", "running"):
                raise ValueError(f"店铺 {key} 包邮门槛须填写 threshold_on。")
        for coupon in shop.get("shipping_coupons", []):
            if any(k in coupon for k in ("only_tags", "only_categories", "exclude_categories")):
                raise ValueError("运费券仅支持按指定店铺的运费适用，请先确认店铺范围。")
    groups = defaultdict(list)
    shop_keys = defaultdict(set)
    ids = set()
    for i, it in enumerate(data["items"]):
        it.setdefault("id", f"item{i+1}")
        if not isinstance(it["id"], str) or it["id"] in ids:
            raise ValueError("商品 id 必须是唯一字符串。")
        ids.add(it["id"])
        it.setdefault("qty", 1)
        if type(it["qty"]) is not int or it["qty"] < 1:
            raise ValueError(f"商品「{it.get('name', it['id'])}」的 qty 必须是正整数。")
        it.setdefault("must", True)
        if type(it["must"]) is not bool:
            raise ValueError("商品 must 必须是 true 或 false。")
        it.setdefault("category", "其他")
        if "offers" not in it:
            it["offers"] = [{k: it[k] for k in ("platform", "shop", "price", "list_price", "shown_price",
                                                  "cross_store", "platform_coupon", "tags", "subsidy",
                                                  "requires_membership", "deposit_paid") if k in it}]
        for o in it["offers"]:
            for field in ("price", "list_price", "shown_price", "deposit_paid"):
                if field in o or field == "price":
                    nonnegative(o.get(field), f"商品 {it['name']} {field} ")
            if o.get("deposit_paid", 0) > o["price"]:
                raise ValueError("已付定金不能超过完整券前价；price 须包含已付定金。")
            o.setdefault("platform", "taobao")
            o.setdefault("cross_store", True)
            o.setdefault("platform_coupon", True)
            o.setdefault("tags", [])
            o["shop_key"] = f'{o["platform"]}/{o["shop"]}'
            shop_keys[o["shop"]].add(o["shop_key"])
            sb = o.get("subsidy")
            if sb is None:
                continue
            if not isinstance(sb, dict) or sb.get("confirmed") is not True:
                data["warnings"].append(f"「{it['name']}」国补资格未确认，本次按无补贴计算。")
                o.pop("subsidy")
                continue
            if (not isinstance(sb.get("group"), str) or not sb["group"].strip()
                    or type(sb.get("limit")) is not int or sb["limit"] < 0
                    or type(sb.get("rate")) not in (int, float) or not 0 < sb["rate"] <= 1
                    or ("total_cap" in sb and (type(sb["total_cap"]) not in (int, float) or not math.isfinite(sb["total_cap"]) or sb["total_cap"] < 0))
                    or ("cap" in sb and (type(sb["cap"]) not in (int, float)
                                         or not math.isfinite(sb["cap"]) or sb["cap"] < 0))):
                o.pop("subsidy")
                data["warnings"].append(f"「{it['name']}」国补类别、名额或金额规则无效，本次按无补贴计算。")
                continue
            groups[sb["group"]].append(sb)
        if len({o.get("deposit_paid", 0) for o in it["offers"]}) > 1:
            raise ValueError("同一商品各报价的已付定金不同，须先确认退款或转移规则后单独计算。")
    for group, entries in groups.items():
        limits = {sb["limit"] for sb in entries}
        if len(limits) > 1:
            data["warnings"].append(f"「{group}」国补名额不一致，统一采用较少的 {min(limits)} 个。")
            for sb in entries:
                sb["limit"] = min(limits)
    for name, keys in shop_keys.items():
        if name not in data["shops"]:
            continue
        cfg = data["shops"].pop(name)
        if len(keys) == 1:
            data["shops"].setdefault(next(iter(keys)), cfg)
        elif not keys <= data["shops"].keys():
            if cfg.get("shipping") or cfg.get("packaging_fee") or cfg.get("service_fee"):
                raise ValueError(f"「{name}」在多个平台有同名店，无法判断运费；请用 平台/店名 分别填写。")
            data["warnings"].append(f"「{name}」在多个平台有同名店，未采用未注明平台的店铺优惠；请分别确认。")
    return data


def shop_cfg(data, offer):
    return data["shops"].get(offer["shop_key"], data["shops"].get(offer["shop"], {}))


def platform_name(p):
    return {"taobao": "淘宝/天猫", "tmall": "天猫", "jd": "京东", "pdd": "拼多多", "douyin": "抖音"}.get(p, p)


# ============================================================ 优惠计算

def coupon_saving(c, base, running):
    amt = running if c.get("threshold_on") == "running" else base
    if amt + EPS < c.get("threshold", 0):
        return 0.0
    if "rate" in c:
        save = running * (1 - c["rate"])
        return min(save, running, c.get("cap", running))
    if c.get("every"):
        n = math.floor((amt + EPS) / c["every"])
        if c.get("max_times") is not None:
            n = min(n, c["max_times"])
        save = n * c["minus"]
        if c.get("cap") is not None:
            save = min(save, c["cap"])
        return min(save, running)
    return min(c.get("minus", 0), running)


def describe(c):
    if c.get("name"):
        return c["name"]
    if "rate" in c:
        s = f"{c['rate']*10:g}折"
        if c.get("threshold"):
            s = f"满{c['threshold']:g}享" + s
        return s + (f"(最高省{c['cap']:g})" if c.get("cap") else "")
    if c.get("every"):
        return f"每满{c['every']:g}减{c['minus']:g}"
    return f"满{c.get('threshold', 0):g}减{c.get('minus', 0):g}"


def eligible(c, row, layer):
    o, it = row["offer"], row["item"]
    if layer == "platform" and not o["platform_coupon"]:
        return False
    if layer == "cross_store" and not o["cross_store"]:
        return False
    tags = c.get("only_tags")
    cats = c.get("only_categories")
    if tags and not any(t in o.get("tags", []) for t in tags):
        return False
    if cats and it.get("category") not in cats:
        return False
    if c.get("exclude_categories") and it.get("category") in c["exclude_categories"]:
        return False
    return True


def coupon_basis(c, rows, layer):
    qualified = [r for r in rows if eligible(c, r, layer)]
    base = sum(r["list"] for r in qualified)
    running = base - sum(r["disc"] for r in qualified)
    return qualified, base, running


def coupon_copies(rules, source, member=False):
    # 库存身份按规则和份数生成。展示名相同的两张券仍是两份独立资源。
    return [dict(c, _resource_id=f"{source}/{index}/{copy}", member=member)
            for index, c in enumerate(rules, 1) for copy in range(1, c.get("count", 1) + 1)]


def coupon_use(c, platform, kind, saving, shop=None):
    details = {key: c[key] for key in ("threshold", "minus", "rate", "every", "cap", "deadline") if key in c}
    return dict(details, name=describe(c), resource_id=c["_resource_id"], kind=kind,
                platform=platform, shop=shop, saving=r2(saving))


def allocate(rows, amount):
    nets = [max(0, round((r["list"]-r["disc"])*100)) for r in rows]
    total = sum(nets)
    if not total:
        return
    cents = min(round(amount*100), total)
    shares = [cents * n // total for n in nets]
    remainder = cents - sum(shares)
    order = sorted(range(len(rows)), key=lambda i: (-(cents*nets[i] % total), i))
    for i in order[:remainder]:
        shares[i] += 1
    for row, share in zip(rows, shares):
        row["disc"] = r2(row["disc"] + share/100)


def finalize_batch(data, out):
    platform = out["platform"]
    rows = [r for shop in out["shops"] for r in shop["rows"]]
    shipping = 0.0
    for s in out["shops"]:
        cfg = shop_cfg(data, s["rows"][0]["offer"])
        ship = cfg.get("shipping", {})
        basis = ship.get("threshold_on", "running")
        paid = sum(r["list"] if basis == "list" else r["list"] - r["disc"] for r in s["rows"])
        fee = 0.0
        if ship.get("fee") and not (ship.get("free_over") is not None and paid + EPS >= ship["free_over"]):
            fee = ship["fee"]
        s["extra_fees"] = r2(cfg.get("packaging_fee", 0) + cfg.get("service_fee", 0))
        s["shipping"] = fee
        shipping += fee

    out["list_total"] = r2(sum(r["list"] for r in rows))
    out["subsidy"] = r2(sum(r["subsidy"] for r in rows))
    out["shipping"] = r2(shipping)
    out["extra_fees"] = r2(sum(s["extra_fees"] for s in out["shops"]))
    out["deposit_paid"] = r2(sum(r["offer"].get("deposit_paid", 0) * r["item"]["qty"] for r in rows))
    out["pay"] = r2(sum(r["list"] - r["disc"] for r in rows) + shipping + out["extra_fees"])
    out["due_now"] = r2(out["pay"] - out["deposit_paid"])
    out["lines"] = [{
        "id": r["item"]["id"], "name": r["item"]["name"], "sku": r["item"].get("sku", ""), "spec": r["item"].get("spec", ""), "qty": r["item"]["qty"], "category": r["item"]["category"],
        "shop": r["offer"]["shop"], "platform": platform, "list": r2(r["list"]), "net": r2(r["list"] - r["disc"]),
        "subsidy": r2(r["subsidy"]), "must": r["item"]["must"],
        "original": r2(r["offer"].get("list_price", r["offer"]["price"]) * r["item"]["qty"]),
        "shown": r["offer"].get("shown_price"), "offer": r["offer"],
    } for r in rows]
    for s in out["shops"]:
        del s["rows"]
    return out


# ============================================================ 方案评估与搜索

def coupon_pool(data, platform, member_on):
    rules = data["platforms"].get(platform, {})
    pool = coupon_copies(rules.get("coupons", []), platform + "/platform")
    m = rules.get("membership")
    if m and member_on:
        pool.extend(coupon_copies(m.get("coupons", []), platform + "/member", member=True))
    return [c for c in pool if member_on or not c.get("requires_membership")]


def available_offers(item, members):
    return [offer for offer in item["offers"]
            if members.get(offer["platform"], False) or not (
                offer.get("requires_membership") or
                (offer["platform"] == "jd" and any(str(tag).lower() == "plus" for tag in offer["tags"])))]


def search(data, basket, members, max_batches, budget=300000):
    from exact_solver import solve
    return solve(sys.modules[__name__], data, basket, members, max_batches,
                 data.get("search_budget", budget))


# ============================================================ 会员回本分析

def member_curve(data, platform):
    # 总消费额不足以确定回本线，需使用本车的真实会员反事实。
    m = data["platforms"][platform]["membership"]
    fee = m.get("fee")
    known = type(fee) in (int, float) and math.isfinite(fee) and fee >= 0
    return {"name": m.get("name", "会员"), "fee": fee if known else 0,
            "fee_known": known, "owned": m.get("owned", False), "rows": [],
            "breakeven": None, "note": m.get("note", "")}


# ============================================================ 报告

def duplicates(basket):
    seen = defaultdict(list)
    for it in basket:
        key = (it.get("sku") or it["name"].strip(), it.get("spec", ""))
        seen[key].append(it)
    return [items for items in seen.values() if len(items) > 1]


def run(data, max_batches, include_addons=True):
    must = [it for it in data["items"] if it["must"]]
    optional = [it for it in data["items"] if not it["must"]]
    plats = sorted({o["platform"] for it in data["items"] for o in it["offers"]})
    mplats = [p for p in plats if data["platforms"].get(p, {}).get("membership")]

    # 会员组合：已拥有的会员默认开着；没开的会员分别算开/不开
    combos = []
    for bits in itertools.product([True, False], repeat=len(mplats)):
        combos.append(dict(zip(mplats, bits)))
    results = {}
    for mb in combos:
        key = tuple(sorted(mb.items()))
        results[key] = search(data, must, mb, max_batches)
    feasible = [k for k, plan in results.items() if plan is not None]
    if not feasible:
        missing = [it["name"] for it in must if not available_offers(it, {p: True for p in mplats})]
        raise NoFeasiblePlan(f"必买商品「{'、'.join(missing)}」没有可行结算方案。请核对普通报价、会籍、已付定金和订单限制。")
    best_key = min(feasible, key=lambda k: (results[k]["total"], sum(
        on != bool(data["platforms"][p]["membership"].get("owned")) for p, on in k)))
    best = results[best_key]
    best_members = dict(best_key)

    # 每个会员：开 vs 不开（其他会员保持最优状态）
    member_cmp = {}
    for p in mplats:
        on = dict(best_members, **{p: True})
        off = dict(best_members, **{p: False})
        member_cmp[p] = {"on": results[tuple(sorted(on.items()))], "off": results[tuple(sorted(off.items()))],
                         "curve": member_curve(data, p)}

    # 对照锁定选中报价和会籍，只改变结算分组。
    same_basket = []
    items_by_id = {it["id"]: it for it in must}
    for batch in best["batches"]:
        for line in batch["lines"]:
            item = items_by_id[line["id"]]
            offer = line["offer"]
            same_basket.append(dict(item, qty=line["qty"], offers=[offer]))
    simple = search(data, same_basket, best_members, 1)
    # 逐个候选和全部候选组合均重新计算会籍。
    addons = []
    addon_combinations = []
    if include_addons:
        if len(optional) > 8:
            raise ValueError("凑单候选超过8种，请先筛选实际想买的商品。")
        for size in range(1, len(optional)+1):
            for group in itertools.combinations(optional, size):
                subset = dict(data, items=must + [dict(it, must=True) for it in group])
                try:
                    plan = run(subset, max_batches, include_addons=False)["best"]
                except NoFeasiblePlan:
                    plan = None
                entry = {"items": list(group), "plan": plan, "extra": r2(plan["total"]-best["total"]) if plan else None}
                addon_combinations.append(entry)
                if size == 1:
                    addons.append(dict(entry, item=group[0]))
    return {"best": best, "simple": simple, "members": member_cmp, "addons": addons,
            "addon_combinations": addon_combinations, "must": must, "dups": duplicates(data["items"])}


def fmt_money(x):
    if x is None:
        return "无可用报价"
    sign = "-" if x < -0.004 else ""
    return f"{sign}¥{abs(x):,.2f}".removesuffix(".00")


def report(data, R):
    best, simple = R["best"], R["simple"]
    L = []
    original = sum(l["original"] for b in best["batches"] for l in b["lines"])
    discount_text = f"，相当于 {best['total'] / original * 10:.1f} 折" if original else ""
    shown_items = [it for it in R["must"] if it["offers"][0].get("shown_price") is not None]
    L.append("# 购物车算账结果\n")
    L.append(f"**当前找到的方案：按下面「方案A」分 {len(best['batches'])} 次结算，预计到手 {fmt_money(best['total'])}"
             + (f"（含会员年费 {fmt_money(best['fees'])}）" if best["fees"] else "")
             + f"，原价 {fmt_money(original)}{discount_text}；"
             + f"相同报价和会籍、每个平台一次结算的对照总额是 {fmt_money(simple['total'])}。**\n")

    if shown_items:
        L.append("截图单件价可能含不同优惠或来自不同勾选状态；请按相同组合的结算明细对账。\n")
    # 会员
    for p, mc in R["members"].items():
        cv = mc["curve"]
        on = mc["on"]["total"] if mc["on"] else None
        off = mc["off"]["total"] if mc["off"] else None
        L.append(f"## {cv['name']} 值不值")
        if on is None or off is None:
            L.append(f"- 开通时 {fmt_money(on)}，不开时 {fmt_money(off)}。缺少一侧的有效整车报价，无法量化会员净省金额。")
            continue
        diff = off - on
        if cv["owned"]:
            L.append(f"- 你已经是 {cv['name']}：本车使用会员权益可省 {fmt_money(diff)}，无需再付年费。")
        elif best["members"].get(p):
            L.append(f"- **建议开**：开通（年费 {fmt_money(cv['fee'])}）后这一车到手 {fmt_money(on)}，不开 {fmt_money(off)}，扣掉年费净省 {fmt_money(diff)}。")
        elif diff == 0:
            L.append(f"- 开或不开这车都要 {fmt_money(off)}，按不开会员下单。")
        else:
            L.append(f"- **不建议开**：这一车开了要 {fmt_money(on)}（含年费），不开 {fmt_money(off)}，开了反而多花 {fmt_money(-diff)}。")
        if cv["note"]:
            L.append(f"- 注意：{cv['note']}")
        L.append("")

    # 方案对比
    L.append("## 方案对比\n")
    L.append("| 方案 | 怎么买 | 到手价 |\n|---|---|---|")
    L.append(f"| A 当前找到的方案 | 分 {len(best['batches'])} 次结算，按下面清单勾选 | **{fmt_money(best['total'])}** |")
    L.append(f"| B 省事 | 每个平台全部勾上一次结算 | {fmt_money(simple['total'])} |")
    for p, mc in R["members"].items():
        if not mc["curve"]["owned"]:
            other = mc["off"] if best["members"].get(p) else mc["on"]
            label = "不开" if best["members"].get(p) else "开"
            L.append(f"| {label}{mc['curve']['name']} | 同样拆单 | {fmt_money(other['total'] if other else None)} |")
    L.append("")

    # 照着做
    L.append("## 方案A：照着做\n")
    for i, b in enumerate(best["batches"], 1):
        names = "、".join(f"{l['name']}×{l['qty']}" for l in b["lines"])
        L.append(f"**第 {i} 次结算（{platform_name(b['platform'])}）**：只勾选 {names}")
        for s in b["shops"]:
            if s["discounts"] or s["shipping"]:
                ds = "、".join(f"{n} -{v:g}" for n, v in s["discounts"])
                L.append(f"- 店铺「{s['shop']}」：{ds or '无店铺券'}" + (f"，运费 ¥{s['shipping']:g}" if s["shipping"] else ""))
        if b["cross_store"]:
            L.append(f"- 跨店满减 -{b['cross_store']:g}")
        for n, v, mem in b["platform_coupons"]:
            L.append(f"- 用券「{n}」-{v:g}" + ("（会员券）" if mem else ""))
        if b["subsidy"]:
            L.append(f"- 国补 -{b['subsidy']:g}（需先领到国补资格）")
        if b["extra_fees"]:
            L.append(f"- 包装及服务费 +{fmt_money(b['extra_fees'])}")
        for c in b["coupon_uses"]:
            if c["kind"] == "shipping":
                L.append(f"- 运费券「{c['name']}」抵扣 {fmt_money(c['saving'])}（运费已扣除）")
        L.append(f"- 券前 {fmt_money(b['list_total'])} → **本单总成本 {fmt_money(b['pay'])}；本次待付 {fmt_money(b['due_now'])}**")
        if b["deposit_paid"]:
            L.append(f"- 已付定金 {fmt_money(b['deposit_paid'])}，计入总成本，不重复付款。")
        for h in b["hints"][:2]:
            L.append(f"- 💡 {h}")
        L.append("")

    # 品类
    cats = defaultdict(lambda: [0.0, 0.0, 0])
    for b in best["batches"]:
        for l in b["lines"]:
            c = cats[l["category"]]
            c[0] += l["original"]
            c[1] += l["net"]
            c[2] += l["qty"]
    L.append("## 按品类看\n")
    L.append("| 品类 | 件数 | 原价 | 到手 | 折扣 |\n|---|---|---|---|---|")
    for k, (o, n, q) in sorted(cats.items(), key=lambda kv: -kv[1][1]):
        ratio = f"{n / o * 10:.1f} 折" if o else "—"
        L.append(f"| {k} | {q} | {fmt_money(o)} | {fmt_money(n)} | {ratio} |")
    L.append("")
    L.append("每件优惠分摊金额（未含运费、附加费及会员费；退款以平台实际规则为准）：")
    for b in best["batches"]:
        for l in b["lines"]:
            L.append(f"- {l['name']}：原价 {fmt_money(l['original'])} → {fmt_money(l['net'])}")
    L.append("")

    # 凑单
    if R["addons"]:
        L.append("## 凑单：这些「想要但不急」的要不要加\n")
        for a in R["addons"]:
            it = a["item"]
            if a["extra"] is None:
                L.append(f"- {it['name']}：没有可用报价。")
                continue
            lp = min(o["price"] for o in it["offers"]) * it["qty"]
            if a["extra"] <= 0:
                L.append(f"- {it['name']}：加上反而**再省 {fmt_money(-a['extra'])}**；如果原本就需要，可考虑加购")
            else:
                ratio = a["extra"] / lp * 10 if lp else 0
                verdict = "很划算，真用得上就加" if ratio <= 5 else ("可以考虑" if ratio <= 8 else "不划算，等单独打折再买")
                L.append(f"- {it['name']}（标价 {fmt_money(lp)}）：实际只多花 {fmt_money(a['extra'])}，相当于 {ratio:.1f} 折，{verdict}")
        L.append("")

    combinations = [a for a in R.get("addon_combinations", []) if len(a["items"]) > 1]
    if combinations:
        L.append("## 多件一起加购\n")
        for a in sorted(combinations, key=lambda x: float("inf") if x["extra"] is None else x["extra"])[:10]:
            names = "、".join(it["name"] for it in a["items"])
            L.append(f"- {names}：合计变化 {fmt_money(a['extra'])}，已重新选择会员和优惠；各组合互为备选，差额不能相加。")

    if data.get("assumptions"):
        L.append("## 这次计算的假设（和结算页对不上时先查这里）\n")
        for x in data["assumptions"]:
            L.append(f"- {x}")
        L.append("")

    if data.get("warnings"):
        L.append("## 未计入或需要确认的优惠\n")
        L.extend(f"- {warning}" for warning in dict.fromkeys(data["warnings"]))
        L.append("")

    # 提醒
    if R["dups"]:
        L.append("## 下单前检查\n")
        for g in R["dups"]:
            if len(g) > 1:
                L.append(f"- 「{g[0]['name']}」在购物车里出现了 {len(g)} 次，确认不是重复加购")
            else:
                L.append(f"- 「{g[0]['name']}」一次买 {g[0]['qty']} 件，确认保质期内用得完")
        L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cart")
    ap.add_argument("--max-batches", type=int, default=0, help="每个平台最多分几次结算（默认允许按全部商品件数拆单）")
    ap.add_argument("--json")
    a = ap.parse_args()
    try:
        data = load(a.cart)
    except ValueError as exc:
        ap.error(str(exc))
    mb = a.max_batches
    if not mb:
        mb = max(1, sum(it["qty"] for it in data["items"]))
    if mb < 1:
        ap.error("--max-batches 必须是正整数")
    try:
        R = run(data, mb)
    except ValueError as exc:
        ap.error(str(exc))
    print(report(data, R))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"best": R["best"], "simple": R["simple"],
                       "as_of": data["as_of"], "warnings": list(dict.fromkeys(data["warnings"])),
                       "members": {p: {"on": v["on"]["total"] if v["on"] else None,
                                       "off": v["off"]["total"] if v["off"] else None, "curve": v["curve"]}
                                   for p, v in R["members"].items()},
                       "addons": [{"id": x["item"]["id"], "extra": x["extra"], "plan": x["plan"]} for x in R["addons"]],
                       "addon_combinations": [{"ids": [i["id"] for i in x["items"]], "extra": x["extra"], "plan": x["plan"]} for x in R["addon_combinations"]]},
                      f, ensure_ascii=False, indent=2, default=str)


if __name__ == "__main__":
    sys.exit(main())
