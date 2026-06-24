from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase


class TestSaleCouponTaxCloudCommon(TransactionCase):
    """The aim of these tests is NOT to test coupon programs, but only that
    what we send to TaxCloud is coherent to the application of discounts.
    There are weird things that may happen with poorly configured discounts.
    E.g. we can remove 100$ on product C, but product C only costs 50$.
    That means that the other 50$ are deduced from the rest of the order.
    We do the same thing in TaxCloud: if the discount applies to C,
    we try to remove everything from the C line(s),
    and if there is a remainder we remove from other lines.
    Worst case, the whole order can have a negative price.
    In TaxCloud negative prices cannot exist, so we would just consider the
    order to be 0 on all lines.
    Note that mindful sellers should avoid such situations by themselves.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.env["loyalty.program"].search([]).sudo().write({"sale_ok": False})

        cls.customer = cls.env["res.partner"].create(
            {
                "name": "Theodore John K.",
            }
        )
        cls.fiscal_position = cls.env["account.fiscal.position"].create(
            {
                "name": "BurgerLand",
                "is_taxcloud": True,
            }
        )
        cls.order = cls.env["sale.order"].create(
            {
                "partner_id": cls.customer.id,
                "fiscal_position_id": cls.fiscal_position.id,
            }
        )
        cls.tic_category = cls.env["product.tic.category"].create(
            {
                "code": 20110,
                "description": "Computers",
            }
        )

        def create_product(name, price):
            product = cls.env["product.product"].create(
                {
                    "name": name,
                    "list_price": price,
                    "sale_ok": True,
                    "tic_category_id": cls.tic_category.id,
                    "taxes_id": False,
                }
            )
            return product

        cls.product_A = create_product("A", 100)
        cls.product_B = create_product("B", 5)
        cls.product_C = create_product("C", 10)

        def create_line(product, quantity):
            line = cls.env["sale.order.line"].create(
                {
                    "order_id": cls.order.id,
                    "product_id": product.id,
                    "product_uom_qty": quantity,
                }
            )
            return line

        lines = (
            create_line(cls.product_A, 1)
            + create_line(cls.product_B, 10)
            + create_line(cls.product_C, 1)
        )

        cls.order.write({"order_line": [(6, 0, lines.ids)]})

        cls.program_order_percent = cls.env["loyalty.program"].create(
            {
                "name": "10% on order",
                "applies_on": "current",
                "trigger": "with_code",
                "rule_ids": [(0, 0, {})],
                "reward_ids": [
                    (
                        0,
                        0,
                        {
                            "reward_type": "discount",
                            "discount": 10,
                            "discount_applicability": "order",
                        },
                    )
                ],
            }
        )
        cls.program_free_product_C = cls.env["loyalty.program"].create(
            {
                "name": "Free product C",
                "applies_on": "current",
                "rule_ids": [(0, 0, {})],
                "trigger": "with_code",
                "reward_ids": [
                    (
                        0,
                        0,
                        {
                            "reward_type": "discount",
                            "discount": 100,
                            "discount_mode": "percent",
                            "discount_applicability": "specific",
                            "discount_product_ids": cls.product_C,
                            "discount_max_amount": cls.product_C.lst_price,
                        },
                    )
                ],
            }
        )
        cls.all_programs = cls.program_order_percent + cls.program_free_product_C

        def generate_coupon(program):
            Generate = cls.env["loyalty.generate.wizard"].with_context(
                active_id=program.id
            )
            Generate.create(
                {
                    "coupon_qty": 1,
                    "points_granted": 1,
                }
            ).generate_coupons()

        for program in cls.all_programs:
            generate_coupon(program)

    def _apply_promo_code(self, order, code, no_reward_fail=True):
        status = order._try_apply_code(code)
        if "error" in status:
            raise ValidationError(status["error"])
        if not status and no_reward_fail:
            # Can happen if global discount got filtered out in `_get_claimable_rewards`
            raise ValidationError(self.env._("No reward to claim with this coupon"))
        coupons = self.env["loyalty.card"]
        rewards = self.env["loyalty.reward"]
        for coupon, coupon_rewards in status.items():
            coupons |= coupon
            rewards |= coupon_rewards
        if len(coupons) == 1 and len(rewards) == 1:
            status = order._apply_program_reward(rewards, coupons)
            if "error" in status:
                raise ValidationError(status["error"])
