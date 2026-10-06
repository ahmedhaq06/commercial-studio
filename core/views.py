from io import BytesIO

from openpyxl import Workbook
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.files.base import ContentFile
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.db import models, transaction
from django.db.models import Q, Sum, Avg, Count

from .forms import ImportForm, PriceUpdateForm, ProductForm, LeadForm, ClientForm, LeadInteractionForm
from .models import ExportFile, ImportFile, MasterProduct, Product, ProductChange, Quotation, Pricing, Notification, Lead, Client, LeadInteraction
import csv
import json
from django.core.serializers.json import DjangoJSONEncoder
from datetime import datetime
from decimal import Decimal
from django.utils import timezone
from django.urls import reverse


User = get_user_model()
from .utils import ADMIN_USERNAME, is_admin, is_sales, is_production

def record_changes(product, old_product, user):
    fields = ["sku", "name", "quantity", "production_price", "profit_percent"]
    for field in fields:
        old_value = getattr(old_product, field)
        new_value = getattr(product, field)
        if old_value != new_value:
            ProductChange.objects.create(
                product=product,
                changed_by=user,
                field_name=field,
                old_value=str(old_value),
                new_value=str(new_value),
            )

@login_required
def get_notifications(request):
    if is_admin(request.user):
        base_qs = Notification.objects.filter(recipient=request.user)
    else:
        base_qs = Notification.objects.filter(models.Q(recipient=request.user) | models.Q(recipient__isnull=True)).distinct()
        
    unread_count = base_qs.exclude(read_by=request.user).count()
    qs = base_qs.prefetch_related('read_by').order_by('-created_at')[:10]

    data = []
    for n in qs:
        is_read_for_user = any(reader.id == request.user.id for reader in n.read_by.all())
        data.append({
            'id': n.id,
            'sender': n.sender.username,
            'message': n.message,
            'is_read': is_read_for_user,
            'created_at': n.created_at.strftime("%b %d, %H:%M")
        })
    return JsonResponse({'notifications': data, 'unread_count': unread_count})

@login_required
def mark_notifications_read(request):
    if request.method == "POST":
        if is_admin(request.user):
            notifications_to_mark = Notification.objects.filter(recipient=request.user).exclude(read_by=request.user)
        else:
            notifications_to_mark = Notification.objects.filter(models.Q(recipient=request.user) | models.Q(recipient__isnull=True)).distinct().exclude(read_by=request.user)
        
        user = request.user
        Notification.read_by.through.objects.bulk_create([
            Notification.read_by.through(notification_id=n_id, user_id=user.id)
            for n_id in notifications_to_mark.values_list('id', flat=True)
        ], ignore_conflicts=True)

        return JsonResponse({'status': 'ok'})
    return JsonResponse({'status': 'error'})

@login_required
def send_inquiry(request):
    if request.method == "POST":
        message = request.POST.get('message')
        if message:
            if is_admin(request.user):
                Notification.objects.create(sender=request.user, message=message, recipient=None)
            else:
                admin_user = User.objects.filter(username__iexact=ADMIN_USERNAME).first()
                if admin_user:
                    Notification.objects.create(sender=request.user, message=message, recipient=admin_user)
    return redirect(request.META.get('HTTP_REFERER', '/'))

@login_required
def dashboard(request):
    if is_admin(request.user) or request.user.groups.filter(name='Sales').exists():
        return sales_dashboard(request)
    else:
        return production_dashboard(request)

@login_required
def sales_dashboard(request):
    query = (request.GET.get('q') or '').strip()

    if query:
        matching_masters = MasterProduct.objects.filter(
            Q(code__icontains=query) |
            Q(description__icontains=query) |
            Q(specification__icontains=query)
        )
        for mp in matching_masters:
            Product.objects.get_or_create(
                sku=mp.code,
                master=mp,
                defaults={
                    'name': mp.description,
                    'production_price': Decimal("0.00"),
                    'profit_percent': Decimal("0.00"),
                    'quantity': Decimal("0.00"),
                }
            )

    search_results = []
    if query:
        search_results = Product.objects.filter(
            Q(sku__icontains=query) |
            Q(master__code__icontains=query) |
            Q(name__icontains=query) |
            Q(master__description__icontains=query) |
            Q(master__specification__icontains=query)
        ).select_related('master')

    recent_quotations = Quotation.objects.filter(salesperson=request.user).order_by('-created_at')[:10]
    recent_updates = ProductChange.objects.select_related("product").filter(field_name__in=['production_price', 'profit_percent', 'selling_price']).order_by('-created_at')[:5]

    master_products = MasterProduct.objects.all().order_by('code')

    return render(request, "sales_dashboard.html", {
        "query": query,
        "search_results": search_results,
        "recent_quotations": recent_quotations,
        "recent_updates": recent_updates,
        "master_products": master_products,
    })

@login_required
def crm_dashboard(request):
    if not is_sales(request.user):
        return redirect('dashboard')
    
    # 1. Filter Parameters
    report_date_str = request.GET.get('report_date')
    if report_date_str:
        try:
            report_date = datetime.strptime(report_date_str, '%Y-%m-%d').date()
        except ValueError:
            report_date = timezone.now().date()
    else:
        report_date = timezone.now().date()
        
    manager_id = request.GET.get('manager', 'All')
    
    # Base Querysets
    leads_qs = Lead.objects.all().select_related('salesperson')
    clients_qs = Client.objects.all().select_related('account_manager')
    interactions_qs = LeadInteraction.objects.all().select_related('author', 'lead', 'client')
    
    if manager_id != 'All' and manager_id.isdigit():
        m_id = int(manager_id)
        leads_qs = leads_qs.filter(salesperson_id=m_id)
        clients_qs = clients_qs.filter(account_manager_id=m_id)
        interactions_qs = interactions_qs.filter(Q(lead__salesperson_id=m_id) | Q(client__account_manager_id=m_id) | Q(author_id=m_id))

    # 2. Today at a Glance KPIs
    activities_today = interactions_qs.filter(created_at__date=report_date).count()
    new_leads_today = leads_qs.filter(date_added=report_date).count()
    followups_due_today = leads_qs.filter(next_action_date=report_date).exclude(stage__in=['Won', 'Lost']).count()
    overdue_followups = leads_qs.filter(next_action_date__lt=report_date).exclude(stage__in=['Won', 'Lost']).count()
    
    open_leads_count = leads_qs.exclude(stage__in=['Won', 'Lost']).count()
    open_pipeline_val = leads_qs.exclude(stage__in=['Won', 'Lost']).aggregate(total=Sum('revenue'))['total'] or Decimal('0.00')
    active_clients_count = clients_qs.filter(status='Active').count()
    sales_ytd_val = clients_qs.aggregate(total=Sum('sales_ytd'))['total'] or Decimal('0.00')

    # 3. Activity on Report Date (by type)
    activity_types = ['Call', 'WhatsApp', 'Email', 'Meeting', 'Visit', 'Offer Sent', 'Samples Sent', 'Order Received', 'Payment Follow-up', 'Note']
    activity_by_type = []
    for act_type in activity_types:
        count = interactions_qs.filter(created_at__date=report_date, activity_type=act_type).count()
        pos_count = interactions_qs.filter(created_at__date=report_date, activity_type=act_type, outcome='Positive').count()
        activity_by_type.append({'type': act_type, 'count': count, 'positive': pos_count})

    # 4. Last 7 Days Trend
    last_7_days = []
    for i in range(6, -1, -1):
        day_date = report_date - timezone.timedelta(days=i)
        day_str = day_date.strftime('%a')
        act_cnt = interactions_qs.filter(created_at__date=day_date).count()
        new_cnt = leads_qs.filter(date_added=day_date).count()
        last_7_days.append({
            'date': day_date.strftime('%Y-%m-%d'),
            'day': day_str,
            'activities': act_cnt,
            'new_leads': new_cnt,
            'is_empty': act_cnt == 0 and day_date.weekday() < 5
        })

    # 5. Pipeline by Status
    status_order = ['New', 'Contacted', 'Interested', 'Samples Sent', 'Offer Sent', 'Negotiation', 'Won', 'Lost', 'On Hold']
    pipeline_by_status = []
    for st in status_order:
        st_leads = leads_qs.filter(stage=st)
        cnt = st_leads.count()
        val = st_leads.aggregate(total=Sum('revenue'))['total'] or Decimal('0.00')
        vol = st_leads.aggregate(total=Sum('est_volume_liters'))['total'] or Decimal('0.00')
        pipeline_by_status.append({'status': st, 'count': cnt, 'value': val, 'volume': vol})

    # 6. Sales Manager Performance Table
    sales_users = list(User.objects.filter(Q(groups__name='Sales') | Q(is_superuser=True)).distinct())
    manager_perf = []
    for u in sales_users:
        u_leads = Lead.objects.filter(salesperson=u)
        u_clients = Client.objects.filter(account_manager=u)
        u_interactions = LeadInteraction.objects.filter(author=u)
        
        acts_rep_date = u_interactions.filter(created_at__date=report_date).count()
        acts_7d = u_interactions.filter(created_at__date__gte=report_date - timezone.timedelta(days=7), created_at__date__lte=report_date).count()
        new_7d = u_leads.filter(date_added__gte=report_date - timezone.timedelta(days=7), date_added__lte=report_date).count()
        u_open_leads = u_leads.exclude(stage__in=['Won', 'Lost']).count()
        u_overdue = u_leads.filter(next_action_date__lt=report_date).exclude(stage__in=['Won', 'Lost']).count()
        u_won_count = u_leads.filter(stage='Won').count()
        u_pipe_val = u_leads.exclude(stage__in=['Won', 'Lost']).aggregate(total=Sum('revenue'))['total'] or Decimal('0.00')
        u_active_clients = u_clients.filter(status='Active').count()
        u_sales_ytd = u_clients.aggregate(total=Sum('sales_ytd'))['total'] or Decimal('0.00')

        manager_perf.append({
            'user': u,
            'acts_rep_date': acts_rep_date,
            'acts_7d': acts_7d,
            'new_7d': new_7d,
            'open_leads': u_open_leads,
            'overdue': u_overdue,
            'won_count': u_won_count,
            'pipe_val': u_pipe_val,
            'active_clients': u_active_clients,
            'sales_ytd': u_sales_ytd
        })

    # 7. Country Breakdown Table
    countries = [
        "United Arab Emirates", "Saudi Arabia", "Oman", "Qatar", "Kuwait",
        "Bahrain", "Iraq", "Jordan", "Lebanon", "Egypt", "Yemen", "Pakistan",
        "India", "Afghanistan", "Kenya", "Tanzania", "Uzbekistan", "Kazakhstan",
        "Azerbaijan", "Georgia"
    ]
    country_summary = []
    for c in countries:
        c_leads = leads_qs.filter(country=c)
        c_clients = clients_qs.filter(country=c)
        c_tot_leads = c_leads.count()
        c_open_leads = c_leads.exclude(stage__in=['Won', 'Lost']).count()
        c_hot_leads = c_leads.filter(priority='Hot').exclude(stage__in=['Won', 'Lost']).count()
        c_offer_neg = c_leads.filter(stage__in=['Offer Sent', 'Negotiation']).count()
        c_won = c_leads.filter(stage='Won').count()
        c_lost = c_leads.filter(stage='Lost').count()
        c_pipe_val = c_leads.exclude(stage__in=['Won', 'Lost']).aggregate(total=Sum('revenue'))['total'] or Decimal('0.00')
        c_act_clients = c_clients.filter(status='Active').count()
        c_sales_ytd = c_clients.aggregate(total=Sum('sales_ytd'))['total'] or Decimal('0.00')
        c_acts_7d = interactions_qs.filter(
            Q(lead__country=c) | Q(client__country=c),
            created_at__date__gte=report_date - timezone.timedelta(days=7)
        ).count()

        if c_tot_leads > 0 or c_act_clients > 0 or c_sales_ytd > 0:
            country_summary.append({
                'country': c,
                'total_leads': c_tot_leads,
                'open_leads': c_open_leads,
                'hot_leads': c_hot_leads,
                'offer_neg': c_offer_neg,
                'won': c_won,
                'lost': c_lost,
                'pipe_val': c_pipe_val,
                'active_clients': c_act_clients,
                'sales_ytd': c_sales_ytd,
                'acts_7d': c_acts_7d
            })

    all_leads = Lead.objects.all().order_by('-created_at')

    return render(request, "crm_dashboard.html", {
        "report_date": report_date.strftime('%Y-%m-%d'),
        "selected_manager": int(manager_id) if manager_id != 'All' and manager_id.isdigit() else 'All',
        "sales_users": sales_users,
        "kpi": {
            "activities_today": activities_today,
            "new_leads_today": new_leads_today,
            "followups_due_today": followups_due_today,
            "overdue_followups": overdue_followups,
            "open_leads": open_leads_count,
            "open_pipeline_val": open_pipeline_val,
            "active_clients": active_clients_count,
            "sales_ytd_val": sales_ytd_val,
        },
        "activity_by_type": activity_by_type,
        "last_7_days": last_7_days,
        "pipeline_by_status": pipeline_by_status,
        "manager_perf": manager_perf,
        "country_summary": country_summary,
        "all_leads": all_leads,
    })


@login_required
def create_quotation(request):
    if not is_sales(request.user):
        return redirect('dashboard')
    if request.method == "POST":
        product_id = request.POST.get("product_id")
        customer_name = request.POST.get("customer_name")
        quantity = request.POST.get("quantity")
        discount_percent = request.POST.get("discount_percent") or 0
        
        if product_id and customer_name and quantity:
            product = get_object_or_404(Product, pk=product_id)
            Quotation.objects.create(
                product=product,
                salesperson=request.user,
                customer_name=customer_name,
                quantity=Decimal(str(quantity)),
                discount_percent=Decimal(str(discount_percent))
            )
    return redirect("dashboard")

@login_required
def production_dashboard(request):
    query = (request.GET.get('q') or '').strip()
    
    if query:
        mp = MasterProduct.objects.filter(code=query).first()
        if mp:
            Product.objects.get_or_create(
                sku=mp.code,
                master=mp,
                defaults={
                    'name': mp.description,
                    'production_price': Decimal("0.00"),
                    'profit_percent': Decimal("0.00"),
                    'quantity': Decimal("0.00"),
                }
            )

    search_results = []
    if query:
        search_results = Product.objects.filter(
            Q(sku__icontains=query) |
            Q(master__code__icontains=query) |
            Q(name__icontains=query)
        ).select_related('master')

    # Quick cost update form handling in dashboard
    if request.method == "POST" and "update_cost" in request.POST:
        product_id = request.POST.get("product_id")
        new_cost = request.POST.get("production_price")
        new_profit = request.POST.get("profit_percent")
        if product_id and new_cost is not None and new_profit is not None:
            try:
                product = Product.objects.get(id=product_id)
                old_product = Product.objects.get(id=product_id)
                product.production_price = Decimal(str(new_cost))
                product.profit_percent = Decimal(str(new_profit))
                product.updated_by = request.user
                product.save()
                record_changes(product, old_product, request.user)
                return redirect(request.path + ("?q=" + query if query else ""))
            except Product.DoesNotExist:
                pass

    # Check for prices older than 4 days
    four_days_ago = timezone.now() - timezone.timedelta(days=4)
    old_products = Product.objects.filter(updated_at__lt=four_days_ago)
    admin_user = User.objects.filter(username__iexact=ADMIN_USERNAME).first()
    
    if admin_user and old_products.exists():
        last_24h = timezone.now() - timezone.timedelta(days=1)
        for product in old_products:
            msg = f"Price for {product.sku} ({product.name}) is more than 4 days old. Please update it."
            if not Notification.objects.filter(message=msg, created_at__gte=last_24h).exists():
                Notification.objects.create(
                    sender=admin_user,
                    recipient=None,
                    message=msg
                )

    attention_products = Product.objects.filter(Q(production_price=0) | Q(selling_price=0)).order_by('-updated_at')[:10]
    history = ProductChange.objects.select_related("product", "changed_by").order_by("-created_at")[:10]
    master_products = MasterProduct.objects.all().order_by('code')

    return render(
        request,
        "dashboard.html",
        {
            "query": query,
            "search_results": search_results,
            "attention_products": attention_products,
            "history": history,
            "can_invite": is_admin(request.user),
            "admin_username": ADMIN_USERNAME,
            "master_products": master_products,
        },
    )


@login_required
def history_view(request):
    if not is_production(request.user):
        return redirect('dashboard')
    query = (request.GET.get("q") or "").strip()
    date_value = (request.GET.get("date") or "").strip()

    changes = ProductChange.objects.select_related("product", "changed_by").order_by("-created_at")

    if query:
        changes = changes.filter(
            (
                models.Q(product__sku__icontains=query)
                | models.Q(product__name__icontains=query)
                | models.Q(changed_by__username__icontains=query)
                | models.Q(field_name__icontains=query)
                | models.Q(old_value__icontains=query)
                | models.Q(new_value__icontains=query)
            )
        )

    if date_value:
        parsed_date = parse_date(date_value)
        if parsed_date:
            changes = changes.filter(created_at__date=parsed_date)

    return render(
        request,
        "history.html",
        {
            "changes": changes,
            "query": query,
            "date_value": date_value,
            "total_count": changes.count(),
        },
    )


@login_required
@user_passes_test(is_admin)
def invite_people(request):
    members = User.objects.order_by("username")
    errors = []
    created_user = None
    invite_text = None

    if request.method == "POST":
        action = request.POST.get("action", "create")

        if action == "create":
            username = (request.POST.get("username") or "").strip()
            password = (request.POST.get("password") or "").strip()
            role = request.POST.get("role")

            if not username:
                errors.append("Username is required")
            if not password:
                errors.append("Password is required")
            if role not in ["sales", "production"]:
                errors.append("Role is required and must be sales or production")

            if not errors:
                if User.objects.filter(username__iexact=username).exists():
                    errors.append("That username already exists")
                else:
                    created_user = User.objects.create_user(username=username, password=password)
                    if role == "sales":
                        group, _ = Group.objects.get_or_create(name="Sales")
                        created_user.groups.add(group)
                    elif role == "production":
                        group, _ = Group.objects.get_or_create(name="Production")
                        created_user.groups.add(group)

                    invite_text = (
                        "Join our company's private collaborative Excel tool.\n\n"
                        f"Login URL: {request.build_absolute_uri('/accounts/login/')}\n"
                        f"Username: {created_user.username}\n"
                        f"Password: {password}\n\n"
                        "Use this account to access the workspace."
                    )

        elif action == "delete":
            username = (request.POST.get("username") or "").strip()
            if not username:
                errors.append("Username is required")
            elif username.lower() == ADMIN_USERNAME.lower():
                errors.append("The admin account cannot be removed")
            else:
                User.objects.filter(username__iexact=username).delete()

        members = User.objects.order_by("username")

    return render(
        request,
        "invite_people.html",
        {
            "members": members,
            "errors": errors,
            "created_user": created_user,
            "invite_text": invite_text,
            "admin_username": ADMIN_USERNAME,
        },
    )


@login_required
def import_detail(request, import_id):
    if not is_production(request.user):
        return redirect('dashboard')
    imp = get_object_or_404(ImportFile, pk=import_id)
    fpath = imp.file.path
    with open(fpath, "rb") as fh:
        rows = list(iter_rows_from_file(fh, imp.file.name))
    parsed, errors = validate_and_parse_rows(rows)
    return render(request, "import_preview.html", {"import": imp, "rows": parsed, "errors": errors})


@login_required
def products_list(request):
    errors = []
    edit_id = None
    can_edit = is_admin(request.user) or request.user.groups.filter(name='Production').exists()
    query = (request.GET.get('q') or '').strip()

    if request.method == 'POST' and can_edit:
        action = request.POST.get('action')
        description = (request.POST.get('description') or '').strip()
        specifications = (request.POST.get('specifications') or '').strip()
        approval = (request.POST.get('approval') or '').strip()
        product_id = request.POST.get('product_id')

        if action == 'add':
            viscosity = (request.POST.get('viscosity') or '').strip()
            liters_per_case_str = (request.POST.get('liters_per_case') or '').strip()
            liters_per_case = None
            if liters_per_case_str:
                try:
                    liters_per_case = Decimal(liters_per_case_str)
                except Exception:
                    errors.append('Litres per case must be a valid number.')

            if not description:
                errors.append('Description is required.')
            
            codes_and_packs = [
                (request.POST.get('code_12x1', '').strip(), '12x1'),
                (request.POST.get('code_4x4', '').strip(), '4x4'),
                (request.POST.get('code_4x5', '').strip(), '4x5'),
                (request.POST.get('code_20', '').strip(), '20'),
                (request.POST.get('code_208', '').strip(), '208'),
            ]
            
            valid_codes = []
            for code, pack in codes_and_packs:
                if code:
                    if not code.isdigit():
                        errors.append(f'Product code {code} for {pack} must contain only digits.')
                    elif MasterProduct.objects.filter(code=code, description=description, packaging=pack).exists():
                        errors.append(f'Product with code {code}, name {description} and packaging {pack} already exists.')
                    else:
                        valid_codes.append((code, pack))
            
            if not valid_codes and not errors:
                errors.append('At least one product code must be provided.')

            if not errors:
                for code, pack in valid_codes:
                    MasterProduct.objects.create(
                        code=code, description=description, viscosity=viscosity, specification=specifications,
                        approval=approval, packaging=pack, liters_per_case=liters_per_case
                    )
                return redirect('products_list')

        if action == 'edit':
            edit_id = request.POST.get('product_id')

        if action == 'update':
            code = (request.POST.get('code') or '').strip()
            packaging = (request.POST.get('packaging') or '').strip()
            viscosity = (request.POST.get('viscosity') or '').strip()
            liters_per_case_str = (request.POST.get('liters_per_case') or '').strip()
            liters_per_case = None
            if liters_per_case_str:
                try:
                    liters_per_case = Decimal(liters_per_case_str)
                except Exception:
                    errors.append('Litres per case must be a valid number.')

            if not code or not code.isdigit():
                errors.append('Product code must contain only digits.')
            if not description:
                errors.append('Description is required.')
                
            if not errors:
                try:
                    master = MasterProduct.objects.get(id=product_id)
                    if (master.code != code or master.description != description or master.packaging != packaging) and MasterProduct.objects.filter(code=code, description=description, packaging=packaging).exists():
                        errors.append(f'Product with code {code}, name {description} and packaging {packaging} already exists.')
                    else:
                        master.code = code
                        master.description = description
                        master.viscosity = viscosity
                        master.specification = specifications
                        master.approval = approval
                        master.packaging = packaging
                        master.liters_per_case = liters_per_case
                        master.save()
                        return redirect('products_list')
                except MasterProduct.DoesNotExist:
                    errors.append('Product not found.')

        if action == 'delete':
            try:
                MasterProduct.objects.get(id=product_id).delete()
                return redirect('products_list')
            except MasterProduct.DoesNotExist:
                errors.append('Product not found.')

    rows = MasterProduct.objects.all().order_by('code')
    if query:
        rows = rows.filter(
            models.Q(description__icontains=query) |
            models.Q(code__icontains=query) |
            models.Q(specification__icontains=query)
        )
    form_values = {}
    if edit_id:
        try:
            master = MasterProduct.objects.get(id=edit_id)
            form_values = {
                'product_id': master.id,
                'code': master.code,
                'description': master.description,
                'specifications': master.specification,
                'approval': master.approval,
                'packaging': master.packaging,
            }
        except MasterProduct.DoesNotExist:
            pass

    return render(request, 'products.html', {'rows': rows, 'errors': errors, 'form': form_values, 'can_edit': can_edit, 'query': query})

@login_required
def pricing_list(request):
    if not is_production(request.user):
        return redirect('dashboard')
    errors = []
    edit_id = None
    can_edit = is_admin(request.user) or request.user.groups.filter(name='Production').exists()

    if request.method == 'POST' and can_edit:
        action = request.POST.get('action')
        product_id = request.POST.get('product')
        packaging = request.POST.get('packaging') or ''
        production_cost_per_liter = request.POST.get('production_cost_per_liter') or 0
        quantity = request.POST.get('quantity') or 0
        discount = request.POST.get('discount') or 0
        pricing_id = request.POST.get('pricing_id')

        final_cost = Decimal("0")
        selling_price = 0
        price = 0

        if action in ('add', 'update'):
            if not product_id:
                errors.append('Product is required.')
            else:
                try:
                    master = MasterProduct.objects.get(id=product_id)
                    import re
                    packaging_master = (master.packaging or "").strip().lower()
                    packaging_master = re.sub(r'l$', '', packaging_master).strip()
                    
                    pack_multiplier = Decimal("0")
                    if packaging_master == '12x1':
                        pack_multiplier = Decimal("12")
                    elif packaging_master == '4x4':
                        pack_multiplier = Decimal("16")
                    elif packaging_master == '4x5':
                        pack_multiplier = Decimal("20")
                    else:
                        match = re.match(r"^(\d+)\s*x\s*(\d+)$", packaging_master)
                        if match:
                            pack_multiplier = Decimal(match.group(1)) * Decimal(match.group(2))
                        else:
                            match_single = re.search(r"^\d+", packaging_master)
                            if match_single:
                                pack_multiplier = Decimal(match_single.group())

                    try:
                        match = re.search(r"[-+]?\d*\.\d+|\d+", packaging)
                        packaging_val = Decimal(match.group()) if match else Decimal("0")
                    except Exception:
                        packaging_val = Decimal("0")
                    
                    prod_cost = Decimal(str(production_cost_per_liter)) if production_cost_per_liter else Decimal("0")
                    final_cost = (prod_cost * pack_multiplier + packaging_val).quantize(Decimal("0.01"))
                    selling_price = (final_cost + (final_cost * Decimal("0.20"))).quantize(Decimal("0.01"))
                    
                    qty_val = Decimal(str(quantity)) if quantity else Decimal("0")
                    discount_val = Decimal(str(discount)) if discount else Decimal("0")
                    price = ((selling_price * qty_val) - discount_val).quantize(Decimal("0.01"))
                except MasterProduct.DoesNotExist:
                    pass

        if action == 'add' and not errors:
            try:
                master = MasterProduct.objects.get(id=product_id)
                Pricing.objects.create(
                    product=master,
                    packaging=packaging,
                    production_cost_per_liter=production_cost_per_liter,
                    quantity=quantity,
                    discount=discount,
                    final_cost=final_cost,
                    selling_price=selling_price,
                    price=price,
                )
                return redirect('pricing_list')
            except MasterProduct.DoesNotExist:
                errors.append("Invalid product selected.")

        if action == 'edit':
            edit_id = request.POST.get('pricing_id')

        if action == 'update' and not errors:
            try:
                pricing = Pricing.objects.get(id=pricing_id)
                master = MasterProduct.objects.get(id=product_id)
                pricing.product = master
                pricing.packaging = packaging
                pricing.production_cost_per_liter = production_cost_per_liter
                pricing.quantity = quantity
                pricing.discount = discount
                pricing.final_cost = final_cost
                pricing.selling_price = selling_price
                pricing.price = price
                pricing.save()
                return redirect('pricing_list')
            except Pricing.DoesNotExist:
                errors.append('Pricing not found.')
            except MasterProduct.DoesNotExist:
                errors.append('Product not found.')

        if action == 'delete':
            try:
                Pricing.objects.get(id=pricing_id).delete()
                return redirect('pricing_list')
            except Pricing.DoesNotExist:
                errors.append('Pricing not found.')

    rows = Pricing.objects.select_related('product').all().order_by('product__code')
    master_products = MasterProduct.objects.all().order_by('code')
    
    form_values = {}
    if edit_id:
        try:
            pricing = Pricing.objects.get(id=edit_id)
            form_values = {
                'pricing_id': pricing.id,
                'product_id': pricing.product.id,
                'production_cost_per_liter': pricing.production_cost_per_liter,
                'quantity': pricing.quantity,
                'discount': pricing.discount,
            }
        except Pricing.DoesNotExist:
            pass

    return render(request, 'pricing.html', {
        'rows': rows, 
        'errors': errors, 
        'form': form_values,
        'master_products': master_products,
        'can_edit': can_edit
    })

@login_required
def export_dashboard(request):
    if not is_production(request.user):
        return redirect('dashboard')
    wb = Workbook()
    ws = wb.active
    ws.title = "Pricing"
    ws.append(["SKU", "Name", "Quantity", "Production Price", "Profit Percent", "Selling Price"])
    for product in Product.objects.order_by("sku"):
        ws.append([
            product.sku,
            product.name,
            float(product.quantity),
            float(product.production_price),
            float(product.profit_percent),
            float(product.selling_price),
        ])
    buffer = BytesIO()
    wb.save(buffer)
    export = ExportFile(created_by=request.user)
    export.file.save("pricing-export.xlsx", ContentFile(buffer.getvalue()), save=True)
    return FileResponse(export.file.open("rb"), as_attachment=True, filename="pricing-export.xlsx")


def parse_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    s = str(value).strip()
    try:
        return datetime.fromisoformat(s).date()
    except Exception:
        pass
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass
    return None


def iter_rows_from_file(fobj, filename):
    name = filename.lower()
    # CSV files (including files uploaded with .csv extension)
    if name.endswith(".csv"):
        text = fobj.read().decode("utf-8-sig")
        reader = csv.DictReader(text.splitlines())
        for row in reader:
            yield row
        return

    # Try openpyxl for modern Excel (.xlsx)
    try:
        from openpyxl import load_workbook

        fobj.seek(0)
        wb = load_workbook(fobj, data_only=True)
        ws = wb.active
        headers = [str(cell.value).strip() if cell.value is not None else "" for cell in next(ws.rows)]
        for r in ws.iter_rows(min_row=2, values_only=True):
            yield {headers[i]: r[i] for i in range(len(headers))}
        return
    except Exception:
        pass

    # Fallback: try pandas if available (supports many spreadsheet types)
    try:
        import pandas as pd

        fobj.seek(0)
        # pandas can read from a file-like object for many formats
        df = pd.read_excel(fobj)
        for _, row in df.fillna("").iterrows():
            yield {str(k): (v if not pd.isna(v) else "") for k, v in row.items()}
        return
    except Exception:
        pass

    # Final fallback: try to decode as text and parse as CSV-like
    try:
        fobj.seek(0)
        text = fobj.read().decode("utf-8-sig")
        reader = csv.DictReader(text.splitlines())
        for row in reader:
            yield row
        return
    except Exception:
        return


def validate_and_parse_rows(rows):
    parsed = []
    errors = []
    for idx, row in enumerate(rows, start=1):
        sku = (row.get("sku") or row.get("SKU") or row.get("Sku") or "").strip()
        name = (row.get("name") or row.get("Name") or "").strip()
        qty = row.get("quantity") or row.get("Quantity") or row.get("QTY") or 0
        prod = row.get("production_price") or row.get("Production Price") or row.get("productionprice")
        profit = row.get("profit_percent") or row.get("Profit Percent") or row.get("profitpercent")
        datev = row.get("date") or row.get("effective_date") or row.get("Date")
        row_errors = []
        if not sku:
            row_errors.append("Missing SKU")
        if not name:
            row_errors.append("Missing name")
        try:
            quantity = float(qty) if qty not in (None, "") else 0
        except Exception:
            row_errors.append("Invalid quantity")
            quantity = 0
        try:
            production_price = float(prod) if prod not in (None, "") else 0
        except Exception:
            row_errors.append("Invalid production_price")
            production_price = 0
        try:
            profit_percent = float(profit) if profit not in (None, "") else 0
        except Exception:
            row_errors.append("Invalid profit_percent")
            profit_percent = 0
        eff_date = parse_date(datev)
        # production_price and profit_percent are important
        if prod in (None, ""):
            row_errors.append("Missing production_price")
        if profit in (None, ""):
            row_errors.append("Missing profit_percent")

        if row_errors:
            errors.append({"row": idx, "sku": sku, "errors": row_errors})
        parsed.append({
            "sku": sku,
            "name": name,
            "quantity": quantity,
            "production_price": production_price,
            "profit_percent": profit_percent,
            "selling_price": (Decimal(str(production_price)) + (Decimal(str(production_price)) * Decimal(str(profit_percent)) / Decimal("100"))).quantize(Decimal("0.01")) if production_price is not None else Decimal("0.00"),
            "effective_date": eff_date,
        })
    return parsed, errors


@login_required
def import_upload(request):
    if not is_production(request.user):
        return redirect('dashboard')
    if request.method == "POST":
        form = ImportForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded_file = request.FILES["file"]
            rows = list(iter_rows_from_file(uploaded_file, uploaded_file.name))
            parsed, errors = validate_and_parse_rows(rows)
            
            session_rows = request.session.get("create_excel_rows", [])
            for row in parsed:
                if row.get("sku"):
                    session_rows.append({
                        "sku": row.get("sku", ""),
                        "name": row.get("name", ""),
                        "quantity": float(row.get("quantity") or 0.0),
                        "production_price": float(row.get("production_price") or 0.0),
                        "profit_percent": float(row.get("profit_percent") or 0.0),
                    })
            request.session["create_excel_rows"] = session_rows
            request.session.modified = True
            
            return redirect("products_list")
    else:
        form = ImportForm()
    return render(request, "import_form.html", {"form": form})


@login_required
def import_apply(request, import_id):
    if not is_production(request.user):
        return redirect('dashboard')
    # Requires Production role; sets updated_by to the active user
    imp = get_object_or_404(ImportFile, pk=import_id)
    fpath = imp.file.path
    with open(fpath, "rb") as fh:
        rows = list(iter_rows_from_file(fh, imp.file.name))
    parsed, errors = validate_and_parse_rows(rows)
    applied = 0
    actor = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
    with transaction.atomic():
        for row in parsed:
            if not row["sku"]:
                continue
            try:
                prod = Product.objects.filter(sku=row["sku"], name=row["name"]).first()
                if prod:
                    prod.name = row["name"] or prod.name
                    prod.quantity = row["quantity"]
                    prod.production_price = row["production_price"]
                    prod.profit_percent = row["profit_percent"]
                    prod.effective_date = row["effective_date"]
                    prod.updated_by = actor
                    prod.save()
                else:
                    master = MasterProduct.objects.filter(code=row["sku"], description=row["name"]).first()
                    Product.objects.create(
                        sku=row["sku"],
                        name=row["name"] or row["sku"],
                        master=master,
                        quantity=row["quantity"],
                        production_price=row["production_price"],
                        profit_percent=row["profit_percent"],
                        effective_date=row["effective_date"],
                        updated_by=actor,
                    )
                applied += 1
            except Exception as e:
                errors.append({"row": row.get("sku"), "errors": [str(e)]})
    imp.processed = True
    imp.error_summary = {"errors": errors, "applied": applied}
    imp.save()
    return redirect(reverse("dashboard"))


@login_required
def download_template(request):
    if not is_production(request.user):
        return redirect('dashboard')
    wb = Workbook()
    ws = wb.active
    ws.title = "Template"
    ws.append(["sku", "name", "quantity", "production_price", "profit_percent", "date"])
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return FileResponse(buffer, as_attachment=True, filename="import-template.xlsx")

@login_required
def crm_lead_create(request):
    if not is_sales(request.user):
        return redirect('dashboard')
    if request.method == 'POST':
        form = LeadForm(request.POST)
        if form.is_valid():
            lead = form.save(commit=False)
            if not form.cleaned_data.get('salesperson'):
                lead.salesperson = request.user
            lead.save()
            
            initial_notes = request.POST.get('initial_notes', '').strip()
            if initial_notes:
                LeadInteraction.objects.create(
                    lead=lead,
                    author=request.user,
                    interaction_type='Note',
                    notes=initial_notes,
                    rating_given=lead.rating
                )
    return redirect('crm_dashboard')

@login_required
def crm_lead_update(request, pk):
    if not is_sales(request.user):
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'status': 'error', 'message': 'Permission denied'}, status=403)
        return redirect('dashboard')
    lead = get_object_or_404(Lead, pk=pk)
    if request.method == 'POST':
        new_stage = request.POST.get('stage')
        valid_stages = dict(Lead.STAGE_CHOICES)
        if new_stage in valid_stages:
            lead.stage = new_stage
            lead.save()
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({
                    'status': 'ok',
                    'id': lead.pk,
                    'stage': lead.stage,
                    'revenue': str(lead.revenue)
                })
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'status': 'error', 'message': 'Invalid stage or request'}, status=400)
    return redirect('crm_dashboard')


@login_required
def crm_lead_edit(request, pk):
    if not is_sales(request.user):
        return redirect('dashboard')
    lead = get_object_or_404(Lead, pk=pk)
    if request.method == 'POST':
        form = LeadForm(request.POST, instance=lead)
        if form.is_valid():
            form.save()
        else:
            print(f"[Lead Edit Error] pk={pk} errors={form.errors.as_json()}")
    return redirect(request.META.get('HTTP_REFERER', 'crm_leads_list'))

@login_required
def crm_lead_delete(request, pk):
    if not is_sales(request.user):
        return redirect('dashboard')
    lead = get_object_or_404(Lead, pk=pk)
    if request.method == 'POST':
        lead.delete()
    return redirect(request.META.get('HTTP_REFERER', 'crm_leads_list'))

@login_required
def crm_lead_interactions(request, pk):
    if not is_sales(request.user):
        return JsonResponse({'status': 'error', 'message': 'Permission denied'}, status=403)
    
    lead = get_object_or_404(Lead, pk=pk)

    if request.method == 'POST':
        notes = request.POST.get('notes', '').strip()
        interaction_type = request.POST.get('interaction_type', 'Call')
        rating_given = request.POST.get('rating')
        assigned_sp_id = request.POST.get('salesperson_id')

        if notes:
            LeadInteraction.objects.create(
                lead=lead,
                author=request.user,
                interaction_type=interaction_type,
                notes=notes,
                rating_given=int(rating_given) if rating_given and rating_given.isdigit() else lead.rating
            )

        if rating_given and rating_given.isdigit():
            lead.rating = int(rating_given)

        if assigned_sp_id and assigned_sp_id.isdigit():
            new_sp = User.objects.filter(pk=int(assigned_sp_id)).first()
            if new_sp:
                lead.salesperson = new_sp

        lead.save()

        interactions_data = [
            {
                'id': item.id,
                'author': item.author.username if item.author else 'System',
                'type': item.get_interaction_type_display(),
                'notes': item.notes,
                'rating': item.rating_given,
                'created_at': item.created_at.strftime('%b %d, %Y %H:%M'),
            } for item in lead.interactions.all()
        ]

        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
            return JsonResponse({
                'status': 'ok',
                'lead_id': lead.id,
                'rating': lead.rating,
                'salesperson': lead.salesperson.username,
                'salesperson_id': lead.salesperson.id,
                'interactions': interactions_data
            })
        return redirect('crm_dashboard')

    interactions_data = [
        {
            'id': item.id,
            'author': item.author.username if item.author else 'System',
            'type': item.get_interaction_type_display(),
            'notes': item.notes,
            'rating': item.rating_given,
            'created_at': item.created_at.strftime('%b %d, %Y %H:%M'),
        } for item in lead.interactions.all()
    ]
    return JsonResponse({
        'status': 'ok',
        'lead_id': lead.id,
        'opportunity_name': lead.opportunity_name,
        'contact_name': lead.contact_name,
        'company_name': lead.company_name or '',
        'rating': lead.rating,
        'salesperson': lead.salesperson.username,
        'salesperson_id': lead.salesperson.id,
        'interactions': interactions_data
    })


@login_required
def crm_interactions_list(request):
    if not is_sales(request.user):
        return redirect('dashboard')
    
    if request.method == 'POST':
        target_id = request.POST.get('target_id', '')
        activity_type = request.POST.get('activity_type', 'Call')
        outcome = request.POST.get('outcome', 'Positive')
        contact_person = request.POST.get('contact_person', '').strip()
        notes = request.POST.get('notes', '').strip()
        next_step = request.POST.get('next_step', '').strip()
        next_step_date_str = request.POST.get('next_step_date', '').strip()

        next_step_date = parse_date(next_step_date_str) if next_step_date_str else None

        lead = None
        client = None
        if target_id.startswith('lead_'):
            lead_id = target_id.replace('lead_', '')
            if lead_id.isdigit():
                lead = Lead.objects.filter(pk=int(lead_id)).first()
        elif target_id.startswith('client_'):
            client_id = target_id.replace('client_', '')
            if client_id.isdigit():
                client = Client.objects.filter(pk=int(client_id)).first()

        if (lead or client) and notes:
            interaction = LeadInteraction.objects.create(
                lead=lead,
                client=client,
                author=request.user,
                activity_type=activity_type,
                outcome=outcome,
                contact_person=contact_person,
                notes=notes,
                next_step=next_step,
                next_step_date=next_step_date
            )
            
            if lead:
                lead.last_contact_date = timezone.now().date()
                if next_step:
                    lead.next_action = next_step
                if next_step_date:
                    lead.next_action_date = next_step_date
                lead.save()
            elif client:
                if next_step_date:
                    client.next_followup_date = next_step_date
                client.save()

        return redirect('crm_interactions_list')

    interactions = LeadInteraction.objects.select_related('lead', 'client', 'author', 'lead__salesperson', 'client__account_manager').all().order_by('-created_at')
    
    selected_sp = request.GET.get('salesperson')
    if selected_sp and selected_sp.isdigit():
        interactions = interactions.filter(Q(lead__salesperson_id=int(selected_sp)) | Q(client__account_manager_id=int(selected_sp)) | Q(author_id=int(selected_sp)))

    q = (request.GET.get('q') or '').strip()
    if q:
        interactions = interactions.filter(
            Q(notes__icontains=q) |
            Q(contact_person__icontains=q) |
            Q(lead__company_name__icontains=q) |
            Q(client__company_name__icontains=q)
        )

    sales_users = list(User.objects.filter(Q(groups__name='Sales') | Q(is_superuser=True)).distinct())
    all_leads = Lead.objects.all().order_by('-created_at')
    all_clients = Client.objects.all().order_by('-created_at')

    return render(request, "crm_interactions.html", {
        "interactions": interactions,
        "sales_users": sales_users,
        "all_leads": all_leads,
        "all_clients": all_clients,
        "selected_sp": int(selected_sp) if selected_sp and selected_sp.isdigit() else None,
        "query": q,
    })


@login_required
def generate_quotation_pdf(request, id):
    if not is_sales(request.user):
        return redirect('dashboard')
    quote = get_object_or_404(Quotation, id=id, salesperson=request.user)
    valid_until = quote.created_at + timezone.timedelta(days=30)
    context = {
        'quote': quote,
        'date': quote.created_at.strftime('%B %d, %Y'),
        'valid_until': valid_until.strftime('%B %d, %Y'),
    }
    return render(request, 'quotation_document.html', context)

@login_required
def generate_proforma_pdf(request, id):
    if not is_sales(request.user):
        return redirect('dashboard')
    quote = get_object_or_404(Quotation, id=id, salesperson=request.user)
    context = {
        'quote': quote,
        'date': quote.created_at.strftime('%B %d, %Y'),
    }
    return render(request, 'proforma_invoice_document.html', context)

@login_required
def generate_lead_quotation(request, pk):
    if not is_sales(request.user):
        return redirect('dashboard')
    lead = get_object_or_404(Lead, pk=pk, salesperson=request.user)
    valid_until = timezone.now() + timezone.timedelta(days=30)
    context = {
        'lead': lead,
        'date': timezone.now().strftime('%B %d, %Y'),
        'valid_until': valid_until.strftime('%B %d, %Y'),
    }
    return render(request, 'quotation_document.html', context)

@login_required
def generate_lead_proforma(request, pk):
    if not is_sales(request.user):
        return redirect('dashboard')
    lead = get_object_or_404(Lead, pk=pk, salesperson=request.user)
    context = {
        'lead': lead,
        'date': timezone.now().strftime('%B %d, %Y'),
    }
    return render(request, 'proforma_invoice_document.html', context)

@login_required
def crm_analytics(request):
    if not is_sales(request.user):
        return redirect('dashboard')

    now = timezone.now()
    
    # 1. Monthly revenue lines calculations
    months_12 = []
    year = now.year
    month = now.month
    for i in range(12):
        m = month - i
        y = year
        while m <= 0:
            m += 12
            y -= 1
        months_12.append((y, m))
    months_12.reverse()

    monthly_revenue = []
    for y, m in months_12:
        total = Lead.objects.filter(
            stage='Won',
            won_at__year=y,
            won_at__month=m
        ).aggregate(total=Sum('revenue'))['total'] or Decimal('0.00')
        
        month_name = datetime(y, m, 1).strftime('%b %Y')
        monthly_revenue.append({
            'month': month_name,
            'revenue': float(total)
        })

    # 2. Win rate by sales reps
    sales_users = User.objects.filter(
        groups__name='Sales'
    ).distinct() | User.objects.filter(is_superuser=True).distinct()
    sales_users = list(set(sales_users))

    win_rates = []
    for u in sales_users:
        total_assigned = Lead.objects.filter(salesperson=u).count()
        if total_assigned > 0:
            total_won = Lead.objects.filter(salesperson=u, stage='Won').count()
            rate = (total_won / total_assigned) * 100
            full_name = f"{u.first_name} {u.last_name}".strip() or u.username
            win_rates.append({
                'name': full_name,
                'win_rate': round(rate, 2)
            })

    # 3. Top 10 products sold pie chart values
    leads_products = Lead.objects.filter(
        stage='Won',
        product__isnull=False
    ).values('product__sku', 'product__name').annotate(
        total_qty=Sum('quantity')
    )
    
    quotations_products = Quotation.objects.all().values('product__sku', 'product__name').annotate(
        total_qty=Sum('quantity')
    )

    product_totals = {}
    for item in leads_products:
        sku = item['product__sku']
        name = item['product__name']
        qty = float(item['total_qty'] or 0)
        key = f"{sku} - {name}"
        product_totals[key] = product_totals.get(key, 0) + qty

    for item in quotations_products:
        sku = item['product__sku']
        name = item['product__name']
        qty = float(item['total_qty'] or 0)
        key = f"{sku} - {name}"
        product_totals[key] = product_totals.get(key, 0) + qty

    sorted_products = sorted(product_totals.items(), key=lambda x: x[1], reverse=True)[:10]
    top_products_data = [{'product': k, 'quantity': v} for k, v in sorted_products]

    # 4. Lead conversion funnel counts and drop-offs
    new_count = Lead.objects.filter(stage='New').count()
    qualified_count = Lead.objects.filter(stage='Qualified').count()
    proposition_count = Lead.objects.filter(stage='Proposition').count()
    won_count = Lead.objects.filter(stage='Won').count()

    dropoffs = {}
    if new_count > 0:
        dropoffs['New_to_Qualified'] = round(((new_count - qualified_count) / new_count) * 100, 1)
    else:
        dropoffs['New_to_Qualified'] = 0.0

    if qualified_count > 0:
        dropoffs['Qualified_to_Proposition'] = round(((qualified_count - proposition_count) / qualified_count) * 100, 1)
    else:
        dropoffs['Qualified_to_Proposition'] = 0.0

    if proposition_count > 0:
        dropoffs['Proposition_to_Won'] = round(((proposition_count - won_count) / proposition_count) * 100, 1)
    else:
        dropoffs['Proposition_to_Won'] = 0.0

    funnel_data = {
        'New': new_count,
        'Qualified': qualified_count,
        'Proposition': proposition_count,
        'Won': won_count,
        'dropoffs': dropoffs
    }

    # 5. Average deal size trend (Last 6 months)
    months_6 = months_12[-6:]
    avg_deal_data = []
    for y, m in months_6:
        avg_size = Lead.objects.filter(
            stage='Won',
            won_at__year=y,
            won_at__month=m
        ).aggregate(avg_val=Avg('revenue'))['avg_val'] or Decimal('0.00')
        
        month_name = datetime(y, m, 1).strftime('%b %Y')
        avg_deal_data.append({
            'month': month_name,
            'avg_deal_size': float(avg_size)
        })

    # 6. Stale leads calculation
    thirty_days_ago = now - timezone.timedelta(days=30)
    stale_leads_count = Lead.objects.exclude(
        stage__in=['Won', 'Lost']
    ).filter(
        updated_at__lt=thirty_days_ago
    ).count()

    # Top KPI counts
    total_pipeline = Lead.objects.exclude(stage__in=['Won', 'Lost']).aggregate(total=Sum('revenue'))['total'] or Decimal('0.00')
    global_total = Lead.objects.count()
    global_won = Lead.objects.filter(stage='Won').count()
    global_win_rate = (global_won / global_total * 100) if global_total > 0 else 0.0

    context_data = {
        'monthly_revenue': monthly_revenue,
        'win_rates': win_rates,
        'top_products': top_products_data,
        'funnel': funnel_data,
        'deal_sizes': avg_deal_data,
        'stale_leads_count': stale_leads_count,
        'total_pipeline_value': float(total_pipeline),
        'global_win_rate': round(global_win_rate, 2),
    }

    context_json = json.dumps(context_data, cls=DjangoJSONEncoder)

    return render(request, "crm_analytics.html", {
        "analytics_data_json": context_json,
        "stale_leads_count": stale_leads_count,
        "total_pipeline_value": total_pipeline,
        "global_win_rate": global_win_rate,
    })


@login_required
def crm_leads_list(request):
    if not is_sales(request.user):
        return redirect('dashboard')
    
    leads = Lead.objects.all().select_related('salesperson').order_by('-created_at')
    
    q = (request.GET.get('q') or '').strip()
    if q:
        leads = leads.filter(
            Q(lead_code__icontains=q) |
            Q(company_name__icontains=q) |
            Q(contact_name__icontains=q) |
            Q(city__icontains=q) |
            Q(country__icontains=q) |
            Q(products_of_interest__icontains=q)
        )
        
    status = request.GET.get('status')
    if status:
        leads = leads.filter(stage=status)
        
    priority = request.GET.get('priority')
    if priority:
        leads = leads.filter(priority=priority)
        
    country = request.GET.get('country')
    if country:
        leads = leads.filter(country=country)
        
    manager = request.GET.get('manager')
    if manager and manager.isdigit():
        leads = leads.filter(salesperson_id=int(manager))
        
    sales_users = list(User.objects.filter(Q(groups__name='Sales') | Q(is_superuser=True)).distinct())

    if request.method == 'POST':
        form = LeadForm(request.POST)
        if form.is_valid():
            lead = form.save(commit=False)
            if not lead.salesperson_id:
                lead.salesperson = request.user
            lead.save()
            return redirect('crm_leads_list')

    return render(request, "crm_leads.html", {
        "leads": leads,
        "sales_users": sales_users,
        "query": q,
        "selected_status": status,
        "selected_priority": priority,
        "selected_country": country,
        "selected_manager": int(manager) if manager and manager.isdigit() else None,
    })


@login_required
def crm_clients_list(request):
    if not is_sales(request.user):
        return redirect('dashboard')
    
    clients = Client.objects.all().select_related('account_manager').order_by('-created_at')
    
    q = (request.GET.get('q') or '').strip()
    if q:
        clients = clients.filter(
            Q(client_code__icontains=q) |
            Q(company_name__icontains=q) |
            Q(contact_name__icontains=q) |
            Q(city__icontains=q) |
            Q(country__icontains=q)
        )

    status = request.GET.get('status')
    if status:
        clients = clients.filter(status=status)

    country = request.GET.get('country')
    if country:
        clients = clients.filter(country=country)

    sales_users = list(User.objects.filter(Q(groups__name='Sales') | Q(is_superuser=True)).distinct())

    return render(request, "crm_clients.html", {
        "clients": clients,
        "sales_users": sales_users,
        "query": q,
        "selected_status": status,
        "selected_country": country,
    })


@login_required
def crm_client_create(request):
    if not is_sales(request.user):
        return redirect('dashboard')
    if request.method == 'POST':
        form = ClientForm(request.POST)
        if form.is_valid():
            client = form.save(commit=False)
            if not client.account_manager_id:
                client.account_manager = request.user
            client.save()
    return redirect('crm_clients_list')


@login_required
def crm_client_edit(request, pk):
    if not is_sales(request.user):
        return redirect('dashboard')
    client = get_object_or_404(Client, pk=pk)
    if request.method == 'POST':
        form = ClientForm(request.POST, instance=client)
        if form.is_valid():
            form.save()
    return redirect('crm_clients_list')


@login_required
def crm_client_delete(request, pk):
    if not is_sales(request.user):
        return redirect('dashboard')
    client = get_object_or_404(Client, pk=pk)
    if request.method == 'POST':
        client.delete()
    return redirect('crm_clients_list')


@login_required
def crm_geography(request):
    if not is_sales(request.user):
        return redirect('dashboard')
    
    cities_master = [
        ("United Arab Emirates", "Dubai"), ("United Arab Emirates", "Abu Dhabi"),
        ("United Arab Emirates", "Sharjah"), ("United Arab Emirates", "Ajman"),
        ("United Arab Emirates", "Ras Al Khaimah"), ("Saudi Arabia", "Riyadh"),
        ("Saudi Arabia", "Jeddah"), ("Saudi Arabia", "Dammam"), ("Oman", "Muscat"),
        ("Qatar", "Doha"), ("Kuwait", "Kuwait City"), ("Bahrain", "Manama"),
        ("Iraq", "Baghdad"), ("Jordan", "Amman"), ("Lebanon", "Beirut"),
        ("Egypt", "Cairo")
    ]
    
    geo_data = []
    for country, city in cities_master:
        c_leads = Lead.objects.filter(country=country, city=city)
        c_clients = Client.objects.filter(country=country, city=city)
        
        tot_leads = c_leads.count()
        open_leads = c_leads.exclude(stage__in=['Won', 'Lost']).count()
        hot_leads = c_leads.filter(priority='Hot').exclude(stage__in=['Won', 'Lost']).count()
        won_deals = c_leads.filter(stage='Won').count()
        pipe_val = c_leads.exclude(stage__in=['Won', 'Lost']).aggregate(total=Sum('revenue'))['total'] or Decimal('0.00')
        act_clients = c_clients.filter(status='Active').count()
        sales_ytd = c_clients.aggregate(total=Sum('sales_ytd'))['total'] or Decimal('0.00')
        acts_30d = LeadInteraction.objects.filter(
            Q(lead__country=country, lead__city=city) | Q(client__country=country, client__city=city),
            created_at__gte=timezone.now() - timezone.timedelta(days=30)
        ).count()
        
        if tot_leads > 0 or act_clients > 0 or sales_ytd > 0:
            geo_data.append({
                'country': country,
                'city': city,
                'total_leads': tot_leads,
                'open_leads': open_leads,
                'hot_leads': hot_leads,
                'won_deals': won_deals,
                'pipeline_val': pipe_val,
                'active_clients': act_clients,
                'sales_ytd': sales_ytd,
                'acts_30d': acts_30d
            })
        
    return render(request, "crm_geography.html", {
        "geo_data": geo_data
    })

