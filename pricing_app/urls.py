from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from core import views
from core.forms import EmailAuthenticationForm

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(
            authentication_form=EmailAuthenticationForm,
            template_name="registration/login.html",
        ),
        name="login",
    ),

    path("accounts/", include("django.contrib.auth.urls")),
    path('documents/quotation/<int:id>/', views.generate_quotation_pdf, name='generate_quotation_pdf'),
    path('documents/proforma/<int:id>/', views.generate_proforma_pdf, name='generate_proforma_pdf'),
    path('documents/lead-quotation/<int:pk>/', views.generate_lead_quotation, name='generate_lead_quotation'),
    path('documents/lead-proforma/<int:pk>/', views.generate_lead_proforma, name='generate_lead_proforma'),
    path("", include("core.urls")),
]