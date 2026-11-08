#!/usr/bin/env python3
"""
把 optimize.py 的结果渲染成「单文件 HTML 下单卡」——这是购物车算账 skill 的最终交付物。

用法：
    python3 build_card.py result.json cart.json -o order_card.html
    python3 build_card.py result.json                 # 没有 cart.json 也能跑（领券去哪领用默认文案）

输入：
    result.json  optimize.py --json 的输出（含 best / simple / members / addons）
    cart.json    （可选）提供 meta（标题/录屏基线）、coupon_meta（每张券去哪领/截止/小魔女提示）

输出：
    一个自包含 HTML（保留红发小魔女向导、动画、勾选盖章、倒计时、进度条），直接发给用户看。

设计要点：
    - 订单区、会员开/不开对比区：100% 由 optimize.py 数据自动生成，不手填。
    - 领券清单：自动列出"这一车实际用到"的券；如 cart.json 提供了 coupon_meta，则富化"去哪领/截止/提示"。
    - 所有金额来自 optimize.py，不心算、不手填估算值。
"""
import argparse
import hashlib
import json
import os
import re
import sys

from optimize import coupon_deadline, platform_name

TPL = os.path.join(os.path.dirname(__file__), "..", "assets", "order_card_template.html")


def money(x):
    if x is None:
        return "—"
    return "¥{:,.2f}".format(x).removesuffix(".00")


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def matching_meta(name, cmap):
    if name in cmap:
        return cmap[name]
    keys = sorted((key for key in cmap if key and key in name), key=len, reverse=True)
    return cmap[keys[0]] if keys else {}


def used_coupons(batch):
    """新结果保留每张券实例；旧报告只能从分单记录恢复其来源。"""
    if "coupon_uses" in batch:
        return batch["coupon_uses"]
    uses = []
    for name, amount, member in batch.get("platform_coupons", []):
        uses.append({"name": name, "kind": "member" if member else "platform", "saving": amount})
    for shop in batch.get("shops", []):
        for name, amount in shop.get("discounts", []):
            uses.append({"name": name, "kind": "shop", "shop": shop["shop"], "saving": amount})
    return uses


def fmt_deadline(dt):
    return dt.isoformat(sep=" ", timespec="seconds") if dt else "未提供截止时间"


def deadline_for(coupon, cmap):
    try:
        return coupon_deadline(coupon, cmap)
    except ValueError as exc:
        raise SystemExit(f"无法确定「{coupon['name']}」的截止时间，请核对券信息后重算：{exc}") from exc


# ============================================================ 区块生成

def build_members(R, cart):
    members = R.get("members") or {}
    if not members:
        return ('<div class="mnote">本方案未采用付费会员权益。如有待核实条件，见下方付款前核对。</div>')
    out = []
    for p, mc in members.items():
        cv = mc.get("curve", {}) or {}
        name = cv.get("name", "会员")
        fee = cv.get("fee", 0)
        owned = cv.get("owned", False)
        note = cv.get("note", "")
        on = mc.get("on")
        off = mc.get("off")
        diff = (off - on) if (on is not None and off is not None) else 0
        selected = bool(R["best"]["members"].get(p))
        if owned:
            badge, bcls = "已是会员", "have"
            verdict = f"本方案{'使用' if selected else '不使用'}{name}权益，本车无需再付年费。"
        elif selected:
            badge, bcls = "本方案需开通", ""
            verdict = f"下列订单按开通{name}计算，年费 {money(fee)} 已计入总额。"
        else:
            badge, bcls = "本方案无需开通", "no"
            verdict = f"下列订单按不开通{name}计算。"
        if on is None or off is None:
            verdict += " " + ("开通" if on is None else "不开") + "状态下没有覆盖必买清单的可用报价，无法比较差额。"
        elif diff > 0:
            verdict += f" 同一车比较，使用会员权益{'扣除本次年费后' if not owned else ''}少花 {money(diff)}。"
        elif diff < 0:
            verdict += f" 同一车比较，开通比不开多花 {money(-diff)}。"
        else:
            verdict += " 两种方案的本次总支出相同。"
        out.append(f'<div class="reco"><span class="badge {bcls}">{badge}</span><span>{esc(verdict)}</span></div>')
        comparison = ((on, "使用", "已付年费不重复计入" if owned else "总额含本次开通费"),
                      (off, "不使用", "总额不含本次开通费"))
        out.append('<div class="mcmp">')
        for amount, label, desc in comparison:
            out.append(f'<div class="col"><span class="lab">{label}{esc(name)}权益</span>'
                       f'<span class="big">{money(amount) if amount is not None else "无可用报价"}</span>'
                       f'<span class="mnote">{desc}</span></div>')
        out.append('</div>')
        if note:
            out.append(f'<p class="mnote">注意：{esc(note)}</p>')
    return "\n".join(out)


def build_coupons(R, cart):
    cmap = cart.get("coupon_meta", {}) or {}
    groups = {"member": [], "platform": [], "shop": [], "shipping": [], "gov": []}
    styles = {"member": "vip", "platform": "plat", "shop": "shop", "shipping": "shop", "gov": "gov"}
    titles = {"member": "会员券", "platform": "平台券", "shop": "店铺券", "shipping": "运费券", "gov": "国补资格"}
    index = 0
    for order, batch in enumerate(R["best"].get("batches", []), 1):
        platform = platform_name(batch["platform"])
        # 同名券按本次所用的每一份列出，各自对应平台、店铺及订单。
        entries = list(used_coupons(batch))
        entries.extend({"kind": "gov", "name": line["name"] + " · 国补", "shop": line["shop"],
                        "saving": line["subsidy"]} for line in batch["lines"] if line.get("subsidy", 0) > 0)
        for coupon in entries:
            kind, name = coupon["kind"], coupon["name"]
            subsidy = kind == "gov"
            meta = {} if subsidy else matching_meta(name, cmap)
            deadline = None if subsidy else deadline_for(coupon, cmap)
            where = meta.get("where") or ("在结算明细核对该商品的补贴资格及抵扣额。" if subsidy
                                           else "在本账号券包或结算页确认有这张券。")
            tip = meta.get("tip") or where
            scope = " · ".join(filter(None, (platform, coupon.get("shop"), f"第 {order} 单")))
            stamp = "已核对" if subsidy else "已确认有券"
            tag = esc(meta.get("tag", titles[kind]))
            tag_class = "tag urgent" if meta.get("urgent") else "tag"
            index += 1
            groups[kind].append(
                f'<div class="coupon {styles[kind]} rise" data-key="c{index}" data-stamp="{stamp}" data-tip="{esc(tip)}">'
                f'<div class="face"><span class="amt">{money(coupon["saving"])}</span><span class="cond">本单预计抵扣</span></div>'
                f'<div class="body"><div class="name">{esc(name)}<span class="{tag_class}">{tag}</span></div>'
                f'<div class="where">{esc(where)}</div>'
                f'<div class="meta">{esc(scope)}' + ('' if subsidy else ' · ' + esc(fmt_deadline(deadline))) + '</div>'
                f'<label class="check"><input type="checkbox" id="c{index}">{stamp}</label></div></div>')
    if not index:
        return '<div class="mnote">本方案没有单独列出的券或国补。</div>'
    return "\n".join(f'<div class="group-title">{titles[kind]}</div>' + "\n".join(parts)
                     for kind, parts in groups.items() if parts)


def build_orders(R, cart):
    batches = R["best"].get("batches", [])
    cmap = cart.get("coupon_meta", {}) or {}
    html = []
    for i, b in enumerate(batches, 1):
        lines = b["lines"]
        names = [line["name"] for line in lines]
        title = " + ".join(names[:2]) + (f" 等{len(names)}项" if len(names) > 2 else "")
        platform = platform_name(b["platform"])
        uses = used_coupons(b)
        deadlines = [deadline_for(c, cmap) for c in uses]
        if b.get("cross_store"):
            rule = {"name": "跨店活动"}
            if b.get("cross_store_deadline"):
                rule["deadline"] = b["cross_store_deadline"]
            deadlines.append(deadline_for(rule, {}))
        valid_deadlines = [d for d in deadlines if d is not None]
        dl_html = ""
        if valid_deadlines:
            first = min(valid_deadlines)
            label = "最早已知截止 " + fmt_deadline(first)
            if len(valid_deadlines) < len(deadlines):
                label += "；另有优惠未提供截止时间"
            dl_html = f'<div class="deadline" data-deadline="{first.isoformat()}">{esc(label)}</div>'
        elif deadlines:
            dl_html = '<div class="deadline">优惠截止时间未提供，下单前核对。</div>'
        used = []
        for coupon in uses:
            scope = {"member": "会员券", "platform": "平台券", "shop": coupon.get("shop") or "店铺券", "shipping": "运费券"}[coupon["kind"]]
            used.append(f'<span>{esc(scope)} · {esc(coupon["name"])} −{money(coupon["saving"])}</span>')
        if b.get("cross_store"):
            used.append(f'<span>跨店满减 −{money(b["cross_store"])}</span>')
        for line in lines:
            if line.get("subsidy"):
                used.append(f'<span class="gov">{esc(line["name"])} · 国补 −{money(line["subsidy"])}</span>')
        for shop in b["shops"]:
            if shop.get("shipping"):
                used.append(f'<span>{esc(shop["shop"])} · 运费 +{money(shop["shipping"])}</span>')
        items = "".join(f'<li><span>{esc(line["name"])} ×{line["qty"]}<br><small>{esc(line["shop"])} {esc(line.get("sku", ""))} {esc(line.get("spec", ""))}</small></span>'
                        f'<span class="num">券前 {money(line["list"])}</span></li>' for line in lines)
        pay = money(b.get("due_now", b["pay"]))
        if b.get("deposit_paid"):
            used.append(f'<span>已付定金 {money(b["deposit_paid"])}；本单总成本 {money(b["pay"])}</span>')
        if b.get("extra_fees"):
            used.append(f'<span>包装及服务费 +{money(b["extra_fees"])}</span>')
        tip = f"第 {i} 单到{platform}，只勾本单清单里的商品，核对抵扣和运费，本次待付 {pay}。"
        html.append(
            f'<article class="order rise" data-key="o{i}" data-stamp="已下单" data-tip="{esc(tip)}">'
            f'<header><div><div class="idx">第 {i} 单 · {esc(platform)}</div><h3>{esc(title)}</h3>{dl_html}</div>'
            f'<div class="pay"><div class="k">本次待付</div><div class="v num">{pay}</div></div></header>'
            f'<div class="inner"><ul>{items}</ul><div class="uses">{"".join(used)}</div>'
            f'<div class="note">券前合计 {money(b["list_total"])}。结算页逐项核对优惠、运费及实付。</div>'
            f'<label class="check"><input type="checkbox" id="o{i}">已下单</label></div></article>')
    return "\n".join(html) or '<div class="mnote">没有可生成的订单。</div>'


def build_verify(R, cart):
    best = R.get("best", {})
    rows = [("计算提醒", w) for w in R.get("warnings", [])]
    rows.extend(("计算假设", a) for a in cart.get("assumptions", []))
    rows.extend([
        ("核对本单商品和店铺", "在本单指定平台勾选相同规格和数量。"),
        ("核对抵扣、运费和实付", "结算页与卡片有差异时，先核对明细，再重新计算。"),
    ])
    if best.get("fees"):
        rows.append(("另付会员费", f'本方案含新增会员费 {money(best["fees"])}，开通前先确认价格与权益。'))
    if any(b.get("subsidy") for b in best.get("batches", [])):
        rows.append(("核对国补", "逐件确认资格仍有效，结算页有对应的补贴抵扣再付款。"))
    rows.append(("退货要再核算", "以订单实际分摊金额和平台退款页为准。"))
    return "\n".join(
        f'<div class="row"><span>✓</span><div><b>{esc(t)}</b><small>{esc(s)}</small></div></div>'
        for t, s in rows)


def build_addons(R, cart):
    entries = R.get("addon_combinations", [])
    if not entries:
        return '<div class="mnote">没有待比较的加购候选。</div>'
    names = {item.get("id", f"item{i+1}"): item["name"] for i, item in enumerate(cart.get("items", []))}
    out = ['<p class="mnote">以下互为备选，差额不能相加；只有原本就需要时才考虑加购。展开可查看该组合的完整订单。</p>']
    for entry in sorted(entries, key=lambda x: float("inf") if x["extra"] is None else x["extra"])[:10]:
        label = "、".join(names.get(i, i) for i in entry["ids"])
        plan = entry.get("plan")
        if plan is None:
            out.append(f'<p>{esc(label)}：没有可行方案。</p>')
            continue
        extra = entry["extra"]
        change = f"少花 {money(-extra)}" if extra < 0 else f"多花 {money(extra)}"
        out.append(f'<details><summary>{esc(label)} · {change} · 总成本 {money(plan["total"])}</summary>')
        membership = "、".join(platform_name(p) for p, on in plan["members"].items() if on) or "无需会员"
        out.append(f'<p>会员：{esc(membership)}；新增会员费 {money(plan["fees"])}。</p>')
        for i, batch in enumerate(plan["batches"], 1):
            goods = "、".join(f'{line["name"]} ×{line["qty"]}（{line["shop"]}）' for line in batch["lines"])
            coupons = "、".join(f'{c["name"]} −{money(c["saving"])}' for c in used_coupons(batch)) or "无券"
            out.append(f'<p>第 {i} 单 · {esc(platform_name(batch["platform"]))}：{esc(goods)}。'
                       f'优惠券：{esc(coupons)}；跨店优惠 {money(batch["cross_store"])}；国补 {money(batch["subsidy"])}；'
                       f'运费 {money(batch["shipping"])}；附加费 {money(batch.get("extra_fees", 0))}；'
                       f'已付定金 {money(batch.get("deposit_paid", 0))}；本次待付 {money(batch.get("due_now", batch["pay"]))}。</p>')
        out.append('</details>')
    if len(entries) > 10:
        out.append(f'<p class="mnote">共计算 {len(entries)} 种组合，此处列出总额变化最小的10种，完整结果见 result.json。</p>')
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="把 optimize.py 结果渲染成 HTML 下单卡")
    ap.add_argument("result", help="optimize.py --json 输出")
    ap.add_argument("cart", nargs="?", help="cart.json（提供 meta / coupon_meta）")
    ap.add_argument("-o", "--out", default="order_card.html")
    a = ap.parse_args()

    R = json.load(open(a.result, encoding="utf-8"))
    cart = {}
    if a.cart:
        try:
            cart = json.load(open(a.cart, encoding="utf-8"))
        except Exception as e:
            ap.error(f"无法读取 cart.json，请先核对购物车输入：{e}")
    meta = cart.get("meta", {}) or {}

    best = R.get("best")
    if best is None:
        ap.error("没有可执行的必买方案，不能生成下单卡。")
    simple = R.get("simple") or {}
    batches = best.get("batches", [])
    original = sum(l.get("original", 0) for b in batches for l in b.get("lines", []))
    n_items = sum(line["qty"] for b in batches for line in b["lines"])
    baseline = meta.get("baseline_one_order")
    fees = best.get("fees", 0)

    diff = simple.get("total")
    hero_sub = ""
    if diff is not None:
        diff -= best["total"]
        hero_sub = (f"与相同商品报价和会籍、每个平台一次结算相比，本方案"
                    + (f"少花 {money(diff)}。" if diff >= 0 else f"多花 {money(-diff)}。"))
    if best.get("deposit_paid"):
        hero_sub += f" 总成本含已付定金 {money(best['deposit_paid'])}，本次待付 {money(best['due_now'])}。"
    if not batches:
        hero_sub += " 当前没有必买商品，可以暂不购买。"
    if fees:
        hero_sub += f" 总额已含新增会员费 {money(fees)}。"
    if baseline is not None:
        hero_sub += f" 录屏所示一次结算金额 {money(baseline)}，比较前核对商品、账号及会员费口径。"
    if R.get("as_of"):
        hero_sub += " 计算时间 " + R["as_of"]
    guide_intro = (f"这一车 {n_items} 件，共 {len(batches)} 单。先核对会员和券，再按清单分单结算。")
    final_msg = (f"清单已勾选完毕。这一车预计总支出 {money(best.get('total'))}。")

    repl = {
        "{{STATE_KEY}}": hashlib.sha256(json.dumps(best, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24],
        "{{SECTION_ADDONS}}": build_addons(R, cart),
        "{{TITLE}}": esc(meta.get("title", "大促拆单下单卡")),
        "{{SUBTITLE}}": esc(meta.get("subtitle", "根据购物车计算")),
        "{{HERO_TITLE}}": esc(meta.get("hero_title", f"当前推荐方案 · 分 {len(batches)} 单结算")),
        "{{ORIG}}": money(original),
        "{{SIMPLE}}": money(simple.get("total")),
        "{{BEST}}": money(best.get("total")),
        "{{HERO_SUB}}": esc(hero_sub),
        "{{SECTION_MEMBERS}}": build_members(R, cart),
        "{{SECTION_COUPONS}}": build_coupons(R, cart),
        "{{SECTION_ORDERS}}": build_orders(R, cart),
        "{{SECTION_VERIFY}}": build_verify(R, cart),
        "{{GUIDE_INTRO}}": esc(guide_intro),
        "{{FINAL_MSG}}": esc(final_msg),
    }

    tpl = open(TPL, encoding="utf-8").read()
    tpl = re.sub(r"\{\{[A-Z_]+\}\}", lambda m: repl[m.group()], tpl)
    open(a.out, "w", encoding="utf-8").write(tpl)
    print(f"已生成 {a.out}（{len(batches)} 单 / {n_items} 件 / 到手 {money(best.get('total'))}）")


if __name__ == "__main__":
    sys.exit(main())
