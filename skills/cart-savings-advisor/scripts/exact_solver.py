"""枚举合法单次结算，再用带券库存/补贴余额的动态规划组合订单。"""
import copy
import itertools
from functools import lru_cache


class SearchLimit(Exception):
    pass


def solve(api, data, basket, members, max_batches, budget=300000):
    units = [dict(item, qty=1) for item in basket for _ in range(item['qty'])]
    units.sort(key=lambda item: (item['name'], str(item.get('sku', '')), str(item['id'])))
    offers = [api.available_offers(item, members) for item in units]
    if any(not choices for choices in offers):
        return None
    if len(units) > 18:
        raise ValueError('必买数量超过精确求解上限18件，请分成独立优惠范围后计算。')
    platforms = sorted({o['platform'] for choices in offers for o in choices})
    resources = []
    for platform in platforms:
        rules = data['platforms'].get(platform, {})
        for coupon in api.coupon_pool(data, platform, members.get(platform, False)):
            resources.append(dict(coupon, layer='platform', platform=platform))
        cross = rules.get('cross_store')
        if cross and 'count' in cross:
            for i in range(cross['count']):
                resources.append(dict(cross, layer='cross_store', platform=platform,
                                      _resource_id=f'{platform}/cross/{i}'))
    shops = {o['shop_key']: o for choices in offers for o in choices}
    for key, offer in sorted(shops.items()):
        cfg = api.shop_cfg(data, offer)
        for layer in ('shop', 'shipping'):
            rules = [c for c in cfg.get('coupons' if layer == 'shop' else 'shipping_coupons', [])
                     if members.get(offer['platform']) or not c.get('requires_membership')]
            resources.extend(dict(c, layer=layer, platform=offer['platform'], shop=offer['shop'])
                             for c in api.coupon_copies(rules, key + '/' + layer))
    subsidy_rules = {}
    for choices in offers:
        for o in choices:
            sb = o.get('subsidy')
            if sb:
                group = sb['group']
                prior = subsidy_rules.get(group, sb)
                subsidy_rules[group] = dict(sb, limit=min(prior['limit'], sb['limit']),
                    total_cap=min(prior.get('total_cap', 1e12), sb.get('total_cap', 1e12)))
    groups = sorted(subsidy_rules)
    initial_limits = tuple(subsidy_rules[g]['limit'] for g in groups)
    initial_caps = tuple(round(subsidy_rules[g].get('total_cap', 1e12) * 100) for g in groups)
    ticks = 0
    def tick():
        nonlocal ticks
        ticks += 1
        if ticks > budget:
            raise SearchLimit

    def coupon_variants(rows, platform, stage, used, selected):
        tick()
        stages = ('shop', 'cross_store', 'platform')
        if stage == len(stages):
            yield rows, used, selected
            return
        layer = stages[stage]
        candidates = [(i, c) for i, c in enumerate(resources) if c['platform'] == platform and c['layer'] == layer]
        cross = data['platforms'].get(platform, {}).get('cross_store')
        if layer == 'cross_store' and cross and 'count' not in cross:
            candidates.append((-1, dict(cross, layer=layer, platform=platform, _resource_id=platform+'/cross/automatic')))
        def visit(current, mask, chosen, occupied, last_order=-1):
            tick()
            # 放弃剩余优惠也是一个合法分支。
            yield from coupon_variants(current, platform, stage+1, mask, chosen)
            equivalent = set()
            for index, c in candidates:
                identity = tuple((k, repr(v)) for k,v in sorted(c.items()) if k != '_resource_id')
                group = (c.get('shop'), c.get('stack_group', layer))
                if (c.get('order', 0) < last_order or identity in equivalent or group in occupied
                        or (index >= 0 and mask & (1 << index))):
                    continue
                equivalent.add(identity)
                eligible = [r for r in current if (not c.get('shop') or r['offer']['shop'] == c['shop'])]
                qualified, base, running = api.coupon_basis(c, eligible, layer)
                saving = api.r2(api.coupon_saving(c, base, running))
                if saving <= 0 or not qualified:
                    continue
                updated = copy.deepcopy(current)
                selected_rows = [updated[i] for i, row in enumerate(current) if any(row is q for q in qualified)]
                api.allocate(selected_rows, saving)
                kind = 'member' if c.get('member') else layer
                entry = api.coupon_use(c, platform, kind, saving, c.get('shop'))
                yield from visit(updated, mask | ((1 << index) if index >= 0 else 0),
                                 chosen+[entry], occupied | {group}, c.get('order', 0))
        yield from visit(rows, used, selected, set())

    def subsidy_variants(rows, limits, caps, applied=frozenset()):
        tick()
        yield rows, limits, caps
        # 总额上限下，先给谁使用会改变剩余额度；不能固定按商品排列分配。
        for index, row in enumerate(rows):
            sb = row['offer'].get('subsidy')
            if index in applied or not sb:
                continue
            group = groups.index(sb['group'])
            net = api.r2(row['list']-row['disc'])
            saving = api.r2(min(api.r2(net*sb['rate']), sb.get('cap', net), caps[group]/100))
            if limits[group] <= 0 or saving <= 0:
                continue
            updated = copy.deepcopy(rows)
            updated[index]['subsidy'] = saving
            updated[index]['disc'] = api.r2(updated[index]['disc'] + saving)
            remaining = list(limits)
            remaining[group] -= 1
            balance = list(caps)
            balance[group] -= round(saving*100)
            yield from subsidy_variants(updated, tuple(remaining), tuple(balance), applied | {index})

    @lru_cache(None)
    def batch_options(mask, platform, used, limits, caps):
        indexes = [i for i in range(len(units)) if mask & (1 << i)]
        choices = [[o for o in offers[i] if o['platform'] == platform] for i in indexes]
        if any(not c for c in choices):
            return ()
        results = {}
        for combination in itertools.product(*choices):
            rows = [dict(item=units[i], offer=o, list=o['price'], disc=0.0, subsidy=0.0)
                    for i,o in zip(indexes, combination)]
            for discounted, coupon_mask, uses in coupon_variants(rows, platform, 0, used, []):
                for subsidized, remaining, balances in subsidy_variants(discounted, limits, caps):
                    by_shop = {}
                    for row in subsidized:
                        by_shop.setdefault(row['offer']['shop'], []).append(row)
                    batch = dict(platform=platform, shops=[], platform_coupons=[], coupon_uses=[], cross_store=0, hints=[])
                    for shop, shop_rows in by_shop.items():
                        discounts = [(u['name'],u['saving']) for u in uses if u['kind']=='shop' and u['shop']==shop]
                        batch['shops'].append(dict(shop=shop, subtotal=api.r2(sum(r['list'] for r in shop_rows)), discounts=discounts, rows=copy.deepcopy(shop_rows)))
                    for use in uses:
                        if use['kind']=='cross_store':
                            batch['cross_store'] += use['saving']
                            if use.get('deadline'): batch['cross_store_deadline']=use['deadline']
                        else:
                            batch['coupon_uses'].append(use)
                            if use['kind'] in ('platform','member'):
                                batch['platform_coupons'].append((use['name'],use['saving'],use['kind']=='member'))
                    api.finalize_batch(data,batch)
                    if batch['due_now'] < 0:
                        continue
                    # 每店运费券仅抵该店实际运费；保留不用券和每张可用券的分支。
                    variants = [(batch,coupon_mask)]
                    for shop_index,shop in enumerate(batch['shops']):
                        expanded=[]
                        for prior,prior_mask in variants:
                            expanded.append((prior,prior_mask))
                            for ci,c in enumerate(resources):
                                if c['layer']!='shipping' or c['platform']!=platform or c['shop']!=shop['shop'] or prior_mask & (1<<ci): continue
                                saving=api.r2(api.coupon_saving(c,shop['shipping'],shop['shipping']))
                                if saving<=0 or saving > prior['due_now']: continue
                                revised=copy.deepcopy(prior)
                                revised['shops'][shop_index]['shipping']-=saving
                                revised['shipping']=api.r2(revised['shipping']-saving)
                                revised['pay']=api.r2(revised['pay']-saving)
                                revised['due_now']=api.r2(revised['due_now']-saving)
                                revised['coupon_uses'].append(api.coupon_use(c,platform,'shipping',saving,shop['shop']))
                                expanded.append((revised,prior_mask|(1<<ci)))
                        variants=expanded
                    for variant,final_mask in variants:
                        key=(final_mask,remaining,balances)
                        if key not in results or variant['pay'] < results[key]['pay']:
                            results[key]=variant
        return tuple((b,*key) for key,b in results.items())

    @lru_cache(None)
    def visit(mask, used, limits, caps, counts):
        tick()
        if not mask: return (0,())
        first = mask & -mask
        best=None
        subset=mask
        while subset:
            if subset & first or groups:
                for pi,platform in enumerate(platforms):
                    if counts[pi]>=max_batches: continue
                    next_counts=list(counts);next_counts[pi]+=1
                    for batch, new_used, new_limits, new_caps in batch_options(subset,platform,used,limits,caps):
                        tail=visit(mask ^ subset,new_used,new_limits,new_caps,tuple(next_counts))
                        if tail is None: continue
                        candidate=(api.r2(batch['pay']+tail[0]),(batch,)+tail[1])
                        if best is None or (candidate[0],len(candidate[1])) < (best[0],len(best[1])): best=candidate
            subset=(subset-1)&mask
        return best
    try:
        result=visit((1<<len(units))-1,0,initial_limits,initial_caps,(0,)*len(platforms))
    except SearchLimit:
        raise ValueError(f'精确求解达到 {budget} 次状态预算，尚未证明最优。请提高 search_budget 或按独立优惠范围拆分输入。') from None
    if result is None: return None
    fees=sum(data['platforms'][p]['membership'].get('fee',0) for p,on in members.items()
             if on and not data['platforms'][p]['membership'].get('owned'))
    batches=list(result[1])
    def earliest_deadline(batch):
        deadlines = [u['deadline'] for u in batch['coupon_uses'] if u.get('deadline')]
        if batch.get('cross_store_deadline'):
            deadlines.append(batch['cross_store_deadline'])
        from datetime import datetime
        return min((datetime.fromisoformat(d).timestamp() for d in deadlines), default=float('inf'))
    if not any('total_cap' in o.get('subsidy', {}) for choices in offers for o in choices):
        batches.sort(key=earliest_deadline)
    return dict(total=api.r2(result[0]+fees),goods=result[0],fees=fees,batches=batches,
                due_now=api.r2(sum(b['due_now'] for b in batches)+fees),
                deposit_paid=api.r2(sum(b['deposit_paid'] for b in batches)),
                members=dict(members),search_mode='exact',states=ticks)
