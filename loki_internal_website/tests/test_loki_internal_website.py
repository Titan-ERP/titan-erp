from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestLokiInternalWebsite(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.name = "LOKI"
        cls.internal_user = new_test_user(
            cls.env,
            login="loki.dashboard.user",
            groups="base.group_user,sales_team.group_sale_salesman",
        )

    def test_loki_requires_authentication(self):
        self.url_open("/web/session/logout")
        response = self.url_open("/loki")
        self.assertIn("/web/login", response.url)
        self.assertNotIn('data-loki-dashboard="true"', response.text)

    def test_internal_user_can_open_loki_dashboard(self):
        self.authenticate(self.internal_user.login, self.internal_user.login)
        response = self.url_open("/loki")
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-loki-dashboard="true"', response.text)
        self.assertIn("CRM pipeline", response.text)
        self.assertIn("Parcel links", response.text)
        self.assertIn("AI Research", response.text)
        self.assertIn("AI Research is not enabled.", response.text)

    def test_dashboard_is_not_available_for_another_company(self):
        other_company = self.env["res.company"].create({"name": "Not LOKI"})
        self.internal_user.company_ids |= other_company
        self.internal_user.company_id = other_company
        self.authenticate(self.internal_user.login, self.internal_user.login)
        response = self.url_open("/loki")
        self.assertEqual(response.status_code, 404)
