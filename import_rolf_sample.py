import os
import sys
import django
from datetime import date

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pricing_app.settings')
django.setup()

from django.contrib.auth import get_user_model
from core.models import Lead, Client, LeadInteraction

User = get_user_model()
admin_user = User.objects.filter(is_superuser=True).first() or User.objects.first()

if not admin_user:
    admin_user = User.objects.create_superuser('admin', 'admin@example.com', 'admin')

# Seed Sample Lead L-0001 if not exists
lead, created = Lead.objects.get_or_create(
    lead_code='L-0001',
    defaults={
        'date_added': date(2026, 9, 28),
        'salesperson': admin_user,
        'country': 'Saudi Arabia',
        'city': 'Riyadh',
        'company_name': 'EXAMPLE Auto Parts Trading',
        'segment': 'Distributor',
        'contact_name': 'Ahmed Example',
        'position': 'Purchasing Manager',
        'contact_phone': '+966 50 000 0000',
        'contact_email': 'ahmed@example.com',
        'whatsapp': '+966 50 000 0000',
        'source': 'Exhibition',
        'products_of_interest': 'ROLF Ultra 5W-30, ROLF GT 5W-40',
        'stage': 'Samples Sent',
        'priority': 'Hot',
        'last_contact_date': date(2026, 10, 1),
        'next_action': 'Call to get feedback on samples',
        'next_action_date': date(2026, 10, 6),
        'est_volume_liters': 5000,
        'revenue': 25000,
        'comments': 'EXAMPLE row — imported from ROLF Sales CRM',
    }
)
print(f"Lead L-0001: {'created' if created else 'already exists'}")

# Seed Sample Client C-0001 if not exists
client, created = Client.objects.get_or_create(
    client_code='C-0001',
    defaults={
        'country': 'United Arab Emirates',
        'city': 'Sharjah',
        'company_name': 'EXAMPLE Lubricants Shop LLC',
        'segment': 'Retailer / Shop',
        'contact_name': 'Omar Example',
        'position': 'Owner',
        'phone': '+971 50 000 0000',
        'email': 'omar@example.com',
        'whatsapp': '+971 50 000 0000',
        'account_manager': admin_user,
        'client_since': date(2026, 3, 15),
        'contract_no': 'S-2026-001',
        'payment_terms': 'Net 30',
        'credit_limit': 20000,
        'last_order_date': date(2026, 9, 20),
        'last_order_value': 8500,
        'sales_ytd': 64000,
        'avg_volume_liters': 1800,
        'status': 'Active',
        'next_followup_date': date(2026, 10, 8),
        'notes': 'EXAMPLE row — imported from ROLF Sales CRM',
    }
)
print(f"Client C-0001: {'created' if created else 'already exists'}")

# Seed Sample Activity Log if not exists
if not LeadInteraction.objects.filter(lead=lead).exists():
    LeadInteraction.objects.create(
        lead=lead,
        author=admin_user,
        activity_type='Call',
        contact_person='Ahmed Example',
        outcome='Positive',
        notes='EXAMPLE — samples received, testing on 2 fleet trucks',
        next_step='Call for feedback',
        next_step_date=date(2026, 10, 6)
    )
    print("Interaction logged for L-0001")

print("Rolf sample seeding complete!")
