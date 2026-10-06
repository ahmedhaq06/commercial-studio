from django.contrib.auth.models import User, Group
from django.test import TestCase
from django.urls import reverse
import json

from .models import ExportFile, Product, Lead


class ProductTests(TestCase):
    def test_selling_price_is_calculated(self):
        product = Product.objects.create(
            sku="SKU-1",
            name="Engine Oil",
            quantity=10,
            production_price=100,
            profit_percent=15,
        )
        self.assertEqual(str(product.selling_price), "115.00")


class ExportTests(TestCase):
    def test_export_creates_file_record(self):
        user = User.objects.create_user(username="user", email="user@example.com", password="pass")
        # Add user to Production group to pass access check
        group, _ = Group.objects.get_or_create(name="Production")
        user.groups.add(group)

        Product.objects.create(
            sku="SKU-1",
            name="Engine Oil",
            quantity=10,
            production_price=100,
            profit_percent=15,
        )
        self.client.login(username="user@example.com", password="pass")
        response = self.client.get(reverse("export_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ExportFile.objects.count(), 1)


class AccessControlTests(TestCase):
    def setUp(self):
        # Create Sales and Production groups
        self.sales_group, _ = Group.objects.get_or_create(name="Sales")
        self.prod_group, _ = Group.objects.get_or_create(name="Production")

        # Create users
        self.sales_user = User.objects.create_user(username="sales_member", email="sales@example.com", password="pass")
        self.sales_user.groups.add(self.sales_group)

        self.prod_user = User.objects.create_user(username="prod_member", email="prod@example.com", password="pass")
        self.prod_user.groups.add(self.prod_group)

        self.admin_user = User.objects.create_user(username="Arshad al Haq", email="admin@example.com", password="pass")

    def test_sales_user_access(self):
        self.client.login(username="sales@example.com", password="pass")
        
        # Sales user should be able to access CRM dashboard and CRM Analytics
        for url_name in ["crm_dashboard", "crm_analytics"]:
            response = self.client.get(reverse(url_name))
            self.assertEqual(response.status_code, 200)

        # Sales user should be able to access products list (read-only)
        response = self.client.get(reverse("products_list"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_edit'])

        # Sales user should NOT be able to access pricing list
        response = self.client.get(reverse("pricing_list"))
        self.assertRedirects(response, reverse("dashboard"))

    def test_production_user_access(self):
        self.client.login(username="prod@example.com", password="pass")

        # Production user should be able to access products list
        response = self.client.get(reverse("products_list"))
        self.assertEqual(response.status_code, 200)

        # Production user should be able to access pricing list
        response = self.client.get(reverse("pricing_list"))
        self.assertEqual(response.status_code, 200)

        # Production user should NOT be able to access CRM dashboard or CRM Analytics
        for url_name in ["crm_dashboard", "crm_analytics"]:
            response = self.client.get(reverse(url_name))
            self.assertRedirects(response, reverse("dashboard"))

    def test_admin_user_access(self):
        self.client.login(username="admin@example.com", password="pass")

        # Admin user should be able to access everything
        for url_name in ["crm_dashboard", "crm_analytics", "products_list", "pricing_list"]:
            response = self.client.get(reverse(url_name))
            self.assertEqual(response.status_code, 200)


class AnalyticsTests(TestCase):
    def setUp(self):
        self.sales_group, _ = Group.objects.get_or_create(name="Sales")
        self.sales_user = User.objects.create_user(username="rep1", email="rep1@example.com", password="pass")
        self.sales_user.groups.add(self.sales_group)
        
        # Create a product
        self.product = Product.objects.create(
            sku="SKU-TEST",
            name="Testing Product",
            quantity=100,
            production_price=50,
            profit_percent=20
        )
        
        # Create won lead with quantity
        self.won_lead = Lead.objects.create(
            salesperson=self.sales_user,
            contact_name="Client Won",
            opportunity_name="Opp Won",
            revenue=5000,
            stage="Won",
            product=self.product,
            quantity=50
        )
        
        # Create qualified lead (open)
        self.open_lead = Lead.objects.create(
            salesperson=self.sales_user,
            contact_name="Client Open",
            opportunity_name="Opp Open",
            revenue=3000,
            stage="Qualified"
        )
        
    def test_analytics_kpis_and_json(self):
        self.client.login(username="rep1@example.com", password="pass")
        response = self.client.get(reverse("crm_analytics"))
        self.assertEqual(response.status_code, 200)
        
        # Verify context data values
        self.assertEqual(float(response.context['total_pipeline_value']), 3000.0)
        self.assertEqual(float(response.context['global_win_rate']), 50.0)
        self.assertEqual(response.context['stale_leads_count'], 0)
        
        # Check JSON payload structure
        data = json.loads(response.context['analytics_data_json'])
        self.assertEqual(data['stale_leads_count'], 0)
        self.assertEqual(len(data['top_products']), 1)
        self.assertEqual(data['top_products'][0]['product'], "SKU-TEST - Testing Product")
        self.assertEqual(data['top_products'][0]['quantity'], 50.0)
        
        self.assertEqual(data['funnel']['New'], 0)
        self.assertEqual(data['funnel']['Qualified'], 1)
        self.assertEqual(data['funnel']['Won'], 1)


class AuthenticationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="AhmedHaq",
            email="ahmed@example.com",
            password="SecurePassword123!"
        )

    def test_login_with_exact_email(self):
        login_success = self.client.login(username="ahmed@example.com", password="SecurePassword123!")
        self.assertTrue(login_success)

    def test_login_with_uppercase_email(self):
        login_success = self.client.login(username="AHMED@EXAMPLE.COM", password="SecurePassword123!")
        self.assertTrue(login_success)

    def test_login_with_username_is_rejected(self):
        # Username login must be rejected
        login_success = self.client.login(username="AhmedHaq", password="SecurePassword123!")
        self.assertFalse(login_success)

    def test_login_form_post_with_email(self):
        response = self.client.post(reverse("login"), {
            "username": "ahmed@example.com",
            "password": "SecurePassword123!",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")

    def test_login_form_post_with_username_is_rejected(self):
        # Submitting username on the login form fails authentication
        response = self.client.post(reverse("login"), {
            "username": "AhmedHaq",
            "password": "SecurePassword123!",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['user'].is_authenticated)

    def test_login_with_invalid_password(self):
        login_success = self.client.login(username="ahmed@example.com", password="WrongPassword")
        self.assertFalse(login_success)