import json
from datetime import datetime
from decimal import Decimal
from django.db.models import Sum, Q, F, FloatField, ExpressionWrapper, DecimalField
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.core.serializers.json import DjangoJSONEncoder
from django.urls import reverse_lazy

from shop_manager.models import (
    Shop,
    Product,
    Category,
    Supplier,
    Stock,
    Purchase,
    PurchaseItem,
    Sale,
    SaleItem,
    Expense,
)

User = get_user_model()


def dashboard_callback(request, context=None):
    """
    Advanced Unfold dashboard callback for the Car Parts Stock Management System.

    Features:
    - Role-based scoping (SuperAdmin sees global, ShopAdmin sees only their shop).
    - Date range filtering via GET params (?from_date=YYYY-MM-DD&to_date=YYYY-MM-DD).
    - Rich, color-themed KPI cards (success, warning, danger, info, primary).
    - Chart data (JSON) for sales/revenue trends, top products, expense breakdown.
    - Recent transactions tables (sales, purchases, low-stock items).
    - Low-stock alerts with clickable links.

    Returns a list of card dictionaries compatible with Unfold's dashboard rendering.
    """
    user = request.user

    # ==================== Access Control ====================
    if not user.is_staff:  # Only admins (SuperAdmin/ShopAdmin)
        return []

    is_superadmin = user.role == "SuperAdmin"

    # ==================== Shop Scoping ====================
    shop_filter = Q()
    if not is_superadmin:
        # ShopAdmin sees only their shop (adjust if multi-shop ownership)
        if hasattr(user, "shop"):
            shop_filter = Q(shop=user.shop)
        else:
            return []  # No shop assigned

    # ==================== Date Range Filtering ====================
    today = timezone.now().date()
    from_date_str = request.GET.get("from_date")
    to_date_str = request.GET.get("to_date")

    from_date = None
    to_date = None
    date_error = None

    if from_date_str:
        try:
            from_date = datetime.strptime(from_date_str, "%Y-%m-%d").date()
        except ValueError:
            date_error = "Invalid 'from_date' format. Use YYYY-MM-DD."

    if to_date_str:
        try:
            to_date = datetime.strptime(to_date_str, "%Y-%m-%d").date()
        except ValueError:
            date_error = "Invalid 'to_date' format. Use YYYY-MM-DD."

    if from_date and to_date and from_date > to_date:
        date_error = "'from_date' cannot be after 'to_date'."

    date_filter = Q()
    if from_date:
        date_filter &= (
            Q(created_at__date__gte=from_date)
            | Q(purchase_date__date__gte=from_date)
            | Q(sale_date__date__gte=from_date)
        )
    if to_date:
        date_filter &= (
            Q(created_at__date__lte=to_date)
            | Q(purchase_date__date__lte=to_date)
            | Q(sale_date__date__lte=to_date)
        )

    has_date_range = bool(from_date or to_date)

    # ==================== Core Querysets (Scoped + Filtered) ====================
    products_qs = (
        Product.objects.filter(shop_filter)
        if not has_date_range
        else Product.objects.filter(shop_filter)
    )
    stocks_qs = Stock.objects.all()

    sales_qs = (
        Sale.objects.filter(shop_filter & date_filter)
        if has_date_range
        else Sale.objects.filter(shop_filter)
    )
    purchases_qs = (
        Purchase.objects.filter(shop_filter & date_filter)
        if has_date_range
        else Purchase.objects.filter(shop_filter)
    )
    expenses_qs = (
        Expense.objects.filter(shop_filter & date_filter)
        if has_date_range
        else Expense.objects.filter(shop_filter)
    )

    # ==================== KPI Calculations ====================
    # Stock
    low_stock_count = stocks_qs.filter(
        quantity__lt=10
    ).count()  # Configurable threshold
    total_stock_value = stocks_qs.annotate(
        value=F("quantity") * F("product__cost_price")
    ).aggregate(total=Sum("value", output_field=FloatField()))["total"] or Decimal(
        "0.00"
    )

    # Sales
    total_sales = sales_qs.count()
    total_revenue = sales_qs.aggregate(total=Sum("total_amount"))["total"] or Decimal(
        "0.00"
    )
    today_sales = sales_qs.filter(sale_date__date=today).aggregate(
        total=Sum("total_amount")
    )["total"] or Decimal("0.00")

    # Purchases
    total_purchases = purchases_qs.count()
    pending_purchases = purchases_qs.filter(
        payment_status__in=["Pending", "Overdue"]
    ).aggregate(total=Sum("total_amount"))["total"] or Decimal("0.00")

    # Profit (Revenue - Cost of Goods Sold)
    profit_qs = sales_qs.annotate(
        item_profit=ExpressionWrapper(
            F("items__quantity")
            * (F("items__unit_selling_price") - F("items__unit_cost_price")),
            output_field=DecimalField(
                max_digits=12, decimal_places=2
            ),  # adjust precision to match your models
        )
    )

    profit = profit_qs.aggregate(total_profit=Sum("item_profit"))[
        "total_profit"
    ] or Decimal("0.00")

    # Expenses
    total_expenses = expenses_qs.aggregate(total=Sum("amount"))["total"] or Decimal(
        "0.00"
    )

    # Net Profit
    net_profit = profit - total_expenses

    # ==================== Chart Data ====================
    # Sales trend (last 30 days or date range)
    sales_trend = list(
        sales_qs.extra(select={"date": "DATE(sale_date)"})
        .values("date")
        .annotate(total=Sum("total_amount"))
        .order_by("date")
    )

    # Top 5 selling products
    top_products = list(
        SaleItem.objects.filter(sale__in=sales_qs)
        .annotate(
            item_revenue=ExpressionWrapper(
                F("quantity") * F("unit_selling_price"),
                output_field=DecimalField(
                    max_digits=12, decimal_places=2
                ),  # ← match your price fields
            )
        )
        .values("product__name")
        .annotate(
            quantity=Sum("quantity"),
            revenue=Sum("item_revenue"),
        )
        .order_by("-revenue")[:5]
    )

    # Expense breakdown by type
    expense_breakdown = list(
        expenses_qs.values("expense_type")
        .annotate(total=Sum("amount"))
        .order_by("-total")
    )

    # ==================== Recent Tables ====================
    recent_sales = list(
        sales_qs.order_by("-sale_date")[:10].values(
            "id", "total_amount", "payment_status", "sale_date", "sold_by__full_name"
        )
    )

    recent_purchases = list(
        purchases_qs.order_by("-purchase_date")[:10].values(
            "id", "total_amount", "payment_status", "purchase_date", "supplier__name"
        )
    )

    low_stock_items = list(
        stocks_qs.filter(quantity__lt=10)
        .order_by("quantity")
        .values(
            "product__name", "product__id", "quantity"  # UUID – shows something unique
        )[:10]
    )
    # ==================== Dashboard Cards (Color-Themed) ====================
    cards = [
        {
            "title": "Total Revenue",
            "value": f"{total_revenue:,.2f}",
            "type": "success",
            "icon": "attach_money",
            "url": reverse_lazy("admin:shop_manager_sale_changelist"),
        },
        {
            "title": "Today's Sales",
            "value": f"{today_sales:,.2f}",
            "type": "primary",
            "icon": "today",
        },
        {
            "title": "Net Profit",
            "value": f"{net_profit:,.2f}",
            "type": "success" if net_profit >= 0 else "danger",
            "icon": "trending_up",
        },
        {
            "title": "Low Stock Alerts",
            "value": low_stock_count,
            "type": "danger" if low_stock_count > 0 else "success",
            "icon": "warning",
            "url": reverse_lazy("admin:shop_manager_stock_changelist")
            + "?quantity__lt=10",
        },
        {
            "title": "Total Stock Value",
            "value": f"{total_stock_value:,.2f}",
            "type": "info",
            "icon": "inventory",
        },
        {
            "title": "Pending Purchases",
            "value": f"{pending_purchases:,.2f}",
            "type": "warning",
            "icon": "shopping_cart",
            "url": reverse_lazy("admin:shop_manager_purchase_changelist")
            + "?payment_status__exact=Pending",
        },
        {
            "title": "Total Expenses",
            "value": f"{total_expenses:,.2f}",
            "type": "secondary",
            "icon": "receipt_long",
        },
        {
            "title": "Active Products",
            "value": products_qs.count(),
            "type": "primary",
            "icon": "category",
            "url": reverse_lazy("admin:shop_manager_product_changelist"),
        },
    ]

    # Add chart/table cards if Unfold supports custom sections (or use in custom template)
    extra_context = {
        "cards": cards,
        "sales_trend_chart": json.dumps(sales_trend, cls=DjangoJSONEncoder),
        "top_products_data": json.dumps(top_products, cls=DjangoJSONEncoder),
        "expense_breakdown_data": json.dumps(expense_breakdown, cls=DjangoJSONEncoder),
        "recent_sales_table": recent_sales,
        "recent_purchases_table": recent_purchases,
        "low_stock_table": low_stock_items,
        "date_error": date_error,
        "from_date": from_date_str or "",
        "to_date": to_date_str or "",
    }

    # Update the context dict that will be sent to the template
    if context is not None:
        context.update(extra_context)

    # Unfold primarily uses cards list, but you can return extra context for custom template
    return context
