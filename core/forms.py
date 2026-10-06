from django import forms
from .models import Product, Lead, Client, LeadInteraction


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["sku", "name", "quantity", "production_price", "profit_percent"]


class PriceUpdateForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["quantity", "production_price"]


class ImportForm(forms.Form):
    file = forms.FileField()


class LeadForm(forms.ModelForm):
    revenue = forms.DecimalField(required=False, initial=0)
    est_volume_liters = forms.DecimalField(required=False, initial=0)
    contact_email = forms.EmailField(required=False)

    class Meta:
        model = Lead
        fields = [
            "lead_code", "company_name", "contact_name", "position", "contact_email",
            "contact_phone", "whatsapp", "country", "city", "segment", "source",
            "products_of_interest", "stage", "priority", "last_contact_date",
            "next_action", "next_action_date", "est_volume_liters", "revenue",
            "salesperson", "comments"
        ]
        widgets = {
            'last_contact_date': forms.DateInput(attrs={'type': 'date'}),
            'next_action_date': forms.DateInput(attrs={'type': 'date'}),
            'comments': forms.Textarea(attrs={'rows': 2}),
        }

    def clean_revenue(self):
        val = self.cleaned_data.get('revenue')
        return val if val is not None else 0

    def clean_est_volume_liters(self):
        val = self.cleaned_data.get('est_volume_liters')
        return val if val is not None else 0


class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = [
            "client_code", "company_name", "contact_name", "position", "email",
            "phone", "whatsapp", "country", "city", "segment", "account_manager",
            "client_since", "contract_no", "payment_terms", "credit_limit",
            "last_order_date", "last_order_value", "sales_ytd", "avg_volume_liters",
            "status", "next_followup_date", "notes"
        ]
        widgets = {
            'client_since': forms.DateInput(attrs={'type': 'date'}),
            'last_order_date': forms.DateInput(attrs={'type': 'date'}),
            'next_followup_date': forms.DateInput(attrs={'type': 'date'}),
        }


class LeadInteractionForm(forms.ModelForm):
    class Meta:
        model = LeadInteraction
        fields = [
            "lead", "client", "activity_type", "contact_person", "outcome",
            "notes", "next_step", "next_step_date"
        ]
        widgets = {
            'next_step_date': forms.DateInput(attrs={'type': 'date'}),
        }