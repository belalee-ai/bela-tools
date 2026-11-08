import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def product(name, price, **fields):
    return {"name": name, "shop": "同一店铺", "price": price, **fields}


def calculate(cart, *, with_card=False):
    with tempfile.TemporaryDirectory() as folder:
        work = Path(folder)
        src, dest, card = (work / name for name in ("cart.json", "result.json", "card.html"))
        src.write_text(json.dumps(cart), encoding="utf-8")
        subprocess.run([sys.executable, str(ROOT / "scripts/optimize.py"), str(src),
                        "--json", str(dest)], check=True, capture_output=True, text=True)
        if with_card:
            subprocess.run([sys.executable, str(ROOT / "scripts/build_card.py"), str(dest),
                            str(src), "-o", str(card)], check=True, capture_output=True, text=True)
        return json.loads(dest.read_text(encoding="utf-8")), card.read_text(encoding="utf-8") if with_card else ""


class EligibilityTests(unittest.TestCase):
    def test_one_shop_coupon_across_two_orders(self):
        cart = {
            "items": [product("A", 100), product("B", 100)],
            "platforms": {"taobao": {"coupons": [
                {"name": "平台券", "threshold_on": "list", "threshold": 100, "minus": 40, "count": 2}
            ]}},
            "shops": {"taobao/同一店铺": {"coupons": [
                {"name": "店铺券", "threshold_on": "list", "threshold": 100, "minus": 30, "count": 1}
            ]}},
        }
        result, _ = calculate(cart)
        self.assertEqual(result["best"]["total"], 90)
        uses = [s["discounts"] for b in result["best"]["batches"] for s in b["shops"]]
        self.assertEqual(sum(len(discounts) for discounts in uses), 1)

    def test_shop_threshold_uses_only_eligible_products(self):
        cart = {
            "items": [product("果蔬", 90, category="食品"), product("耳机", 10, category="数码")],
            "shops": {"taobao/同一店铺": {"coupons": [
                {"name": "数码券", "threshold_on": "list", "threshold": 100, "minus": 40, "only_categories": ["数码"]}
            ]}},
        }
        self.assertEqual(calculate(cart)[0]["best"]["total"], 100)

    def test_membership_required_to_use_discounted_offer(self):
        regular = {"platform": "jd", "shop": "京东", "price": 100}
        cart = {
            "items": [{"name": "同款", "offers": [regular, {**regular, "price": 60, "tags": ["plus"]}]}],
            "platforms": {"jd": {"membership": {"name": "PLUS", "fee": 20, "owned": False}}},
        }
        result, _ = calculate(cart)
        self.assertEqual((result["members"]["jd"]["on"], result["members"]["jd"]["off"]), (80, 100))
        self.assertEqual(result["best"]["total"], 80)
        cart["items"][0]["offers"] = [{**regular, "price": 60, "requires_membership": True}]
        result, _ = calculate(cart, with_card=True)
        self.assertIsNone(result["members"]["jd"]["off"])
        self.assertEqual(result["best"]["total"], 80)

    def test_invalid_or_expired_deadline_never_reduces_payment(self):
        for deadline in ("2000-01-01T12:00:00+08:00", "日期看不清"):
            with self.subTest(deadline=deadline):
                cart = {
                    "items": [product("A", 200)],
                    "platforms": {"taobao": {"coupons": [
                        {"name": "活动满100减50", "threshold_on": "list", "threshold": 100, "minus": 50}
                    ]}},
                    "coupon_meta": {"满100减50": {"deadline": deadline}},
                }
                result, card = calculate(cart, with_card=True)
                self.assertEqual(result["best"]["total"], 200)
                self.assertNotIn('data-deadline="2026-11-03T23:59:00+08:00"', card)

    def test_cross_store_deadline_reaches_the_order_card(self):
        activity = {"threshold_on": "list", "every": 100, "minus": 20, "deadline": "2099-01-01T12:30:00+08:00"}
        cart = {"items": [product("A", 100)], "platforms": {"taobao": {"cross_store": activity}}}
        result, card = calculate(cart, with_card=True)
        self.assertEqual(result["best"]["total"], 80)
        self.assertIn('data-deadline="2099-01-01T12:30:00+08:00"', card)
        activity["deadline"] = "2000-01-01T12:30:00+08:00"
        result, _ = calculate(cart)
        self.assertEqual(result["best"]["total"], 100)
        self.assertTrue(result["warnings"])

    def test_missing_membership_fee_is_not_free_membership(self):
        membership = {"name": "示例会员", "owned": False,
                      "coupons": [{"name": "会员券", "threshold_on": "list", "threshold": 100, "minus": 30}]}
        cart = {"items": [product("A", 100)], "platforms": {"taobao": {"membership": membership}}}
        result, _ = calculate(cart)
        self.assertEqual(result["best"]["total"], 100)
        self.assertTrue(result["warnings"])
        membership["fee"] = 0
        self.assertEqual(calculate(cart)[0]["best"]["total"], 70)

    def test_subsidy_requires_confirmation_and_uses_shared_quantity(self):
        subsidy = {"rate": 0.15, "cap": 50, "group": "家电", "limit": 1}
        cart = {"items": [product("A", 100, qty=2, subsidy=subsidy),
                          product("B", 120, subsidy=subsidy)]}
        self.assertEqual(calculate(cart)[0]["best"]["total"], 320)
        for item in cart["items"]:
            item["subsidy"] = {**subsidy, "confirmed": True}
        result, card = calculate(cart, with_card=True)
        self.assertEqual(result["best"]["total"], 302)
        self.assertEqual(sum(b["subsidy"] for b in result["best"]["batches"]), 18)
        self.assertIn("国补", card)

    def test_membership_card_matches_paid_plan(self):
        cart = {
            "items": [product("A", 200)],
            "platforms": {"taobao": {"membership": {
                "name": "88VIP", "fee": 88, "owned": False,
                "coupons": [{"name": "会员券", "threshold_on": "list", "threshold": 200, "minus": 100}],
            }}},
        }
        result, card = calculate(cart, with_card=True)
        self.assertEqual(result["best"]["total"], 188)
        self.assertTrue(result["best"]["members"]["taobao"])
        self.assertNotIn("只为这一车不值得开", card)
        self.assertNotIn("7 天可退", card)


if __name__ == "__main__":
    unittest.main()
