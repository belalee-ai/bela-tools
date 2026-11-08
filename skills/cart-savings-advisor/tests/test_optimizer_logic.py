"""独立反例与小规模穷举对照；数值均为合成规则，不代表平台活动。"""
import itertools
import json
from pathlib import Path
import random
import re
import sys
import tempfile
import unittest

from test_checkout_eligibility import calculate, product, ROOT
sys.path.insert(0, str(ROOT / 'scripts'))
import optimize


def coupon(threshold, minus, **fields):
    return dict(threshold=threshold, minus=minus, threshold_on='list', **fields)


def normalized(cart):
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / 'cart.json'
        path.write_text(json.dumps(cart))
        return optimize.load(path)


def solve(cart, batches=20):
    return optimize.run(normalized(cart), batches)


def oracle(prices, coupons):
    """独立枚举集合划分及订单-优惠券配对；不调用生产计价函数。"""
    partitions = []
    def partition(index, groups):
        if index == len(prices):
            partitions.append([g[:] for g in groups])
            return
        for group in groups:
            group.append(index)
            partition(index + 1, groups)
            group.pop()
        groups.append([index])
        partition(index + 1, groups)
        groups.pop()
    partition(0, [])
    best = sum(prices)
    for groups in partitions:
        def assign(index, available, saving):
            nonlocal best
            if index == len(groups):
                best = min(best, sum(prices) - saving)
                return
            assign(index + 1, available, saving)
            for ci in available:
                threshold, minus, eligible = coupons[ci]
                basis = sum(prices[i] for i in groups[index] if i in eligible)
                if basis >= threshold:
                    assign(index + 1, available - {ci}, saving + min(minus, basis))
        assign(0, set(range(len(coupons))), 0)
    return best


class LogicTests(unittest.TestCase):
    def test_global_coupon_allocation_130(self):
        cart = dict(items=[product('A', 120, tags=['a']), product('B', 100)],
                    platforms={'taobao': {'coupons': [coupon(100, 50), coupon(120, 40, only_tags=['a'])]}})
        self.assertEqual(solve(cart)['best']['total'], 130)

    def test_input_order_invariant_360(self):
        for prices in ([65,66,67,68,35,34,33,32], [65,35,66,34,67,33,68,32]):
            cart = dict(items=[product(str(p), p) for p in prices],
                        platforms={'taobao': {'coupons': [coupon(100, 10, count=4)]}})
            self.assertEqual(solve(cart)['best']['total'], 360)

    def test_quantity_can_split_beyond_five_orders(self):
        cart = dict(items=[product('A', 100, qty=6)],
                    platforms={'taobao': {'coupons': [coupon(100, 20, count=6)]}})
        result, _ = calculate(cart)
        self.assertEqual(result['best']['total'], 480)
        self.assertEqual(len(result['best']['batches']), 6)
        self.assertEqual(sum(l['qty'] for b in result['best']['batches'] for l in b['lines']), 6)

    def test_can_skip_coupon_to_keep_free_shipping(self):
        cart = dict(items=[product('A', 100)], shops={'同一店铺': {
            'coupons': [coupon(0, 2)], 'shipping': dict(fee=10, free_over=99, threshold_on='running')}})
        self.assertEqual(solve(cart)['best']['total'], 100)

    def test_threshold_basis_explicit_or_excluded(self):
        c = coupon(600, 50)
        cart = dict(items=[product('A', 300, qty=2)], shops={'同一店铺': {'coupons': [coupon(300,30)]}},
                    platforms={'taobao': {'coupons': [c]}})
        self.assertEqual(solve(cart)['best']['total'], 520)
        c['threshold_on'] = 'running'
        self.assertEqual(solve(cart)['best']['total'], 550)  # 放弃30元店铺券，使用50元平台券。
        del c['threshold_on']
        result, html = calculate(cart, with_card=True)
        self.assertEqual(result['best']['total'], 570)
        self.assertIn('门槛口径未确认', html)

    def test_owned_membership_has_no_fake_breakeven(self):
        cart = dict(items=[product('A', 200)], platforms={'taobao': {'membership': dict(
            owned=True, name='88VIP', fee=88, coupons=[coupon(200, 100)])}})
        result, html = calculate(cart, with_card=True)
        self.assertEqual(result['best']['fees'], 0)
        self.assertIsNone(result['members']['taobao']['curve']['breakeven'])
        self.assertNotIn('不开更划算', html)
        self.assertNotIn('理想可用券面额', html)
        self.assertIn('已付年费不重复计入', html)

    def test_member_coupon_replacing_normal_coupon_has_no_incremental_value(self):
        cart = dict(items=[product('A', 200)], platforms={'taobao': {
            'coupons': [coupon(200,50)], 'membership': dict(fee=30, coupons=[coupon(200,50)])}})
        result = solve(cart)
        self.assertEqual((result['members']['taobao']['on']['total'], result['members']['taobao']['off']['total']), (180,150))

    def test_addons_reoptimize_membership_and_combinations(self):
        cart = dict(items=[product('A',99),product('B',1,must=False)], platforms={'taobao': {
            'membership':dict(fee=10,coupons=[coupon(100,50)])}})
        self.assertEqual(solve(cart)['addons'][0]['extra'], -39)
        cart['items'] = [product('A',98),product('B',1,must=False),product('C',1,must=False)]
        result, html = calculate(cart, with_card=True)
        pair = next(a for a in result['addon_combinations'] if len(a['ids'])==2)
        self.assertEqual(pair['extra'], -38)
        self.assertTrue(pair['plan']['members']['taobao'])
        self.assertIn('完整订单', html)
        self.assertIn('少花 ¥38', html)

    def test_empty_cart_and_unavailable_optional(self):
        cart = dict(items=[product('A',10,must=False,requires_membership=True)])
        result, html = calculate(cart, with_card=True)
        self.assertEqual(result['best']['total'],0)
        self.assertIsNone(result['addons'][0]['extra'])
        self.assertIn('可以暂不购买',html)
        self.assertIn('boxes.length ?',html)

    def test_baseline_uses_same_offer(self):
        cart = dict(items=[{'name':'A','offers':[dict(platform='taobao',shop='T',price=90),dict(platform='jd',shop='J',price=100)]}],
                    platforms={'jd': {'coupons':[coupon(0,40)]}})
        result, html = calculate(cart, with_card=True)
        self.assertEqual((result['best']['total'],result['simple']['total']), (60,60))
        self.assertNotIn('最低券前报价',html)

    def test_subsidy_caps_shipping_fee_and_deposit(self):
        sb=dict(confirmed=True,rate=.2,cap=15,limit=2,total_cap=25,group='G')
        cart=dict(items=[product('A',100,qty=2,subsidy=sb,deposit_paid=10)],shops={'同一店铺': {
            'shipping':dict(fee=10), 'shipping_coupons':[coupon(0,30)],'packaging_fee':2,'service_fee':3}})
        result, html=calculate(cart,with_card=True)
        best=result['best']
        self.assertEqual((best['total'],best['deposit_paid'],best['due_now']),(180,20,160))
        self.assertEqual(sum(b['subsidy'] for b in best['batches']),25)
        self.assertIn('运费券',html)
        self.assertIn('本次待付 ¥160',html)
        self.assertIn('包装及服务费 +¥5',html)
        self.assertEqual(sum(c['saving'] for b in best['batches'] for c in b['coupon_uses'] if c['kind']=='shipping'),10)

    def test_subsidy_cap_allocation_order_with_shipping(self):
        sb=dict(confirmed=True,rate=.1,limit=2,total_cap=15,group='G')
        cart=dict(items=[product('A',100,shop='a',subsidy=sb),product('B',100,shop='b',subsidy=sb)],
                  shops={'a':{'shipping':dict(fee=20,free_over=95,threshold_on='running')}})
        self.assertEqual(solve(cart)['best']['total'],185)

    def test_shipping_coupon_requires_membership(self):
        cart=dict(items=[product('A',100)],platforms={'taobao':{'membership':dict(fee=3)}},
                  shops={'同一店铺':{'shipping':dict(fee=10),'shipping_coupons':[coupon(0,10,requires_membership=True)]}})
        self.assertEqual(solve(cart)['best']['total'],103)

    def test_stack_group_order_is_fixed(self):
        cart=dict(items=[product('A',100)],platforms={'taobao':{'coupons':[
            coupon(0,20,stack_group='a',order=1),dict(rate=.5,stack_group='b',order=2)]}})
        self.assertEqual(solve(cart)['best']['total'],40)
        cart['platforms']['taobao']['coupons'][0]['order']=3
        self.assertEqual(solve(cart)['best']['total'],30)
        del cart['platforms']['taobao']['coupons'][0]['order']
        with self.assertRaisesRegex(ValueError,'order'):
            solve(cart)

    def test_money_allocation_conserves_cents(self):
        cart=dict(items=[product('A',.01),product('B',.01),product('C',.01)],
                  platforms={'taobao':{'coupons':[coupon(0,.01)]}})
        best=solve(cart)['best']
        self.assertEqual(best['total'],.02)
        self.assertAlmostEqual(sum(l['net'] for b in best['batches'] for l in b['lines']),.02)

    def test_duplicate_detection_uses_full_spec(self):
        items=[dict(name='iPhone 18 Pro 1TB',qty=3),dict(name='iPhone 18 Pro 512GB',qty=1)]
        self.assertEqual(optimize.duplicates(items),[])
        self.assertEqual(len(optimize.duplicates(items+[items[0]])),1)
        self.assertEqual(optimize.fmt_money(1000), '¥1,000')

    def test_card_progress_is_plan_specific(self):
        keys=[]
        for price in (10,20):
            _,html=calculate(dict(items=[product('A',price)]),with_card=True)
            keys.append(re.search(r'var KEY="([^"]+)"',html).group(1))
            self.assertNotIn('{{',html)
            self.assertIn('<meta charset="utf-8">',html)
        self.assertNotEqual(*keys)

    def test_budget_exhaustion_never_returns_unproved_plan(self):
        with self.assertRaisesRegex(ValueError,'尚未证明最优'):
            solve(dict(search_budget=1,items=[product('A',100)]))

    def test_negative_and_duplicate_inputs_fail_clearly(self):
        for items in ([product('A',-1)], [product('A',1,id='same'),product('B',2,id='same')]):
            with self.assertRaises(ValueError):
                solve(dict(items=items))

    def test_random_small_carts_against_independent_oracle(self):
        rng=random.Random(20261101)
        for iteration in range(25):
            n=rng.randint(1,5)
            prices=[rng.randint(1,12)*10 for _ in range(n)]
            rules=[]
            for j in range(rng.randint(1,3)):
                eligible=set(rng.sample(range(n),rng.randint(1,n)))
                rules.append((rng.randint(1,15)*10,rng.randint(1,5)*10,eligible))
            coupons=[coupon(t,m,only_tags=[str(i) for i in scope]) for t,m,scope in rules]
            cart=dict(items=[product(str(i),p,tags=[str(i)]) for i,p in enumerate(prices)],platforms={'taobao':{'coupons':coupons}})
            with self.subTest(iteration=iteration):
                self.assertEqual(solve(cart)['best']['total'],oracle(prices,rules))


if __name__=='__main__':
    unittest.main()
