from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("history/", views.history_view, name="history_view"),
    path("invite/", views.invite_people, name="invite_people"),
    path("products/", views.products_list, name="products_list"),
    path("pricing/", views.pricing_list, name="pricing_list"),
    path("export/", views.export_dashboard, name="export_dashboard"),
    path("import/", views.import_upload, name="import_upload"),
    path("import/<int:import_id>/", views.import_detail, name="import_detail"),
    path("import/apply/<int:import_id>/", views.import_apply, name="import_apply"),
    path("quotation/create/", views.create_quotation, name="create_quotation"),
    path("api/notifications/", views.get_notifications, name="get_notifications"),
    path("api/notifications/read/", views.mark_notifications_read, name="mark_notifications_read"),
    path("inquiry/send/", views.send_inquiry, name="send_inquiry"),
    path("crm/", views.crm_dashboard, name="crm_dashboard"),
    path("crm/leads/", views.crm_leads_list, name="crm_leads_list"),
    path("crm/lead/create/", views.crm_lead_create, name="crm_lead_create"),
    path("crm/lead/<int:pk>/update/", views.crm_lead_update, name="crm_lead_update"),
    path("crm/lead/<int:pk>/edit/", views.crm_lead_edit, name="crm_lead_edit"),
    path("crm/lead/<int:pk>/delete/", views.crm_lead_delete, name="crm_lead_delete"),
    path("crm/lead/<int:pk>/interactions/", views.crm_lead_interactions, name="crm_lead_interactions"),
    path("crm/clients/", views.crm_clients_list, name="crm_clients_list"),
    path("crm/client/create/", views.crm_client_create, name="crm_client_create"),
    path("crm/client/<int:pk>/edit/", views.crm_client_edit, name="crm_client_edit"),
    path("crm/client/<int:pk>/delete/", views.crm_client_delete, name="crm_client_delete"),
    path("crm/interactions/", views.crm_interactions_list, name="crm_interactions_list"),
    path("crm/geography/", views.crm_geography, name="crm_geography"),
    path("crm/analytics/", views.crm_analytics, name="crm_analytics"),
]
