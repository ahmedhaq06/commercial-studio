from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


class MasterProduct(models.Model):
    code = models.CharField(max_length=50, db_index=True, verbose_name="Product Code")
    description = models.CharField(max_length=500, verbose_name="Description")
    viscosity = models.CharField(max_length=100, blank=True, verbose_name="Viscosity")
    specification = models.CharField(max_length=500, blank=True, verbose_name="Specification")
    approval = models.CharField(max_length=500, blank=True, verbose_name="Approval")
    packaging = models.CharField(max_length=100, blank=True, verbose_name="Packaging")
    liters_per_case = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Liters per case")

    def __str__(self):
        return self.code

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.pricing_records.update(name=self.description)


class Product(models.Model):
    master = models.ForeignKey(MasterProduct, on_delete=models.CASCADE, null=True, blank=True, related_name="pricing_records")
    sku = models.CharField(max_length=50, db_index=True)
    name = models.CharField(max_length=200)
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    production_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    profit_percent = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    selling_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="updated_products",
    )
    updated_at = models.DateTimeField(auto_now=True)
    effective_date = models.DateField(null=True, blank=True)

    def calculate_selling_price(self):
        return (self.production_price + (self.production_price * self.profit_percent / Decimal("100"))).quantize(Decimal("0.01"))

    def save(self, *args, **kwargs):
        if self.master:
            self.name = self.master.description
        self.selling_price = self.calculate_selling_price()
        super().save(*args, **kwargs)


class ProductChange(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="changes")
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    field_name = models.CharField(max_length=100)
    old_value = models.CharField(max_length=255, blank=True)
    new_value = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class ExportFile(models.Model):
    file = models.FileField(upload_to="exports/")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    created_at = models.DateTimeField(auto_now_add=True)


class ImportFile(models.Model):
    file = models.FileField(upload_to="imports/")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    template_mode = models.BooleanField(default=False)
    processed = models.BooleanField(default=False)
    error_summary = models.JSONField(null=True, blank=True)

class Quotation(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="quotations")
    salesperson = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="quotations")
    customer_name = models.CharField(max_length=200)
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    discount_percent = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    final_price = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    def calculate_final_price(self):
        discount = self.product.selling_price * self.discount_percent / Decimal("100")
        return ((self.product.selling_price - discount) * self.quantity).quantize(Decimal("0.01"))

    def save(self, *args, **kwargs):
        if not self.final_price:
            self.final_price = self.calculate_final_price()
        super().save(*args, **kwargs)


class Pricing(models.Model):
    product = models.ForeignKey(MasterProduct, on_delete=models.CASCADE, related_name="pricings", verbose_name="Product")
    packaging = models.CharField(max_length=100, blank=True, verbose_name="Packaging")
    production_cost_per_liter = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Production Cost Per Liter")
    production_cost_date = models.DateTimeField(auto_now=True, verbose_name="Production Cost Date")
    final_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Final Cost")
    selling_price = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Selling Price")
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Quantity")
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Price")
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Discount")

    def __str__(self):
        return f"Pricing for {self.product.code}"


class Notification(models.Model):
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sent_notifications")
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications", null=True, blank=True)
    message = models.TextField()
    read_by = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="read_notifications", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class Lead(models.Model):
    STATUS_CHOICES = (
        ('New', 'New'),
        ('Contacted', 'Contacted'),
        ('Interested', 'Interested'),
        ('Samples Sent', 'Samples Sent'),
        ('Offer Sent', 'Offer Sent'),
        ('Negotiation', 'Negotiation'),
        ('Won', 'Won'),
        ('Lost', 'Lost'),
        ('On Hold', 'On Hold'),
        # Backward compatibility aliases
        ('Suspect', 'Suspect (New)'),
        ('Prospect', 'Prospect (Contacted)'),
        ('Approach', 'Approach (Interested)'),
        ('Closing', 'Closing (Negotiation)'),
        ('Order', 'Order (Won)'),
        ('PostSale', 'Post-Sale'),
    )

    PRIORITY_CHOICES = (
        ('Hot', 'Hot'),
        ('Warm', 'Warm'),
        ('Cold', 'Cold'),
    )

    SEGMENT_CHOICES = (
        ('Distributor', 'Distributor'),
        ('Wholesaler', 'Wholesaler'),
        ('Retailer / Shop', 'Retailer / Shop'),
        ('Workshop / Service', 'Workshop / Service'),
        ('Fleet / Transport', 'Fleet / Transport'),
        ('Industrial', 'Industrial'),
        ('Marketplace', 'Marketplace'),
        ('Other', 'Other'),
    )

    lead_code = models.CharField(max_length=20, blank=True, null=True, verbose_name="Lead ID Code")
    date_added = models.DateField(default=timezone.now, verbose_name="Date Added")
    salesperson = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="leads")
    country = models.CharField(max_length=100, default="Saudi Arabia", verbose_name="Country")
    city = models.CharField(max_length=100, default="Riyadh", verbose_name="City")
    company_name = models.CharField(max_length=255, default="Unspecified Company", blank=True, verbose_name="Company / Account Name")
    segment = models.CharField(max_length=100, choices=SEGMENT_CHOICES, default="Distributor", verbose_name="Segment")
    contact_name = models.CharField(max_length=255, verbose_name="Contact Person")
    position = models.CharField(max_length=100, blank=True, null=True, verbose_name="Position")
    contact_phone = models.CharField(max_length=50, blank=True, null=True, verbose_name="Phone")
    contact_email = models.EmailField(blank=True, null=True, verbose_name="Email")
    whatsapp = models.CharField(max_length=50, blank=True, null=True, verbose_name="WhatsApp")
    source = models.CharField(max_length=100, blank=True, default="Exhibition", verbose_name="Lead Source")
    products_of_interest = models.TextField(blank=True, null=True, verbose_name="Products of Interest")
    
    opportunity_name = models.CharField(max_length=255, blank=True, null=True, verbose_name="Opportunity Name")
    revenue = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Est. Deal Value (USD)")
    stage = models.CharField(max_length=30, choices=STATUS_CHOICES, default='New', verbose_name="Status")
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='Hot', verbose_name="Priority")
    rating = models.IntegerField(default=3, verbose_name="Rating (1-5)")
    
    last_contact_date = models.DateField(null=True, blank=True, verbose_name="Last Contact Date")
    next_action = models.CharField(max_length=255, blank=True, null=True, verbose_name="Next Action")
    next_action_date = models.DateField(null=True, blank=True, verbose_name="Next Action Date")
    
    est_volume_liters = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Est. Volume (L/month)")
    comments = models.TextField(blank=True, null=True, verbose_name="Comments / Notes")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    product = models.ForeignKey('Product', on_delete=models.SET_NULL, null=True, blank=True, related_name="leads")
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    won_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        code_prefix = f"[{self.lead_code}] " if self.lead_code else ""
        return f"{code_prefix}{self.company_name} - {self.contact_name}"

    @property
    def days_since_contact(self):
        if self.last_contact_date:
            return (timezone.now().date() - self.last_contact_date).days
        return None

    @property
    def followup_status(self):
        if not self.next_action_date:
            return 'None'
        today = timezone.now().date()
        if self.next_action_date < today and self.stage not in ['Won', 'Lost']:
            return 'Overdue'
        elif self.next_action_date == today:
            return 'Today'
        return 'Upcoming'

    def save(self, *args, **kwargs):
        if not self.opportunity_name:
            self.opportunity_name = f"{self.company_name} Deal"
        if not self.lead_code:
            last_lead = Lead.objects.exclude(lead_code__isnull=True).order_by('-id').first()
            new_id = (last_lead.id + 1) if last_lead else 1
            self.lead_code = f"L-{new_id:04d}"
        if self.stage in ['Order', 'Won']:
            if not self.won_at:
                self.won_at = timezone.now()
        else:
            self.won_at = None
        super().save(*args, **kwargs)


class Client(models.Model):
    CLIENT_STATUS_CHOICES = (
        ('Active', 'Active'),
        ('At Risk', 'At Risk'),
        ('Inactive', 'Inactive'),
        ('Suspended', 'Suspended'),
    )

    PAYMENT_TERMS_CHOICES = (
        ('Prepayment', 'Prepayment'),
        ('Cash on delivery', 'Cash on delivery'),
        ('Net 15', 'Net 15'),
        ('Net 30', 'Net 30'),
        ('Net 45', 'Net 45'),
        ('Net 60', 'Net 60'),
        ('Letter of credit', 'Letter of credit'),
    )

    client_code = models.CharField(max_length=20, unique=True, verbose_name="Client ID Code")
    country = models.CharField(max_length=100, default="United Arab Emirates", verbose_name="Country")
    city = models.CharField(max_length=100, default="Sharjah", verbose_name="City")
    company_name = models.CharField(max_length=255, verbose_name="Company Name")
    segment = models.CharField(max_length=100, default="Retailer / Shop", verbose_name="Segment")
    contact_name = models.CharField(max_length=255, verbose_name="Contact Person")
    position = models.CharField(max_length=100, blank=True, null=True, verbose_name="Position")
    phone = models.CharField(max_length=50, blank=True, null=True, verbose_name="Phone")
    email = models.EmailField(blank=True, null=True, verbose_name="Email")
    whatsapp = models.CharField(max_length=50, blank=True, null=True, verbose_name="WhatsApp")
    account_manager = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="clients", verbose_name="Account Manager")
    
    client_since = models.DateField(null=True, blank=True, verbose_name="Client Since")
    contract_no = models.CharField(max_length=100, blank=True, null=True, verbose_name="Contract No.")
    payment_terms = models.CharField(max_length=50, choices=PAYMENT_TERMS_CHOICES, default="Net 30", verbose_name="Payment Terms")
    credit_limit = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Credit Limit (USD)")
    
    last_order_date = models.DateField(null=True, blank=True, verbose_name="Last Order Date")
    last_order_value = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Last Order Value (USD)")
    sales_ytd = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Sales YTD (USD)")
    avg_volume_liters = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Avg Volume (L/month)")
    
    status = models.CharField(max_length=30, choices=CLIENT_STATUS_CHOICES, default='Active', verbose_name="Client Status")
    next_followup_date = models.DateField(null=True, blank=True, verbose_name="Next Follow-up Date")
    notes = models.TextField(blank=True, null=True, verbose_name="Notes")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"[{self.client_code}] {self.company_name}"

    @property
    def days_since_last_order(self):
        if self.last_order_date:
            return (timezone.now().date() - self.last_order_date).days
        return None

    @property
    def alert_status(self):
        if self.status == 'At Risk':
            return 'At Risk'
        if self.next_followup_date and self.next_followup_date < timezone.now().date():
            return 'Overdue Follow-up'
        if self.days_since_last_order and self.days_since_last_order > 60:
            return 'Overdue Order'
        return 'OK'

    def save(self, *args, **kwargs):
        if not self.client_code:
            last_client = Client.objects.order_by('-id').first()
            new_id = (last_client.id + 1) if last_client else 1
            self.client_code = f"C-{new_id:04d}"
        super().save(*args, **kwargs)


class LeadInteraction(models.Model):
    INTERACTION_TYPES = (
        ('Call', 'Phone Call'),
        ('WhatsApp', 'WhatsApp Message'),
        ('Email', 'Email Correspondence'),
        ('Meeting', 'Client Meeting'),
        ('Visit', 'On-site Visit'),
        ('Offer Sent', 'Offer Sent'),
        ('Samples Sent', 'Samples Sent'),
        ('Order Received', 'Order Received'),
        ('Payment Follow-up', 'Payment Follow-up'),
        ('Note', 'General Note'),
    )

    OUTCOME_CHOICES = (
        ('Positive', 'Positive'),
        ('Neutral', 'Neutral'),
        ('Negative', 'Negative'),
        ('No answer', 'No answer'),
    )

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, null=True, blank=True, related_name="interactions")
    client = models.ForeignKey(Client, on_delete=models.CASCADE, null=True, blank=True, related_name="interactions")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="lead_interactions")
    
    activity_type = models.CharField(max_length=30, choices=INTERACTION_TYPES, default='Call')
    contact_person = models.CharField(max_length=255, blank=True, null=True, verbose_name="Contact Person")
    outcome = models.CharField(max_length=30, choices=OUTCOME_CHOICES, default='Positive', verbose_name="Outcome")
    notes = models.TextField(verbose_name="Summary / Discussion Notes")
    next_step = models.CharField(max_length=255, blank=True, null=True, verbose_name="Next Step")
    next_step_date = models.DateField(null=True, blank=True, verbose_name="Next Step Date")
    rating_given = models.IntegerField(null=True, blank=True, choices=[(i, f"{i} Stars") for i in range(1, 6)])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        target = self.lead.company_name if self.lead else (self.client.company_name if self.client else "Unknown")
        return f"{self.activity_type} with {target} by {self.author}"


