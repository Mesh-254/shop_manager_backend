# shop_manager/dashboard.py
import json
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum, Count, F, ExpressionWrapper, DecimalField
from django.utils import timezone
from django.urls import reverse
from django.contrib import messages

from accounts.models import User, UserRole
from shop_manager.models import (
    Shop,
    Product,
    Stock,
    Sale,
    SaleItem,
    Purchase,
    PurchaseItem,
    Expense,
    Category,
    Supplier,
    OfflineSyncLog,
    Returns,  # if you want to include returns metrics later
)


def get_filtered_qs(request, queryset):
    """
    Apply role-based scoping to any queryset.
    - SuperAdmin: full access
    - ShopAdmin: only their shop (handles direct & nested relationships)
    - Others: empty
    """
    user = request.user

    if user.role == UserRole.SUPER_ADMIN:
        return queryset

    if user.role != UserRole.SHOP_ADMIN or not user.shop:
        return queryset.none()

    model = queryset.model

    # Models with direct 'shop' field
    if hasattr(model, 'shop') and hasattr(model._meta.get_field('shop'), 'remote_field'):
        return queryset.filter(shop=user.shop)

    # Special cases: nested shop relationships
    if model == Stock:
        return queryset.filter(product__shop=user.shop)

    if model == SaleItem:
        return queryset.filter(sale__shop=user.shop)

    if model == PurchaseItem:
        return queryset.filter(purchase__shop=user.shop)

    if model == Returns:
        return queryset.filter(sale_item__sale__shop=user.shop)

    # Default: no access
    return queryset.none()


def dashboard_callback(request, context=None):
    """
    Modern, professional dashboard callback for Unfold admin.
    Displays key metrics, recent activity, and low stock alerts.
    Fully scoped by user role.
    """
    now = timezone.now()
    this_month_start = now.replace(day=1)
    thirty_days_ago = now - timedelta(days=30)

    user = request.user

    # Early exit for users without a shop
    if user.role == UserRole.SHOP_ADMIN and not user.shop:
        messages.warning(request, "Your account is not assigned to any shop. Contact support.")
        return {
            "cards": [],
            "recent_sales": [],
            "low_stock_alerts": [],
            "show_date_filter": False,
            "error": "No shop assigned",
        }

    # Scoped querysets helper
    def filtered(qs):
        return get_filtered_qs(request, qs)

    # ─── Core Querysets ────────────────────────────────────────────────
    sales_qs = filtered(Sale.objects.all())
    purchases_qs = filtered(Purchase.objects.all())
    expenses_qs = filtered(Expense.objects.all())
    stock_qs = filtered(Stock.objects.all())
    # returns_qs = filtered(Returns.objects.all())  # optional

    # ─── This Month Metrics ────────────────────────────────────────────
    sales_this_month = sales_qs.filter(sale_date__gte=this_month_start)
    revenue_this_month = sales_this_month.aggregate(
        total=Sum("total_amount", default=Decimal("0.00"))
    )["total"]

    expenses_this_month = expenses_qs.filter(date__gte=this_month_start).aggregate(
        total=Sum("amount", default=Decimal("0.00"))
    )["total"]

    # ─── All-Time Metrics ──────────────────────────────────────────────
    total_revenue = sales_qs.aggregate(
        total=Sum("total_amount", default=Decimal("0.00"))
    )["total"]

    total_sales_count = sales_qs.count()
    total_orders_count = total_sales_count  # assuming 1 sale = 1 order

    avg_order_value = (
        revenue_this_month / sales_this_month.count()
        if sales_this_month.exists()
        else Decimal("0.00")
    )

    # ─── Stock Health ──────────────────────────────────────────────────
    low_stock_threshold = 10  # can be made configurable per shop later
    low_stock_count = stock_qs.filter(
        quantity__lte=F("product__reorder_level"),
        quantity__gt=0
    ).count()

    out_of_stock_count = stock_qs.filter(quantity=0).count()

    total_stock_value = stock_qs.aggregate(
        value=Sum(
            ExpressionWrapper(
                F("quantity") * F("product__selling_price"),
                output_field=DecimalField(max_digits=15, decimal_places=2)
            ),
            default=Decimal("0.00")
        )
    )["value"]

    # ─── Cards (KPIs) ──────────────────────────────────────────────────
    cards = [
        {
            "title": "Total Revenue",
            "value": f"KES {total_revenue:,.2f}",
            "subtitle": f"This month: KES {revenue_this_month:,.2f}",
            "color": "success",
            "icon": "payments",
            "url": "/admin/reports/sales/",
            "help_text": "Total income from all sales",
        },
        {
            "title": "Net Profit (30d)",
            "value": f"KES {(revenue_this_month - expenses_this_month):,.2f}",
            "subtitle": f"Revenue - Expenses",
            "color": "primary" if revenue_this_month > expenses_this_month else "danger",
            "icon": "trending_up" if revenue_this_month > expenses_this_month else "trending_down",
            "url": "/admin/reports/expenses/",
        },
        {
            "title": "Average Order Value",
            "value": f"KES {avg_order_value:,.2f}",
            "subtitle": "This month",
            "color": "info",
            "icon": "calculate",
            "url": "/admin/reports/sales/",
        },
        {
            "title": "Low Stock Items",
            "value": str(low_stock_count),
            "subtitle": f"Out of stock: {out_of_stock_count}",
            "color": "warning" if low_stock_count > 0 else "success",
            "icon": "warning_amber",
            "url": "/admin/shop_manager/stock/?quantity__lte=10",
            "help_text": "Items below reorder level",
        },
        {
            "title": "Inventory Value",
            "value": f"KES {total_stock_value:,.2f}",
            "subtitle": "Current stock value at selling price",
            "color": "secondary",
            "icon": "inventory_2",
            "url": "/admin/shop_manager/stock/",
        },
        {
            "title": "Total Sales",
            "value": str(total_sales_count),
            "subtitle": f"This month: {sales_this_month.count()}",
            "color": "primary",
            "icon": "shopping_cart",
            "url": "/admin/shop_manager/sale/",
        },
    ]

    # ─── Recent Activity ───────────────────────────────────────────────
    recent_sales = sales_qs.order_by("-sale_date")[:8].values(
        "id",
        "sale_date",
        "total_amount",
        "payment_status",
        "sold_by__full_name",
    )

    recent_low_stock = stock_qs.filter(
        quantity__lte=F("product__reorder_level")
    ).order_by("quantity")[:8].values(
        "product__name",
        "quantity",
        "product__category__name",
        "product__shop__name",
    )

    # ─── Final Context ─────────────────────────────────────────────────
    extra_context = {
        "cards": cards,
        "recent_sales": recent_sales,
        "low_stock_alerts": recent_low_stock,
        "show_date_filter": True,
        "current_month": now.strftime("%B %Y"),
        "user_role": user.role,
        "is_superadmin": user.role == UserRole.SUPER_ADMIN,
    }

    if context is not None:
        context.update(extra_context)

    return context